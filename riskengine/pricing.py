"""Value of one unit of each instrument, in GBP.

Kept deliberately simple and transparent:
- equity / commodity: price / FX rate
- rates: a zero-coupon bond, exp(-y * T) per unit of face, / FX rate
- fx: one unit of the foreign currency is worth 1 / rate GBP

`unit_values_np` works on any array whose last axis is risk factors, so the same
code values today's book and thousands of shocked scenarios at once.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def unit_values_np(levels: np.ndarray, columns: list[str], instruments, fx_map: dict[str, str]) -> np.ndarray:
    """levels: (..., F) factor levels in `columns` order -> (..., N) GBP values per unit."""
    col = {c: k for k, c in enumerate(columns)}
    out = np.empty(levels.shape[:-1] + (len(instruments),))
    for n, inst in enumerate(instruments):
        level = levels[..., col[inst.id]]
        if inst.quote == "fx":
            out[..., n] = 1.0 / level
            continue
        if inst.quote == "price":
            local = level
        elif inst.quote == "yield_pct":
            local = np.exp(-level / 100.0 * inst.tenor_years)
        else:
            raise ValueError(f"Unknown quote type {inst.quote!r} for {inst.id}")
        fx = 1.0 if inst.currency == "GBP" else levels[..., col[fx_map[inst.currency]]]
        out[..., n] = local / fx
    return out


def unit_values_gbp(panel: pd.DataFrame, instruments, fx_map: dict[str, str]) -> pd.DataFrame:
    values = unit_values_np(panel.to_numpy(dtype=float), list(panel.columns), instruments, fx_map)
    return pd.DataFrame(values, index=panel.index, columns=[i.id for i in instruments])
