"""Independent check of the engine's historical-simulation numbers.

Recomputes scenario P&L with closed-form formulas per asset type (not the
engine's pricing code), then compares VaR, ES and the backtest P&L.

    python tools/verify_var.py 2026-09-29
"""
from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from riskengine import var as V                      # noqa: E402  (only for the estimator, checked separately below)
from riskengine.config import load_config             # noqa: E402
from riskengine.returns import factor_returns          # noqa: E402

as_of = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2026-09-29")
cfg = load_config()
con = duckdb.connect("data/risk.duckdb", read_only=True)
levels, rets = factor_returns(con, cfg, as_of.date(), cfg.controls["panel"]["ffill_limit_bdays"])
W = cfg.risk["window_days"]
win = rets.loc[:as_of].iloc[-W:]
L = levels.loc[as_of]
q = con.execute("SELECT instrument_id, quantity FROM positions WHERE as_of = ?", [as_of.date()]).df() \
       .set_index("instrument_id")["quantity"]


def closed_form_pnl(L, r, q) -> pd.Series:
    """P&L per position for one scenario r (a row of returns), written out by hand."""
    out = {}
    fx = {"USD": "USD", "EUR": "EUR", "JPY": "JPY"}
    for i in cfg.instruments:
        rF = r[fx[i.currency]] if i.currency != "GBP" else 0.0
        F = L[fx[i.currency]] if i.currency != "GBP" else 1.0
        if i.quote == "fx":
            out[i.id] = q[i.id] / L[i.id] * (np.exp(-r[i.id]) - 1)
        elif i.quote == "yield_pct":
            z = np.exp(-L[i.id] / 100 * i.tenor_years)
            out[i.id] = q[i.id] * z / F * (np.exp(-r[i.id] / 100 * i.tenor_years - rF) - 1)
        else:
            out[i.id] = q[i.id] * L[i.id] / F * (np.exp(r[i.id] - rF) - 1)
    return pd.Series(out)


pnl = np.array([closed_form_pnl(L, row, q).sum() for _, row in win.iterrows()])
loss = np.sort(-pnl)[::-1]                                    # largest loss first
n = len(loss)
k99 = int(np.floor(n * 0.01 + 1e-9))                           # VaR = smallest L with P(loss <= L) >= 99%
print(f"scenarios: {n} ({win.index[0].date()} to {win.index[-1].date()})")
print(f"independent VaR99 = loss #{k99 + 1} largest = {loss[k99]:,.0f}   (1% of {n} = {n * 0.01:.1f})")
print(f"independent VaR95 = loss #{int(np.floor(n * 0.05 + 1e-9)) + 1} largest = {loss[int(np.floor(n * 0.05 + 1e-9))]:,.0f}")
m = int(np.floor(n * 0.025)); frac = n * 0.025 - m
es = (loss[:m].sum() + frac * loss[m]) / (n * 0.025)
print(f"independent ES97.5 = mean of worst {n * 0.025:.1f} = {es:,.0f}")

eng = con.execute("""SELECT measure, value_gbp FROM risk_results WHERE method = 'hs_equal' AND scope = 'total'
                     AND run_id = (SELECT max(run_id) FROM risk_results)""").df().set_index("measure")["value_gbp"]
print("engine:", {k: round(v) for k, v in eng.items()})

# Backtest P&L vs plain mark-to-market change of yesterday's positions (plus dividends received)
bt = con.execute("""SELECT date, pnl FROM backtest WHERE method = 'hs_equal'
                    AND run_id = (SELECT max(run_id) FROM backtest) ORDER BY date""").df()
bt["date"] = pd.to_datetime(bt["date"])
pos = con.execute("SELECT as_of, instrument_id, quantity FROM positions").df() \
         .pivot(index="as_of", columns="instrument_id", values="quantity")
pos.index = pd.to_datetime(pos.index)
div = con.execute("SELECT ex_date, instrument_id, amount FROM dividends").df() \
         .pivot_table(index="ex_date", columns="instrument_id", values="amount", aggfunc="sum")
div.index = pd.to_datetime(div.index)
from riskengine.pricing import unit_values_gbp   # noqa: E402
uv = unit_values_gbp(levels, cfg.instruments, cfg.fx_map)
diffs = []
for d, eng_pnl in zip(bt.date, bt.pnl):
    k = levels.index.get_loc(d); prev = levels.index[k - 1]
    qp = pos.loc[:prev].iloc[-1]
    mtm = (qp * (uv.loc[d] - uv.loc[prev])).drop(["BRENT", "GOLD"]).sum()
    divs = sum(qp[c] * div.loc[d, c] / (1.0 if cfg.instrument(c).currency == "GBP" else levels.loc[d, "USD"])
               for c in div.columns if d in div.index and not np.isnan(div.loc[d, c]))
    comm = closed_form_pnl(levels.loc[prev], rets.loc[d], qp)[["BRENT", "GOLD"]].sum()
    diffs.append(eng_pnl - (mtm + divs + comm))
diffs = np.abs(np.array(diffs))
print(f"backtest P&L vs mark-to-market + dividends: {len(diffs)} days, max abs gap {diffs.max():,.2f} GBP, "
      f"days with gap > 1 GBP: {(diffs > 1).sum()}")

# Where do the gaps come from?
gap = pd.Series(diffs, index=bt.date)
big = gap[gap > 1].sort_values(ascending=False)
div_days = set(div.index)
print(f"gap days that are ex-dividend days: {sum(d in div_days for d in big.index)} of {len(big)}")
print("largest gaps:", [(d.date().isoformat(), round(v)) for d, v in big.head(5).items()])
