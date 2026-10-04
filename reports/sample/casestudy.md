# Case study: what public data can and cannot say about Goldman Sachs' market risk

Goldman Sachs publishes its average daily VaR every quarter, split into four risk categories. This study asks how much of the change in those numbers can be explained by observable market volatility, what is left over, and what cannot be said at all. It uses public filings and public market data only. It is not an estimate of Goldman's positions.

## Data and checks

- **Source:** 67 10-Q and 10-K filings, 2010-03-01 to 2026-08-03, from SEC EDGAR. Each file is saved and hashed (`exports/bi/gs_filings_lineage.csv`).
- **Confidence level:** 67 of 67 filings state a one-day horizon at 95% confidence.
- **Not machine-readable:** the VaR tables have no XBRL tags, so they are read from the filing HTML (`riskengine/gs_var.py`).
- **Parsed:** 144 tables, including 60 quarterly averages from Mar 2010 to Jun 2026.
- **Arithmetic check:** in every table, the four categories plus the diversification effect equal the total (largest gap: 0).
- **Consistency check:** each 10-Q reprints an earlier quarter. 37 reprints match the original filing exactly and 0 do not. 13 have nothing to compare with (mostly Q1 filings, whose second column is the previous Q4, published nowhere else).
- **Gaps:** 2010Q4, 2011Q4, 2012Q4, 2013Q4, 2014Q4, 2015Q4. For those years only the annual average was published; from 2016 the Q4 average appears as the second column of the next Q1 10-Q.

## Reported VaR, 2010 to 2026

95% one-day VaR, mean of each year's available quarterly averages, USD millions:

| Year | Interest rates | Equity prices | Currency rates | Commodity prices | Diversification | Total |
|---|---:|---:|---:|---:|---:|---:|
| 2010 | 95 | 69 | 31 | 37 | -92 | 139 |
| 2011 | 84 | 36 | 20 | 34 | -69 | 105 |
| 2012 | 82 | 24 | 14 | 23 | -54 | 89 |
| 2013 | 63 | 30 | 18 | 19 | -50 | 80 |
| 2014 | 54 | 27 | 18 | 21 | -45 | 75 |
| 2015 | 48 | 26 | 29 | 21 | -48 | 77 |
| 2016 | 45 | 25 | 21 | 18 | -46 | 63 |
| 2017 | 40 | 24 | 12 | 13 | -37 | 54 |
| 2018 | 46 | 31 | 14 | 11 | -42 | 60 |
| 2019 | 46 | 27 | 11 | 12 | -40 | 56 |
| 2020 | 72 | 55 | 23 | 20 | -76 | 94 |
| 2021 | 60 | 43 | 13 | 24 | -55 | 86 |
| 2022 | 96 | 33 | 31 | 48 | -95 | 114 |
| 2023 | 96 | 29 | 24 | 19 | -68 | 99 |
| 2024 | 81 | 38 | 26 | 18 | -72 | 92 |
| 2025 | 70 | 47 | 24 | 17 | -68 | 90 |
| 2026 | 84 | 60 | 17 | 30 | -75 | 116 |

Total VaR was lowest in 2017 (54m) and highest in 2010 (139m). Interest rates are the largest category in 60 of 60 quarters.

## How much of the change is market volatility?

For each category, one public benchmark stands in for the market Goldman is exposed to:

- Interest rates: US 10y Treasury yield (FRED DGS10), daily change
- Equity prices: S&P 500 (Yahoo ^GSPC), daily log return
- Currency rates: Nominal broad US dollar index (FRED DTWEXBGS), daily log return
- Commodity prices: Brent spot (FRED DCOILBRENTEU), daily log return

Volatility is the trailing standard deviation of daily moves over 1, 2 or 5 years, averaged over each quarter. The table shows the correlation between quarter-on-quarter changes in log VaR and in log volatility (consecutive quarters only), with a 95% confidence interval.

| Category | 1y | 2y | 5y | Quarters |
|---|---:|---:|---:|---:|
| Interest rates | 0.37 (0.11 to 0.58) | 0.21 (-0.07 to 0.45) | 0.35 (0.09 to 0.57) | 53 |
| Equity prices | 0.35 (0.09 to 0.57) | 0.26 (-0.01 to 0.49) | 0.43 (0.18 to 0.63) | 53 |
| Currency rates | 0.15 (-0.12 to 0.41) | 0.07 (-0.20 to 0.33) | 0.01 (-0.27 to 0.28) | 53 |
| Commodity prices | 0.35 (0.09 to 0.57) | 0.20 (-0.08 to 0.45) | 0.34 (0.08 to 0.56) | 53 |

**Reading.** The best fit (equity prices, 5y window, r = 0.43) explains about 18% of the quarter-on-quarter variation (the correlation squared). Most of each change is something else: position changes, model changes, the benchmark being a poor stand-in for Goldman's actual exposures, or a mix. For every category the three windows' intervals overlap, so the data cannot say which lookback Goldman's model effectively behaves like. Goldman's UK Pillar 3 says it weights five years of history towards recent data, but does not give the weights.

## Episodes

