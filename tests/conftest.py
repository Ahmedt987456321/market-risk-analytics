"""Synthetic market data for tests. No network access anywhere in the test suite."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from riskengine.config import load_config
from riskengine.sources import Fetch

AS_OF = pd.Timestamp("2026-09-29")
START = "2021-01-01"
# A clock that meets the SLA for AS_OF: 20:00 London on the day itself.
ON_TIME = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)

LEVELS = {"price": 100.0, "yield_pct": 4.0, "fx": 1.3}


@pytest.fixture
def cfg():
    c = load_config()
    c.risk["backtest"]["start"] = "2026-08-03"   # a short backtest keeps the suite fast; the logic is the same
    return c


def synthetic_frames(cfg, seed: int = 1) -> dict[str, pd.DataFrame]:
    """Random walks for every (source, ticker) the config asks for."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(START, AS_OF)
    quote_of = {(i.source, i.ticker): i.quote for i in cfg.instruments}
    for c in cfg.cross_checks:
        quote_of[(c["source"], c["ticker"])] = "fx"
    frames = {}
    for source in ("yahoo", "boe", "fred"):
        parts = []
        for ticker in cfg.tickers(source):
            quote = quote_of.get((source, ticker), "price")    # proxies are overwritten below
            if quote == "yield_pct":
                values = LEVELS[quote] + np.cumsum(rng.normal(0, 0.03, len(dates)))
            else:
                values = LEVELS[quote] * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
            parts.append(pd.DataFrame({"ticker": ticker, "date": dates, "close": values, "adj_close": values}))
        frames[source] = pd.concat(parts, ignore_index=True)
    # Second-source FX and roll-free proxies must track their primary series, not be independent walks.
    pairs = [(cfg.instrument(c["instrument"]), c["source"], c["ticker"]) for c in cfg.cross_checks]
    pairs += [(i, "yahoo", i.returns_proxy) for i in cfg.instruments if i.returns_proxy]
    for prim, source, ticker in pairs:
        src = frames[prim.source]
        base = src[src.ticker == prim.ticker].copy()
        base["ticker"] = ticker
        other = frames[source]
        frames[source] = pd.concat([other[other.ticker != ticker], base], ignore_index=True)
    frames["yahoo"]["dividend"] = 0.0
    return frames


def add_dividend(frames, ticker: str, date, amount: float) -> None:
    """Give `ticker` a dividend on `date` and rebuild its adj_close the way Yahoo does for US stocks."""
    f = frames["yahoo"]
    rows = f.index[f.ticker == ticker]
    f.loc[rows[f.loc[rows, "date"] == pd.Timestamp(date)], "dividend"] = amount
    close = f.loc[rows, "close"].to_numpy()
    div = f.loc[rows, "dividend"].to_numpy()
    adj = np.empty_like(close)
    adj[0] = close[0]
    for t in range(1, len(close)):
        adj[t] = adj[t - 1] * close[t] / (close[t - 1] - div[t])
    f.loc[rows, "adj_close"] = adj


def make_fetcher(frames: dict[str, pd.DataFrame]):
    def fetcher(cfg, raw_dir):
        out = {}
        for source, frame in frames.items():
            text = frame.to_csv(index=False)
            sha = hashlib.sha256(text.encode()).hexdigest()
            out[source] = Fetch(source, f"synthetic:{source}", datetime(2026, 9, 29, tzinfo=timezone.utc),
                                raw_dir / f"{source}.csv", sha, frame)
        return out
    return fetcher


@pytest.fixture
def frames(cfg):
    return synthetic_frames(cfg)


@pytest.fixture
def run_pipeline(cfg, tmp_path):
    from riskengine.pipeline import run

    def _run(frames, as_of=AS_OF, clock_time=ON_TIME, db_name="risk.duckdb"):
        return run(as_of, db_path=tmp_path / db_name, raw_dir=tmp_path / "raw", report_dir=tmp_path / "reports",
                   export_dir=tmp_path / "bi",
                   cfg=cfg, fetcher=make_fetcher(frames), clock=lambda: clock_time)
    return _run


def status_of(info, control: str) -> str:
    return next(r.status for r in info["results"] if r.control == control)
