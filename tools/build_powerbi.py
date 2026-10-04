"""Generate the Power BI project (PBIP: TMDL semantic model + PBIR report) in powerbi/.

The model loads the CSVs that the pipeline writes to exports/bi/. Run the pipeline
first, then this script, then open powerbi/MarketRisk.pbip in Power BI Desktop and
click Refresh (a freshly generated project has no cached data).

    python tools/build_powerbi.py

Design rules for the report: one question per page, stated in its header; plain
names (never internal codes); money in GBP millions; every chart says how to read
it in its subtitle; bars sorted with value labels; one theme for every page.

File formats follow Microsoft's published PBIP docs and JSON schemas. The schema
versions used are ones a real PBIP saved by Power BI Desktop uses; Desktop
upgrades them on save. IDs are derived from names, so reruns produce the same files.
"""
from __future__ import annotations

import csv
import json
import shutil
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "powerbi"
NAME = "MarketRisk"
BI = ROOT / "exports" / "bi"
BI_DIR = str(BI) + "\\"
THEME_SRC = Path(r"C:\Program Files\WindowsApps\Microsoft.MicrosoftPowerBIDesktop_2.158.1177.0_x64__8wekyb3d8bbwe"
                 r"\bin\WebView2Resources\minerva\sharedresources\BaseThemes\CY24SU10.json")
THEME_NAME = "MarketRiskTheme.json"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report"
NS = uuid.UUID("6f1c2b8e-3d4a-4e5f-9a7b-1c2d3e4f5a6b")
PRIMARY = "hs_equal"

# Colours: the dataviz reference palette (validated for colour-blind separation), plus neutral chrome.
INK, INK2, MUTED, LINE, PAGE, SURFACE = "#0B0B0B", "#52514E", "#898781", "#E1E0D9", "#F7F7F5", "#FFFFFF"
SERIES = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#008300", "#4A3AA7", "#E34948"]


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def oid(*parts: str) -> str:
    """20-character object name, the PBIR convention."""
    return uuid.uuid5(NS, "obj/" + "/".join(parts)).hex[:20]


def q(name: str) -> str:
    """Quote a TMDL object name. Always quoting is allowed, and avoids clashes with keywords
    such as a column called 'measure', 'column' or 'date'."""
    return "'" + name.replace("'", "''") + "'"


def run_dates() -> tuple[str, str]:
    """As-of date and previous market day of the exported run, for page headers."""
    with open(BI / "fact_var_explain.csv", encoding="utf-8") as f:
        row_ = next(csv.DictReader(f))
    fmt = lambda s: date.fromisoformat(s).strftime("%d %b %Y").lstrip("0")
    return fmt(row_["date"]), fmt(row_["prev_date"])


# --- Semantic model -----------------------------------------------------------------

TABLES = {
    "dim_instrument": [("instrument_id", "string"), ("name", "string"), ("asset_class", "string"), ("desk", "string"),
                       ("currency", "string"), ("quote", "string"), ("source", "string"), ("ticker", "string"),
                       ("tenor_years", "double")],
    "dim_desk": [("desk", "string")],
    "dim_date": [("date", "dateTime"), ("year", "int64"), ("quarter", "int64"), ("month", "int64"),
                 ("month_name", "string"), ("weekday", "string")],
    "dim_method": [("method", "string"), ("method_label", "string"), ("is_primary", "int64"), ("method_order", "int64")],
    "dim_scenario": [("scenario", "string"), ("scenario_name", "string"), ("description", "string"), ("kind", "string"),
                     ("window", "string"), ("pnl_gbp", "double"), ("worst_point_gbp", "double"),
                     ("largest_loss", "string")],
    "fact_position_daily": [("date", "dateTime"), ("instrument_id", "string"), ("quantity", "double"),
                            ("unit_value_gbp", "double"), ("market_value_gbp", "double")],
    "fact_backtest": [("date", "dateTime"), ("method", "string"), ("var95", "double"), ("var99", "double"),
                      ("pnl", "double"), ("exception_99", "int64"), ("exception_95", "int64")],
    "fact_risk": [("date", "dateTime"), ("method", "string"), ("measure", "string"), ("scope", "string"),
                  ("scope_id", "string"), ("value_gbp", "double")],
    "fact_stress": [("date", "dateTime"), ("scenario", "string"), ("kind", "string"), ("desk", "string"),
                    ("pnl_gbp", "double"), ("scenario_name", "string")],
    "fact_var_explain": [("date", "dateTime"), ("prev_date", "dateTime"), ("measure", "string"),
                         ("component", "string"), ("value_gbp", "double"), ("component_label", "string"),
                         ("component_order", "int64")],
    "fact_controls": [("date", "dateTime"), ("control", "string"), ("status", "string"), ("n_affected", "int64"),
                      ("detail", "string"), ("control_label", "string"), ("status_rank", "int64")],
    "fact_gs_var": [("accession", "string"), ("form", "string"), ("filed", "dateTime"), ("period_end", "dateTime"),
                    ("measure", "string"), ("horizon", "string"), ("column", "int64"), ("interest_rates", "double"),
                    ("equity_prices", "double"), ("currency_rates", "double"), ("commodity_prices", "double"),
                    ("diversification", "double"), ("total", "double"), ("identity_gap", "double")],
}
# Display sort orders (column -> column it sorts by) and helper columns hidden from the field list.
SORT_BY = {("fact_var_explain", "component_label"): "component_order", ("fact_controls", "status"): "status_rank",
           ("dim_method", "method_label"): "method_order"}
