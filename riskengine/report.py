from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import ROOT

LABELS = {
    "raw_duplicates": "Duplicate source rows",
    "invalid_values": "Invalid values",
    "missing_prices": "Missing prices",
    "stale_prices": "Stale prices",
    "interior_gaps": "Gaps inside series",
    "futures_roll": "Futures roll check",
    "dividend_adjustment": "Dividend adjustment check",
    "return_outliers": "Unusual moves",
    "cross_source": "Cross-source check",
    "revisions": "Historical revisions",
    "history_sufficiency": "History for VaR",
    "position_reconciliation": "Position reconciliation",
    "position_coverage": "Positions priced",
    "lineage_complete": "Data lineage recorded",
    "reporting_sla": "Reporting SLA",
}


def book_snapshot(con, cfg, as_of, unit_values: pd.Series) -> pd.DataFrame:
    pos = con.execute(
        "SELECT instrument_id, quantity FROM positions WHERE as_of = ?", [as_of.date()]
    ).df().set_index("instrument_id")["quantity"]
    mv = pos * unit_values.reindex(pos.index)
    desk = pd.Series({i.id: i.desk for i in cfg.instruments})
    frame = pd.DataFrame({"desk": desk.reindex(mv.index), "mv": mv})
    return frame.groupby("desk")["mv"].agg(
        long=lambda s: s[s > 0].sum(), short=lambda s: s[s < 0].sum(), net="sum", positions="count"
    )


