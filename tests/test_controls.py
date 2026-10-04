"""Each control is shown to pass on clean data and to fire on an injected fault."""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pandas as pd

from riskengine import controls as C
from tests.conftest import AS_OF, status_of


def _drop_last(frames, source, ticker, n_days):
    f = frames[source]
    dates = sorted(f.loc[f.ticker == ticker, "date"].unique())[-n_days:]
    frames[source] = f[~((f.ticker == ticker) & f.date.isin(dates))]
    return frames


def test_clean_data_passes_every_control(run_pipeline, frames):
    info = run_pipeline(frames)
    bad = {r.control: r.detail for r in info["results"] if r.status not in ("PASS", "INFO")}
    assert bad == {}
    assert info["status"] == "PASS"
    assert info["report_path"].exists()


def test_missing_price_fails(run_pipeline, frames):
    info = run_pipeline(_drop_last(frames, "yahoo", "HSBA.L", 5))
    assert status_of(info, "missing_prices") == "FAIL"
    assert info["status"] == "FAIL"


def test_one_day_gap_is_stale_not_missing(run_pipeline, frames):
    info = run_pipeline(_drop_last(frames, "boe", "IUDMNZC", 1))
    assert status_of(info, "stale_prices") == "WARN"
    assert status_of(info, "missing_prices") == "PASS"


def test_pence_pound_glitch_is_flagged(run_pipeline, frames):
    # A classic LSE data error: one day quoted in pounds instead of pence (100x).
    f = frames["yahoo"]
    mask = (f.ticker == "AZN.L") & (f.date == AS_OF)
    f.loc[mask, ["close", "adj_close"]] /= 100
    info = run_pipeline(frames)
    r = next(r for r in info["results"] if r.control == "return_outliers")
    assert r.status == "WARN" and "AZN" in r.detail


def test_duplicate_rows_fail(run_pipeline, frames):
    f = frames["fred"]
    frames["fred"] = pd.concat([f, f[f.ticker == "DGS10"].tail(1)], ignore_index=True)
    assert status_of(run_pipeline(frames), "raw_duplicates") == "FAIL"


def test_negative_price_fails(run_pipeline, frames):
    f = frames["yahoo"]
    f.loc[(f.ticker == "GC=F") & (f.date == AS_OF - pd.offsets.BDay(10)), "close"] = -1.0
    assert status_of(run_pipeline(frames), "invalid_values") == "FAIL"


def test_cross_source_break_warns(run_pipeline, frames):
    f = frames["yahoo"]
    f.loc[(f.ticker == "GBPUSD=X") & (f.date == AS_OF), "close"] *= 1.02
    info = run_pipeline(frames)
    r = next(r for r in info["results"] if r.control == "cross_source")
    assert r.status == "WARN" and "USD" in r.detail


def test_old_revision_warns_recent_does_not(run_pipeline, frames, cfg):
    run_pipeline(frames)
    f = frames["yahoo"]
    f.loc[(f.ticker == "MSFT") & (f.date == AS_OF - pd.offsets.BDay(1)), ["close", "adj_close"]] *= 1.01
    assert status_of(run_pipeline(frames), "revisions") == "INFO"
    f.loc[(f.ticker == "MSFT") & (f.date == pd.Timestamp("2025-03-03")), ["close", "adj_close"]] *= 1.01
    assert status_of(run_pipeline(frames), "revisions") == "WARN"


def test_short_history_warns(run_pipeline, frames):
    f = frames["yahoo"]
    frames["yahoo"] = f[~((f.ticker == "NVDA") & (f.date < pd.Timestamp("2024-01-01")))]
    assert status_of(run_pipeline(frames), "history_sufficiency") == "WARN"


def _ctx(tmp_path, cfg):
    con = duckdb.connect(str(tmp_path / "risk.duckdb"))
    return C.Context(con=con, cfg=cfg, as_of=AS_OF, raw_md=pd.DataFrame(columns=["instrument_id", "date"]),
                     revisions=pd.DataFrame())


def test_position_break_fails(run_pipeline, frames, cfg, tmp_path):
    run_pipeline(frames)
    ctx = _ctx(tmp_path, cfg)
    assert C.position_reconciliation(ctx).status == "PASS"
    # Position system books a trade that trade capture never saw.
    ctx.con.execute("UPDATE positions SET quantity = quantity + 500 WHERE as_of = ? AND instrument_id = 'BARC'",
                    [AS_OF.date()])
    r = C.position_reconciliation(ctx)
    assert r.status == "FAIL" and "BARC" in r.detail


def test_row_without_lineage_fails(run_pipeline, frames, cfg, tmp_path):
    run_pipeline(frames)
    ctx = _ctx(tmp_path, cfg)
    ctx.con.execute("UPDATE market_data SET fetch_id = 'manual-edit' WHERE instrument_id = 'GOLD' AND date = ?",
                    [AS_OF.date()])
    assert C.lineage_complete(ctx).status == "FAIL"


def test_sla(cfg):
    assert C.sla(AS_OF, datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc), cfg).status == "PASS"   # 07:00 BST
    assert C.sla(AS_OF, datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc), cfg).status == "FAIL"   # 08:00 BST
    assert C.sla(AS_OF, datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc), cfg).status == "INFO"   # backfill


def test_overall_status_is_worst_result():
    r = lambda s: C.ControlResult("x", s, 0, "")
    assert C.overall_status([r("PASS"), r("INFO")]) == "PASS"
    assert C.overall_status([r("PASS"), r("WARN")]) == "WARN"
    assert C.overall_status([r("WARN"), r("FAIL")]) == "FAIL"