HIDDEN_COLUMNS = {("fact_var_explain", "component_order"), ("fact_controls", "status_rank"),
                  ("dim_method", "method_order")}

M_TYPES = {"string": "type text", "double": "type number", "int64": "Int64.Type", "dateTime": "type date"}
M2, M1 = "#,0.00", "#,0.0"
SIGNED2, SIGNED1 = "+#,0.00;-#,0.00;0.00", "+#,0.0;-#,0.0;0.0"

METHOD = f'VAR m = SELECTEDVALUE ( dim_method[method], "{PRIMARY}" ) RETURN CALCULATE ( {{x}}, fact_backtest[method] = m )'


def risk_total(measure: str) -> str:
    return (f'CALCULATE ( SUM ( fact_risk[value_gbp] ), fact_risk[method] = "{PRIMARY}", '
            f'fact_risk[measure] = "{measure}", fact_risk[scope] = "total" ) / 1e6')


def explain(component: str) -> str:
    return (f'CALCULATE ( SUM ( fact_var_explain[value_gbp] ), REMOVEFILTERS ( fact_var_explain ), '
            f'fact_var_explain[measure] = "VaR99", fact_var_explain[component] = "{component}" )')


def gs(col_: str) -> str:
    return f'CALCULATE ( SUM ( fact_gs_var[{col_}] ), fact_gs_var[measure] = "average", fact_gs_var[horizon] = "quarter" )'


LATEST = "VAR d = CALCULATE ( MAX ( fact_position_daily[date] ), ALL ( fact_position_daily ) )"
GROSS_AT_D = ("CALCULATE ( SUMX ( fact_position_daily, ABS ( fact_position_daily[market_value_gbp] ) ), "
              "fact_position_daily[date] = d )")

