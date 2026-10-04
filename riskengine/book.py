"""Synthetic trading book: seeded daily trades and end-of-day position snapshots.

Random draws come from one array filled in date order, so the trades for any
date never change when the run date moves forward.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .pricing import unit_values_gbp


def generate_book(cfg, panel: pd.DataFrame, as_of, run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    spec = cfg.book
    start = pd.Timestamp(spec["start"])
    as_of = pd.Timestamp(as_of)
    if as_of < start:
        raise ValueError(f"as_of {as_of.date()} is before the book start {start.date()}")

    ids = list(spec["targets_gbp"])
    instruments = [cfg.instrument(i) for i in ids]
    targets = np.array([spec["targets_gbp"][i] for i in ids], dtype=float)

    uv = unit_values_gbp(panel.loc[:start], instruments, cfg.fx_map).iloc[-1]
    if uv.isna().any():
        missing = ", ".join(uv[uv.isna()].index)
        raise ValueError(f"Cannot size the book on {start.date()}: no price for {missing}")
    base = targets / uv[ids].values

    dates = pd.bdate_range(start, as_of)
    rng = np.random.default_rng(spec["seed"])
    u = rng.random((len(dates), len(ids), 3))   # [trade?, size, sign] per date and instrument
    lo, hi = spec["trade_size_range"]
    trade = u[..., 0] < spec["daily_trade_probability"]
    size = lo + (hi - lo) * u[..., 1]
    sign = np.where(u[..., 2] < 0.5, -1.0, 1.0)
    deltas = np.where(trade, sign * size * np.abs(base), 0.0)
    deltas[0] = base                             # opening trades on the start date

    quantities = np.cumsum(deltas, axis=0)

    positions = pd.DataFrame({
        "as_of": np.repeat(dates.date, len(ids)),
        "instrument_id": np.tile(ids, len(dates)),
        "quantity": quantities.ravel(),
        "run_id": run_id,
    })
    d_idx, i_idx = np.nonzero(deltas)
    trades = pd.DataFrame({
        "trade_id": [f"T{dates[d]:%Y%m%d}-{ids[i]}" for d, i in zip(d_idx, i_idx)],
        "trade_date": dates[d_idx].date,
        "instrument_id": [ids[i] for i in i_idx],
        "quantity": deltas[d_idx, i_idx],
        "run_id": run_id,
    })
    return positions, trades