Change in each category's quarterly average VaR, split into the change in benchmark volatility and a residual (VaR change after removing the volatility change). The residual is shown for the 1-year window, with the range across all three windows in brackets. A wide range means the split is not robust.

**COVID crash**: Dec 2019 quarter to Mar 2020 quarter

| Category | VaR (USD m) | VaR change | Vol change (1y) | Residual (1y) [range across windows] |
|---|---|---:|---:|---|
| Interest rates | 49 to 60 | +22% | +11% | +10% [+10% to +22%] |
| Equity prices | 24 to 41 | +71% | +8% | +58% [+57% to +61%] |
| Currency rates | 11 to 18 | +64% | -2% | +67% [+67% to +68%] |
| Commodity prices | 12 to 11 | -8% | +7% | -14% [-17% to -9%] |

**2022 rate rises**: Dec 2021 quarter to Jun 2022 quarter

| Category | VaR (USD m) | VaR change | Vol change (1y) | Residual (1y) [range across windows] |
|---|---|---:|---:|---|
| Interest rates | 58 to 104 | +79% | +35% | +33% [+33% to +88%] |
| Equity prices | 34 to 36 | +6% | +37% | -23% [-23% to +64%] |
| Currency rates | 15 to 23 | +53% | +9% | +41% [+41% to +75%] |
| Commodity prices | 32 to 63 | +97% | +35% | +46% [+46% to +317%] |

**April 2025 tariff sell-off**: Mar 2025 quarter to Jun 2025 quarter

| Category | VaR (USD m) | VaR change | Vol change (1y) | Residual (1y) [range across windows] |
|---|---|---:|---:|---|
| Interest rates | 70 to 79 | +13% | +1% | +12% [+12% to +16%] |
| Equity prices | 42 to 48 | +14% | +47% | -22% [-22% to +20%] |
| Currency rates | 36 to 23 | -36% | +16% | -45% [-45% to -35%] |
| Commodity prices | 15 to 15 | +0% | +9% | -8% [-8% to +45%] |

**Latest quarter**: Mar 2026 quarter to Jun 2026 quarter

| Category | VaR (USD m) | VaR change | Vol change (1y) | Residual (1y) [range across windows] |
|---|---|---:|---:|---|
| Interest rates | 85 to 82 | -4% | -11% | +8% [-4% to +8%] |
| Equity prices | 55 to 65 | +18% | -32% | +74% [+15% to +74%] |
| Currency rates | 15 to 19 | +27% | -10% | +40% [+23% to +40%] |
| Commodity prices | 31 to 30 | -3% | +46% | -34% [-34% to -12%] |

In the COVID quarter, equity VaR rose 71% while equal-weighted trailing equity volatility rose between 6% and 9%, depending on the window. A model that weights recent days more reacts faster than any of these benchmarks, which fits Goldman's description of its own method; so would larger equity positions. The public numbers cannot tell these apart.

The 2022 commodity row shows how fragile the split is: with the 2-year window, benchmark volatility *fell* 53%, because the extreme Brent moves of spring 2020 left the window, so the residual looks huge. Benchmarks have window cliffs of their own.

## Diversification

The diversification effect cancelled between 32% and 51% of the sum of the four category VaRs. One might expect less cancellation when stocks and bonds move together. Against the trailing one-year correlation of S&P 500 returns with Treasury returns, the relationship is weak (r = 0.27 over 60 quarters). Diversification depends on how Goldman's positions offset each other, which one market correlation cannot capture.

## Why the UK disclosure cannot be compared directly

The UK Pillar 3 reports an average 99% 10-day regulatory VaR of 216m for H2 2025 (Goldman Sachs Group UK Limited, Q4 2025, Table 16). The group's 95% one-day VaR averaged 85.5m over the same two quarters. Rescaling as if returns were normal (x 1.414 from 95% to 99%, x sqrt(10) for 10 days, x 4.47 in total) gives 382m. The two differ for reasons the documents name but do not quantify: a different legal entity (the UK group, not the whole firm), a different scope (regulatory covered positions), a scaler for an effective observation period of at least one year, and fat tails that break the normal rescaling. Their ratio is not meaningful, and this study does not report one.

## What can and cannot be inferred

**Can:**
- The level and mix of Goldman's reported VaR over 16 years, from its own filings, every figure checked arithmetically and against reprints.
- That market volatility, measured by public benchmarks, explains only a small share of quarter-to-quarter changes.
- That in a sharp sell-off Goldman's reported VaR moved much faster than equal-weighted trailing volatility.

**Cannot:**
- Goldman's positions, or how they changed: the residual mixes position changes, model changes and benchmark error.
- Which lookback or weighting Goldman's model effectively uses.
- A like-for-like comparison with the UK regulatory figures.
- Anything within a quarter: only quarterly averages and quarter-end values are published.

## Method notes

- Quarter ends are inferred from filing dates (each filing covers the last quarter end before it).
- Benchmarks are US-centred; Goldman's exposures are global.
- Correlations use changes between consecutive quarters only, so missing Q4s do not create two-quarter jumps.
- Figures typed by hand from the Pillar 3 PDF are kept, with their source, in `config/casestudy.yaml`.