# (table, name, DAX, format string or None, hidden)
MEASURES = [
    # Headline risk, GBP millions
    ("fact_risk", "99% VaR (GBP m)", risk_total("VaR99"), M2, False),
    ("fact_risk", "95% VaR (GBP m)", risk_total("VaR95"), M2, False),
    ("fact_risk", "97.5% ES (GBP m)", risk_total("ES975"), M2, False),
    ("fact_risk", "ES contribution (GBP m)",
     'CALCULATE ( SUM ( fact_risk[value_gbp] ), fact_risk[measure] = "ES975_contribution", '
     'fact_risk[scope] = "desk" ) / 1e6', M2, False),
    # What moved VaR
    ("fact_var_explain", "Change in 99% VaR since previous day (GBP m)",
     f"( {explain('current')} - {explain('previous')} ) / 1e6", SIGNED2, False),
    ("fact_var_explain", "Change by cause (GBP m)",
     'CALCULATE ( SUM ( fact_var_explain[value_gbp] ), fact_var_explain[measure] = "VaR99", '
     'fact_var_explain[component] IN { "positions", "levels", "window" } ) / 1e6', SIGNED2, False),
    # Exposure
    ("fact_position_daily", "Gross exposure (GBP m)", f"{LATEST} RETURN {GROSS_AT_D} / 1e6", M1, False),
    ("fact_position_daily", "Net market value (GBP m)", "SUM ( fact_position_daily[market_value_gbp] ) / 1e6", M1, False),
    ("fact_position_daily", "Market value today (GBP m)",
     f"{LATEST} RETURN CALCULATE ( SUM ( fact_position_daily[market_value_gbp] ), fact_position_daily[date] = d ) / 1e6",
     M2, False),
    ("fact_position_daily", "Share of gross exposure",
     f"{LATEST} RETURN DIVIDE ( {GROSS_AT_D}, CALCULATE ( SUMX ( fact_position_daily, "
     f"ABS ( fact_position_daily[market_value_gbp] ) ), REMOVEFILTERS ( dim_instrument ), REMOVEFILTERS ( dim_desk ), "
     f"fact_position_daily[date] = d ) )", "0.0%", False),
    # Stress
    ("dim_scenario", "Scenario P&L (GBP m)", "SUM ( dim_scenario[pnl_gbp] ) / 1e6", SIGNED1, False),
    ("dim_scenario", "Worst point in window (GBP m)", "SUM ( dim_scenario[worst_point_gbp] ) / 1e6", SIGNED1, False),
    ("dim_scenario", "Worst stress scenario",
     'VAR t = TOPN ( 1, ALL ( dim_scenario ), dim_scenario[pnl_gbp], ASC ) '
     'RETURN MAXX ( t, dim_scenario[scenario_name] & ": " & FORMAT ( dim_scenario[pnl_gbp] / 1e6, "#,0.0" ) & "m" )',
     None, False),
    # Backtest (a selected method, or the primary one when nothing is selected)
    ("fact_backtest", "Exceptions (99%)", METHOD.format(x="SUM ( fact_backtest[exception_99] )"), "0", False),
    ("fact_backtest", "Days tested", METHOD.format(x="COUNTROWS ( fact_backtest )"), "0", False),
    ("fact_backtest", "Expected exceptions", "[Days tested] * 0.01", "0.0", False),
    ("fact_backtest", "Exception rate (99%)", "DIVIDE ( [Exceptions (99%)], [Days tested] )", "0.00%", False),
    ("fact_backtest", "Basel zone, last 250 days",
     f'VAR m = SELECTEDVALUE ( dim_method[method], "{PRIMARY}" ) '
     'VAR t = TOPN ( 250, FILTER ( ALL ( fact_backtest ), fact_backtest[method] = m ), fact_backtest[date], DESC ) '
     'VAR x = SUMX ( t, fact_backtest[exception_99] ) '
     'RETURN SWITCH ( TRUE (), x <= 4, "Green", x <= 9, "Yellow", "Red" ) & " (" & x & " exceptions)"', None, False),
    ("fact_backtest", "Daily P&L (GBP m)",
     f'CALCULATE ( SUM ( fact_backtest[pnl] ), fact_backtest[method] = "{PRIMARY}" ) / 1e6', M2, False),
    ("fact_backtest", "Minus 99% VaR (GBP m)", METHOD.format(x="- SUM ( fact_backtest[var99] )") + " / 1e6", M2, False),
    # Data quality
    ("fact_controls", "Checks passed",
     'CALCULATE ( COUNTROWS ( fact_controls ), fact_controls[status] IN { "PASS", "INFO" } ) + 0', "0", False),
    ("fact_controls", "Warnings", 'CALCULATE ( COUNTROWS ( fact_controls ), fact_controls[status] = "WARN" ) + 0', "0", False),
    ("fact_controls", "Failures", 'CALCULATE ( COUNTROWS ( fact_controls ), fact_controls[status] = "FAIL" ) + 0', "0", False),
    ("fact_controls", "Data checks",
     '[Checks passed] & " passed, " & [Warnings] & " warnings, " & [Failures] & " failed"', None, False),
    # Goldman case study, USD millions as published
    ("fact_gs_var", "Total VaR (USD m)", gs("total"), "0", False),
    ("fact_gs_var", "Interest rates (USD m)", gs("interest_rates"), "0", False),
    ("fact_gs_var", "Equity prices (USD m)", gs("equity_prices"), "0", False),
    ("fact_gs_var", "Currency rates (USD m)", gs("currency_rates"), "0", False),
    ("fact_gs_var", "Commodity prices (USD m)", gs("commodity_prices"), "0", False),
    ("fact_gs_var", "Latest quarter, total (USD m)",
     'VAR d = CALCULATE ( MAX ( fact_gs_var[period_end] ), fact_gs_var[measure] = "average", '
     'fact_gs_var[horizon] = "quarter" ) RETURN CALCULATE ( SUM ( fact_gs_var[total] ), '
     'fact_gs_var[measure] = "average", fact_gs_var[horizon] = "quarter", fact_gs_var[period_end] = d )', "0", False),
    ("fact_gs_var", "Quarters published",
     'CALCULATE ( COUNTROWS ( fact_gs_var ), fact_gs_var[measure] = "average", fact_gs_var[horizon] = "quarter" )',
     "0", False),
]

