"""VaR backtesting.

Each day's VaR forecast (made with data up to the previous close and the
positions held at that close) is compared with that day's hypothetical P&L:
the change in value of yesterday's positions caused by today's market moves.
This is the definition in the UK CRR rules as described in Goldman Sachs
Group UK Limited's Pillar 3 disclosure (Q4 2025), and it excludes any effect
of trading during the day.

Tests:
- Kupiec (1995) proportion of failures: is the exception rate right?
- Christoffersen (1998) independence: do exceptions cluster?
- Basel Committee (Jan 1996) traffic light, Table 2: green 0-4, yellow 5-9,
  red 10+ exceptions in 250 days at 99%. For other sample sizes, the yellow
  zone starts where the binomial cumulative probability reaches 95% and the
  red zone where it reaches 99.99% (same document, notes to Table 2).
"""
from __future__ import annotations

import numpy as np
from scipy.stats import binom, chi2

BASEL_PLUS_FACTOR = {5: 0.40, 6: 0.50, 7: 0.65, 8: 0.75, 9: 0.85}   # Table 2, 250 observations


def _xlogy(x: float, y: float) -> float:
    return 0.0 if x == 0 else x * np.log(y)


def kupiec(n: int, x: int, p: float) -> tuple[float, float]:
    """Likelihood ratio and p-value for H0: exception probability is p."""
    if n == 0:
        return float("nan"), float("nan")
    phat = x / n
    ll0 = _xlogy(n - x, 1 - p) + _xlogy(x, p)
    ll1 = _xlogy(n - x, 1 - phat) + _xlogy(x, phat)
    lr = -2 * (ll0 - ll1)
    return lr, float(chi2.sf(lr, 1))


def christoffersen(exceptions: np.ndarray) -> tuple[float, float]:
    """Likelihood ratio and p-value for H0: an exception today does not depend on yesterday."""
    e = exceptions.astype(int)
    prev, cur = e[:-1], e[1:]
    n00 = int(((prev == 0) & (cur == 0)).sum()); n01 = int(((prev == 0) & (cur == 1)).sum())
    n10 = int(((prev == 1) & (cur == 0)).sum()); n11 = int(((prev == 1) & (cur == 1)).sum())
    if n01 + n11 == 0:
        return 0.0, 1.0
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    ll0 = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    ll1 = _xlogy(n00, 1 - pi0) + _xlogy(n01, pi0) + _xlogy(n10, 1 - pi1) + _xlogy(n11, pi1)
    lr = -2 * (ll0 - ll1)
    return lr, float(chi2.sf(lr, 1))


def traffic_light(n: int, x: int, p: float = 0.01) -> tuple[str, float | None]:
    """Zone, and the Basel plus factor when n == 250 (None otherwise)."""
    if x > 0 and binom.cdf(x, n, p) >= 0.9999 - 1e-9:
        zone = "red"
    elif binom.cdf(x, n, p) >= 0.95 - 1e-9:
        zone = "yellow"
    else:
        zone = "green"
    if n != 250:
        return zone, None
    return zone, {"green": 0.0, "red": 1.0}.get(zone, BASEL_PLUS_FACTOR.get(x))


def summarise(pnl: np.ndarray, var: np.ndarray, p: float) -> dict:
    exc = -pnl > var
    n, x = len(exc), int(exc.sum())
    lr_uc, p_uc = kupiec(n, x, p)
    lr_ind, p_ind = christoffersen(exc)
    return {"n": n, "exceptions": x, "expected": n * p, "kupiec_lr": lr_uc, "kupiec_p": p_uc,
            "christoffersen_lr": lr_ind, "christoffersen_p": p_ind,
            "cc_p": float(chi2.sf(lr_uc + lr_ind, 2))}
