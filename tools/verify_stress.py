"""Independent check of the stress results.

Historical: uses only the window's start and end values straight from the
database (prices, yields, FX, proxies), plus dividends paid inside the window,
and closed-form P&L per asset type. The engine instead compounds daily returns
along the path, so the two differ only by dividend reinvestment inside a window.

Hypothetical: closed-form P&L for each shock.

    python tools/verify_stress.py
"""
from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from riskengine.config import load_config   # noqa: E402

cfg = load_config()
con = duckdb.connect("data/risk.duckdb", read_only=True)
run = con.execute("SELECT max(run_id), max(as_of) FROM stress_results").fetchone()
as_of = pd.Timestamp(run[1])
eng = con.execute("SELECT scenario, pnl_gbp FROM stress_results WHERE run_id = ? AND desk = 'total'", [run[0]]).df() \
         .set_index("scenario")["pnl_gbp"]
q = con.execute("SELECT instrument_id, quantity FROM positions WHERE as_of = ?", [as_of.date()]).df() \
       .set_index("instrument_id")["quantity"]


def value(iid: str, date) -> float:
    return con.execute("SELECT close FROM market_data WHERE instrument_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                       [iid, pd.Timestamp(date).date()]).fetchone()[0]


def ref(iid: str, purpose: str, date) -> float:
    return con.execute("""SELECT value FROM reference_prices WHERE instrument_id = ? AND purpose = ? AND date <= ?
                          ORDER BY date DESC LIMIT 1""", [iid, purpose, pd.Timestamp(date).date()]).fetchone()[0]


L = {i.id: value(i.id, as_of) for i in cfg.instruments}


def pnl_closed_form(r: dict) -> float:
    """r: instrument -> move (log for prices/FX, percentage points for yields)."""
    total = 0.0
    for i in cfg.instruments:
        F = 1.0 if i.currency == "GBP" else L[cfg.fx_map[i.currency]]
        rF = 0.0 if i.currency == "GBP" else r[cfg.fx_map[i.currency]]
        if i.quote == "fx":
            total += q[i.id] / L[i.id] * (np.exp(-r[i.id]) - 1)
        elif i.quote == "yield_pct":
            z = np.exp(-L[i.id] / 100 * i.tenor_years)
            total += q[i.id] * z / F * (np.exp(-r[i.id] / 100 * i.tenor_years - rF) - 1)
        else:
            total += q[i.id] * L[i.id] / F * (np.exp(r[i.id] - rF) - 1)
    return total


windows = {"lehman_2008": ("2008-09-12", "2008-10-10"), "covid_2020": ("2020-02-19", "2020-03-23"),
           "gilt_2022": ("2022-09-22", "2022-09-27")}
print(f"{'scenario':<22}{'engine':>14}{'independent':>14}{'gap':>12}")
for sid, (a, b) in windows.items():
    r = {}
    for i in cfg.instruments:
        if i.stress_proxy:
            r[i.id] = np.log(ref(i.id, "stress_proxy", b) / ref(i.id, "stress_proxy", a))
        elif i.returns_proxy:
            r[i.id] = np.log(ref(i.id, "return_proxy", b) / ref(i.id, "return_proxy", a))
        elif i.quote == "yield_pct":
            r[i.id] = value(i.id, b) - value(i.id, a)
        else:
            d = con.execute("SELECT coalesce(sum(amount), 0) FROM dividends WHERE instrument_id = ? AND ex_date > ? AND ex_date <= ?",
                            [i.id, a, b]).fetchone()[0] if i.asset_class == "equity" else 0.0
            r[i.id] = np.log((value(i.id, b) + d) / value(i.id, a))
    ind = pnl_closed_form(r)
    print(f"{sid:<22}{eng[sid]:>14,.0f}{ind:>14,.0f}{eng[sid] - ind:>12,.0f}")

for spec in cfg.stress["hypothetical"]:
    r = {i.id: 0.0 for i in cfg.instruments}
    for s in spec["shocks"]:
        for i in cfg.instruments:
            if s.get("asset_class", i.asset_class) == i.asset_class and s.get("desk", i.desk) == i.desk \
                    and (i.id in s["ids"] if "ids" in s else True):
                r[i.id] += s["pp"] if "pp" in s else np.log(1 + s["pct"] / 100)
    ind = pnl_closed_form(r)
    print(f"{spec['id']:<22}{eng[spec['id']]:>14,.0f}{ind:>14,.0f}{eng[spec['id']] - ind:>12,.0f}")
