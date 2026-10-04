"""One-page daily risk commentary, written by rules from the run's own numbers.

No language model is involved: every sentence is a template filled from values
the run has already computed and stored, so each claim can be traced back to a
table. Thresholds for the "Attention" list live in config/risk.yaml.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .pricing import unit_values_np

DESK_ORDER = ["UK Equities", "US Equities", "Rates", "FX", "Commodities"]


def gbp(x: float, dp: int = 2) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}GBP {abs(x) / 1e6:,.{dp}f}m"


def signed(x: float, dp: int = 2) -> str:
    return f"{'+' if x >= 0 else '-'}GBP {abs(x) / 1e6:,.{dp}f}m"


def position_table(cfg, rr) -> pd.DataFrame:
    """Market value of every position today, largest gross first."""
    ids = [i.id for i in cfg.instruments]
    uv = unit_values_np(rr.levels0[None, :], ids, cfg.instruments, cfg.fx_map)[0]
    mv = rr.q * uv
    t = pd.DataFrame({"instrument_id": ids, "name": [i.name for i in cfg.instruments],
                      "desk": [i.desk for i in cfg.instruments], "mv": mv})
    t["share_of_gross"] = t.mv.abs() / t.mv.abs().sum()
    return t.sort_values("mv", key=np.abs, ascending=False).reset_index(drop=True)


def attention_items(cfg, rr, results, positions: pd.DataFrame) -> list[str]:
    rules = cfg.risk["commentary"]
    primary = cfg.risk["primary_method"]
    items = []

    top = positions.iloc[0]
    if top.share_of_gross > rules["concentration_share"]:
        items.append(f"Concentration: {top['name']} is {top.share_of_gross:.1%} of gross exposure "
                     f"({gbp(top.mv)}), above the {rules['concentration_share']:.0%} review level.")

    ex = rr.explain["VaR99"]
    change = ex["current"] - ex["previous"]
    if abs(change) > rules["var_move_share"] * ex["previous"]:
        items.append(f"VaR moved {change / ex['previous']:+.1%} in one day ({signed(change)}).")

    for method, s in rr.backtest_summary.items():
        if s["zone"] != "green":
            items.append(f"Backtest: {method} is in the Basel {s['zone']} zone "
                         f"({s['last_exceptions']} exceptions in the last {s['last_n']} days).")
    p_ind = rr.backtest_summary[primary]["99"]["christoffersen_p"]
    if p_ind < rules["independence_p"]:
        items.append(f"Backtest: exceptions of the primary model cluster in time "
                     f"(Christoffersen p = {p_ind:.4f}), so it reacts slowly when volatility jumps.")

    for r in results:
        if r.status in ("WARN", "FAIL"):
            items.append(f"Data ({r.status}): {r.control.replace('_', ' ')}: {r.detail}")
    return items


def render(cfg, rr, results, controls_status: str) -> str:
    primary = cfg.risk["primary_method"]
    m = rr.totals[primary]
    ex_v, ex_e = rr.explain["VaR99"], rr.explain["ES975"]
    change = ex_v["current"] - ex_v["previous"]
    positions = position_table(cfg, rr)
    gross = positions.mv.abs().sum()

    lines = [f"# Daily market risk summary, {rr.as_of:%d %B %Y}", "",
             "Synthetic book, public market data. One-day horizon, GBP.", ""]
    if controls_status == "FAIL":
        lines += ["**Not for use: a data control failed. See the controls report before relying on any figure.**", ""]

    lines += ["## Headline", "",
              f"99% VaR is {gbp(m['VaR99'])} and 97.5% Expected Shortfall is {gbp(m['ES975'])}. "
              f"VaR is {'down' if change < 0 else 'up'} {gbp(abs(change))} ({change / ex_v['previous']:+.1%}) "
              f"from {rr.prev_date:%d %b}. Gross exposure is {gbp(gross, 1)}.", ""]

    # What changed
    parts = {"trades": ex_v["positions"], "market moves": ex_v["levels"], "the scenario window rolling": ex_v["window"]}
    main = max(parts, key=lambda k: abs(parts[k]))
    rest = ", ".join(f"{k} {signed(v)}" for k, v in parts.items() if k != main)
    d_prev, d_cur = rr.explain["var_day_previous"][0], rr.explain["var_day_current"][0]
    scen = (f"The 99% VaR is set by the scenario from {d_cur:%d %b %Y}"
            + ("." if d_cur == d_prev else f", which replaced {d_prev:%d %b %Y}."))
    lines += ["## What changed", "",
              f"The largest effect was {main} ({signed(parts[main])}); {rest}. {scen}"]
    if rr.trades is not None and len(rr.trades):
        t = rr.trades.sort_values("value_gbp", key=abs, ascending=False)
        lines[-1] += " Trades: " + ", ".join(f"{r.instrument_id} {signed(r.value_gbp)}" for r in t.itertuples()) + "."
    lines.append("")

    # Where the risk sits
    d = rr.desks.set_index("desk")
    order = [x for x in DESK_ORDER if x in d.index]
    shares = {k: d.loc[k, "ES975_contribution"] / m["ES975"] for k in order}
    big = sorted(shares, key=lambda k: -shares[k])
    top = positions.iloc[0]
    start_mv = cfg.book["targets_gbp"][top.instrument_id]
    lines += ["## Where the risk sits", "",
              f"{big[0]} contributes {shares[big[0]]:.0%} of Expected Shortfall and {big[1]} {shares[big[1]]:.0%}. "
              + (f"{big[-1]} reduces it ({shares[big[-1]]:+.0%}). " if shares[big[-1]] < 0 else "")
              + f"The largest single position is {top['name']} at {gbp(top.mv)}, {top.share_of_gross:.1%} of gross "
              f"exposure (it started at {gbp(start_mv)} on {pd.Timestamp(cfg.book['start']):%d %b %Y}).", ""]

    # Stress
    hist = [s for s in rr.stress if s.kind == "historical" and s.window is not None]
    if hist:
        worst = min(hist, key=lambda s: s.pnl)
        repeat = [s.top_positions[0][0] for s in hist if s.top_positions]
        common = repeat[0] if repeat and all(x == repeat[0] for x in repeat) else None
        line = (f"The worst historical scenario is {worst.name}: {gbp(worst.pnl)} over the window "
                f"(worst point {gbp(worst.worst_pnl)}), {abs(worst.pnl) / m['VaR99']:.1f} times today's 99% VaR.")
        if common:
            name = next(i.name for i in cfg.instruments if i.id == common)
            line += f" {name} is the largest single loss in every historical scenario."
        hyp = min((s for s in rr.stress if s.kind == "hypothetical"), key=lambda s: s.pnl)
        line += f" The worst hypothetical shock is {hyp.name.lower()}: {gbp(hyp.pnl)}."
        lines += ["## Stress", "", line, ""]

    # Model health
    s = rr.backtest_summary[primary]
    a = s["99"]
    lines += ["## Model health", "",
              f"Since {pd.Timestamp(cfg.risk['backtest']['start']):%d %b %Y}, losses exceeded the primary model's 99% "
              f"VaR on {a['exceptions']} of {a['n']} days (expected {a['expected']:.1f}; Kupiec p = {a['kupiec_p']:.2f}). "
              f"In the last {s['last_n']} days: {s['last_exceptions']}, Basel {s['zone']} zone.", ""]

    # Data
    flagged = [r for r in results if r.status in ("WARN", "FAIL")]
    lines += ["## Data", "",
              f"Controls status: {controls_status}. "
              + (f"{len(flagged)} of {len(results)} controls raised an issue; details are listed below."
                 if flagged else f"All {len(results)} controls passed."), ""]

    items = attention_items(cfg, rr, results, positions)
    lines += ["## Attention", ""] + ([f"- {x}" for x in items] if items else ["- Nothing above review levels."]) + [""]
    return "\n".join(lines)
