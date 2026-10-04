"""One daily run: fetch -> normalise -> load -> book -> controls -> risk -> reports."""
from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import controls as C
from . import db, report, sources
from . import risk as risk_engine
from . import stress
from . import bi_export, commentary, dashboard
from .book import generate_book
from .config import ROOT, load_config
from .marketdata import dedupe, find_revisions, normalise, price_panel
from .pricing import unit_values_gbp

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
EXPORT_DIR = ROOT / "exports" / "bi"


def code_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


def store_risk(con, run_id: str, as_of, rr, cfg) -> None:
    rows = [(method, measure, "total", "book", value)
            for method, measures in rr.totals.items() for measure, value in measures.items()]
    primary = cfg.risk["primary_method"]
    for row in rr.desks.itertuples():
        rows.append((primary, "VaR99_standalone", "desk", row.desk, row.VaR99_standalone))
        rows.append((primary, "ES975_contribution", "desk", row.desk, row.ES975_contribution))
    frame = pd.DataFrame(rows, columns=["method", "measure", "scope", "scope_id", "value_gbp"])
    frame.insert(0, "as_of", as_of.date())
    frame.insert(0, "run_id", run_id)
    db.upsert(con, "risk_results", frame, replace=False)
    bt = rr.backtest.copy()
    bt.insert(0, "run_id", run_id)
    db.upsert(con, "backtest", bt, replace=False)

    stress_rows = []
    for s in rr.stress:
        if s.pnl != s.pnl:                       # NaN: scenario not available on this data
            continue
        stress_rows.append((s.id, s.kind, "total", s.pnl))
        stress_rows += [(s.id, s.kind, desk, v) for desk, v in s.by_desk.items()]
    frame = pd.DataFrame(stress_rows, columns=["scenario", "kind", "desk", "pnl_gbp"])
    frame.insert(0, "as_of", as_of.date())
    frame.insert(0, "run_id", run_id)
    db.upsert(con, "stress_results", frame, replace=False)

    explain_rows = [(rr.prev_date.date(), measure, component, value)
                    for measure in ("VaR99", "ES975") for component, value in rr.explain[measure].items()]
    frame = pd.DataFrame(explain_rows, columns=["prev_date", "measure", "component", "value_gbp"])
    frame.insert(0, "as_of", as_of.date())
    frame.insert(0, "run_id", run_id)
    db.upsert(con, "var_explain", frame, replace=False)


def run(as_of, offline: bool = False, db_path: Path | str = DATA_DIR / "risk.duckdb",
        raw_dir: Path = DATA_DIR / "raw", report_dir: Path = REPORT_DIR, export_dir: Path = EXPORT_DIR,
        cfg=None, fetcher=None, clock=None) -> dict:
    """Run the pipeline for one as_of date. `fetcher` and `clock` exist for tests."""
    cfg = cfg or load_config()
    clock = clock or (lambda: datetime.now(timezone.utc))
    as_of = pd.Timestamp(as_of).normalize()
    started = clock()
    run_id = f"{started:%Y%m%dT%H%M%S}-{as_of:%Y%m%d}-{uuid.uuid4().hex[:6]}"
    mode = "offline" if offline else "live"
    info = {"run_id": run_id, "as_of": as_of.date(), "mode": mode, "code_version": code_version(),
            "config_sha256": cfg.sha256}

    con = db.connect(db_path)
    con.execute("INSERT INTO run_log (run_id, as_of, mode, started_at, code_version, config_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                [run_id, as_of.date(), mode, started, info["code_version"], cfg.sha256])
    try:
        if fetcher is None:
            fetcher = sources.fetch_cached if offline else sources.fetch_live
        fetches = fetcher(cfg, raw_dir)

        lineage = pd.DataFrame([{
            "fetch_id": f.fetch_id, "run_id": run_id, "source": f.source, "request": f.request,
            "fetched_at": f.fetched_at, "raw_path": str(f.raw_path), "sha256": f.sha256, "n_rows": len(f.frame),
        } for f in fetches.values()])

        raw_md, ref, dividends = normalise(fetches, cfg)
        md = dedupe(raw_md)
        md["run_id"] = run_id
        revisions = find_revisions(con, md, cfg.controls["revisions"]["tolerance_pct"])

        db.load_instruments(con, cfg)
        db.upsert(con, "lineage", lineage)
        db.upsert(con, "market_data", md)
        db.upsert(con, "reference_prices", ref.drop_duplicates(subset=["instrument_id", "purpose", "date"], keep="last"))
        db.upsert(con, "dividends", dividends.drop_duplicates(subset=["instrument_id", "ex_date"], keep="last"))

        ffill = cfg.controls["panel"]["ffill_limit_bdays"]
        panel = price_panel(con, as_of.date(), "close", ffill)
        positions, trades = generate_book(cfg, panel, as_of, run_id)
        db.replace_book(con, positions, trades, as_of.date())

        ctx = C.Context(con=con, cfg=cfg, as_of=as_of, raw_md=raw_md, revisions=revisions)
        results = C.run_controls(ctx, panel)

        # Risk is computed even when data controls fail; the risk report then says so at the top.
        rr = risk_engine.run(con, cfg, as_of)
        rr.stress = stress.historical(con, cfg, rr.levels0, rr.q, as_of) + stress.hypothetical(cfg, rr.levels0, rr.q)
        store_risk(con, run_id, as_of, rr, cfg)

        finished = clock()                       # SLA covers the whole run, including risk
        results.append(C.sla(as_of, finished, cfg))
        status = C.overall_status(results)

        con.execute("DELETE FROM control_results WHERE run_id = ?", [run_id])
        db.upsert(con, "control_results", pd.DataFrame([{
            "run_id": run_id, "as_of": as_of.date(), "control": r.control, "status": r.status,
            "n_affected": r.n_affected, "detail": r.detail} for r in results]), replace=False)
        con.execute("UPDATE run_log SET finished_at = ?, status = ? WHERE run_id = ?", [finished, status, run_id])

        uv = unit_values_gbp(panel, cfg.instruments, cfg.fx_map).loc[as_of]
        snapshot = report.book_snapshot(con, cfg, as_of, uv)
        info.update(status=status, results=results, risk=rr)
        info["report_path"] = report.write(Path(report_dir), as_of, report.render(info, results, snapshot, lineage))
        info["risk_report_path"] = report.write(Path(report_dir), as_of, report.render_risk(info, rr, cfg, status),
                                                name="risk.md")
        text = commentary.render(cfg, rr, results, status)
        info["commentary_path"] = report.write(Path(report_dir), as_of, text, name="commentary.md")
        info["dashboard_path"] = report.write(Path(report_dir), as_of, dashboard.build(cfg, info, rr, results, text),
                                              name="dashboard.html")
        info["bi_counts"] = bi_export.export(con, cfg, run_id, as_of, Path(export_dir), stress=rr.stress)
        return info
    except Exception:
        con.execute("UPDATE run_log SET finished_at = ?, status = 'ERROR' WHERE run_id = ?", [clock(), run_id])
        raise
    finally:
        con.close()
