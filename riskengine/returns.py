"""Daily risk-factor returns, the input to every scenario.

Shock types follow the instrument:
- relative (log return) for prices and FX rates
- absolute (change in percentage points) for yields, so a low yield can still
  move by a realistic amount

Two corrections to the raw prices, both found by verification (DECISIONS 006, 011):
- equities use total return, built from our own dividend table, because
  Yahoo's adjusted close is wrong for LSE stocks
- futures with a `returns_proxy` use the proxy's returns, because the
  continuous front-month series jumps at every contract roll
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .marketdata import price_panel


def is_absolute(inst) -> bool:
    return inst.quote == "yield_pct"


def _proxy_panel(con, end, index: pd.DatetimeIndex, ffill_limit: int, purpose: str = "return_proxy") -> pd.DataFrame:
    long = con.execute(
        "SELECT date, instrument_id, value FROM reference_prices WHERE purpose = ? AND date <= ?",
        [purpose, end],
    ).df()
    if long.empty:
        return pd.DataFrame(index=index)
    wide = long.pivot(index="date", columns="instrument_id", values="value")
    wide.index = pd.to_datetime(wide.index)
    return wide.reindex(index.union(wide.index)).ffill(limit=ffill_limit).reindex(index)


def _dividend_panel(con, end, index: pd.DatetimeIndex) -> pd.DataFrame:
    long = con.execute("SELECT ex_date, instrument_id, amount FROM dividends WHERE ex_date <= ?", [end]).df()
    if long.empty:
        return pd.DataFrame(0.0, index=index, columns=[])
    wide = long.pivot_table(index="ex_date", columns="instrument_id", values="amount", aggfunc="sum")
    wide.index = pd.to_datetime(wide.index)
    return wide.reindex(index).fillna(0.0)


def market_days(con, cfg, levels: pd.DataFrame) -> pd.DataFrame:
    """Keep dates on which enough instruments have a real price.

    Weekday holidays that close most markets (25 Dec, 1 Jan) would otherwise
    appear as days where nothing moved, which dilutes the scenario set and
    adds free passes to the backtest.
    """
    share = cfg.risk["market_day_min_share"]
    counts = con.execute("SELECT date, count(*) AS n FROM market_data GROUP BY 1").df()
    counts["date"] = pd.to_datetime(counts["date"])
    days = set(counts.loc[counts["n"] >= share * len(cfg.instruments), "date"])
    return levels[levels.index.isin(days)]


def equity_total_return(close: pd.Series, dividend: pd.Series) -> pd.Series:
    """log((P_t + D_t) / P_{t-1}): what a holder of one share actually earns over the day.

    Applied to today's price in a scenario, this reproduces the holding P&L
    exactly, which the backtest needs (checked in tools/verify_var.py).
    """
    return np.log((close + dividend) / close.shift(1))


def yahoo_style_return(close: pd.Series, dividend: pd.Series) -> pd.Series:
    """log(P_t / (P_{t-1} - D_t)), the convention behind Yahoo's adjusted close.

    Used only by the dividend control, to test our dividend data against
    Yahoo's US adjustment on like-for-like terms.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.log(close / (close.shift(1) - dividend))


def factor_returns(con, cfg, end, ffill_limit: int = 5, stress: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (levels, returns): market-day panels, one column per instrument.

    stress=True swaps in each instrument's stress_proxy where it has one, for
    historical windows older than its returns_proxy (BNO starts in 2010).
    """
    levels = market_days(con, cfg, price_panel(con, end, "close", ffill_limit))
    proxies = _proxy_panel(con, end, levels.index, ffill_limit)
    stress_proxies = _proxy_panel(con, end, levels.index, ffill_limit, "stress_proxy") if stress else pd.DataFrame()
    dividends = _dividend_panel(con, end, levels.index)

    out = {}
    for inst in cfg.instruments:
        level = levels[inst.id]
        if stress and inst.stress_proxy and inst.id in stress_proxies:
            proxy = stress_proxies[inst.id]
            out[inst.id] = np.log(proxy / proxy.shift(1))
        elif inst.returns_proxy:
            proxy = proxies[inst.id] if inst.id in proxies else pd.Series(np.nan, index=levels.index)
            out[inst.id] = np.log(proxy / proxy.shift(1))
        elif inst.asset_class == "equity":
            div = dividends[inst.id] if inst.id in dividends else 0.0
            out[inst.id] = equity_total_return(level, div)
        elif is_absolute(inst):
            out[inst.id] = level.diff()
        else:
            out[inst.id] = np.log(level / level.shift(1))
    returns = pd.DataFrame(out, index=levels.index)
    return levels[[i.id for i in cfg.instruments]], returns


def shock(levels0: np.ndarray, returns: np.ndarray, absolute: np.ndarray) -> np.ndarray:
    """Apply scenario returns (S, F) to today's levels (F,)."""
    return np.where(absolute, levels0 + returns, levels0 * np.exp(returns))
