"""Commentary, dashboard and BI export."""
from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from riskengine.dashboard import commentary_blocks


def test_commentary_blocks_are_plain_text():
    md = "# Title\n\n## Headline\n\nVaR is **up**.\n\n## Attention\n\n- one <b>x</b>\n- two\n"
    blocks = commentary_blocks(md)
    assert blocks == [{"type": "h", "text": "Headline"}, {"type": "p", "text": "VaR is up."},
                      {"type": "h", "text": "Attention"}, {"type": "ul", "items": ["one <b>x</b>", "two"]}]


def test_outputs_exist_and_agree(run_pipeline, frames, cfg, tmp_path):
    info = run_pipeline(frames)
    rr = info["risk"]

    text = info["commentary_path"].read_text(encoding="utf-8")
    assert f"99% VaR is GBP {rr.totals[cfg.risk['primary_method']]['VaR99'] / 1e6:,.2f}m" in text
    assert "## Attention" in text

    html = info["dashboard_path"].read_text(encoding="utf-8")
    raw = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
    assert "</" not in raw                           # nothing inside the JSON can close the script tag
    data = json.loads(raw)
    assert data["tiles"]["var99"] == pytest.approx(rr.totals[cfg.risk["primary_method"]]["VaR99"])
    assert data["varHistory"]["var"]["hs_equal"][-1] == pytest.approx(data["tiles"]["var99"], abs=0.01)
    assert len(data["history"]["dates"]) == len(data["history"]["pnl"])

    bi = tmp_path / "bi"
    pos = pd.read_csv(bi / "fact_position_daily.csv")
    assert pos.market_value_gbp.notna().all()
    today = pos[pos.date == f"{rr.as_of:%Y-%m-%d}"]
    assert today.market_value_gbp.abs().sum() == pytest.approx(data["tiles"]["gross"])
    bt = pd.read_csv(bi / "fact_backtest.csv")
    primary = bt[bt.method == cfg.risk["primary_method"]]
    assert primary.exception_99.sum() == rr.backtest_summary[cfg.risk["primary_method"]]["99"]["exceptions"]
    assert set(pd.read_csv(bi / "dim_instrument.csv").instrument_id) >= set(pos.instrument_id)
