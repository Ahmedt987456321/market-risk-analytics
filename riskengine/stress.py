"""Stress tests: today's book under historical and hypothetical shocks.

Historical: the cumulative factor moves over a past window (relative moves for
prices and FX, absolute moves for yields) are applied to today's levels, and the
whole book is repriced every day along the path, so the report shows both the
end-of-window loss and the worst point within it. Positions are held constant:
no trading, no hedging, no stop-losses.

Hypothetical: round-number shocks defined in config/stress.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .returns import factor_returns, is_absolute
from .var import scenario_pnl


@dataclass
class StressResult:
    id: str
    name: str
    kind: str
    pnl: float
    by_desk: dict
    top_positions: list            # [(instrument_id, pnl)], largest losses first
    window: tuple | None = None    # (start, end) for historical
    worst_pnl: float | None = None
    worst_date: pd.Timestamp | None = None
    no_data: list = field(default_factory=list)


def _summarise(sid, name, kind, pnl_by_pos: np.ndarray, cfg, **extra) -> StressResult:
    ids = [i.id for i in cfg.instruments]
    desks = {}
    for inst, p in zip(cfg.instruments, pnl_by_pos):
        desks[inst.desk] = desks.get(inst.desk, 0.0) + float(p)
    order = np.argsort(pnl_by_pos)[:3]
    return StressResult(sid, name, kind, float(pnl_by_pos.sum()), desks,
                        [(ids[k], float(pnl_by_pos[k])) for k in order], **extra)


def _window(index: pd.DatetimeIndex, spec: dict) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The window's start and end market days, or None if the data does not cover it."""
    before = index[index <= pd.Timestamp(spec["start"])]
    if len(before) == 0:
        return None
    start = before[-1]
    if "end" in spec:
        end = index[index <= pd.Timestamp(spec["end"])][-1]
    else:
        k = index.get_loc(start) + spec["market_days"]
        if k >= len(index):
            return None
        end = index[k]
    return start, end


def historical(con, cfg, levels0: np.ndarray, q: np.ndarray, as_of) -> list[StressResult]:
    ffill = cfg.controls["panel"]["ffill_limit_bdays"]
    _, rets = factor_returns(con, cfg, pd.Timestamp(as_of).date(), ffill, stress=True)
    columns = [i.id for i in cfg.instruments]
    absolute = np.array([is_absolute(i) for i in cfg.instruments])
    out = []
    for spec in cfg.stress["historical"]:
        window = _window(rets.index, spec)
        if window is None:
            out.append(StressResult(spec["id"], spec["name"], "historical", float("nan"), {}, [],
                                    no_data=["window outside loaded history"]))
            continue
        start, end = window
        daily = rets.loc[start:end].iloc[1:]                   # moves after the start close, up to the end close
        no_data = sorted(daily.columns[daily.isna().any()])
        path = np.nancumsum(daily.to_numpy(dtype=float), axis=0)
        pnl_path = scenario_pnl(levels0, path, absolute, columns, cfg.instruments, cfg.fx_map, q)
        totals = pnl_path.sum(axis=1)
        k = int(np.argmin(totals))
        out.append(_summarise(spec["id"], spec["name"], "historical", pnl_path[-1], cfg,
                              window=(start, end), worst_pnl=float(totals[k]), worst_date=daily.index[k],
                              no_data=no_data))
    return out


def hypothetical_shock(cfg, shocks: list[dict]) -> np.ndarray:
    r = np.zeros(len(cfg.instruments))
    for s in shocks:
        hit = [k for k, i in enumerate(cfg.instruments)
               if (s.get("asset_class") in (None, i.asset_class)) and (s.get("desk") in (None, i.desk))
               and (i.id in s["ids"] if "ids" in s else True)]
        if not hit:
            raise ValueError(f"Shock selects no instruments: {s}")
        for k in hit:
            inst = cfg.instruments[k]
            if "pp" in s:
                if not is_absolute(inst):
                    raise ValueError(f"pp shock on a price-type factor {inst.id}")
                r[k] += s["pp"]
            else:
                if is_absolute(inst):
                    raise ValueError(f"pct shock on a yield {inst.id}; use pp")
                r[k] += np.log1p(s["pct"] / 100)
    return r


def hypothetical(cfg, levels0: np.ndarray, q: np.ndarray) -> list[StressResult]:
    columns = [i.id for i in cfg.instruments]
    absolute = np.array([is_absolute(i) for i in cfg.instruments])
    out = []
    for spec in cfg.stress["hypothetical"]:
        r = hypothetical_shock(cfg, spec["shocks"])
        pnl = scenario_pnl(levels0, r[None, :], absolute, columns, cfg.instruments, cfg.fx_map, q)[0]
        out.append(_summarise(spec["id"], spec["name"], "hypothetical", pnl, cfg))
    return out
