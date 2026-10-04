"""The values the Power BI report should show, computed directly from exports/bi/ with pandas.

Compare with the output of tools/verify_powerbi.ps1 (which asks Power BI itself).

    python tools/verify_powerbi_expected.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BI = Path(__file__).resolve().parent.parent / "exports" / "bi"
r = pd.read_csv(BI / "fact_risk.csv")
b = pd.read_csv(BI / "fact_backtest.csv")
p = pd.read_csv(BI / "fact_position_daily.csv")
e = pd.read_csv(BI / "fact_var_explain.csv")
c = pd.read_csv(BI / "fact_controls.csv")
s = pd.read_csv(BI / "dim_scenario.csv")
g = pd.read_csv(BI / "fact_gs_var.csv")
inst = pd.read_csv(BI / "dim_instrument.csv")
m = pd.read_csv(BI / "dim_method.csv")

tot = r[(r.method == "hs_equal") & (r.scope == "total")].set_index("measure").value_gbp / 1e6
ex = e[e.measure == "VaR99"].set_index("component").value_gbp / 1e6
today = p[p.date == p.date.max()].merge(inst[["instrument_id", "name", "desk"]], on="instrument_id")
gross = today.market_value_gbp.abs().sum() / 1e6


def zone(method: str) -> str:
    x = int(b[b.method == method].sort_values("date").tail(250).exception_99.sum())
    return ("Green" if x <= 4 else "Yellow" if x <= 9 else "Red") + f" ({x} exceptions)"


worst = s.sort_values("pnl_gbp").iloc[0]
print("== headline")
print(f"   VaR99 {tot['VaR99']:.4f} | ES975 {tot['ES975']:.4f} | VaR95 {tot['VaR95']:.4f} | "
      f"VaR change {ex['current'] - ex['previous']:+.4f} | Gross {gross:.4f} | Zone {zone('hs_equal')} | "
      f"Checks {int(c.status.isin(['PASS', 'INFO']).sum())} passed, {int((c.status == 'WARN').sum())} warnings, "
      f"{int((c.status == 'FAIL').sum())} failed | Worst {worst.scenario_name}: {worst.pnl_gbp / 1e6:,.1f}m")
print("== backtest by method")
for row in m.itertuples():
    x = b[b.method == row.method]
    print(f"   {row.method_label} | {int(x.exception_99.sum())} | {x.exception_99.mean():.4f} | {zone(row.method)}")
print("== what moved VaR")
for k in ("positions", "levels", "window"):
    print(f"   {k} {ex[k]:+.4f}")
print("== ES by desk")
for row in r[r.measure == "ES975_contribution"].itertuples():
    print(f"   {row.scope_id} {row.value_gbp / 1e6:.4f}")
print("== stress")
for row in s.itertuples():
    print(f"   {row.scenario_name} {row.pnl_gbp / 1e6:+.4f} | {row.worst_point_gbp / 1e6 if row.worst_point_gbp == row.worst_point_gbp else float('nan'):+.4f}")
print("== gross by desk")
for desk, v in today.groupby("desk").market_value_gbp.apply(lambda v: v.abs().sum() / 1e6).items():
    print(f"   {desk} {v:.4f}")
print("== top positions")
today["share"] = today.market_value_gbp.abs() / today.market_value_gbp.abs().sum()
for row in today.sort_values("share", ascending=False).head(5).itertuples():
    print(f"   {row.name} {row.market_value_gbp / 1e6:.4f} | {row.share:.4f}")
q = g[(g.measure == "average") & (g.horizon == "quarter")].sort_values("period_end")
print(f"== goldman\n   latest {q.total.iloc[-1]:.0f} | quarters {len(q)}")
print(f"== row counts\n   positions {len(p)} | backtest {len(b)} | gs_var {len(g)}")
