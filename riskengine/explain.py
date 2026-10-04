"""Why did VaR move since the previous market day?

VaR depends on three things that all change overnight:
- positions: what the book holds (trades)
- levels: today's prices, yields and FX rates (the market moved)
- window: the scenario set (one new day enters, the oldest drops out)

VaR is a quantile, so these effects do not add up on their own, and the answer
from changing one thing at a time depends on the order. We use the Shapley
split: average each factor's effect over every order (3! = 6 orders, 8
evaluations). The three parts then add up exactly to the total change, and no
order is favoured. The split is exact but still a convention: another
convention would give different parts with the same total.
"""
from __future__ import annotations

from itertools import combinations
from math import factorial

import numpy as np

from .var import scenario_pnl, tail, var_es

PLAYERS = ("positions", "levels", "window")


def shapley(value) -> dict[str, float]:
    """value(frozenset of players switched to 'today') -> number. Returns each player's share."""
    n = len(PLAYERS)
    out = {}
    for p in PLAYERS:
        others = [x for x in PLAYERS if x != p]
        total = 0.0
        for size in range(n):
            for s in combinations(others, size):
                w = factorial(size) * factorial(n - size - 1) / factorial(n)
                total += w * (value(frozenset(s) | {p}) - value(frozenset(s)))
        out[p] = total
    return out


def explain(prev: dict, cur: dict, absolute, columns, instruments, fx_map, weights_fn, es_alpha: float) -> dict:
    """prev/cur: {"q": (N,), "levels": (F,), "window": (S, F), "dates": index of the window rows}."""
    cache = {}

    def measures(switched: frozenset) -> tuple[float, float]:
        if switched not in cache:
            q = cur["q"] if "positions" in switched else prev["q"]
            lv = cur["levels"] if "levels" in switched else prev["levels"]
            win = cur["window"] if "window" in switched else prev["window"]
            pnl = scenario_pnl(lv, win, absolute, columns, instruments, fx_map, q).sum(axis=1)
            w = weights_fn(len(win))
            cache[switched] = (var_es(pnl, w, 0.99)[0], var_es(pnl, w, es_alpha)[1])
        return cache[switched]

    result = {}
    for j, name in enumerate(("VaR99", "ES975")):
        parts = shapley(lambda s: measures(s)[j])
        result[name] = {"previous": measures(frozenset())[j], **parts, "current": measures(frozenset(PLAYERS))[j]}

    # Which historical day sets VaR, before and after
    def var_day(state):
        pnl = scenario_pnl(state["levels"], state["window"], absolute, columns, instruments, fx_map,
                           state["q"]).sum(axis=1)
        v, _ = tail(pnl, weights_fn(len(pnl)), 0.99)
        k = int(np.flatnonzero(np.isclose(-pnl, v))[0])      # the scenario whose loss is the VaR
        return state["dates"][k], float(pnl[k])

    result["var_day_previous"] = var_day(prev)
    result["var_day_current"] = var_day(cur)

    # Scenarios that left and entered the window, valued on today's book and market
    entered = [d for d in cur["dates"] if d not in set(prev["dates"])]
    left = [d for d in prev["dates"] if d not in set(cur["dates"])]
    def value_on_today(dates, state):
        rows = [list(state["dates"]).index(d) for d in dates]
        return [(d, float(scenario_pnl(cur["levels"], state["window"][[r]], absolute, columns, instruments,
                                       fx_map, cur["q"]).sum())) for d, r in zip(dates, rows)]
    result["entered"] = value_on_today(entered, cur)
    result["left"] = value_on_today(left, prev)
    return result
