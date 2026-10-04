"""Stress scenarios and the VaR explain."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from riskengine.explain import PLAYERS, explain, shapley
from riskengine.stress import hypothetical_shock
from riskengine.var import scenario_weights
from tests.test_risk import ABS, COLS, INSTS

# --- Shapley ----------------------------------------------------------------


def test_shapley_parts_add_up_and_are_order_free():
    def v(s):                     # deliberately non-additive: an interaction between positions and levels
        x = {p: 1.0 if p in s else 0.0 for p in PLAYERS}
        return 3 * x["positions"] + 2 * x["levels"] + 5 * x["positions"] * x["levels"] - x["window"]
    parts = shapley(v)
    assert sum(parts.values()) == pytest.approx(v(frozenset(PLAYERS)) - v(frozenset()))
    assert parts["positions"] == pytest.approx(3 + 2.5)      # the interaction is split evenly
    assert parts["levels"] == pytest.approx(2 + 2.5)
    assert parts["window"] == pytest.approx(-1)


def _state(q, levels, window, start="2020-01-01"):
    return {"q": np.array(q, float), "levels": np.array(levels, float), "window": window,
            "dates": pd.bdate_range(start, periods=len(window))}


def test_explain_adds_up_and_isolates_each_effect():
    rng = np.random.default_rng(11)
    win = rng.normal(0, 0.01, size=(500, 4)) * [1, 1, 10, 1]
    prev = _state([1000, 2000, 1e6, 5e5], [200, 50, 4.0, 1.25], win)
    kw = dict(absolute=ABS, columns=COLS, instruments=INSTS, fx_map={"USD": "USD"},
              weights_fn=lambda n: scenario_weights(n, None), es_alpha=0.975)

    same = explain(prev, prev, **kw)
    assert all(same["VaR99"][p] == pytest.approx(0) for p in PLAYERS)

    # A one-position book: VaR is linear in size, so 1.5x the position adds exactly 0.5x the VaR.
    # (In a diversified book a bigger long position can even lower VaR, if the 99% scenario is
    # one where that asset rose, so there is no sign to test there.)
    single = {**prev, "q": np.array([1000.0, 0, 0, 0])}
    trades_only = explain(single, {**single, "q": single["q"] * 1.5}, **kw)
    assert trades_only["VaR99"]["levels"] == pytest.approx(0)
    assert trades_only["VaR99"]["window"] == pytest.approx(0)
    assert trades_only["VaR99"]["positions"] == pytest.approx(0.5 * trades_only["VaR99"]["previous"])

    cur = _state(prev["q"] * [1.2, 0.9, 1, 1], [205, 49, 4.1, 1.26],
                 np.vstack([win[1:], rng.normal(0, 0.03, size=(1, 4))]), start="2020-01-02")
    ex = explain(prev, cur, **kw)
    for m in ("VaR99", "ES975"):
        e = ex[m]
        assert e["previous"] + e["positions"] + e["levels"] + e["window"] == pytest.approx(e["current"])
    assert len(ex["entered"]) == 1 and len(ex["left"]) == 1


# --- Stress -----------------------------------------------------------------


def test_hypothetical_shock_selection(cfg):
    r = hypothetical_shock(cfg, [{"asset_class": "equity", "pct": -15}, {"ids": ["GILT_5Y"], "pp": 1.0},
                                 {"desk": "UK Equities", "pct": -10}])
    by_id = dict(zip([i.id for i in cfg.instruments], r))
    assert by_id["AAPL"] == pytest.approx(np.log(0.85))
    assert by_id["HSBA"] == pytest.approx(np.log(0.85) + np.log(0.90))   # both shocks hit UK equities
    assert by_id["GILT_5Y"] == pytest.approx(1.0) and by_id["GILT_10Y"] == 0
    with pytest.raises(ValueError, match="use pp"):
        hypothetical_shock(cfg, [{"asset_class": "rates", "pct": 5}])
    with pytest.raises(ValueError, match="no instruments"):
        hypothetical_shock(cfg, [{"desk": "Nonexistent", "pct": 5}])


def test_pipeline_stress_and_explain(run_pipeline, frames, cfg):
    info = run_pipeline(frames)
    rr = info["risk"]
    ids = {s.id for s in rr.stress}
    assert ids == {s["id"] for s in cfg.stress["historical"]} | {s["id"] for s in cfg.stress["hypothetical"]}
    by_id = {s.id: s for s in rr.stress}
    # Synthetic history starts in 2021: the 2008 and 2020 windows must be reported as unavailable, not crash
    assert by_id["lehman_2008"].window is None and by_id["lehman_2008"].no_data
    assert by_id["covid_2020"].window is None
    g = by_id["gilt_2022"]
    assert g.window is not None and not g.no_data and g.worst_pnl <= g.pnl + 1e-6
    for s in rr.stress:
        if s.window is not None or s.kind == "hypothetical":
            assert sum(s.by_desk.values()) == pytest.approx(s.pnl)
    e = rr.explain["VaR99"]
    assert e["previous"] + e["positions"] + e["levels"] + e["window"] == pytest.approx(e["current"])
    assert "What moved VaR" in info["risk_report_path"].read_text()