RELATIONSHIPS = [
    ("fact_position_daily", "instrument_id", "dim_instrument", "instrument_id"),
    ("fact_position_daily", "date", "dim_date", "date"),
    ("fact_backtest", "date", "dim_date", "date"),
    ("fact_backtest", "method", "dim_method", "method"),
    ("dim_instrument", "desk", "dim_desk", "desk"),
]


def partition_m(table: str, cols) -> str:
    types = ", ".join(f'{{"{c}", {M_TYPES[t]}}}' for c, t in cols)
    return "\n".join([
        "let",
        f'    Source = Csv.Document(File.Contents(BiFolder & "{table}.csv"), [Delimiter = ",", Encoding = 65001, '
        f'QuoteStyle = QuoteStyle.Csv]),',
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        '    Blanks = Table.ReplaceValue(Promoted, "", null, Replacer.ReplaceValue, Table.ColumnNames(Promoted)),',
        f'    Typed = Table.TransformColumnTypes(Blanks, {{{types}}}, "en-US")',
        "in",
        "    Typed",
    ])


def table_tmdl(table: str, cols) -> str:
    out = [f"table {table}", f"\tlineageTag: {tag('table', table)}", ""]
    for t, name, dax, fmt, hidden in MEASURES:
        if t != table:
            continue
        out.append(f"\tmeasure {q(name)} = {dax}")
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        if hidden:
            out.append("\t\tisHidden")
        out += [f"\t\tlineageTag: {tag('measure', table, name)}", ""]
    for c, dtype in cols:
        out += [f"\tcolumn {q(c)}", f"\t\tdataType: {dtype}"]
        if dtype == "dateTime":
            out.append("\t\tformatString: d mmm yyyy")
        if (table, c) in HIDDEN_COLUMNS:
            out.append("\t\tisHidden")
        out += [f"\t\tlineageTag: {tag('column', table, c)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {c}"]
        if (table, c) in SORT_BY:
            out.append(f"\t\tsortByColumn: {q(SORT_BY[(table, c)])}")
        out += ["", "\t\tannotation SummarizationSetBy = User", ""]
    out += [f"\tpartition {table} = m", "\t\tmode: import", "\t\tsource ="]
    out += ["\t\t\t\t" + line for line in partition_m(table, cols).splitlines()]
    out += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def write(path: Path, text_: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text_.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))   # UTF-8 without BOM, CRLF


def write_json(path: Path, obj) -> None:
    write(path, json.dumps(obj, indent=2))


