"""Risk engine: estimators, P&L, backtest statistics, and the stage 2 controls."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from riskengine import backtest as bt
from riskengine.config import Instrument
from riskengine.returns import equity_total_return, yahoo_style_return
from riskengine.var import es_contributions, parametric, scenario_pnl, scenario_weights, var_es
from tests.conftest import AS_OF, add_dividend, status_of

# --- Estimators -------------------------------------------------------------


def test_var_es_on_a_known_distribution():
    pnl = -np.arange(1, 101, dtype=float)          # losses 1..100, equally likely
    w = scenario_weights(100, None)
    assert var_es(pnl, w, 0.99) == pytest.approx((99.0, 100.0))
    # 97.5%: VaR 98; ES = (0.005 * 98 + 0.01 * 99 + 0.01 * 100) / 0.025
    assert var_es(pnl, w, 0.975) == pytest.approx((98.0, 99.2))


def test_weights():
    assert scenario_weights(4, None) == pytest.approx([0.25] * 4)
    w = scenario_weights(1000, 0.995)
    assert w.sum() == pytest.approx(1.0)
    assert w[-1] > w[0]                             # newest scenario (last row) weighs most
    assert w[-1] / w[-2] == pytest.approx(1 / 0.995)


def test_es_contributions_add_up():
    rng = np.random.default_rng(3)
    by_desk = rng.standard_t(4, size=(1260, 5)) * [1, 2, 0.5, 3, 1]
    w = scenario_weights(1260, 0.995)
    contrib = es_contributions(by_desk, w, 0.975)
    assert contrib.sum() == pytest.approx(var_es(by_desk.sum(axis=1), w, 0.975)[1])


# --- P&L --------------------------------------------------------------------

INSTS = [
    Instrument("X", "x", "equity", "d", "s", "t", "price", "GBP"),
    Instrument("Y", "y", "equity", "d", "s", "t", "price", "USD"),
    Instrument("B", "b", "rates", "d", "s", "t", "yield_pct", "GBP", tenor_years=10),
    Instrument("USD", "usd", "fx", "d", "s", "t", "fx", "USD"),
]
COLS = ["X", "Y", "B", "USD"]
ABS = np.array([False, False, True, False])


def test_scenario_pnl_matches_closed_form():
    L0 = np.array([200.0, 50.0, 4.0, 1.25])
    r = np.array([[0.01, -0.02, 0.10, 0.005]])
    q = np.array([1000.0, 2000.0, 1e6, 5e5])
    pnl = scenario_pnl(L0, r, ABS, COLS, INSTS, {"USD": "USD"}, q)[0]
    assert pnl[0] == pytest.approx(1000 * 200 * (np.exp(0.01) - 1))
    assert pnl[1] == pytest.approx(2000 * 50 / 1.25 * (np.exp(-0.02 - 0.005) - 1))
    assert pnl[2] == pytest.approx(1e6 * np.exp(-0.4) * (np.exp(-0.10 / 100 * 10) - 1))
    assert pnl[3] == pytest.approx(5e5 / 1.25 * (np.exp(-0.005) - 1))


def test_total_return_gives_exact_holding_pnl():
    close = pd.Series([100.0, 97.0])                 # goes ex a 2.0 dividend and falls 3.0
    r = equity_total_return(close, pd.Series([0.0, 2.0])).iloc[1]
    assert 100 * (np.exp(r) - 1) == pytest.approx(97 - 100 + 2)
    # Yahoo's convention differs slightly, which is why it is only used for the check
    assert yahoo_style_return(close, pd.Series([0.0, 2.0])).iloc[1] != pytest.approx(r)


def test_parametric_matches_normal_formula_for_one_linear_position():
    rng = np.random.default_rng(5)
    rets = rng.normal(0, 0.01, size=(1260, 4))
    L0 = np.array([200.0, 50.0, 4.0, 1.25])
    q = np.array([1000.0, 0, 0, 0])
    out = parametric(L0, rets, ABS, COLS, INSTS, {"USD": "USD"}, q, 0.94, [0.99], 0.975)
    w = scenario_weights(1260, 0.94)
    sigma = 1000 * 200 * np.sqrt((w * rets[:, 0] ** 2).sum())
    assert out["VaR99"] == pytest.approx(norm.ppf(0.99) * sigma, rel=1e-3)


# --- Backtest statistics ----------------------------------------------------


def test_kupiec_zero_exceptions():
    lr, p = bt.kupiec(250, 0, 0.01)
    assert lr == pytest.approx(-2 * 250 * np.log(0.99))
    assert p == pytest.approx(0.02497, abs=1e-4)


def test_traffic_light_reproduces_basel_table_2():
    expected = {**{x: ("green", 0.0) for x in range(5)},
                5: ("yellow", 0.40), 6: ("yellow", 0.50), 7: ("yellow", 0.65), 8: ("yellow", 0.75),
                9: ("yellow", 0.85), 10: ("red", 1.0), 12: ("red", 1.0)}
    for x, zone in expected.items():
        assert bt.traffic_light(250, x) == zone, x


def test_christoffersen():
    assert bt.christoffersen(np.zeros(250, bool))[1] == 1.0
    spread = np.zeros(500, bool); spread[[50, 150, 250, 350, 450]] = True
    clustered = np.zeros(500, bool); clustered[[200, 201, 202, 203, 204]] = True
    assert bt.christoffersen(clustered)[1] < 0.01 < bt.christoffersen(spread)[1]


# --- Stage 2 controls -------------------------------------------------------


def test_interior_gap_is_flagged(run_pipeline, frames):
    f = frames["yahoo"]
    day = AS_OF - pd.offsets.BDay(1)
    frames["yahoo"] = f[~((f.ticker == "GC=F") & (f.date == day))]     # the real 28 Sep 2026 case
    info = run_pipeline(frames)
    r = next(r for r in info["results"] if r.control == "interior_gaps")
    assert r.status == "WARN" and "GOLD" in r.detail


def test_futures_roll_is_flagged(run_pipeline, frames):
    f = frames["yahoo"]
    f.loc[(f.ticker == "BZ=F") & (f.date == AS_OF), "close"] *= 0.91      # future jumps, BNO does not
    info = run_pipeline(frames)
    r = next(r for r in info["results"] if r.control == "futures_roll")
    assert r.status == "WARN" and "BRENT" in r.detail


def test_dividend_check_passes_then_catches_a_unit_error(run_pipeline, frames):
    add_dividend(frames, "JPM", AS_OF - pd.offsets.BDay(30), 1.5)
    assert status_of(run_pipeline(frames, db_name="a.duckdb"), "dividend_adjustment") == "PASS"
    f = frames["yahoo"]
    f.loc[f.ticker == "JPM", "dividend"] *= 100          # dividend in cents, price in dollars
    assert status_of(run_pipeline(frames, db_name="b.duckdb"), "dividend_adjustment") == "FAIL"


def test_pipeline_produces_consistent_risk(run_pipeline, frames, cfg):
    info = run_pipeline(frames)
    rr = info["risk"]
    assert info["risk_report_path"].exists()
    assert rr.n_scenarios == cfg.risk["window_days"]
    for m in rr.totals.values():
        assert 0 < m["VaR95"] < m["VaR99"]
    primary = rr.totals[cfg.risk["primary_method"]]
    assert rr.desks.ES975_contribution.sum() == pytest.approx(primary["ES975"])
    assert set(rr.backtest.method) == set(cfg.risk["methods"])
