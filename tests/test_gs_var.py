"""Parser for Goldman's VaR tables, on small hand-written HTML in each layout seen in real filings."""
from __future__ import annotations

import pandas as pd

from riskengine import gs_var


def table(header_rows, rows, broken_brackets=False, split_phrase=False):
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    body = "".join(tr(h) for h in header_rows)
    for label, nums in rows:
        if label == "Diversification effect" and split_phrase:
            label = "Diversification</font> <font>effect"
        cells = [label] + [(f"({-n}" if broken_brackets else f"({-n})") if n < 0 else f"$ {n}" for n in nums]
        body += tr(cells)
    return f"<table>{body}</table>"


ROWS_2026 = [("Interest rates", [82, 85, 79]), ("Equity prices", [65, 55, 48]), ("Currency rates", [19, 15, 23]),
             ("Commodity prices", [30, 31, 15]), ("Diversification effect", [-76, -74, -67]), ("Total", [120, 112, 98])]


def test_parses_first_column_and_checks_identity():
    p = gs_var.parse_table(table([["Three Months Ended"], ["June", "March", "June"]], ROWS_2026))
    assert p.values == {"interest_rates": 82, "equity_prices": 65, "currency_rates": 19, "commodity_prices": 30,
                        "diversification": -76, "total": 120}
    assert p.identity_gap == 0


def test_old_layout_quirks():
    rows = [("Interest rates", [76]), ("Equity prices", [35]), ("Currency rates", [21]), ("Commodity prices", [39]),
            ("Diversification effect 1", [-70]), ("Total", [101])]           # footnote marker, missing ")"
    p = gs_var.parse_table(table([["Three Months"]], rows, broken_brackets=True))
    assert p.values["diversification"] == -70 and p.identity_gap == 0


def test_split_phrase_is_still_found():
    html = table([["Three Months Ended"], ["June", "March", "June"]], ROWS_2026, split_phrase=True)
    out = gs_var.parse_filing(html + html, "10-Q", "2026-08-03", "x")
    assert len(out) == 2 and out[0]["period_end"] == pd.Timestamp("2026-06-30").date()


def test_q1_filing_yields_previous_q4():
    html = table([["Three Months Ended"], ["March", "December", "March"]], ROWS_2026)
    out = gs_var.parse_filing(html + html, "10-Q", "2026-05-01", "x")
    q4 = [r for r in out if r["column"] == 2]
    assert len(q4) == 1 and q4[0]["period_end"] == pd.Timestamp("2025-12-31").date() and q4[0]["total"] == 112


def test_reprint_check():
    a = {"total": 1}
    q = lambda s: pd.Timestamp(s)
    parsed = {q("2025-06-30"): (a, None), q("2025-09-30"): ({"total": 2}, a), q("2026-06-30"): ({"total": 3}, {"total": 9})}
    out = gs_var.reprint_check(parsed)
    assert out["prior_quarter"] == 1 and out["unmatched"] == [] and out["no_reference"] == 2
    parsed[q("2026-03-31")] = ({"total": 4}, None)
    parsed[q("2025-06-30")] = ({"total": 5}, None)
    assert len(gs_var.reprint_check(parsed)["unmatched"]) == 1                 # both references exist, neither matches
