# BI guide: the Power BI report

Every pipeline run writes a star schema to `exports/bi/` as plain CSV. The Power BI
report in `powerbi/` is generated from code (`tools/build_powerbi.py`) as a Power BI
Project (PBIP): a TMDL semantic model plus a PBIR report, both plain text.

## Status

- **Checked:**
  - every JSON file, including the custom theme, passes Microsoft's published schemas
    (`tools/validate_powerbi.py`, 53 of 53)
  - the TMDL model parses with the serializer inside Power BI Desktop (`tools/validate_tmdl.ps1`:
    12 tables, 31 measures, 5 relationships)
  - every visual type and data role appears in Power BI Desktop's own code
  - every field a visual uses exists in the model (`tests/test_powerbi.py`)
  - **opened in Power BI Desktop 2.158 on 4 Oct 2026**: the data loaded, and every number the report
    shows was read back from Desktop's engine (`tools/verify_powerbi.ps1`) and matched an independent
    pandas calculation (`tools/verify_powerbi_expected.py`)
- **Not yet checked by eye:** layout and readability of each page on screen.

## Open it

1. Run the pipeline so `exports/bi/` is current, then `python tools/build_powerbi.py`.
2. Double-click `powerbi/MarketRisk.pbip`. If a page is empty, click **Home > Refresh**.
3. If the CSVs are somewhere other than `C:\dev\market-risk-engine\exportsi\`, change the
   `BiFolder` parameter (Home > Transform data > Edit parameters) and refresh.

## Design

One question per page, stated in its header; plain names, never internal codes; money in GBP
millions; a subtitle on every chart saying how to read it; bars sorted, with value labels; one
theme (colour-blind-checked palette, white cards on a light grey page).

| Page | Question | What is on it |
|---|---|---|
| Summary | Is today's risk normal, and can we trust the numbers? | VaR, ES, change since yesterday, gross exposure; what moved VaR; ES by desk; Basel zone, data checks, worst stress |
| Exposure | What does the book hold, and how has it changed? | net market value by desk over time; every position today with its share; gross exposure by desk |
| Stress | What would past crises and simple shocks cost today's book? | P&L by scenario, worst first; scenario detail table |
| Backtest | Has the VaR model been right? | method picker (single choice; none = primary); exceptions, expected, rate, Basel zone; daily P&L against minus VaR |
| Data quality | Can the inputs be trusted? | passed, warnings, failures; every check, problems first |
| Goldman case study | What does Goldman's own published VaR show? | latest quarter, quarters published; total VaR; VaR by risk category |

## Values to expect (29 Sep 2026 run)

| Where | Value |
|---|---|
| Summary cards | 99% VaR 3.00, 97.5% ES 3.40, change -0.07, gross exposure 229.2 (GBP m) |
| Summary, lower cards | Green (2 exceptions); 12 passed, 3 warnings, 0 failed; COVID 2020: -31.7m |
| Backtest, by method | primary 5 exceptions (Green); age-weighted 10 (Green); parametric 14 (Yellow) |
| Goldman case study | latest quarter 120 (USD m), 60 quarters |

## The model

| Table | Grain | Rows (29 Sep 2026) | Key columns |
|---|---|---:|---|
| `dim_instrument` | one per instrument | 24 | `instrument_id`, `desk`, `asset_class`, `currency` |
| `dim_desk` | one per desk | 5 | `desk` |
| `dim_date` | one per business day from the book start | 716 | `date`, `year`, `quarter`, `month` |
| `fact_position_daily` | instrument x day | 17,184 | `date`, `instrument_id`, `quantity`, `market_value_gbp` |
| `fact_backtest` | method x day | 2,082 | `date`, `method`, `var95`, `var99`, `pnl`, `exception_99` |
| `fact_risk` | today's measures | 19 | `method`, `measure`, `scope`, `scope_id`, `value_gbp` |
| `fact_stress` | scenario x desk (plus a `total` row) | 42 | `scenario`, `kind`, `desk`, `pnl_gbp` |
| `fact_var_explain` | today's explain | 10 | `measure`, `component`, `value_gbp` |
| `fact_controls` | today's controls | 15 | `control`, `status`, `detail` |
| `fact_gs_var` | Goldman VaR table x filing (case study) | 144 | `period_end`, `measure`, `horizon`, one column per category |
| `fact_benchmark_vol` | window x quarter (case study) | varies | `window`, `period_end`, one column per category |

Relationships (Model view, all one-to-many, single direction):

- `dim_instrument[instrument_id]` -> `fact_position_daily[instrument_id]`
- `dim_date[date]` -> `fact_position_daily[date]`
- `dim_date[date]` -> `fact_backtest[date]`
- `dim_desk[desk]` -> `dim_instrument[desk]`

Leave `fact_stress` unrelated to `dim_desk`: its `desk` column also holds a `total`
row per scenario, which has no match in `dim_desk`. Filter `desk <> "total"` in
visuals that show desks.

## Build it by hand (practice)

To learn the tool, rebuild the same report from the CSVs: load each file with Get data >
Text/CSV, create the relationships above, then add the measures from
`powerbi/MarketRisk.SemanticModel/definition/tables/*.tmdl` (each `measure` line is the DAX).

## Refreshing

Rerun the pipeline, then Home > Refresh in Power BI. The CSV names and columns stay
the same from run to run, so the report keeps working. If a column is ever renamed,
this guide and `riskengine/bi_export.py` change together.
