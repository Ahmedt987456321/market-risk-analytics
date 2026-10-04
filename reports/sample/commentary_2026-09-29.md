# Daily market risk summary, 29 September 2026

Synthetic book, public market data. One-day horizon, GBP.

## Headline

99% VaR is GBP 3.00m and 97.5% Expected Shortfall is GBP 3.40m. VaR is down GBP 0.07m (-2.4%) from 28 Sep. Gross exposure is GBP 229.2m.

## What changed

The largest effect was market moves (-GBP 0.06m); trades -GBP 0.01m, the scenario window rolling +GBP 0.00m. The 99% VaR is set by the scenario from 09 Jun 2026, which replaced 12 Feb 2026. Trades: USD -GBP 0.86m, BARC -GBP 0.40m.

## Where the risk sits

UK Equities contributes 62% of Expected Shortfall and US Equities 32%. FX reduces it (-1%). The largest single position is HSBC Holdings at GBP 26.84m, 11.7% of gross exposure (it started at GBP 12.00m on 02 Jan 2024).

## Stress

The worst historical scenario is COVID crash, 2020: -GBP 31.73m over the window (worst point -GBP 34.80m), 10.6 times today's 99% VaR. Barclays is the largest single loss in every historical scenario. The worst hypothetical shock is all equities -15%: -GBP 16.71m.

## Model health

Since 03 Jan 2024, losses exceeded the primary model's 99% VaR on 5 of 694 days (expected 6.9; Kupiec p = 0.44). In the last 250 days: 2, Basel green zone.

## Data

Controls status: WARN. 3 of 15 controls raised an issue; details are listed below.

## Attention

- Concentration: HSBC Holdings is 11.7% of gross exposure (GBP 26.84m), above the 10% review level.
- Backtest: parametric is in the Basel yellow zone (5 exceptions in the last 250 days).
- Backtest: exceptions of the primary model cluster in time (Christoffersen p = 0.0002), so it reacts slowly when volatility jumps.
- Data (WARN): stale prices: GILT_5Y (2d, last 2026-09-25), GILT_10Y (2d, last 2026-09-25), GILT_20Y (2d, last 2026-09-25), UST_2Y (1d, last 2026-09-28), UST_10Y (1d, last 2026-09-28), USD (1d, last 2026-09-28) (+2 more); carried forward
- Data (WARN): interior gaps: GOLD@2026-09-28 (100% of peers priced); carried forward
- Data (WARN): futures roll: BRENT@2026-09-29: future -9.13% vs BNO -2.62%; likely contract roll, risk uses proxy returns
