# Decision log

Each entry records a choice made with incomplete information: what was
considered, what was chosen, and what it costs. Newest last.

---

## 001 - Market data sources

**Context.** Needed free daily history (back to 2007, to cover the 2008 crisis)
for UK and US equities, gilts, Treasuries, FX and commodities, with no API keys
so anyone can clone and run the project.

**Options.** Stooq CSV endpoints; Yahoo Finance via `yfinance`; Bank of England
Statistical Database; FRED; paid vendors.

**Decision.** Yahoo for equities and commodity futures, Bank of England for the
gilt zero-coupon curve and GBP FX, FRED for US Treasuries. Stooq was the
original plan, but it now serves a JavaScript bot check to scripts (see
INCIDENTS 001). The check is there on purpose, so it is not worked around.

**Cost.** `yfinance` is an unofficial interface and can break without notice.
Mitigations: every raw response is saved and hashed, runs can be replayed
offline, and rates and FX come from the publishers themselves (BoE, FRED), not from Yahoo.

## 002 - DuckDB as the store

**Context.** Needed SQL for ETL and analysis without running a database server.

**Options.** SQLite, Postgres, DuckDB, plain Parquet files.

**Decision.** DuckDB: a single file, columnar, fast for analytical queries
(window functions, `QUALIFY`), and reads pandas frames directly.

**Cost.** Single writer. Fine for a batch pipeline, wrong for a multi-user service.

## 003 - A synthetic book, clearly labelled

**Context.** No access to real positions.

**Decision.** A 24-position book across five desks, sized by target GBP market
value on 2 Jan 2024, with seeded random daily trades. Random draws are generated
in date order from one array, so moving the run date forward never changes past
trades (tested).

**Cost.** Trades are random, not driven by any strategy, so "why did the
position change" has no business answer. The engine does not depend on that;
the VaR explain step (stage 3) only needs to know *that* positions changed.

## 004 - Rates positions as zero-coupon bonds on curve points

**Decision.** Each rates position is a zero-coupon bond at 5y/10y/20y (gilts) or
2y/10y (Treasuries), valued `exp(-y * T)`. Transparent, and matches the published
curve points exactly.