def _rel(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def _gbp_m(x: float, dp: int = 1) -> str:
    return f"{x / 1e6:,.{dp}f}m"


def render(run: dict, results, snapshot: pd.DataFrame, lineage: pd.DataFrame) -> str:
    lines = [
        f"# Market risk run: controls report, {run['as_of']}",
        "",
        f"Run `{run['run_id']}` | mode {run['mode']} | code {run['code_version']} | config {run['config_sha256'][:8]}",
        "",
        f"**Overall status: {run['status']}**",
        "",
        "| Control | Status | Affected | Detail |",
        "|---|---|---|---|",
    ]
    for r in results:
        detail = r.detail.replace("|", "/")
        lines.append(f"| {LABELS.get(r.control, r.control)} | {r.status} | {r.n_affected} | {detail} |")

    lines += ["", "## Book snapshot (GBP, market value)", "",
              "| Desk | Long | Short | Net | Positions |", "|---|---:|---:|---:|---:|"]
    for desk, row in snapshot.iterrows():
        lines.append(f"| {desk} | {_gbp_m(row['long'])} | {_gbp_m(row['short'])} | {_gbp_m(row['net'])} | {int(row['positions'])} |")
    tot = snapshot.sum()
    lines.append(f"| **Total** | {_gbp_m(tot['long'])} | {_gbp_m(tot['short'])} | {_gbp_m(tot['net'])} | {int(tot['positions'])} |")

    lines += ["", "## Lineage", "", "| Source | Fetched (UTC) | Rows | SHA-256 | Raw file |", "|---|---|---:|---|---|"]
    for r in lineage.itertuples():
        lines.append(f"| {r.source} | {pd.Timestamp(r.fetched_at):%Y-%m-%d %H:%M} | {r.n_rows:,} | `{r.sha256[:16]}` | `{_rel(r.raw_path)}` |")
    lines.append("")
    return "\n".join(lines)


METHOD_LABELS = {
    "hs_equal": "Historical simulation, equal weights",
    "hs_weighted": "Historical simulation, age-weighted",
    "parametric": "Parametric (delta-normal, EWMA)",
}


def render_risk(run: dict, risk, cfg, controls_status: str) -> str:
    r = cfg.risk
    primary = r["primary_method"]
    lines = [f"# Market risk run: risk report, {run['as_of']}", "",
             f"Run `{run['run_id']}` | code {run['code_version']} | config {run['config_sha256'][:8]}", ""]
    if controls_status == "FAIL":
        lines += ["> **Not for use: one or more data controls failed.** See controls.md.", ""]
    elif controls_status == "WARN":
        lines += ["> Data controls raised warnings (see controls.md). Figures use carried-forward prices where flagged.", ""]
    lines += [
        f"One-day horizon, GBP. {risk.n_scenarios:,} historical scenarios from {risk.first_scenario:%d %b %Y} "
        f"to {risk.as_of:%d %b %Y}, applied to today's book with full revaluation. "
        f"Primary method (fixed before backtesting): {METHOD_LABELS[primary]}.",
        "",
        "## Headline", "",
        "| Method | VaR 95% | VaR 99% | ES 97.5% | VaR 99% x sqrt(10) |", "|---|---:|---:|---:|---:|",
    ]
    for method, m in risk.totals.items():
        name = METHOD_LABELS[method] + (" (primary)" if method == primary else "")
        lines.append(f"| {name} | {_gbp_m(m['VaR95'], 2)} | {_gbp_m(m['VaR99'], 2)} | {_gbp_m(m['ES975'], 2)} "
                     f"| {_gbp_m(m['VaR99'] * 10 ** 0.5, 2)} |")
    lines += ["", "The last column scales one-day VaR by the square root of 10, the convention Goldman's UK Pillar 3 "
              "describes for its regulatory 10-day VaR. It assumes independent days and is shown only for comparability.", ""]

    d = risk.desks
    es_total = risk.totals[primary]["ES975"]
    lines += ["## By desk (primary method)", "",
              "| Desk | VaR 99% on its own | Contribution to ES 97.5% | Share of ES |", "|---|---:|---:|---:|"]
    for row in d.itertuples():
        lines.append(f"| {row.desk} | {_gbp_m(row.VaR99_standalone, 2)} | {_gbp_m(row.ES975_contribution, 2)} "
                     f"| {row.ES975_contribution / es_total:.1%} |")
    standalone = d.VaR99_standalone.sum()
    whole = risk.totals[primary]["VaR99"]
    lines += [f"| **Total** | {_gbp_m(standalone, 2)} | {_gbp_m(d.ES975_contribution.sum(), 2)} | 100% |", "",
              f"Diversification: the desks' stand-alone 99% VaRs add up to {_gbp_m(standalone)}, against "
              f"{_gbp_m(whole)} for the whole book, a benefit of {_gbp_m(standalone - whole)}. "
              "ES contributions add up exactly to total ES; stand-alone VaRs do not add up, by design.", ""]

    lines += _explain_section(risk, cfg) + _stress_section(risk)

    lines += ["## Backtest", "",
              "Each day's VaR, forecast at the previous close, against that day's hypothetical P&L "
              f"(yesterday's positions, today's market moves). Period: {r['backtest']['start']} to {run['as_of']}.", "",
              "| Method | Days | 99% exceptions (expected) | Kupiec p | Christoffersen p | Last 250 days | Basel zone "
              "| 95% exceptions (expected) | Kupiec p |",
              "|---|---:|---:|---:|---:|---:|---|---:|---:|"]
    for method, s in risk.backtest_summary.items():
        a, b = s["99"], s["95"]
        zone = s["zone"] + (f" (+{s['plus_factor']:.2f})" if s["plus_factor"] else "")
        lines.append(f"| {METHOD_LABELS[method]} | {a['n']} | {a['exceptions']} ({a['expected']:.1f}) "
                     f"| {a['kupiec_p']:.2f} | {a['christoffersen_p']:.2f} | {s['last_exceptions']} of {s['last_n']} "
                     f"| {zone} | {b['exceptions']} ({b['expected']:.1f}) | {b['kupiec_p']:.2f} |")
    first = next(iter(risk.backtest_summary.values()))
    lines += ["", f"Worst hypothetical day: {first['worst_day']} ({_gbp_m(first['worst_pnl'])}). "
              "A Kupiec or Christoffersen p-value below 0.05 rejects the model at the 5% level. "
              "Basel zones: green 0-4, yellow 5-9, red 10+ exceptions in 250 days (BCBS, Jan 1996, Table 2).", ""]
    return "\n".join(lines)


def _explain_section(risk, cfg) -> list[str]:
    ex = risk.explain
    v, e = ex["VaR99"], ex["ES975"]
    lines = [f"## What moved VaR since {risk.prev_date:%d %b %Y}", "",
             "| | VaR 99% | ES 97.5% |", "|---|---:|---:|",
             f"| Previous market day | {_gbp_m(v['previous'], 2)} | {_gbp_m(e['previous'], 2)} |",
             f"| Positions changed (trades) | {v['positions'] / 1e6:+,.2f}m | {e['positions'] / 1e6:+,.2f}m |",
             f"| Market levels moved | {v['levels'] / 1e6:+,.2f}m | {e['levels'] / 1e6:+,.2f}m |",
             f"| Scenario window rolled | {v['window'] / 1e6:+,.2f}m | {e['window'] / 1e6:+,.2f}m |",
             f"| **Today** | **{_gbp_m(v['current'], 2)}** | **{_gbp_m(e['current'], 2)}** |", ""]
    (d0, p0), (d1, p1) = ex["var_day_previous"], ex["var_day_current"]
    same = "the same day" if d0 == d1 else f"{d1:%d %b %Y} (it was {d0:%d %b %Y})"
    lines.append(f"The 99% VaR is set by the scenario from {same}. ")
    if ex["entered"] or ex["left"]:
        parts = [f"entered: {d:%d %b %Y} ({p / 1e6:+.2f}m on today's book)" for d, p in ex["entered"]]
        parts += [f"left: {d:%d %b %Y} ({p / 1e6:+.2f}m on today's book)" for d, p in ex["left"]]
        lines.append("Window roll: " + "; ".join(parts) + ".")
    if risk.trades is not None and len(risk.trades):
        t = risk.trades.sort_values("value_gbp", key=abs, ascending=False)
        lines.append("Trades: " + ", ".join(f"{r.instrument_id} {r.value_gbp / 1e6:+.2f}m" for r in t.itertuples()) + ".")
    else:
        lines.append("No trades since the previous market day.")
    lines += ["", "The three effects are a Shapley split (each averaged over every order of applying the changes), "
              "so they add up exactly to the total change. The split is a convention: VaR is a quantile and has no "
              "unique additive breakdown.", ""]
    return lines


def _stress_section(risk) -> list[str]:
    lines = ["## Stress tests", "",
             "Today's book, held constant (no trading or hedging), repriced in full under each scenario.", "",
             "| Scenario | Window | P&L at end | Worst point | Largest losses |", "|---|---|---:|---:|---|"]
    for s in risk.stress:
        if s.kind == "historical" and s.window is None:
            lines.append(f"| {s.name} | not available: {', '.join(s.no_data)} | | | |")
            continue
        window = f"{s.window[0]:%d %b %Y} to {s.window[1]:%d %b %Y}" if s.window else "instant"
        worst = f"{_gbp_m(s.worst_pnl, 2)} ({s.worst_date:%d %b})" if s.worst_pnl is not None else ""
        top = ", ".join(f"{i} {p / 1e6:+.2f}m" for i, p in s.top_positions if p < 0)
        name = s.name + (f" (no data: {', '.join(s.no_data)})" if s.no_data else "")
        lines.append(f"| {name} | {window} | {_gbp_m(s.pnl, 2)} | {worst} | {top} |")
    lines += ["", "Historical windows apply the cumulative move over the window: relative for prices and FX, absolute "
              "for yields. Hypothetical shocks are round numbers chosen for this project, not regulatory scenarios. "
              "Brent in historical windows uses FRED Brent spot, because the BNO fund only starts in 2010.", ""]
    return lines


def write(report_dir: Path, as_of, text: str, name: str = "controls.md") -> Path:
    folder = report_dir / f"{as_of:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path
