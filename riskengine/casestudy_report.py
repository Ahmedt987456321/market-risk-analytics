"""Run the Goldman Sachs case study and write reports/casestudy/casestudy.md."""
from __future__ import annotations

import html
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm

from . import gs_var
from .casestudy import CAT_LABEL, CATEGORY_PROXY, WINDOWS, analyse, decompose, load_benchmarks, load_gs_var


def fisher_ci(r: float, n: int) -> tuple[float, float]:
    z, se = np.arctanh(r), 1 / np.sqrt(n - 3)
    return float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se))


def run(root: Path, out_dir: Path, export_dir: Path) -> dict:
    manual = yaml.safe_load((root / "config" / "casestudy.yaml").read_text(encoding="utf-8"))
    cache = root / "data" / "raw" / "edgar"
    gs, lineage = load_gs_var(cache)

    reprints = {}
    for r in lineage.itertuples():
        if r.form == "10-Q":
            doc = (cache / f"{r.accession}.htm").read_bytes().decode("utf-8", "ignore")
            reprints[gs_var.quarter_end(r.filed)] = gs_var.first_two_columns(doc)
    reprint = gs_var.reprint_check(reprints)

    # Every filing should state the confidence level the table uses; count the ones that do.
    def states_95(acc: str) -> bool:
        t = (cache / f"{acc}.htm").read_bytes().decode("utf-8", "ignore")
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
        return bool(re.search(r"95% confidence level|95% one-day VaR", t))
    lineage["states_95_one_day"] = [states_95(a) for a in lineage.accession]

    levels = load_benchmarks(root / "data" / "raw" / "casestudy")
    res = analyse(gs, levels)
    var, vols, fit = res["var"], res["vols"], res["fit"].copy()
    ci = [fisher_ci(r.corr_changes, r.quarters) for r in fit.itertuples()]
    fit["ci_lo"], fit["ci_hi"] = [c[0] for c in ci], [c[1] for c in ci]

    pairs = [(str(e["from"]), str(e["to"])) for e in manual["episodes"]]
    dec = pd.concat([decompose(var, vols, pd.DataFrame({"window": w}, index=list(CATEGORY_PROXY)), pairs)
                     for w in WINDOWS], ignore_index=True)

    text = render(gs, lineage, reprint, var, fit, dec, res["div"], manual)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "casestudy.md"
    path.write_text(text, encoding="utf-8")

    export_dir.mkdir(parents=True, exist_ok=True)
    tidy = gs.drop(columns=["header"]).copy()
    tidy["period_end"] = tidy["period_end"].dt.strftime("%Y-%m-%d")
    tidy.to_csv(export_dir / "fact_gs_var.csv", index=False)
    res["vol_long"].reset_index().to_csv(export_dir / "fact_benchmark_vol.csv", index=False)
    lineage.to_csv(export_dir / "gs_filings_lineage.csv", index=False)
    return {"report": path, "filings": len(lineage), "tables": len(gs), "reprint": reprint, "fit": fit,
            "decomposition": dec, "var": var, "div": res["div"]}


def _pct(x: float) -> str:
    return f"{x:+.0%}"


