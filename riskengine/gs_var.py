"""Parse Goldman Sachs' VaR tables out of its 10-Q and 10-K filings.

The tables are not tagged in XBRL (checked for the FY2025 10-K: zero tags), so
they are read from the HTML. Each filing has two tables with a "Diversification
effect" row:
  1. average daily VaR: first number column = the current quarter (10-Q) or year (10-K)
  2. VaR at period end: first number column = the current period end
Only the first number column is used, because the other columns change over
the years. Every parsed table is checked: the four categories plus the
diversification effect must equal the total (to rounding).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

import pandas as pd

CATEGORIES = {"interest_rates": r"^interest rates", "equity_prices": r"^equity prices",
              "currency_rates": r"^currency rates", "commodity_prices": r"^commodity prices",
              "diversification": r"^diversification effect", "total": r"^total"}


@dataclass
class Parsed:
    values: dict           # category -> USD millions (diversification negative)
    header: str            # header text of the table, kept for audit
    identity_gap: float    # sum of categories + diversification - total


def _cells(row: str) -> list[str]:
    out = []
    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
        if t and t not in ("$", ")", "%"):
            out.append(t)
    return out


def _number(text: str) -> float | None:
    t = text.replace("$", "").replace(",", "").strip()
    neg = t.startswith("(")
    t = t.strip("()").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    return -float(t) if neg else float(t)


def parse_table(table_html: str, column: int = 0) -> Parsed | None:
    values, header = {}, []
    for row in re.findall(r"<tr.*?</tr>", table_html, re.S | re.I):
        cells = _cells(row)
        if not cells:
            continue
        label = re.sub(r"\s*\d+$", "", cells[0]).strip().lower()      # drop footnote markers ("effect 1")
        key = next((k for k, pat in CATEGORIES.items() if re.search(pat, label)), None)
        if key is None:
            if not values:
                header.append(" | ".join(cells))
            continue
        nums = [n for n in (_number(c) for c in cells[1:]) if n is not None]
        if len(nums) > column and key not in values:
            values[key] = nums[column]
    if set(values) != set(CATEGORIES):
        return None
    gap = sum(values[k] for k in CATEGORIES if k != "total") - values["total"]
    return Parsed(values, " / ".join(header), gap)


def quarter_end(filed: str) -> pd.Timestamp:
    """The last calendar quarter end before the filing date (10-Qs and 10-Ks are filed within about 60 days)."""
    return (pd.Timestamp(filed) - pd.offsets.QuarterEnd(1)).normalize()


def parse_filing(raw_html: str, form: str, filed: str, accession: str) -> list[dict]:
    def text(t: str) -> str:            # markup can split the phrase ("Diversification</font> effect")
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
    tables = [t for t in re.findall(r"<table.*?</table>", raw_html, re.S | re.I)
              if re.search(r"Diversification\s+effect", text(t), re.I)]
    parsed = [p for p in (parse_table(t) for t in tables) if p is not None]
    period = quarter_end(filed)
    out = []
    for kind, p in zip(("average", "period_end"), parsed[:2]):
        out.append({"accession": accession, "form": form, "filed": filed, "period_end": period.date(),
                    "measure": kind, "horizon": ("year" if form == "10-K" else "quarter") if kind == "average" else "day",
                    "column": 1, **p.values, "identity_gap": p.identity_gap, "header": p.header[:300]})

    # 10-Ks give only the annual average. From the Q1 2017 10-Q on, the Q1 10-Q's second column is the
    # previous Q4's quarterly average ("Three Months Ended ... December"): the only place it is published.
    if form == "10-Q" and period.month == 3 and parsed:
        months = re.findall(r"(March|June|September|December)", parsed[0].header)
        if months[:2] == ["March", "December"]:
            q4 = parse_table(tables[0], column=1)
            if q4 is not None:
                out.append({"accession": accession, "form": form, "filed": filed,
                            "period_end": (period - pd.offsets.QuarterEnd(1)).date(), "measure": "average",
                            "horizon": "quarter", "column": 2, **q4.values, "identity_gap": q4.identity_gap,
                            "header": q4.header[:300]})
    return out


def reprint_check(parsed_by_quarter: dict) -> dict:
    """Each 10-Q's second average column should reprint an earlier filing's first column exactly.

    parsed_by_quarter: {quarter_end: (first_column_values, second_column_values)} for 10-Qs.
    Q1 filings from 2017 print the previous Q4 in column 2, which no other filing
    publishes, so they have nothing to be checked against.
    """
    out = {"prior_quarter": 0, "same_quarter_last_year": 0, "unmatched": [], "no_reference": 0}
    for qe, (c1, c2) in sorted(parsed_by_quarter.items()):
        pq = (qe - pd.offsets.QuarterEnd(1)).normalize()
        py = (qe - pd.DateOffset(years=1)).normalize() + pd.offsets.QuarterEnd(0)
        if c2 is None:
            out["no_reference"] += 1
        elif pq in parsed_by_quarter and c2 == parsed_by_quarter[pq][0]:
            out["prior_quarter"] += 1
        elif py in parsed_by_quarter and c2 == parsed_by_quarter[py][0]:
            out["same_quarter_last_year"] += 1
        elif qe.month == 3 or pq not in parsed_by_quarter or py not in parsed_by_quarter:
            # column 2 is either the prior quarter or the same quarter last year; if either original is
            # missing we cannot tell which one it reprints, so it cannot be judged
            out["no_reference"] += 1
        else:
            out["unmatched"].append((qe.date(), c2))
    return out


def first_two_columns(raw_html: str) -> tuple[dict | None, dict | None]:
    def text(t: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
    tables = [t for t in re.findall(r"<table.*?</table>", raw_html, re.S | re.I)
              if re.search(r"Diversification\s+effect", text(t), re.I)]
    if not tables:
        return None, None
    a, b = parse_table(tables[0], 0), parse_table(tables[0], 1)
    return (a.values if a else None), (b.values if b else None)
