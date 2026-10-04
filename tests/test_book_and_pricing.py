from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from riskengine.book import generate_book
from riskengine.config import Instrument
from riskengine.pricing import unit_values_gbp
from tests.conftest import AS_OF


def _panel(cfg, value_by_quote={"price": 100.0, "yield_pct": 4.0, "fx": 1.25}):
    days = pd.bdate_range("2023-12-01", AS_OF)
    return pd.DataFrame({i.id: value_by_quote[i.quote] for i in cfg.instruments}, index=days)


def test_unit_values():
    panel = pd.DataFrame({"X": [200.0], "Y": [50.0], "B": [5.0], "USD": [1.25]})
    fx_map = {"USD": "USD"}
    insts = [
        Instrument("X", "x", "equity", "d", "s", "t", "price", "GBP"),
        Instrument("Y", "y", "equity", "d", "s", "t", "price", "USD"),
        Instrument("B", "b", "rates", "d", "s", "t", "yield_pct", "GBP", tenor_years=10),
        Instrument("USD", "usd", "fx", "d", "s", "t", "fx", "USD"),
    ]
    uv = unit_values_gbp(panel, insts, fx_map).iloc[0]
    assert uv["X"] == pytest.approx(200.0)
    assert uv["Y"] == pytest.approx(50.0 / 1.25)
    assert uv["B"] == pytest.approx(np.exp(-0.05 * 10))
    assert uv["USD"] == pytest.approx(0.8)


def test_book_hits_targets_on_start_date(cfg):
    panel = _panel(cfg)
    positions, _ = generate_book(cfg, panel, AS_OF, "test")
    start = pd.Timestamp(cfg.book["start"]).date()
    day0 = positions[positions.as_of == start].set_index("instrument_id")["quantity"]
    uv = unit_values_gbp(panel, cfg.instruments, cfg.fx_map).loc[pd.Timestamp(start)]
    mv = day0 * uv[day0.index]
    for iid, target in cfg.book["targets_gbp"].items():
        assert mv[iid] == pytest.approx(target)


def test_book_is_deterministic_and_stable_as_dates_advance(cfg):
    panel = _panel(cfg)
    a, ta = generate_book(cfg, panel, AS_OF, "a")
    b, tb = generate_book(cfg, panel, AS_OF, "b")
    pd.testing.assert_frame_equal(a.drop(columns="run_id"), b.drop(columns="run_id"))

    earlier = AS_OF - pd.offsets.BDay(20)
    c, tc = generate_book(cfg, panel, earlier, "c")
    overlap = a[a.as_of <= earlier.date()].reset_index(drop=True)
    pd.testing.assert_frame_equal(overlap.drop(columns="run_id"), c.drop(columns="run_id"))
    assert set(tc.trade_id) <= set(ta.trade_id)


def test_positions_equal_cumulative_trades(cfg):
    positions, trades = generate_book(cfg, _panel(cfg), AS_OF, "t")
    from_trades = trades.groupby("instrument_id")["quantity"].sum()
    final = positions[positions.as_of == AS_OF.date()].set_index("instrument_id")["quantity"]
    pd.testing.assert_series_equal(final.sort_index(), from_trades.sort_index(), check_names=False)


def test_book_refuses_to_size_without_prices(cfg):
    panel = _panel(cfg)
    panel.loc[:pd.Timestamp(cfg.book["start"]), "NVDA"] = np.nan
    with pytest.raises(ValueError, match="NVDA"):
        generate_book(cfg, panel, AS_OF, "t")