def build_model(base: Path) -> None:
    write_json(base / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.1", "settings": {}})
    d = base / "definition"
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1601\n")
    order = json.dumps(["BiFolder"] + list(TABLES))
    write(d / "model.tmdl", "\n".join([
        "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-GB", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
        f"annotation PBI_QueryOrder = {order}", "", "annotation __PBI_TimeIntelligenceEnabled = 0", "",
        *[f"ref table {t}" for t in TABLES], ""]))
    write(d / "expressions.tmdl", "\n".join([
        f'expression BiFolder = "{BI_DIR}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]',
        f"\tlineageTag: {tag('expression', 'BiFolder')}", "", "\tannotation PBI_ResultType = Text", ""]))
    rel = []
    for ft, fc, tt, tc in RELATIONSHIPS:
        rel += [f"relationship {tag('rel', ft, fc, tt, tc)}", f"\tfromColumn: {ft}.{q(fc)}",
                f"\ttoColumn: {tt}.{q(tc)}", ""]
    write(d / "relationships.tmdl", "\n".join(rel))
    for t, cols in TABLES.items():
        write(d / "tables" / f"{t}.tmdl", table_tmdl(t, cols))


# --- Theme -----------------------------------------------------------------------

def solid(c: str) -> dict:
    return {"solid": {"color": c}}


def theme() -> dict:
    axis = {"showAxisTitle": False, "labelColor": solid(INK2), "fontSize": 9}
    legend = {"show": True, "position": "Top", "labelColor": solid(INK2)}
    return {
        "name": "Market risk",
        "dataColors": SERIES,
        "foreground": INK, "background": SURFACE, "tableAccent": SERIES[0],
        "good": "#0CA30C", "neutral": "#FAB219", "bad": "#D03B3B",
        "maximum": SERIES[0], "center": "#F0EFEC", "minimum": SERIES[7],
        "textClasses": {
            "callout": {"fontSize": 24, "fontFace": "Segoe UI Semibold", "color": INK},
            "title": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
            "header": {"fontSize": 11, "fontFace": "Segoe UI Semibold", "color": INK},
            "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": INK2},
        },
        "visualStyles": {
            "*": {"*": {
                "background": [{"show": True, "color": solid(SURFACE), "transparency": 0}],
                "border": [{"show": True, "color": solid(LINE), "radius": 8, "width": 1}],
                "dropShadow": [{"show": False}],
                "title": [{"show": True, "fontSize": 12, "bold": True, "fontColor": solid(INK)}],
                "subTitle": [{"show": True, "fontSize": 10, "fontColor": solid(INK2)}],
            }},
            "page": {"*": {"background": [{"color": solid(PAGE), "transparency": 0}],
                           "outspace": [{"color": solid(PAGE), "transparency": 0}]}},
            "card": {"*": {"title": [{"show": False}], "subTitle": [{"show": False}],
                           "labels": [{"fontSize": 24, "color": solid(INK)}],
                           "categoryLabels": [{"show": True, "fontSize": 10, "color": solid(INK2)}]}},
            "clusteredBarChart": {"*": {"labels": [{"show": True, "fontSize": 9, "color": solid(INK2)}],
                                        "valueAxis": [{"show": False, "gridlineShow": False}],
                                        "categoryAxis": [axis],
                                        "dataPoint": [{"defaultColor": solid(SERIES[0])}]}},
            "lineChart": {"*": {"categoryAxis": [axis], "valueAxis": [{**axis, "gridlineColor": solid(LINE)}],
                                "legend": [legend]}},
            "lineClusteredColumnComboChart": {"*": {"categoryAxis": [axis],
                                                    "valueAxis": [{**axis, "gridlineColor": solid(LINE)}],
                                                    "legend": [legend]}},
            "slicer": {"*": {"selection": [{"singleSelect": True, "selectAllCheckboxEnabled": False}],
                             "header": [{"show": False}]}},
            "tableEx": {"*": {"columnHeaders": [{"bold": True, "backColor": solid(PAGE), "fontColor": solid(INK)}],
                              "values": [{"fontColor": solid(INK)}],
                              "grid": [{"gridVertical": False, "gridHorizontal": True,
                                        "gridHorizontalColor": solid(LINE)}]}},
            "textbox": {"*": {"background": [{"show": False}], "border": [{"show": False}],
                              "title": [{"show": False}], "subTitle": [{"show": False}]}},
        },
    }


# --- Report ------------------------------------------------------------------------

def lit(v: str) -> dict:
    return {"expr": {"Literal": {"Value": v}}}


def text(s: str) -> dict:
    return lit("'" + s.replace("'", "''") + "'")


