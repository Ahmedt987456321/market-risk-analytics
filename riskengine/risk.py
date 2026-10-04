"""Today's risk numbers and the daily backtest, for every configured method."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import backtest as bt
from .returns import factor_returns, is_absolute
from .explain import explain
from .var import es_contributions, parametric, scenario_pnl, scenario_weights, var_es


@dataclass
class RiskRun:
    as_of: pd.Timestamp
    n_scenarios: int
    first_scenario: pd.Timestamp
    totals: dict                          # method -> {measure: value}
    desks: pd.DataFrame                   # desk, VaR99_standalone, ES975_contribution (primary method)
    backtest: pd.DataFrame                # date, method, var95, var99, pnl
    backtest_summary: dict = field(default_factory=dict)
    dropped_scenarios: int = 0
    explain: dict | None = None
    levels0: np.ndarray | None = None
    q: np.ndarray | None = None
    prev_date: pd.Timestamp | None = None
    trades: pd.DataFrame | None = None
    stress: list = field(default_factory=list)


def _positions(con, as_of) -> pd.DataFrame:
    long = con.execute("SELECT as_of, instrument_id, quantity FROM positions WHERE as_of <= ?", [as_of.date()]).df()
    wide = long.pivot(index="as_of", columns="instrument_id", values="quantity")
    wide.index = pd.to_datetime(wide.index)
    return wide


def _measures(pnl_total: np.ndarray, weights: np.ndarray, alphas, es_alpha) -> dict:
    out = {f"VaR{round(a * 100)}": var_es(pnl_total, weights, a)[0] for a in alphas}
    out[f"ES{round(es_alpha * 1000)}"] = var_es(pnl_total, weights, es_alpha)[1]
    return out


def forecast(method: str, spec: dict, levels0, window, absolute, columns, instruments, fx_map, q, alphas, es_alpha):
    """Returns (measures, scenario P&L by position or None for parametric)."""
    if method == "parametric":
        return parametric(levels0, window, absolute, columns, instruments, fx_map, q,
                          spec["decay"], alphas, es_alpha), None
    pnl = scenario_pnl(levels0, window, absolute, columns, instruments, fx_map, q)
    w = scenario_weights(len(window), spec.get("decay"))
    return _measures(pnl.sum(axis=1), w, alphas, es_alpha), (pnl, w)


def run(con, cfg, as_of) -> RiskRun:
    r = cfg.risk
    alphas, es_alpha, W = r["confidence"], r["es_confidence"], r["window_days"]
    ffill = cfg.controls["panel"]["ffill_limit_bdays"]
    levels, returns = factor_returns(con, cfg, as_of.date(), ffill)
    as_of = pd.Timestamp(as_of)
    if as_of not in levels.index:
        raise ValueError(f"{as_of.date()} is not a market day in the data")

    instruments = cfg.instruments
    columns = [i.id for i in instruments]
    absolute = np.array([is_absolute(i) for i in instruments])
    positions = _positions(con, as_of).reindex(columns=columns).fillna(0.0)
    desks = [i.desk for i in instruments]

    L = levels.to_numpy(dtype=float)
    R = returns.to_numpy(dtype=float)
    idx = {d: k for k, d in enumerate(levels.index)}

    def window_ending(k: int):
        win = R[max(1, k - W + 1): k + 1]
        ok = ~np.isnan(win).any(axis=1)
        return win[ok], int((~ok).sum())

    def window_dates(k: int):
        win = R[max(1, k - W + 1): k + 1]
        ok = ~np.isnan(win).any(axis=1)
        return levels.index[max(1, k - W + 1): k + 1][ok]

    # --- today ---
    k = idx[as_of]
    q = positions.loc[:as_of].iloc[-1].to_numpy()
    win, dropped = window_ending(k)
    totals, primary_pnl = {}, None
    for method, spec in r["methods"].items():
        spec = spec or {}
        totals[method], scen = forecast(method, spec, L[k], win, absolute, columns, instruments, cfg.fx_map, q,
                                        alphas, es_alpha)
        if method == r["primary_method"]:
            primary_pnl = scen

    pnl, w = primary_pnl
    desk_names = sorted(set(desks))
    by_desk = np.stack([pnl[:, [d == name for d in desks]].sum(axis=1) for name in desk_names], axis=1)
    desk_table = pd.DataFrame({
        "desk": desk_names,
        "VaR99_standalone": [var_es(by_desk[:, j], w, 0.99)[0] for j in range(len(desk_names))],
        "ES975_contribution": es_contributions(by_desk, w, es_alpha),
    })

    # --- backtest: forecast at close t-1, compare with hypothetical P&L on t ---
    start = pd.Timestamp(r["backtest"]["start"])
    rows = []
    days = [d for d in levels.index if start <= d <= as_of]
    for d in days:
        kt = idx[d]
        prev = levels.index[kt - 1]
        q_prev = positions.loc[:prev].iloc[-1].to_numpy() if (positions.index <= prev).any() else None
        if q_prev is None or np.isnan(R[kt]).any():
            continue
        win_prev, _ = window_ending(kt - 1)
        realised = scenario_pnl(L[kt - 1], R[kt][None, :], absolute, columns, instruments, cfg.fx_map, q_prev).sum()
        for method, spec in r["methods"].items():
            m, _ = forecast(method, spec or {}, L[kt - 1], win_prev, absolute, columns, instruments, cfg.fx_map,
                            q_prev, alphas, es_alpha)
            rows.append({"date": d.date(), "method": method, "var95": m["VaR95"], "var99": m["VaR99"],
                         "pnl": float(realised)})
    bt_df = pd.DataFrame(rows)

    summary = {}
    last_n = r["backtest"]["traffic_light_days"]
    for method, g in bt_df.groupby("method"):
        g = g.sort_values("date")
        s99 = bt.summarise(g.pnl.to_numpy(), g.var99.to_numpy(), 0.01)
        s95 = bt.summarise(g.pnl.to_numpy(), g.var95.to_numpy(), 0.05)
        tail = g.tail(last_n)
        x_last = int((-tail.pnl > tail.var99).sum())
        zone, plus = bt.traffic_light(len(tail), x_last)
        summary[method] = {"99": s99, "95": s95, "last_n": len(tail), "last_exceptions": x_last,
                           "zone": zone, "plus_factor": plus,
                           "worst_day": g.loc[g.pnl.idxmin(), "date"], "worst_pnl": float(g.pnl.min())}

    # --- explain: what moved VaR since the previous market day ---
    prev_date = levels.index[k - 1]
    q_prev = positions.loc[:prev_date].iloc[-1].to_numpy()
    spec = r["methods"][r["primary_method"]] or {}
    if r["primary_method"] == "parametric":
        raise ValueError("VaR explain is implemented for historical-simulation methods only")
    decay = spec.get("decay")
    ex = explain(
        prev={"q": q_prev, "levels": L[k - 1], "window": window_ending(k - 1)[0], "dates": window_dates(k - 1)},
        cur={"q": q, "levels": L[k], "window": win, "dates": window_dates(k)},
        absolute=absolute, columns=columns, instruments=instruments, fx_map=cfg.fx_map,
        weights_fn=lambda n: scenario_weights(n, decay), es_alpha=es_alpha)
    trades = con.execute("""
        SELECT instrument_id, sum(quantity) AS quantity FROM trades
        WHERE trade_date > ? AND trade_date <= ? GROUP BY 1
    """, [prev_date.date(), as_of.date()]).df()
    if len(trades):
        from .pricing import unit_values_np
        uv = dict(zip(columns, unit_values_np(L[k][None, :], columns, instruments, cfg.fx_map)[0]))
        trades["value_gbp"] = [qty * uv[i] for i, qty in zip(trades.instrument_id, trades.quantity)]

    return RiskRun(as_of=as_of, n_scenarios=len(win), first_scenario=levels.index[max(1, k - W + 1)],
                   totals=totals, desks=desk_table, backtest=bt_df, backtest_summary=summary,
                   dropped_scenarios=dropped, explain=ex, levels0=L[k], q=q, prev_date=prev_date, trades=trades)
