# Incident log

Real problems found while building and running the pipeline. Deliberately
injected faults live in the test suite, not here.

---

## 001 - Planned primary data source blocked automated access

- **Date:** 29 Sep 2026
- **Detection:** the first request to Stooq returned HTTP 200, but the body was an
  HTML bot-check page, not CSV.
- **Impact:** none on data (caught before any load). The design changed (DECISIONS 001).
- **Root cause:** the provider now requires a browser to pass a JavaScript check.
- **Lesson:** a 200 status is not proof of success. Parsers check the content
  itself (for example `parse_boe` rejects a response that does not start with the
  expected header).

## 002 - Lineage hashes did not match the raw files on disk

- **Date:** 29 Sep 2026
- **Detection:** an offline replay of the same raw files produced different SHA-256
  hashes from the live run for Yahoo and BoE (FRED matched).
- **Impact:** the lineage records from the first run could not be verified against
  the stored files. No numbers were affected.
- **Root cause:** the hash was taken from the text in memory, then the text was
  saved in text mode. On Windows that rewrites line endings, so the bytes on disk
  differed from the bytes hashed. FRED matched only by luck: its response used
  plain `\n` line endings, so nothing was rewritten.
- **Fix:** raw payloads are now written and hashed as bytes.
- **Prevention:** `test_saved_hash_matches_bytes_on_disk_and_reload` checks that the
  recorded hash equals `sha256(file)` and survives an offline reload.

## 003 - Run ID collision

- **Date:** 29 Sep 2026
- **Detection:** the test suite. Two runs in the same second got the same run ID
  and the second failed on the primary key.
- **Impact:** a quick rerun of a failed job (a normal operational action) would crash.
- **Fix:** run IDs now end in a random 6-character suffix.

## 004 - Brent "-9.1%" on 29 Sep 2026 was mostly a contract roll

- **Detection:** reviewing the first live run by hand. The outlier control did not
  flag it (-3.4 sigma, below the 6 sigma threshold).
- **Evidence (Yahoo closes, USD):**

  | Date | `BZ=F` (continuous) | `BZX26` (Nov 26) | `BZZ26` (Dec 26) |
  |---|---:|---:|---:|
  | 28 Sep 2026 | 105.28 | 105.28 | 97.83 |
  | 29 Sep 2026 | 95.70 | 102.33 | 95.70 |

  `BZ=F` equalled the November contract up to 28 Sep, and the December contract on
  29 Sep. The real one-day moves were -2.8% (Nov) and -2.2% (Dec). Most of the
  apparent -9.1% is the gap between two different contracts, with the curve in
  backwardation (Nov above Dec).
- **Impact:** none yet (no VaR is computed in stage 1). Had this gone into
  historical VaR as a real return, it would have overstated commodity risk.
- **Also noticed:** Yahoo's `BZ=F` close for 29 Sep changed from 95.690002 (an earlier
  test download that evening) to 95.699997 (a later download). A small same-day revision,
  the kind the revisions control reports as INFO rather than WARN.
- **Next step (stage 2):** build commodity returns from individual contracts, or
  detect roll dates and exclude the roll gap, and add a roll control.

## 005 - Yahoo's adjusted close ignores UK dividends (off by 100x)

- **Date:** 29 Sep 2026
- **Detection:** a verification pass. Comparing each step in Yahoo's adjustment
  factor with Yahoo's own list of dividends.
- **Evidence:** for all 10 LSE stocks and every dividend since 2007, Yahoo's listed
  dividend divided by the dividend implied by its adjusted close is about 100
  (median between 99.98 and 100.04 per stock). The listed dividends are right:
  they match AstraZeneca's SEC-filed dividends after currency conversion.
- **Root cause (likely, not confirmed with Yahoo):** prices are quoted in pence
  while the adjustment appears to treat the dividend in pounds, the same kind of
  pence/pound mix-up the outlier control tests for.
- **Impact:** on each UK ex-dividend date, the "adjusted" return still contains the
  full dividend drop (a few percent for HSBC, Shell, BP), which would look like a
  trading loss. Stage 1 only uses adjusted close in the outlier control. Checked
  over 2007 to 2026: of the 61 six-sigma flags on UK stocks, 1 falls on an
  ex-dividend date (RIO, 7 Mar 2019, -6.2 sigma), so that flag is at least partly
  caused by this problem. No VaR has been computed yet.
- **Fix (stage 2):** build dividend adjustment from the dividend data directly
  (DECISIONS 006), with a control that compares it against Yahoo's for US stocks.

## 006 - Missing gold price inside the series, and a control that could never fire

- **Date:** 29 Sep 2026
- **Detection:** by hand, while testing the commodity data. Yahoo `GC=F` has no row
  for 28 Sep 2026; the December contract `GCZ26.CMX` (4,168.4) and the GLD fund
  (377.91) both do.
- **Impact:** the price was carried forward from 25 Sep, so the 29 Sep return spans
  two trading days. None of the stage 1 controls noticed, because they only check
  each series' latest date.
- **First fix, which did not work:** a new `interior_gaps` control. On the first
  live run it reported PASS. The dates from the database were pandas Timestamps and
  the calendar dates were plain dates, so they never matched: every day looked
  missing for every instrument, the peer rule then suppressed every flag, and the
  control could not fire at all.
- **Fix:** normalise both to plain dates. It now reports `GOLD@2026-09-28 (100% of
  peers priced)`, with no false positives in the same window.
- **Prevention:** `test_interior_gap_is_flagged` reproduces the real case.
  **Lesson:** a new control is not working until it has been seen to fail.

## 007 - Backtest P&L was slightly wrong on ex-dividend days

- **Date:** 29 Sep 2026
- **Detection:** `tools/verify_var.py` compares the engine's backtest P&L with a plain
  mark-to-market of yesterday's positions plus dividends received. They differed on
  69 of 694 days, by up to 20,224 GBP (12 Mar 2026). All 69 were ex-dividend days.
- **Root cause:** equity returns used Yahoo's adjustment convention, which is not
  the exact holding return (DECISIONS 013).
- **Impact:** small but systematic. The largest gap (20,224 GBP on 12 Mar 2026) was 0.73% of that day's 99% VaR.
- **Fix:** exact holding return. After the fix the two agree on all 694 days
  (largest gap 0.00 GBP, excluding commodities, which use proxy returns by design).

## 008 - The dividend control passed on data it could not evaluate

- **Date:** 29 Sep 2026
- **Detection:** the test suite. A test that feeds dividends in cents against prices
  in dollars expected FAIL and got PASS.
- **Root cause:** a dividend larger than the price makes the comparison the log of a
  negative number (NaN), and `NaN > tolerance` is false, so the control counted
  nothing.
- **Fix:** any day that cannot be evaluated now counts as a failure.

## 009 - Stress run crashed when a window was outside the data

- **Date:** 29 Sep 2026
- **Detection:** the test suite. The synthetic test history starts in 2021, so the
  2008 and 2020 windows do not exist, and the stress code raised an IndexError,
  which stopped the whole daily run.
- **Why it matters:** one bad scenario definition, or a data source with shorter
  history, should not take down the risk report.
- **Fix:** a window the data does not cover is reported as "not available", with
  the reason, and is not stored as a number.
