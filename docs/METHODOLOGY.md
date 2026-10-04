# Methodology and results

Detail behind the README: the data controls, the risk results, stress tests, the daily VaR explain, and how every number is checked independently. Design choices are argued in [DECISIONS.md](../DECISIONS.md); real problems found along the way are in [INCIDENTS.md](../INCIDENTS.md).

## Data flow

```
Yahoo Finance --\
Bank of England --+--> raw files (saved as received, SHA-256 hashed)
FRED -----------/          |
                           v
                normalise (pence -> GBP, map tickers) --> DuckDB
                           |
          synthetic book: seeded trades -> daily positions
                           |
                           v
          15 controls -> controls report (reports/<date>/controls.md)
                           |
                           v
          factor returns (dividends, roll-free commodity proxies)
                           |
          VaR / ES: historical simulation (equal and age-weighted), parametric
          daily backtest since Jan 2024, stress tests, VaR explain
                           -> risk report (reports/<date>/risk.md)
                           |
          commentary.md, dashboard.html, exports/bi/*.csv (star schema)
```

Every market data row points to a `fetch_id`. That points to a lineage record:
the source, the exact request, when it was fetched, the raw file, and a hash of
its bytes. Every run is recorded with its code version and a hash of its config.

## Controls

| Control | Fails or warns when |
|---|---|
| Duplicate source rows | the same instrument and date appears twice in raw data |
| Invalid values | a price is zero or negative, or a yield is outside a sane range |
| Missing prices | an instrument has no price for more than 3 business days |
| Stale prices | an instrument's latest price is 1 to 3 business days old (carried forward) |
| Gaps inside series | a day is missing in the middle of a series while its peers have a price |
| Unusual moves | today's move is beyond 6 sigma of its own EWMA volatility (catches data errors) |
| Cross-source check | BoE FX disagrees with Yahoo FX by more than 0.75% |
| Futures roll check | a future and its roll-free fund differ by more than 2 points in a day |
| Dividend adjustment check | our dividend-adjusted US returns disagree with Yahoo's by more than 1bp |
| Historical revisions | a source changed history older than 5 business days |
| History for VaR | an instrument has fewer than 750 observations in 5 years |
| Position reconciliation | positions(T) is not equal to positions(T-1) + trades(T) |
| Positions priced | a held instrument has no usable price |
| Data lineage recorded | any market data row cannot be traced to a raw file |
| Reporting SLA | the report for T finished after 07:30 London on T+1 |

Each control is tested against a fault injected on purpose, for example a
pence/pound glitch, a trade the position system booked but trade capture never
saw, or a manual edit with no lineage. See `[tests/test_controls.py](../tests/test_controls.py)`.

## Risk

Figures for 29 Sep 2026 (one day, GBP, 1,260 scenarios from Oct 2021):

| Method | VaR 95% | VaR 99% | ES 97.5% | 99% exceptions since Jan 2024 (expected 6.9) | Last 250 days |
|---|---:|---:|---:|---:|---|
| Historical simulation, equal weights (primary) | 1.66m | 3.00m | 3.40m | 5 | 2, green |
| Historical simulation, age-weighted | 1.39m | 3.00m | 2.94m | 10 | 4, green |
| Parametric (delta-normal) | 1.28m | 1.81m | 1.82m | 14 | 5, yellow |

What this shows:
- The parametric model assumes normal returns and gets twice the expected number of
  99% exceptions (Kupiec rejects it, p = 0.02). Fat tails matter.
- The primary model has the right number of exceptions, but three fall on
  consecutive days (3, 4 and 7 Apr 2025), so the independence test rejects it
  (Christoffersen p < 0.01). A five-year equal-weighted window reacts slowly when
  volatility jumps. This is the known weakness of the method, and it is visible here.
- The primary method was fixed before the backtest ran ([DECISIONS](../DECISIONS.md) 012), so these
  results judge it rather than select it.
- **Why the exceptions cluster.** Between 20 Feb and 3 Apr 2025 the primary model's
  VaR fell 47% (4.32m to 2.29m) as the COVID crash days of Feb to Apr 2020 aged out of
  the five-year window, one a day. The April 2025 sell-off then breached it three days
  running. This is the known "cliff effect" of fixed-window historical simulation,
  traced here scenario by scenario ([DECISIONS](../DECISIONS.md) 016).

## Stress tests (29 Sep 2026 book)

| Scenario | P&L | Largest single loss |
|---|---:|---|
| Lehman 2008 (12 Sep to 10 Oct 2008) | -27.70m | Barclays -8.65m |
| COVID 2020 (19 Feb to 23 Mar 2020) | -31.73m (worst point -34.80m, 16 Mar) | Barclays -11.04m |
| UK gilt crisis 2022 (22 to 27 Sep 2022) | -0.43m (worst point -1.21m, 26 Sep) | Barclays -1.33m |
| All equities -15% | -16.71m | HSBC -4.03m |
| All yields +100bp | +0.70m | 5y gilt -1.17m |
| Sterling -10% | +5.69m | 10y Treasury (short) -1.15m |
| UK crisis (gilts +150bp, sterling -8%, UK equities -10%) | -3.71m | HSBC -2.68m |

Each historical window is anchored to a dated, sourced event ([DECISIONS](../DECISIONS.md) 014). The
book is equity-heavy, so equity crashes dominate. Sterling falling helps it, because
it holds USD and foreign assets. Barclays is the largest loss in every historical
scenario because it fell hardest, though it is only the third-largest position;
HSBC is the largest, at 11.7% of gross exposure ([DECISIONS](../DECISIONS.md) 003 update).

## What moved VaR

Every run splits the day-over-day change in VaR and ES into trades, market moves
and the scenario window rolling forward, using a Shapley split so the parts add up
exactly ([DECISIONS](../DECISIONS.md) 015). On 29 Sep 2026: 99% VaR went from 3.07m to 3.00m, with
-0.01m from trades, -0.06m from the market and 0.00m from the window roll.

## How the numbers are checked

`tools/verify_var.py` recomputes today's VaR and ES
with hand-written P&L formulas (not the engine's pricing code) and matches the
engine to the pound. It also rebuilds every day's backtest P&L as a plain
mark-to-market plus dividends: all 694 days agree. `tools/verify_stress.py`
recomputes every stress scenario from endpoint prices: hypothetical and gilt
scenarios agree to the pound; the 2008 and 2020 gaps (1.5k and 54k GBP) are fully
explained by dividend reinvestment inside the window.