**Cost.** Real bonds have coupons and sit between curve points. The Treasury
series are par yields (FRED: "Market Yield on U.S. Treasury Securities at
N-Year Constant Maturity") used as if they were zero rates. The size of that
error has not been measured yet; it is listed as a known limitation.

## 005 - FX: Bank of England as primary, Yahoo as the check

**Decision.** Primary FX is the BoE daily spot rate (foreign currency per GBP).
Yahoo's GBPUSD / GBPEUR / GBPJPY closes are loaded as a second source, used only
by the cross-source control.

**Why the tolerance is 0.75%.** The two sources are not the same observation.
The BoE says its daily spot rates are "prepared using multiple sources" and are
statistical data, not official rates; it does not state an observation time.
Yahoo does not document when its daily FX close is taken. So some gap is
expected, and the tolerance is a judgement, to be revisited once there is a
longer record of observed gaps. Observed gaps on 28 Sep 2026: EUR 0.31%,
JPY 0.22%, USD 0.16%.

## 006 - What Yahoo's "close" and "adjusted close" actually are (verified)

**Checked 29 Sep 2026, against company filings with the SEC.**

- **`close` is split-adjusted, not the price that traded.** Apple's 10-K records a
  four-for-one split effective 28 Aug 2020; NVIDIA's 10-Q (quarter to 28 Jul 2024)
  records a ten-for-one split in June 2024. Yahoo's close shows no 4x or 10x jump
  at either (largest NVIDIA day-to-day ratio in June 2024: 1.068). So all history
  is restated in today's share count. For this project that is what we want: the
  book is sized in today's shares.
- **US adjusted close is dividend-adjusted correctly.** For JPM, the dividend implied
  by each step in Yahoo's adjustment matches the dividend per share JPMorgan filed
  with the SEC (XBRL `CommonStockDividendsPerShareDeclared`) to 4 decimal places,
  on all 8 ex-dates that could be compared (2024 to 2026).
- **LSE adjusted close is wrong by a factor of 100.** See INCIDENTS 005. For all 10
  UK stocks, over every dividend since 2007, Yahoo's adjustment reflects a dividend
  about 100 times smaller than the dividend Yahoo itself lists. The listed dividend
  is the right one: AstraZeneca's SEC-filed dividends (USD 2.10 and 1.03 in 2025)
  convert at BoE rates to 166.1p and 76.8p, against Yahoo's 168.0p and 76.7p.

**Decision.** Value positions at `close`. Do not use Yahoo's `adj_close` for UK
stocks. Stage 2 will store dividends as their own dataset (with lineage) and
build the total-return adjustment itself, with a control that compares its own
adjustment with Yahoo's for US stocks, where Yahoo's is known to be right.

## 007 - Controls flag, they never fix

**Decision.** No control changes data. Stale prices are carried forward (up to 5
business days) *and* flagged. Anything longer is left missing and fails the run.
Fixing data is a human decision, and should be logged as one.

## 008 - Outlier threshold: 6 EWMA sigma

**Context.** The outlier control exists to catch *data errors* (for example an LSE
price quoted in pounds instead of pence, a 100x move), not to comment on markets.

**Decision.** Flag moves beyond 6 sigma of the instrument's own EWMA volatility
(lambda 0.94). Measured over 2007 to Sep 2026, this fires between 1 (GILT_10Y,
USD, UST_10Y) and 11 (AZN) times per instrument, which is a reviewable number.

**Observed consequence.** Brent fell 9.1% on 29 Sep 2026 and was *not* flagged:
oil volatility was already high, so the move was -3.4 sigma. That is the control
working as designed. Large genuine market moves belong in the risk commentary
(stage 4), not in the data-quality report. That move turned out to be mostly a
futures contract roll, not a market move (INCIDENTS 004), which the outlier control
could not have caught either. Roll detection needs its own control.

## 009 - Reporting SLA: the report for date T is due 07:30 London on T+1

**Observed tension.** Running on the evening of 29 Sep 2026, the latest BoE curve
value was 25 Sep (the BoE file had a row for 28 Sep with the curve cells empty),
the latest BoE FX rate and FRED Treasury yield were 28 Sep, and Yahoo equity,
commodity and FX closes were 29 Sep. Why each source lags, and by how much on a
normal day, has not been established yet; one evening is not a pattern. So the stale-price warnings on a
same-day run are real, not a bug. Open question for stage 2: do we wait for the
curve (and risk the SLA) or run on time with carried-forward rates and say so?
A real A&R desk makes this trade-off every morning.

## 010 - VaR conventions for the public-disclosure case study (verified)

**Source.** The Goldman Sachs Group 10-K for fiscal 2025, filed with the SEC on
25 Feb 2026 (`gs-20251231.htm`, accession 0000886982-26-000091), Market Risk
Management section.

**What it says.** Risk-management VaR is one-day at 95% confidence. Regulatory
VaR, used for capital, is 10-day at 99%. The same filing says the firm's daily
market risk reports cover key risks, drivers and changes for each desk.

**Decision.** The engine will compute VaR at both 95% and 99% one-day. Any
comparison with disclosed figures must use the matching confidence level and
horizon, and say so.

**Update, same day: the UK Pillar 3 disclosure (verified).** Goldman's UK Pillar 3
is published for **Goldman Sachs Group UK Limited (GSGUK)**, not for GSI on its own.
GSGUK consolidates GSI and Goldman Sachs International Bank (GSIB); the document
says GSGUK's risk profile is "materially the same" as GSI and GSIB combined.
From the Q4 2025 document (166 pages, `gsguk-q4-2025-pillar-3.pdf`):

- Regulatory VaR: historical simulation with full revaluation, sampling five years
  of history, weighted so that older data counts less. 10-day VaR is the one-day
  figure scaled by the square root of 10.
- Table 16, 99% 10-day VaR over the six months to Dec 2025 (USD m, GSGUK = GSI):
  maximum 356, average 216, minimum 162, period end 182. Stressed VaR: 749 / 588 /
  486 / 593.
- Backtesting: GSI's hypothetical losses exceeded 99% one-day regulatory VaR 3
  times in the 12 months to Dec 2025 (two in April 2025, one in May 2025); actual
  losses exceeded it 0 times; the multiplier stayed at 3.
- The Q1 2026 document (26 pages) has no VaR table, only market risk RWA and its
  movements. Which quarters carry the full VaR tables has not been checked beyond
  these two documents.

**Consequence for this project.** The UK case study can use real disclosed
numbers (VaR range, stressed VaR, backtesting exceptions). The five-year weighted
historical simulation is a documented design worth mirroring in stage 2.

## 011 - Commodity risk uses roll-free fund returns, not the futures series

**Context.** Yahoo's continuous front-month series (`BZ=F`, `GC=F`) switches
contract at each expiry, and the gap between contracts appears as a price move
(INCIDENTS 004: an apparent -9.1% Brent day that was mostly a roll).

**Options.** (a) Rebuild a rolled series from individual contracts: Yahoo only
lists currently-traded contracts, so history cannot be rebuilt. (b) FRED Brent spot
(DCOILBRENTEU): no rolls, but on 29 Sep 2026 its latest value was 22 Sep, a week
behind. (c) Exchange-traded funds that hold and roll futures internally: BNO
(Brent, prices from 2 Jun 2010) and GLD (gold, from 2007).

**Decision.** (c). Positions are still valued at the futures price; risk
scenarios and the backtest use the fund's daily return. On 29 Sep 2026 BNO moved
-2.62%, between the real moves of the November (-2.8%) and December (-2.2%)
contracts. A new control, `futures_roll`, flags any day the future and its fund
differ by more than 2 percentage points.

**Cost.** Basis risk: a fund is not the front-month contract. The two differ in
roll timing, closing time and fees. BNO starts in 2010, so a 2008 Brent stress
scenario (stage 3) will need another source.

## 012 - VaR design

- **Method.** Historical simulation with full revaluation: each past day's factor
  returns are applied to today's levels and every position is repriced. Goldman's
  UK Pillar 3 (Q4 2025) describes the same approach ("historical simulations with
  full valuation").
- **Shocks.** Relative (log returns) for prices and FX, absolute for yields. The
  Pillar 3 says Goldman uses "a mix of absolute and relative returns"; which factors
  get which is our choice, not theirs.
- **Window.** 1,260 market days (about five years), the length the same document
  says Goldman samples.
- **Market days.** A weekday counts only if at least half the instruments have a real
  price. Checked against the gov.uk bank holiday API: the 43 weekdays this drops
  between Oct 2021 and Sep 2026 are exactly the 43 England and Wales bank holidays
  in that period. On those days US and commodity moves are carried into the next
  UK market day's return.
- **Estimator.** VaR is the smallest loss L with P(loss <= L) >= confidence; ES
  splits the boundary scenario (Acerbi and Tasche). With 1,260 equal weights, 99%
  VaR is the 13th-largest loss. Both are reproduced by an independent script
  (`tools/verify_var.py`) using hand-written P&L formulas: they match to the pound.
- **Three methods, primary fixed in advance.** Equal-weighted HS is the primary
  method, chosen before any backtest was run, so the backtest can judge it rather
  than be used to pick the winner. Age-weighted HS (decay 0.995, half-life about 138
  days; Goldman weights its history but does not publish the decay, so this value
  is our judgement) and delta-normal parametric (RiskMetrics EWMA, 0.94) are
  challengers.
- **Attribution.** ES contributions by desk (exactly additive) and stand-alone desk
  VaR (not additive). VaR contributions from a single scenario are too noisy to show.

## 013 - Exact holding return for equities, Yahoo's convention only for the check

**Found by the independent check (INCIDENTS 007).** Stage 2 first used
`log(P_t / (P_{t-1} - D))`, the convention behind Yahoo's adjusted close. Applied
to today's price, that does not reproduce what a shareholder earns on an
ex-dividend day. The exact holding return is `log((P_t + D) / P_{t-1})`.

**Decision.** Risk scenarios and the backtest use the exact return. The dividend
control still uses Yahoo's convention, because its job is to test our dividend
data against Yahoo's US adjustment like for like (271 US dividends agree to
0.01bp).

## 014 - Stress scenarios

**Historical windows, each anchored to a dated, sourced event:**

| Scenario | Window | Anchor (source) |
|---|---|---|
| Lehman 2008 | 12 Sep to 10 Oct 2008 (last close before the filing, then 20 market days) | Chapter 11 filed 15 Sep 2008 (Lehman's SEC 8-K, archived on FRASER) |
| COVID 2020 | 19 Feb to 23 Mar 2020 | S&P 500 peak and trough closes (FRED `SP500`: 3,386.15 and 2,237.40) |
| UK gilt crisis 2022 | 22 to 27 Sep 2022 | Growth Plan published 23 Sep 2022 (gov.uk); BoE gilt purchases announced 28 Sep 2022 (BoE news release) |

The window lengths are our choice. In our own data, the 20-year gilt zero yield
rose from 3.88% to 4.87% between 22 and 27 Sep 2022, then fell to 4.02% on 28 Sep.

**Method.** The cumulative move over the window (relative for prices and FX,
absolute for yields) is applied to today's levels, and today's book is repriced in
full on every day of the path. The report shows the end-of-window P&L and the worst
point along the way. Positions are held constant: no trading, hedging or stop-losses.

**Brent.** Historical windows use FRED Brent spot (`DCOILBRENTEU`, from 1987),
because BNO only starts in June 2010 (DECISIONS 011).

**Dividends inside a window are reinvested** (daily total returns are compounded).
`tools/verify_stress.py` recomputes every scenario from endpoint prices, holding
dividends as cash instead. Hypothetical scenarios and the gilt window agree to the
pound. Lehman differs by 1,479 GBP and COVID by 54,107 GBP. Broken down by
instrument, every gap is an equity that paid a dividend inside the window (for
COVID: AZN, ULVR, RIO, GSK, NVDA). The reinvested dividend falls with the market.
Both conventions are defensible; this one matches how the VaR scenarios work.

**Hypothetical shocks** (equities -15%, yields +100bp, sterling -10%, and a combined
UK crisis) are our own round numbers, not regulatory scenarios.

**Unavailable windows** are reported as "not available", never silently zeroed or
skipped (INCIDENTS 009).

## 015 - VaR explain: a Shapley split over three causes

**Problem.** VaR moves overnight for three reasons at once: trades (positions), the
market moving (levels), and the scenario window rolling forward one day. VaR is a
quantile, so the three effects do not add up, and changing one at a time gives
different answers depending on the order.

**Decision.** Average each effect over all 6 orders (the Shapley value; 8 VaR
evaluations). The parts then add up exactly to the total change, and no order is
favoured. The report also names the historical day that sets today's 99% VaR, the
scenarios that entered and left the window, and today's trades.

**Stated plainly in the report:** the split is a convention. Another convention
would give different parts with the same total.

**A property worth knowing.** In a diversified book, making a long position bigger
can *lower* VaR, if the scenario at the 99% point is one where that asset rose. My
first test assumed the opposite and failed; the test was wrong, not the code. The
test now checks an exact case instead (one position: 1.5x the size adds exactly
0.5x the VaR).

## 003 (update) - The book has drifted

Seeded random trades have no pull back towards the starting sizes, and prices
moved. By 29 Sep 2026, Barclays is a 21.2m GBP position against its 5.0m start:
its price rose 2.96x and random trades added 1.43x the shares. It is the largest
single loss in both the Lehman (-8.65m) and COVID (-11.04m) scenarios, matching
its actual price falls in those windows (-40.8% and -52.1%).

**Correction (stage 4).** An earlier version of this note, and of the README,
said Barclays had become the largest position. It had not. Ranked by market value
on 29 Sep 2026: HSBC 26.84m (11.7% of 229.2m gross exposure), 5y gilt 24.07m,
Barclays 21.20m. Barclays is the largest *stress loss* because it fell the most in
those windows, not because it is the largest holding. The stage 4 commentary
computes the ranking instead of assuming it, which is how this was caught.

## 016 - Finding: the five-year window forgot COVID just before the April 2025 sell-off

**What the data shows.** The primary model's 99% VaR fell 47%, from 4.32m to
2.29m GBP, between the forecasts for 20 Feb and 3 Apr 2025. Over those 30 market
days, the scenarios leaving the five-year window were exactly 24 Feb to 3 Apr 2020:
the COVID crash. Eleven of them were losses above 2m on the book of the time; the
worst, 12 Mar 2020, was -9.38m. The largest single one-day drop in VaR (-0.54m,
forecast for 21 Feb 2025) is the day 24 Feb 2020 (-4.35m) left. Then the 3, 4 and 7
Apr 2025 losses (-5.56m, -4.65m, -2.26m) breached the lowered VaR on consecutive days.

**Why it matters.** This is the "cliff effect" of fixed-window historical
simulation: a stress period stops counting on a fixed date five years later,
however recently markets were calm. It explains why the primary model's exceptions
cluster (Christoffersen p = 0.0002) while their count is fine (Kupiec p = 0.44).
The age-weighted model has no cliff, because old scenarios fade gradually, but it
has more exceptions overall (10 against 5): it forgets calm and stress alike.

**What a desk could do (not built).** Keep a stressed-VaR measure alongside VaR, as
regulators require (Goldman's UK Pillar 3 reports one), or warn when a large
scenario is about to leave the window. The explain step already shows each day's
leaving scenario, so a warning would be a small addition.

## 017 - Commentary, dashboard and BI export

- **Commentary is written by rules, not a language model.** Each sentence is a
  template filled from stored numbers, so every claim can be traced. Review levels
  (concentration 10% of gross, VaR move 10%, Christoffersen p 0.05) are our own
  choices, in `config/risk.yaml`. It caught a mistake in my own stage 3 notes
  (DECISIONS 003 correction).
- **Dashboard is plain HTML, JavaScript and SVG** with the data embedded, no
  libraries, so it opens offline and cannot break on a CDN. Charts are drawn at the
  real container width and redrawn on resize, so text stays readable on a phone.
  Colours come from the dataviz skill's reference palette and passed its
  validator (colour-blind separation and contrast) in light and dark mode. The one
  light-mode colour below 3:1 contrast is backed by direct labels and table views.
  Every chart has a table view; every status carries an icon and a word, not colour
  alone. Checked in a browser at 338px and 1,185px wide: no text overlaps and no
  sideways page scroll.
- **BI export is a star schema in CSV**, tested against the engine
  (`tests/test_outputs.py`). The Power BI report itself is to be built by hand from
  `docs/BI_GUIDE.md`; it has not been built yet.

## 018 - The Goldman Sachs case study

**Question.** From Goldman's published VaR and public market data only, how much of
the change in its reported risk can observable volatility explain, and what cannot
be said at all? It is deliberately not an attempt to estimate Goldman's positions.

**Data.** 67 10-Q and 10-K filings, 2010 to 2026, from SEC EDGAR. The VaR tables have
no XBRL tags, so they are parsed from the HTML (`riskengine/gs_var.py`). Checks built
into every run:
- all 144 tables pass the arithmetic identity (categories + diversification = total)
- all 37 reprints that can be checked match the original filing exactly
- all 67 filings state a one-day, 95% confidence VaR
The Q4 quarterly average appears only in the next Q1 10-Q (from 2017), so Q4 2010
to Q4 2015 are genuine gaps, not parser failures.

**SEC access.** SEC's fair-access policy asks automated tools for a declared contact.
The contact is read from the `SEC_USER_AGENT` environment variable and is not stored
in the code or the repo. Requests are paced at 4 a second (SEC's limit is 10).

**Benchmarks, one per category:** 10y Treasury yield, S&P 500, the Fed's broad dollar
index, Brent spot. They are US-centred stand-ins for a global book.

**Findings.**
- Benchmark volatility explains at most about 18% of quarter-on-quarter changes in any
  category (best r = 0.43, equities, 5y window). Most of the change is something
  public data cannot see.
- The 95% confidence intervals of the 1y, 2y and 5y windows overlap for every
  category, so the data cannot identify the lookback Goldman's model behaves like.
- In the COVID quarter, equity VaR rose 71% while equal-weighted trailing volatility
  rose 6% to 9%. Consistent with Goldman's stated weighting of recent data, and
  equally consistent with larger positions.
- Benchmarks have window cliffs too: the 2y Brent window "fell" 53% in H1 2022 as the
  spring 2020 oil crash left it.
- Rescaling the group's 95% 1-day VaR to 99% 10-day gives 382m against the UK
  group's 216m regulatory figure. The documents give four reasons they differ, none
  quantified, so no ratio is reported.

**Limits.** Quarterly data only (53 usable quarter-on-quarter changes); proxies are
crude; the episodes were chosen for the events they cover, but the decomposition was
first run on these same four while building the code.

## 019 - The case study's own mistakes, caught before they reached the report

- The table filter first searched raw HTML, where markup sometimes splits
  "Diversification effect"; 5 filings parsed partially. Now it searches the text.
- The reprint check first reported 2 mismatches. Both were Q2 and Q3 2010, whose
  column 2 is the same quarter of 2009, a year not downloaded. The check now only calls
  something a mismatch when both possible originals exist.
- A comment in `config/casestudy.yaml` first said the episodes were chosen before
  seeing results. That was not true, and it now says so.