def render(gs, lineage, reprint, var, fit, dec, div, manual) -> str:
    cats = list(CATEGORY_PROXY)
    q = gs[(gs.measure == "average") & (gs.horizon == "quarter")]
    missing = [str(p) for p in pd.period_range(q.period_end.min(), q.period_end.max(), freq="Q")
               if p.to_timestamp(how="end").normalize() not in set(q.period_end)]
    matched = reprint["prior_quarter"] + reprint["same_quarter_last_year"]
    lines = [
        "# Case study: what public data can and cannot say about Goldman Sachs' market risk", "",
        "Goldman Sachs publishes its average daily VaR every quarter, split into four risk categories. This study "
        "asks how much of the change in those numbers can be explained by observable market volatility, what is "
        "left over, and what cannot be said at all. It uses public filings and public market data only. It is not "
        "an estimate of Goldman's positions.", "",
        "## Data and checks", "",
        f"- **Source:** {len(lineage)} 10-Q and 10-K filings, {lineage.filed.min()} to {lineage.filed.max()}, "
        "from SEC EDGAR. Each file is saved and hashed (`exports/bi/gs_filings_lineage.csv`).",
        f"- **Confidence level:** {int(lineage.states_95_one_day.sum())} of {len(lineage)} filings state a one-day "
        "horizon at 95% confidence.",
        "- **Not machine-readable:** the VaR tables have no XBRL tags, so they are read from the filing HTML "
        "(`riskengine/gs_var.py`).",
        f"- **Parsed:** {len(gs)} tables, including {len(q)} quarterly averages from {q.period_end.min():%b %Y} to "
        f"{q.period_end.max():%b %Y}.",
        f"- **Arithmetic check:** in every table, the four categories plus the diversification effect equal the total "
        f"(largest gap: {gs.identity_gap.abs().max():.0f}).",
        f"- **Consistency check:** each 10-Q reprints an earlier quarter. {matched} reprints match the original filing "
        f"exactly and {len(reprint['unmatched'])} do not. {reprint['no_reference']} have nothing to compare with "
        "(mostly Q1 filings, whose second column is the previous Q4, published nowhere else).",
        f"- **Gaps:** {', '.join(missing) if missing else 'none'}. For those years only the annual average was "
        "published; from 2016 the Q4 average appears as the second column of the next Q1 10-Q.", "",
        "## Reported VaR, 2010 to 2026", "",
        "95% one-day VaR, mean of each year's available quarterly averages, USD millions:", "",
        "| Year | " + " | ".join(CAT_LABEL[c] for c in cats) + " | Diversification | Total |",
        "|---|" + "---:|" * (len(cats) + 2),
    ]
    yearly = var.groupby(var.index.year).mean()
    for y, r in yearly.iterrows():
        lines.append(f"| {y} | " + " | ".join(f"{r[c]:.0f}" for c in cats) + f" | {r['diversification']:.0f} | {r['total']:.0f} |")
    lo_y, hi_y = yearly["total"].idxmin(), yearly["total"].idxmax()
    n_rates = int((var[cats].idxmax(axis=1) == "interest_rates").sum())
    lines += ["", f"Total VaR was lowest in {lo_y} ({yearly.loc[lo_y, 'total']:.0f}m) and highest in {hi_y} "
              f"({yearly.loc[hi_y, 'total']:.0f}m). Interest rates are the largest category in {n_rates} of "
              f"{len(var)} quarters.", ""]

    lines += ["## How much of the change is market volatility?", "",
              "For each category, one public benchmark stands in for the market Goldman is exposed to:", ""]
    lines += [f"- {CAT_LABEL[c]}: {CATEGORY_PROXY[c][3]}" for c in cats]
    lines += ["", "Volatility is the trailing standard deviation of daily moves over 1, 2 or 5 years, averaged over "
              "each quarter. The table shows the correlation between quarter-on-quarter changes in log VaR and in log "
              "volatility (consecutive quarters only), with a 95% confidence interval.", "",
              "| Category | 1y | 2y | 5y | Quarters |", "|---|---:|---:|---:|---:|"]
    for c in cats:
        cells = []
        for w in WINDOWS:
            r = fit[(fit.category == c) & (fit.window == w)]
            cells.append("n/a" if r.empty else f"{r.corr_changes.iloc[0]:.2f} ({r.ci_lo.iloc[0]:.2f} to {r.ci_hi.iloc[0]:.2f})")
        lines.append(f"| {CAT_LABEL[c]} | " + " | ".join(cells) + f" | {int(fit[fit.category == c].quarters.max())} |")
    best = fit.loc[fit.corr_changes.idxmax()]
    overlap = all(
        fit[fit.category == c].ci_lo.max() <= fit[fit.category == c].ci_hi.min() for c in cats)
    lines += ["", f"**Reading.** The best fit ({CAT_LABEL[best.category].lower()}, {best.window} window, r = "
              f"{best.corr_changes:.2f}) explains about {best.corr_changes ** 2:.0%} of the quarter-on-quarter variation "
              "(the correlation squared). Most of each change is something else: position changes, model changes, the "
              "benchmark being a poor stand-in for Goldman's actual exposures, or a mix. "
              + ("For every category the three windows' intervals overlap, so the data cannot say which lookback "
                 "Goldman's model effectively behaves like. " if overlap else "")
              + "Goldman's UK Pillar 3 says it weights five years of history towards recent data, but does not give "
              "the weights.", ""]

    lines += ["## Episodes", "",
              "Change in each category's quarterly average VaR, split into the change in benchmark volatility and a "
              "residual (VaR change after removing the volatility change). The residual is shown for the 1-year window, "
              "with the range across all three windows in brackets. A wide range means the split is not robust.", ""]
    for e in manual["episodes"]:
        a, b = pd.Timestamp(e["from"]).date(), pd.Timestamp(e["to"]).date()
        d = dec[(dec["from"] == a) & (dec["to"] == b)]
        if d.empty:
            continue
        lines += [f"**{e['name']}**: {pd.Timestamp(e['from']):%b %Y} quarter to {pd.Timestamp(e['to']):%b %Y} quarter", "",
                  "| Category | VaR (USD m) | VaR change | Vol change (1y) | Residual (1y) [range across windows] |",
                  "|---|---|---:|---:|---|"]
        for c in cats:
            dc = d[d.category == c]
            one = dc[dc.window == "1y"].iloc[0]
            lines.append(f"| {CAT_LABEL[c]} | {one.var_from:.0f} to {one.var_to:.0f} | {_pct(one.var_change_pct)} "
                         f"| {_pct(one.vol_change_pct)} | {_pct(one.residual_pct)} "
                         f"[{_pct(dc.residual_pct.min())} to {_pct(dc.residual_pct.max())}] |")
        lines.append("")
    covid = dec[(dec["to"] == pd.Timestamp("2020-03-31").date()) & (dec.category == "equity_prices")]
    if not covid.empty:
        lines += [f"In the COVID quarter, equity VaR rose {covid.var_change_pct.iloc[0]:.0%} while equal-weighted trailing "
                  f"equity volatility rose between {covid.vol_change_pct.min():.0%} and {covid.vol_change_pct.max():.0%}, "
                  "depending on the window. A model that weights recent days more reacts faster than any of these "
                  "benchmarks, which fits Goldman's description of its own method; so would larger equity positions. "
                  "The public numbers cannot tell these apart.", ""]
    oil = dec[(dec["to"] == pd.Timestamp("2022-06-30").date()) & (dec.category == "commodity_prices")]
    if not oil.empty:
        two = oil[oil.window == "2y"].iloc[0]
        lines += [f"The 2022 commodity row shows how fragile the split is: with the 2-year window, benchmark volatility "
                  f"*fell* {abs(two.vol_change_pct):.0%}, because the extreme Brent moves of spring 2020 left the "
                  "window, so the residual looks huge. Benchmarks have window cliffs of their own.", ""]

    d = div.dropna()
    lines += ["## Diversification", "",
              f"The diversification effect cancelled between {d.diversification_share.min():.0%} and "
              f"{d.diversification_share.max():.0%} of the sum of the four category VaRs. One might expect less "
              "cancellation when stocks and bonds move together. Against the trailing one-year correlation of S&P 500 "
              f"returns with Treasury returns, the relationship is weak (r = "
              f"{d.diversification_share.corr(d.stock_bond_corr_1y):.2f} over {len(d)} quarters). Diversification "
              "depends on how Goldman's positions offset each other, which one market correlation cannot capture.", ""]

    p3 = manual["pillar3_uk"]
    h2 = var.loc["2025-07-01":"2025-12-31", "total"]
    conf = norm.ppf(0.99) / norm.ppf(0.95)
    if len(h2) == 2:
        lines += ["## Why the UK disclosure cannot be compared directly", "",
                  f"The UK Pillar 3 reports an average 99% 10-day regulatory VaR of {p3['var_10d_99']['average']}m for "
                  f"{p3['period']} (Goldman Sachs Group UK Limited, Q4 2025, Table 16). The group's 95% one-day VaR "
                  f"averaged {h2.mean():.1f}m over the same two quarters. Rescaling as if returns were normal (x "
                  f"{conf:.3f} from 95% to 99%, x sqrt(10) for 10 days, x {conf * np.sqrt(10):.2f} in total) gives "
                  f"{h2.mean() * conf * np.sqrt(10):.0f}m. The two differ for reasons the documents name but do not "
                  "quantify: a different legal entity (the UK group, not the whole firm), a different scope (regulatory "
                  "covered positions), a scaler for an effective observation period of at least one year, and fat tails "
                  "that break the normal rescaling. Their ratio is not meaningful, and this study does not report one.", ""]

    lines += ["## What can and cannot be inferred", "",
              "**Can:**",
              "- The level and mix of Goldman's reported VaR over 16 years, from its own filings, every figure checked "
              "arithmetically and against reprints.",
              "- That market volatility, measured by public benchmarks, explains only a small share of quarter-to-quarter "
              "changes.",
              "- That in a sharp sell-off Goldman's reported VaR moved much faster than equal-weighted trailing volatility.", "",
              "**Cannot:**",
              "- Goldman's positions, or how they changed: the residual mixes position changes, model changes and "
              "benchmark error.",
              "- Which lookback or weighting Goldman's model effectively uses.",
              "- A like-for-like comparison with the UK regulatory figures.",
              "- Anything within a quarter: only quarterly averages and quarter-end values are published.", "",
              "## Method notes", "",
              "- Quarter ends are inferred from filing dates (each filing covers the last quarter end before it).",
              "- Benchmarks are US-centred; Goldman's exposures are global.",
              "- Correlations use changes between consecutive quarters only, so missing Q4s do not create two-quarter jumps.",
              "- Figures typed by hand from the Pillar 3 PDF are kept, with their source, in `config/casestudy.yaml`.", ""]
    return "\n".join(lines)
