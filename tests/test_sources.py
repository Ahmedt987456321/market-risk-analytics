"""Parsers and the raw-file lineage guarantee. Uses small inline payloads, no network."""
from __future__ import annotations

import hashlib

import pandas as pd

from riskengine import sources

BOE = "DATE,IUDMNZC,XUDLUSS\r\n24 Sep 2026,5.2068,1.3220\r\n25 Sep 2026,5.2416,\r\n"
FRED = "observation_date,DGS10\n2026-09-24,4.10\n2026-09-25,.\n2026-09-28,4.15\n"


def test_parse_boe_handles_crlf_and_gaps():
    df = sources.parse_boe(BOE)
    assert len(df) == 3                                   # the blank XUDLUSS cell is dropped
    assert df.loc[df.ticker == "IUDMNZC", "close"].tolist() == [5.2068, 5.2416]


def test_parse_fred_drops_dot_gaps_and_respects_start():
    df = sources.parse_fred(FRED, start="2026-09-25")
    assert df["date"].tolist() == [pd.Timestamp("2026-09-28")]


def test_saved_hash_matches_bytes_on_disk_and_reload(cfg, tmp_path):
    fetch = sources._save("boe", "test", BOE, tmp_path, sources.parse_boe(BOE))
    assert hashlib.sha256(fetch.raw_path.read_bytes()).hexdigest() == fetch.sha256
    for other in ("yahoo", "fred"):
        (fetch.raw_path.parent / f"{other}.csv").write_bytes(
            ("ticker,date,close,adj_close\n" if other == "yahoo" else FRED).encode())
    reloaded = sources.fetch_cached(cfg, tmp_path)["boe"]
    assert reloaded.sha256 == fetch.sha256
    pd.testing.assert_frame_equal(reloaded.frame.reset_index(drop=True), fetch.frame.reset_index(drop=True))
