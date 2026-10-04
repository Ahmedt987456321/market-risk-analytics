"""Command line entry point: python -m riskengine run [--as-of YYYY-MM-DD] [--offline]"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

from .controls import ZoneInfo
from .pipeline import run


def default_as_of() -> pd.Timestamp:
    """Latest business day whose closes are final.

    US markets close 16:00 New York, which is 21:00 London except in the weeks
    when UK and US clocks change on different dates (then 20:00). 22:00 covers both.
    """
    now = datetime.now(ZoneInfo("Europe/London"))
    today = pd.Timestamp(now.date())
    if today.dayofweek < 5 and now.hour >= 22:
        return today
    return today - pd.offsets.BDay(1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="riskengine")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run the daily pipeline for one date")
    p_run.add_argument("--as-of", help="business date, YYYY-MM-DD (default: latest complete business day)")
    p_run.add_argument("--offline", action="store_true", help="reuse the latest raw files instead of downloading")
    sub.add_parser("casestudy", help="Goldman Sachs public-disclosure case study (needs SEC_USER_AGENT)")
    args = parser.parse_args(argv)

    if args.command == "casestudy":
        from .casestudy_report import run as run_case
        from .config import ROOT
        out = run_case(ROOT, ROOT / "reports" / "casestudy", ROOT / "exports" / "bi")
        r = out["reprint"]
        print(f"Filings {out['filings']}, tables {out['tables']}; reprints matched "
              f"{r['prior_quarter'] + r['same_quarter_last_year']}, unmatched {len(r['unmatched'])}, "
              f"nothing to compare {r['no_reference']}")
        print(f"Report: {out['report']}")
        return 0

    as_of = pd.Timestamp(args.as_of) if args.as_of else default_as_of()
    info = run(as_of, offline=args.offline)
    width = max(len(r.control) for r in info["results"])
    print(f"Run {info['run_id']}  as of {info['as_of']}  status {info['status']}")
    for r in info["results"]:
        print(f"  {r.control:<{width}}  {r.status:<4}  {r.detail}")
    rr = info["risk"]
    for method, m in rr.totals.items():
        print(f"  {method:<12} VaR95 {m['VaR95'] / 1e6:6.2f}m  VaR99 {m['VaR99'] / 1e6:6.2f}m  ES97.5 {m['ES975'] / 1e6:6.2f}m")
    for method, s in rr.backtest_summary.items():
        print(f"  backtest {method:<12} 99%: {s['99']['exceptions']}/{s['99']['n']} (expected {s['99']['expected']:.1f}), "
              f"last {s['last_n']} days: {s['last_exceptions']} -> {s['zone']}")
    for key in ("report_path", "risk_report_path", "commentary_path", "dashboard_path"):
        print(f"  report: {info[key]}")
    print("  BI export: " + ", ".join(f"{k} {v:,}" for k, v in info["bi_counts"].items()))
    return {"PASS": 0, "WARN": 0}.get(info["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
