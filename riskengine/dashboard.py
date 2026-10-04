"""Build the self-contained HTML dashboard from a finished run.

All figures are the run's own numbers (the same ones in risk.md and
commentary.md). The page embeds them as JSON and draws the charts with plain
JavaScript and SVG, so it works offline with no libraries.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .commentary import position_table
from .report import LABELS, METHOD_LABELS

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
METHOD_SHORT = {"hs_equal": "HS equal", "hs_weighted": "HS weighted", "parametric": "Parametric"}
STRESS_SHORT = {"lehman_2008": "Lehman 2008", "covid_2020": "COVID 2020", "gilt_2022": "UK gilt crisis 2022",
                "equities_down_15": "Equities -15%", "rates_up_100bp": "Yields +100bp",
                "gbp_down_10": "Sterling -10%", "uk_crisis": "UK crisis (combined)"}


def commentary_blocks(markdown: str) -> list[dict]:
    """Turn the commentary's small markdown subset into blocks the page renders as text (never as HTML)."""
    blocks, items = [], []
    for line in markdown.splitlines():
        if line.startswith("- "):
            items.append(line[2:])
            continue
        if items:
            blocks.append({"type": "ul", "items": items}); items = []
        if line.startswith("## "):
            blocks.append({"type": "h", "text": line[3:]})
        elif line.strip() and not line.startswith("# "):
            blocks.append({"type": "p", "text": line.replace("**", "")})
    if items:
        blocks.append({"type": "ul", "items": items})
    return blocks


def build(cfg, info: dict, rr, results, commentary_md: str) -> str:
    primary = cfg.risk["primary_method"]
    m = rr.totals[primary]
    ex_v, ex_e = rr.explain["VaR99"], rr.explain["ES975"]
    positions = position_table(cfg, rr)
    s = rr.backtest_summary[primary]

    bt = rr.backtest.pivot(index="date", columns="method", values="var99").sort_index()
    pnl = rr.backtest[rr.backtest.method == primary].set_index("date")["pnl"].reindex(bt.index)
    history = {"dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in bt.index],
               "pnl": [round(float(x), 2) for x in pnl],
               "var": {k: [round(float(x), 2) for x in bt[k]] for k in cfg.risk["methods"]}}

    # VaR on the day it was calculated: each backtest forecast was made at the previous market
    # day's close, and today's figure is appended, so the last point equals the headline.
    computed_on = [pd.Timestamp(cfg.book["start"])] + list(pd.to_datetime(bt.index[:-1]))
    var_history = {"dates": [d.strftime("%Y-%m-%d") for d in computed_on] + [f"{rr.as_of:%Y-%m-%d}"],
                   "var": {k: [round(float(x), 2) for x in bt[k]] + [round(float(rr.totals[k]["VaR99"]), 2)]
                           for k in cfg.risk["methods"]}}

    stress = []
    for x in rr.stress:
        if x.pnl != x.pnl:                       # not available on this data
            continue
        stress.append({"name": x.name, "short": STRESS_SHORT.get(x.id, x.name), "pnl": x.pnl,
                       "worst": x.worst_pnl, "kind": x.kind,
                       "window": f"{x.window[0]:%d %b %Y} to {x.window[1]:%d %b %Y}" if x.window else "instant",
                       "top": ", ".join(f"{i} {p / 1e6:+.2f}m" for i, p in x.top_positions if p < 0)})

    es_total = m["ES975"]
    data = {
        "meta": {"asOfLong": f"{rr.as_of:%d %B %Y}", "prevLong": f"{rr.prev_date:%d %B %Y}",
                 "prevShort": f"{rr.prev_date:%d %b}", "runId": info["run_id"], "code": info["code_version"],
                 "status": info["status"], "primary": primary, "methods": list(cfg.risk["methods"]),
                 "methodLabels": {k: METHOD_LABELS[k] for k in cfg.risk["methods"]},
                 "methodShort": {k: METHOD_SHORT[k] for k in cfg.risk["methods"]},
                 "concentration": cfg.risk["commentary"]["concentration_share"]},
        "tiles": {"var99": m["VaR99"], "es975": m["ES975"], "var95": m["VaR95"], "prevVar99": ex_v["previous"],
                  "gross": float(positions.mv.abs().sum()), "zone": s["zone"], "lastExc": s["last_exceptions"],
                  "lastN": s["last_n"]},
        "explain": {"previous": ex_v["previous"], "positions": ex_v["positions"], "levels": ex_v["levels"],
                    "window": ex_v["window"], "current": ex_v["current"],
                    "esPrevious": ex_e["previous"], "esPositions": ex_e["positions"], "esLevels": ex_e["levels"],
                    "esWindow": ex_e["window"], "esCurrent": ex_e["current"]},
        "desks": [{"desk": r.desk, "es": float(r.ES975_contribution), "share": float(r.ES975_contribution / es_total),
                   "var": float(r.VaR99_standalone)}
                  for r in rr.desks.sort_values("ES975_contribution", ascending=False).itertuples()],
        "stress": stress,
        "history": history,
        "varHistory": var_history,
        "positions": [{"id": r.instrument_id, "name": r.name, "desk": r.desk, "mv": float(r.mv),
                       "share": float(r.share_of_gross)} for r in positions.head(10).itertuples()],
        "controls": [{"label": LABELS.get(r.control, r.control), "status": r.status, "detail": r.detail} for r in results],
        "commentary": commentary_blocks(commentary_md),
    }
    payload = json.dumps(data, default=lambda o: float(o) if isinstance(o, np.floating) else str(o))
    payload = payload.replace("</", "<\\/")      # cannot close the <script> element early
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)
