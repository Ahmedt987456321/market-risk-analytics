"""Case study: what can public data tell us about Goldman Sachs' reported market risk?

Inputs, all public:
- Goldman's quarterly VaR tables (10-Q, 10-K), parsed by gs_var.py
- one market benchmark per VaR category (FRED, Yahoo)

The question is deliberately limited: given only disclosed VaR and observable
markets, what changes in the reported risk profile can be attributed to market
volatility, what is left over (a mix of position changes, model changes and the
proxy's own errors), and what cannot be said at all.

A VaR number is roughly exposure x volatility. Taking logs,
    log VaR = log exposure + log volatility,
so the part of a VaR change that volatility cannot explain is an upper bound
on "position changes", contaminated by everything else. That residual is
called the implied exposure index here, and treated with suspicion.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import edgar, gs_var, sources

CATEGORY_PROXY = {
    "interest_rates": ("fred", "DGS10", "abs", "US 10y Treasury yield (FRED DGS10), daily change"),
    "equity_prices": ("yahoo", "^GSPC", "log", "S&P 500 (Yahoo ^GSPC), daily log return"),
    "currency_rates": ("fred", "DTWEXBGS", "log", "Nominal broad US dollar index (FRED DTWEXBGS), daily log return"),
    "commodity_prices": ("fred", "DCOILBRENTEU", "log", "Brent spot (FRED DCOILBRENTEU), daily log return"),
}
CAT_LABEL = {"interest_rates": "Interest rates", "equity_prices": "Equity prices",
             "currency_rates": "Currency rates", "commodity_prices": "Commodity prices"}
WINDOWS = {"1y": 250, "2y": 500, "5y": 1260}     # trailing market days


@dataclass
class CaseStudy:
    var: pd.DataFrame           # quarterly average VaR by category
    vol: pd.DataFrame           # quarterly average of trailing vol, per category and window
    fit: pd.DataFrame           # how well each window's vol explains VaR changes
    implied: pd.DataFrame       # implied exposure index (best window)
    episodes: pd.DataFrame      # decomposition of selected quarter-on-quarter changes
    diversification: pd.DataFrame
    lineage: pd.DataFrame
    coverage: dict


def load_gs_var(cache: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, lineage = [], []
    for form in ("10-Q", "10-K"):
        for f in edgar.list_filings(form):
            if f.filed < "2010-01-01":
                continue
            f = edgar.fetch(f, cache)
            got = gs_var.parse_filing(f.raw_path.read_bytes().decode("utf-8", "ignore"), f.form, f.filed, f.accession)
            rows += got
            lineage.append({"accession": f.accession, "form": f.form, "filed": f.filed, "url": f.doc_url,
                            "sha256": f.sha256, "tables_parsed": len(got)})
    df = pd.DataFrame(rows)
    df["period_end"] = pd.to_datetime(df["period_end"])
    return df, pd.DataFrame(lineage)


def load_benchmarks(raw_dir: Path, start: str = "2004-01-01") -> pd.DataFrame:
    """Daily benchmark levels, one column per category. Raw responses saved and hashed like the main pipeline."""
    fred_ids = [t for s, t, _, _ in CATEGORY_PROXY.values() if s == "fred"]
    url = sources.fred_url(fred_ids, start)
    text = sources._get(url)
    fred = sources._save("fred_casestudy", url, text, raw_dir, sources.parse_fred(text, start)).frame
    ytext = sources.download_yahoo(["^GSPC"], start)
    yahoo = sources._save("yahoo_casestudy", f"yfinance.download(['^GSPC'], start={start})", ytext, raw_dir,
                          sources.parse_yahoo(ytext)).frame
    both = pd.concat([fred, yahoo], ignore_index=True)
    wide = both.pivot_table(index="date", columns="ticker", values="close")
    return wide.rename(columns={t: c for c, (_, t, _, _) in CATEGORY_PROXY.items()})


def trailing_vol(levels: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Daily trailing standard deviation of each benchmark's daily move, for each window."""
    moves = {}
    for c, (_, _, kind, _) in CATEGORY_PROXY.items():
        s = levels[c].dropna()
        moves[c] = s.diff() if kind == "abs" else np.log(s / s.shift(1))
    moves = pd.DataFrame(moves)
    return {w: moves.rolling(n, min_periods=int(n * 0.9)).std() for w, n in WINDOWS.items()}


