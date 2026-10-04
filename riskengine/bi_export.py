"""Export a star schema for Power BI, Tableau or any BI tool.

Plain CSV, one file per table, rebuilt on every run into exports/bi/. Dimensions
and facts join on instrument_id, desk and date. The model is documented in
docs/BI_GUIDE.md.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .dashboard import STRESS_SHORT
from .marketdata import price_panel
from .pricing import unit_values_gbp
from .report import LABELS, METHOD_LABELS

# Plain-English names and display order, so reports never show internal codes.
EXPLAIN_LABELS = {"previous": ("Previous day", 0), "positions": ("Trades", 1), "levels": ("Market moves", 2),
                  "window": ("Scenario window rolled", 3), "current": ("Today", 4)}
STATUS_RANK = {"FAIL": 1, "WARN": 2, "INFO": 3, "PASS": 4}


def export(con, cfg, run_id: str, as_of, out_dir: Path, stress: list | None = None) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, pd.DataFrame] = {}

    tables["dim_instrument"] = con.execute("""
        SELECT instrument_id, name, asset_class, desk, currency, quote, source, ticker, tenor_years
        FROM dim_instrument ORDER BY desk, instrument_id""").df()
    tables["dim_desk"] = pd.DataFrame({"desk": sorted({i.desk for i in cfg.instruments})})

    # Daily positions with GBP market value, from the book start to as_of
    pos = con.execute("SELECT as_of AS date, instrument_id, quantity FROM positions WHERE as_of <= ? ORDER BY 1, 2",
                      [as_of.date()]).df()
    pos["date"] = pd.to_datetime(pos["date"])
    uv = unit_values_gbp(price_panel(con, as_of.date(), "close", cfg.controls["panel"]["ffill_limit_bdays"]),
                         cfg.instruments, cfg.fx_map)
    uv_long = uv.stack().rename("unit_value_gbp").reset_index().rename(columns={"level_0": "date", "level_1": "instrument_id"})
    uv_long.columns = ["date", "instrument_id", "unit_value_gbp"]
    pos = pos.merge(uv_long, on=["date", "instrument_id"], how="left")
    pos["market_value_gbp"] = pos["quantity"] * pos["unit_value_gbp"]
    tables["fact_position_daily"] = pos

    bt = con.execute("SELECT date, method, var95, var99, pnl FROM backtest WHERE run_id = ? ORDER BY 1, 2", [run_id]).df()
    bt["exception_99"] = (-bt["pnl"] > bt["var99"]).astype(int)
    bt["exception_95"] = (-bt["pnl"] > bt["var95"]).astype(int)
    tables["fact_backtest"] = bt

    tables["fact_risk"] = con.execute(
        "SELECT as_of AS date, method, measure, scope, scope_id, value_gbp FROM risk_results WHERE run_id = ?", [run_id]).df()
    tables["fact_stress"] = con.execute(
        "SELECT as_of AS date, scenario, kind, desk, pnl_gbp FROM stress_results WHERE run_id = ?", [run_id]).df()
    tables["fact_stress"]["scenario_name"] = tables["fact_stress"]["scenario"].map(STRESS_SHORT).fillna(
        tables["fact_stress"]["scenario"])
    tables["fact_var_explain"] = con.execute(
        "SELECT as_of AS date, prev_date, measure, component, value_gbp FROM var_explain WHERE run_id = ?", [run_id]).df()
    tables["fact_var_explain"]["component_label"] = tables["fact_var_explain"]["component"].map(
        {k: v[0] for k, v in EXPLAIN_LABELS.items()})
    tables["fact_var_explain"]["component_order"] = tables["fact_var_explain"]["component"].map(
        {k: v[1] for k, v in EXPLAIN_LABELS.items()})
    tables["fact_controls"] = con.execute(
        "SELECT as_of AS date, control, status, n_affected, detail FROM control_results WHERE run_id = ?", [run_id]).df()
    tables["fact_controls"]["control_label"] = tables["fact_controls"]["control"].map(LABELS).fillna(
        tables["fact_controls"]["control"])
    tables["fact_controls"]["status_rank"] = tables["fact_controls"]["status"].map(STATUS_RANK).fillna(9).astype(int)

    primary = cfg.risk["primary_method"]
    tables["dim_method"] = pd.DataFrame([
        {"method": m, "method_label": METHOD_LABELS.get(m, m) + (" (primary)" if m == primary else ""),
         "is_primary": int(m == primary), "method_order": i}
        for i, m in enumerate(cfg.risk["methods"])])

    if stress:
        tables["dim_scenario"] = pd.DataFrame([{
            "scenario": s.id, "scenario_name": STRESS_SHORT.get(s.id, s.name), "description": s.name,
            "kind": s.kind.capitalize(),
            "window": f"{s.window[0]:%d %b %Y} to {s.window[1]:%d %b %Y}" if s.window else "Instant shock",
            "pnl_gbp": s.pnl, "worst_point_gbp": s.worst_pnl,
            "largest_loss": ", ".join(f"{i} {p / 1e6:+.2f}m" for i, p in s.top_positions[:2] if p < 0) or "none",
        } for s in stress if s.pnl == s.pnl])

    dates = pd.bdate_range(tables["fact_position_daily"]["date"].min(), as_of)
    tables["dim_date"] = pd.DataFrame({"date": dates, "year": dates.year, "quarter": dates.quarter,
                                       "month": dates.month, "month_name": dates.strftime("%b"),
                                       "weekday": dates.strftime("%a")})

    counts = {}
    for name, df in tables.items():
        for col in df.columns:
            if col in ("date", "prev_date"):
                df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
        df.to_csv(out_dir / f"{name}.csv", index=False)
        counts[name] = len(df)
    return counts