def col(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def meas(entity: str, prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(field: dict, active: bool = False, display: str | None = None) -> dict:
    kind = next(iter(field))
    entity = field[kind]["Expression"]["SourceRef"]["Entity"]
    prop = field[kind]["Property"]
    p = {"field": field, "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if display:
        p["displayName"] = display
    if active:
        p["active"] = True
    return p


def visual(page: str, key: str, vtype: str, box: tuple, roles: dict, title: str | None = None,
           subtitle: str | None = None, sort: tuple | None = None, objects: dict | None = None) -> tuple[str, dict]:
    name = oid(page, key)
    x, y, w, h = box
    v = {"visualType": vtype}
    if roles:
        v["query"] = {"queryState": {r: {"projections": ps} for r, ps in roles.items()}}
    container = {}
    if title:
        container["title"] = [{"properties": {"show": lit("true"), "text": text(title)}}]
    if subtitle:
        container["subTitle"] = [{"properties": {"show": lit("true"), "text": text(subtitle)}}]
    if container:
        v["visualContainerObjects"] = container
    if objects:
        v["objects"] = objects
    if sort:
        field, direction = sort
        v.setdefault("query", {})["sortDefinition"] = {"sort": [{"field": field, "direction": direction}],
                                                       "isDefaultSort": True}
    v["drillFilterOtherVisuals"] = True
    return name, {"$schema": f"{SCHEMA}/definition/visualContainer/2.0.0/schema.json", "name": name,
                  "position": {"x": x, "y": y, "z": 0, "height": h, "width": w, "tabOrder": 0}, "visual": v}


def card(page, key, box, entity, measure, small: bool = False):
    objs = {"labels": [{"properties": {"fontSize": lit("14D")}}]} if small else None
    return visual(page, key, "card", box, {"Values": [proj(meas(entity, measure))]}, objects=objs)


def textbox(page: str, key: str, box: tuple, runs: list[tuple[str, str, str, str]]):
    """runs: (text, size, font family, colour), one paragraph each."""
    objs = {"general": [{"properties": {"paragraphs": [
        {"textRuns": [{"value": t, "textStyle": {"fontFamily": f, "fontSize": s, "color": c}}]} for t, s, f, c in runs]}}]}
    return visual(page, key, "textbox", box, {}, objects=objs)


def header(page: str, title: str, subtitle: str):
    return textbox(page, "header", (24, 12, 1232, 60),
                   [(title, "18pt", "Segoe UI Semibold", INK), (subtitle, "10pt", "Segoe UI", INK2)])


def footer(page: str, note: str):
    return textbox(page, "footer", (24, 688, 1232, 26), [(note, "8pt", "Segoe UI", MUTED)])


FOOT = ("Synthetic book, not any real firm's. Public market data (Yahoo Finance, Bank of England, FRED). "
        "One-day horizon. GBP m = GBP millions.")


def row(n: int, y: int, h: int, x0: int = 24, width: int = 1232, gap: int = 16) -> list[tuple]:
    w = (width - gap * (n - 1)) / n
    return [(round(x0 + i * (w + gap)), y, round(w), h) for i in range(n)]


def pages() -> list[tuple[str, str, list]]:
    as_of, prev = run_dates()
    out = []

    p = "summary"
    c, b, s = row(4, 84, 104), row(2, 204, 300), row(3, 520, 104)
    out.append((p, "Summary", [
        header(p, "Is today's market risk normal, and can we trust the numbers?",
               f"Daily risk summary for {as_of}. VaR and ES are shown as positive losses; P&L is negative for a loss."),
        card(p, "var99", c[0], "fact_risk", "99% VaR (GBP m)"),
        card(p, "es975", c[1], "fact_risk", "97.5% ES (GBP m)"),
        card(p, "change", c[2], "fact_var_explain", "Change in 99% VaR since previous day (GBP m)"),
        card(p, "gross", c[3], "fact_position_daily", "Gross exposure (GBP m)"),
        visual(p, "explain", "clusteredBarChart", b[0],
               {"Category": [proj(col("fact_var_explain", "component_label"), True, "Cause")],
                "Y": [proj(meas("fact_var_explain", "Change by cause (GBP m)"))]},
               f"What moved 99% VaR since {prev}",
               "The three causes add up exactly to the change (Shapley split). Positive raises risk.",
               sort=(col("fact_var_explain", "component_label"), "Ascending")),
        visual(p, "desks", "clusteredBarChart", b[1],
               {"Category": [proj(col("fact_risk", "scope_id"), True, "Desk")],
                "Y": [proj(meas("fact_risk", "ES contribution (GBP m)"))]},
               "Where the risk sits: contribution to 97.5% ES by desk",
               "Desk contributions add up to total ES. A negative bar is a desk that offsets the rest.",
               sort=(meas("fact_risk", "ES contribution (GBP m)"), "Descending")),
        card(p, "zone", s[0], "fact_backtest", "Basel zone, last 250 days", small=True),
        card(p, "checks", s[1], "fact_controls", "Data checks", small=True),
        card(p, "worst", s[2], "dim_scenario", "Worst stress scenario", small=True),
        footer(p, FOOT),
    ]))

    p = "exposure"
    out.append((p, "Exposure", [
        header(p, "What does the book hold, and how has it changed?",
               f"Market value of each desk since the book opened on 2 Jan 2024, and every position on {as_of}."),
        visual(p, "bydesk", "lineChart", (24, 84, 1232, 300),
               {"Category": [proj(col("dim_date", "date"), True, "Date")],
                "Series": [proj(col("dim_instrument", "desk"), display="Desk")],
                "Y": [proj(meas("fact_position_daily", "Net market value (GBP m)"))]},
               "Net market value by desk (GBP m)",
               "Long positions minus short positions. Changes come from both price moves and trades.",
               sort=(col("dim_date", "date"), "Ascending")),
        visual(p, "positions", "tableEx", (24, 400, 808, 280),
               {"Values": [proj(col("dim_instrument", "name"), display="Position"),
                           proj(col("dim_instrument", "desk"), display="Desk"),
                           proj(meas("fact_position_daily", "Market value today (GBP m)"), display="Market value (GBP m)"),
                           proj(meas("fact_position_daily", "Share of gross exposure"), display="Share of gross")]},
               f"Positions on {as_of}", "Largest first. A negative market value is a short position.",
               sort=(meas("fact_position_daily", "Share of gross exposure"), "Descending")),
        visual(p, "deskgross", "clusteredBarChart", (848, 400, 408, 280),
               {"Category": [proj(col("dim_instrument", "desk"), True, "Desk")],
                "Y": [proj(meas("fact_position_daily", "Gross exposure (GBP m)"))]},
               f"Gross exposure by desk, {as_of} (GBP m)", "Longs plus shorts, ignoring sign.",
               sort=(meas("fact_position_daily", "Gross exposure (GBP m)"), "Descending")),
        footer(p, FOOT),
    ]))

    p = "stress"
    out.append((p, "Stress", [
        header(p, "What would past crises and simple shocks cost today's book?",
               "Today's positions held unchanged and repriced in full under each scenario. Historical windows replay "
               "the actual market moves of that period."),
        visual(p, "bars", "clusteredBarChart", (24, 84, 560, 596),
               {"Category": [proj(col("dim_scenario", "scenario_name"), True, "Scenario")],
                "Y": [proj(meas("dim_scenario", "Scenario P&L (GBP m)"))]},
               "Profit or loss by scenario (GBP m)", "Worst first. Negative is a loss.",
               sort=(meas("dim_scenario", "Scenario P&L (GBP m)"), "Ascending")),
        visual(p, "table", "tableEx", (600, 84, 656, 596),
               {"Values": [proj(col("dim_scenario", "scenario_name"), display="Scenario"),
                           proj(col("dim_scenario", "kind"), display="Type"),
                           proj(col("dim_scenario", "window"), display="Window"),
                           proj(meas("dim_scenario", "Scenario P&L (GBP m)"), display="P&L (GBP m)"),
                           proj(meas("dim_scenario", "Worst point in window (GBP m)"), display="Worst point (GBP m)"),
                           proj(col("dim_scenario", "largest_loss"), display="Largest losses")]},
               "Scenario detail",
               "Worst point is the deepest loss on any day inside a historical window. Hypothetical shocks are instant.",
               sort=(meas("dim_scenario", "Scenario P&L (GBP m)"), "Ascending")),
        footer(p, FOOT + " Hypothetical shocks are round numbers chosen for this project, not regulatory scenarios."),
    ]))

    p = "backtest"
    c = row(4, 84, 104, x0=416, width=840)
    out.append((p, "Backtest", [
        header(p, "Has the VaR model been right?",
               "Each day's loss is compared with the 99% VaR forecast made the evening before. A correct model is "
               "exceeded on about 1 day in 100."),
        visual(p, "method", "slicer", (24, 84, 376, 104),
               {"Values": [proj(col("dim_method", "method_label"), True, "Method")]},
               "VaR method", "Pick one. With none picked, the primary method is shown.",
               sort=(col("dim_method", "method_label"), "Ascending")),
        card(p, "exc", c[0], "fact_backtest", "Exceptions (99%)"),
        card(p, "expected", c[1], "fact_backtest", "Expected exceptions"),
        card(p, "rate", c[2], "fact_backtest", "Exception rate (99%)"),
        card(p, "zone", c[3], "fact_backtest", "Basel zone, last 250 days", small=True),
        visual(p, "chart", "lineClusteredColumnComboChart", (24, 204, 1232, 476),
               {"Category": [proj(col("dim_date", "date"), True, "Date")],
                "Y": [proj(meas("fact_backtest", "Daily P&L (GBP m)"))],
                "Y2": [proj(meas("fact_backtest", "Minus 99% VaR (GBP m)"))]},
               "Daily P&L against the 99% VaR forecast (GBP m)",
               "Columns are each day's P&L from yesterday's positions. A column below the line is an exception.",
               sort=(col("dim_date", "date"), "Ascending")),
        footer(p, FOOT + " Basel traffic light: green 0-4, yellow 5-9, red 10 or more exceptions in 250 days (BCBS, 1996)."),
    ]))

    p = "data"
    c = row(3, 84, 104)
    out.append((p, "Data quality", [
        header(p, "Can the inputs be trusted?",
               f"Automatic checks on the market data, positions and run for {as_of}. Problems are listed first."),
        card(p, "ok", c[0], "fact_controls", "Checks passed"),
        card(p, "warn", c[1], "fact_controls", "Warnings"),
        card(p, "fail", c[2], "fact_controls", "Failures"),
        visual(p, "table", "tableEx", (24, 204, 1232, 476),
               {"Values": [proj(col("fact_controls", "control_label"), display="Check"),
                           proj(col("fact_controls", "status"), display="Status"),
                           proj(col("fact_controls", "detail"), display="Detail")]},
               "Every check", "WARN: usable, but review. FAIL: do not rely on the figures. INFO: noted, no action needed.",
               sort=(col("fact_controls", "status"), "Ascending")),
        footer(p, FOOT),
    ]))

    p = "goldman"
    c = row(2, 84, 104, width=600)
    out.append((p, "Goldman case study", [
        header(p, "What does Goldman Sachs' own published VaR show?",
               "Average daily 95% one-day VaR per quarter, from Goldman's 10-Q and 10-K filings with the SEC, in USD "
               "millions as published. Q4 2010 to Q4 2015 were not published as quarters."),
        card(p, "latest", c[0], "fact_gs_var", "Latest quarter, total (USD m)"),
        card(p, "quarters", c[1], "fact_gs_var", "Quarters published"),
        visual(p, "total", "lineChart", (24, 204, 600, 476),
               {"Category": [proj(col("fact_gs_var", "period_end"), True, "Quarter")],
                "Y": [proj(meas("fact_gs_var", "Total VaR (USD m)"))]},
               "Total VaR (USD m)", "After diversification between the four risk categories.",
               sort=(col("fact_gs_var", "period_end"), "Ascending")),
        visual(p, "cats", "lineChart", (640, 204, 616, 476),
               {"Category": [proj(col("fact_gs_var", "period_end"), True, "Quarter")],
                "Y": [proj(meas("fact_gs_var", m)) for m in ("Interest rates (USD m)", "Equity prices (USD m)",
                                                            "Currency rates (USD m)", "Commodity prices (USD m)")]},
               "By risk category (USD m)", "Interest rates are the largest category in every quarter.",
               sort=(col("fact_gs_var", "period_end"), "Ascending")),
        footer(p, "Source: Goldman Sachs 10-Q and 10-K filings, 2010 to 2026, parsed and checked by this project. "
                  "See reports/sample/casestudy.md for what these figures can and cannot show."),
    ]))
    return out


def build_report(base: Path, model_folder: str) -> None:
    write_json(base / "definition.pbir", {
        "$schema": f"{SCHEMA}/definitionProperties/2.0.0/schema.json", "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{model_folder}"}}})
    d = base / "definition"
    write_json(d / "version.json", {"$schema": f"{SCHEMA}/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    write_json(d / "report.json", {
        "$schema": f"{SCHEMA}/definition/report/1.3.0/schema.json",
        "layoutOptimization": "None",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61", "type": "SharedResources"},
            "customTheme": {"name": THEME_NAME, "reportVersionAtImport": "5.61", "type": "RegisteredResources"}},
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": THEME_NAME, "path": THEME_NAME, "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarizedAndUnderlying",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True, "useEnhancedTooltips": True}})
    shared = base / "StaticResources" / "SharedResources" / "BaseThemes" / "CY24SU10.json"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(THEME_SRC, shared)
    write_json(base / "StaticResources" / "RegisteredResources" / THEME_NAME, theme())

    order = []
    for key, display, visuals in pages():
        name = oid("page", key)
        order.append(name)
        pd_ = d / "pages" / name
        write_json(pd_ / "page.json", {"$schema": f"{SCHEMA}/definition/page/1.4.0/schema.json", "name": name,
                                       "displayName": display, "displayOption": "FitToPage", "height": 720, "width": 1280})
        for i, (vname, vjson) in enumerate(visuals):
            vjson["position"]["z"] = vjson["position"]["tabOrder"] = i * 1000
            write_json(pd_ / "visuals" / vname / "visual.json", vjson)
    write_json(d / "pages" / "pages.json", {"$schema": f"{SCHEMA}/definition/pagesMetadata/1.0.0/schema.json",
                                            "pageOrder": order, "activePageName": order[0]})


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    model_folder, report_folder = f"{NAME}.SemanticModel", f"{NAME}.Report"
    build_model(OUT / model_folder)
    build_report(OUT / report_folder, model_folder)
    write_json(OUT / f"{NAME}.pbip",
               {"$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
                "version": "1.0", "artifacts": [{"report": {"path": report_folder}}],
                "settings": {"enableAutoRecovery": True}})
    write(OUT / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    n = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"Wrote {n} files to {OUT}")


if __name__ == "__main__":
    main()