def quarterly_average(daily: pd.DataFrame) -> pd.DataFrame:
    q = daily.groupby(daily.index.to_period("Q")).mean()
    q.index = q.index.to_timestamp(how="end").normalize()
    return q


def analyse(gs: pd.DataFrame, levels: pd.DataFrame) -> dict:
    avg = gs[(gs.measure == "average") & (gs.horizon == "quarter")].set_index("period_end").sort_index()
    cats = list(CATEGORY_PROXY)
    var = avg[cats + ["diversification", "total"]]

    vols = {w: quarterly_average(v) for w, v in trailing_vol(levels).items()}
    vol_long = pd.concat({w: v for w, v in vols.items()}, names=["window", "period_end"])

    # Fit: correlation of quarter-on-quarter log changes in VaR and in trailing vol,
    # using only consecutive quarters (Q4 is missing for 2010-2015).
    fit_rows = []
    for c in cats:
        for w, v in vols.items():
            j = pd.concat([np.log(var[c]).rename("var"), np.log(v[c]).rename("vol")], axis=1, join="inner").dropna()
            consecutive = j.index.to_series().diff().dt.days.between(80, 100)
            d = j.diff()[consecutive]
            if len(d) < 8:
                continue
            fit_rows.append({"category": c, "window": w, "quarters": len(d),
                             "corr_changes": d["var"].corr(d["vol"]), "corr_levels": j["var"].corr(j["vol"]),
                             "elasticity": np.polyfit(d["vol"], d["var"], 1)[0]})
    fit = pd.DataFrame(fit_rows)

    best = fit.loc[fit.groupby("category")["corr_changes"].idxmax()].set_index("category")
    implied = {}
    for c in cats:
        w = best.loc[c, "window"]
        e = (var[c] / vols[w][c].reindex(var.index)).dropna()
        implied[c] = 100 * e / e.iloc[0]
    implied = pd.DataFrame(implied)

    # Diversification: how much of the sum of category VaRs is cancelled, against the trailing
    # 1y correlation of equity returns with bond returns (bond return ~ minus the yield change).
    div = pd.DataFrame({"diversification_share": -var["diversification"] / var[cats].sum(axis=1)})
    moves = pd.DataFrame({"eq": np.log(levels["equity_prices"] / levels["equity_prices"].shift(1)),
                          "bond": -levels["interest_rates"].diff()}).dropna()
    sb = moves["eq"].rolling(250, min_periods=225).corr(moves["bond"])
    div["stock_bond_corr_1y"] = quarterly_average(sb.to_frame("c"))["c"].reindex(div.index)

    return {"var": var, "vols": vols, "vol_long": vol_long, "fit": fit, "best": best, "implied": implied, "div": div}


def decompose(var: pd.DataFrame, vols: dict, best: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """For each (from, to) quarter: change in log VaR split into volatility and residual, per category."""
    rows = []
    for a, b in pairs:
        a, b = pd.Timestamp(a), pd.Timestamp(b)
        for c in CATEGORY_PROXY:
            w = best.loc[c, "window"]
            if a not in var.index or b not in var.index:
                continue
            d_var = np.log(var.loc[b, c] / var.loc[a, c])
            d_vol = np.log(vols[w].loc[b, c] / vols[w].loc[a, c])
            rows.append({"from": a.date(), "to": b.date(), "category": c, "window": w,
                         "var_from": var.loc[a, c], "var_to": var.loc[b, c],
                         "var_change_pct": np.expm1(d_var), "vol_change_pct": np.expm1(d_vol),
                         "residual_pct": np.expm1(d_var - d_vol)})
    return pd.DataFrame(rows)
