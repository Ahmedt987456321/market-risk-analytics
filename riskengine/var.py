"""Value-at-Risk and Expected Shortfall.

Historical simulation with full revaluation: every past day's factor returns
are applied to today's market, the whole book is repriced, and the P&L of each
position is kept. That keeps P&L per position, so risk can be broken down by
desk without extra assumptions.

Estimator (stated so results can be reproduced):
- losses are sorted; each scenario carries a probability weight (equal, or
  decaying with age)
- VaR_a is the smallest loss L with P(loss <= L) >= a
- ES_a is the probability-weighted mean of the worst (1 - a) of the
  distribution, splitting the scenario that straddles the boundary
  (Acerbi and Tasche). ES contributions by desk add up exactly to ES.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from .pricing import unit_values_np
from .returns import shock


@dataclass
class Book:
    ids: list[str]
    quantities: np.ndarray        # (N,)
    desks: list[str]              # (N,)


def scenario_pnl(levels0: np.ndarray, returns: np.ndarray, absolute: np.ndarray, columns: list[str],
                 instruments, fx_map: dict, quantities: np.ndarray) -> np.ndarray:
    """P&L (S, N) in GBP of each position under each scenario."""
    base = unit_values_np(levels0[None, :], columns, instruments, fx_map)[0]
    shocked = unit_values_np(shock(levels0, returns, absolute), columns, instruments, fx_map)
    return (shocked - base) * quantities


def scenario_weights(n: int, decay: float | None) -> np.ndarray:
    """Probability of each scenario, oldest first. decay=None gives equal weights."""
    if decay is None:
        return np.full(n, 1.0 / n)
    w = decay ** np.arange(n - 1, -1, -1, dtype=float)   # newest scenario has weight decay**0
    return w / w.sum()


def tail(pnl: np.ndarray, weights: np.ndarray, alpha: float) -> tuple[float, np.ndarray]:
    """Returns (VaR_alpha, tail weights). ES = sum(tail * loss) / (1 - alpha)."""
    loss = -pnl
    order = np.argsort(loss, kind="stable")
    cw = np.cumsum(weights[order])
    k = min(int(np.searchsorted(cw, alpha - 1e-12)), len(cw) - 1)
    t = np.zeros_like(weights)
    t[order[k + 1:]] = weights[order[k + 1:]]
    t[order[k]] = max(cw[k] - alpha, 0.0)
    return float(loss[order[k]]), t


def var_es(pnl: np.ndarray, weights: np.ndarray, alpha: float) -> tuple[float, float]:
    v, t = tail(pnl, weights, alpha)
    return v, float(t @ -pnl) / (1 - alpha)


def es_contributions(pnl_by_group: np.ndarray, weights: np.ndarray, alpha: float) -> np.ndarray:
    """pnl_by_group: (S, G). Contributions (G,) that sum exactly to ES of the total."""
    _, t = tail(pnl_by_group.sum(axis=1), weights, alpha)
    return (t @ -pnl_by_group) / (1 - alpha)


def ewma_cov(returns: np.ndarray, decay: float) -> np.ndarray:
    """RiskMetrics covariance, zero mean, oldest row first."""
    w = scenario_weights(len(returns), decay)
    return (returns * w[:, None]).T @ returns


def parametric(levels0, returns, absolute, columns, instruments, fx_map, quantities,
               decay: float, alphas: list[float], es_alpha: float) -> dict[str, float]:
    """Delta-normal VaR: first-order sensitivities (by full revaluation of a small bump) and EWMA covariance."""
    h = 1e-4
    bumps = np.eye(len(columns)) * h
    g = scenario_pnl(levels0, bumps, absolute, columns, instruments, fx_map, quantities).sum(axis=1) / h
    sigma = float(np.sqrt(g @ ewma_cov(returns, decay) @ g))
    out = {f"VaR{round(a * 100)}": norm.ppf(a) * sigma for a in alphas}
    out[f"ES{round(es_alpha * 1000)}"] = sigma * norm.pdf(norm.ppf(es_alpha)) / (1 - es_alpha)
    return out
