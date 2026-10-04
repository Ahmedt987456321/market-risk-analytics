"""Daily data controls.

Each control answers one question an attestation would have to answer
("is every price there?", "do positions agree with trades?") and returns a
ControlResult. Controls never fix data; they only report on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
SEVERITY = {PASS: 0, INFO: 0, WARN: 1, FAIL: 2}


@dataclass
class ControlResult:
    control: str
    status: str
    n_affected: int
    detail: str


@dataclass
class Context:
    con: object
    cfg: object
    as_of: pd.Timestamp
    raw_md: pd.DataFrame        # market data before de-duplication
    revisions: pd.DataFrame


def _summary(items, limit: int = 6) -> str:
    items = list(items)
    shown = ", ".join(str(i) for i in items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def _bday_lag(last: pd.Timestamp, as_of: pd.Timestamp) -> int:
    return int(np.busday_count(last.date(), as_of.date()))


def _last_dates(ctx: Context) -> dict[str, pd.Timestamp | None]:
    rows = ctx.con.execute(
        "SELECT instrument_id, max(date) AS last FROM market_data WHERE date <= ? GROUP BY 1",
        [ctx.as_of.date()],
    ).df()
    found = {r.instrument_id: pd.Timestamp(r.last) for r in rows.itertuples()}
    return {i.id: found.get(i.id) for i in ctx.cfg.instruments}


# --- Market data ------------------------------------------------------------

def raw_duplicates(ctx: Context) -> ControlResult:
    dup = ctx.raw_md[ctx.raw_md.duplicated(subset=["instrument_id", "date"], keep=False)]
    keys = sorted({f"{r.instrument_id}@{r.date}" for r in dup.itertuples()})
    if keys:
        return ControlResult("raw_duplicates", FAIL, len(keys), f"duplicate (instrument, date) rows: {_summary(keys)}")
    return ControlResult("raw_duplicates", PASS, 0, "no duplicate rows in source data")


def invalid_values(ctx: Context) -> ControlResult:
    lo, hi = ctx.cfg.controls["valid_ranges"]["yield_pct"]
    bad = ctx.con.execute("""
        SELECT m.instrument_id, m.date, m.close
        FROM market_data m JOIN dim_instrument d USING (instrument_id)
        WHERE m.date <= ?
          AND (isnan(m.close)
               OR (d.quote IN ('price', 'fx') AND m.close <= 0)
               OR (d.quote = 'yield_pct' AND (m.close < ? OR m.close > ?)))
    """, [ctx.as_of.date(), lo, hi]).df()
    if len(bad):
        return ControlResult("invalid_values", FAIL, len(bad),
                             _summary(f"{r.instrument_id}@{r.date}={r.close}" for r in bad.itertuples()))
    return ControlResult("invalid_values", PASS, 0, "all values positive / within range")


def missing_prices(ctx: Context) -> ControlResult:
    limit = ctx.cfg.controls["staleness"]["fail_after_bdays"]
    missing = []
    for iid, last in _last_dates(ctx).items():
        if last is None:
            missing.append(f"{iid} (never loaded)")
        elif _bday_lag(last, ctx.as_of) > limit:
            missing.append(f"{iid} (last {last.date()})")
    if missing:
        return ControlResult("missing_prices", FAIL, len(missing), _summary(missing))
    return ControlResult("missing_prices", PASS, 0, f"every instrument priced within {limit} business days")


def stale_prices(ctx: Context) -> ControlResult:
    s = ctx.cfg.controls["staleness"]
    stale = []
    for iid, last in _last_dates(ctx).items():
        if last is None:
            continue
        lag = _bday_lag(last, ctx.as_of)
        if s["warn_after_bdays"] <= lag <= s["fail_after_bdays"]:
            stale.append(f"{iid} ({lag}d, last {last.date()})")
    if stale:
        return ControlResult("stale_prices", WARN, len(stale), _summary(stale) + "; carried forward")
    return ControlResult("stale_prices", PASS, 0, f"all prices dated {ctx.as_of.date()}")


def return_outliers(ctx: Context) -> ControlResult:
    """Today's move against its own recent volatility (EWMA). Flags, never removes."""
    o = ctx.cfg.controls["outliers"]
    lam = o["ewma_lambda"]
    rows = ctx.con.execute("""
        SELECT m.instrument_id, m.date, m.adj_close, d.quote
        FROM market_data m JOIN dim_instrument d USING (instrument_id)
        WHERE m.date <= ? ORDER BY m.instrument_id, m.date
    """, [ctx.as_of.date()]).df()

    flagged = []
    for iid, g in rows.groupby("instrument_id"):
        if len(g) < o["min_history"] or pd.Timestamp(g["date"].iloc[-1]) != ctx.as_of:
            continue
        x = g["adj_close"].to_numpy(dtype=float)
        r = np.diff(x) if g["quote"].iloc[0] == "yield_pct" else np.diff(np.log(x))
        var = pd.Series(r ** 2).ewm(alpha=1 - lam, adjust=False).mean().shift(1).to_numpy()
        sigma = np.sqrt(var[-1])
        if sigma > 0 and abs(r[-1]) > o["z_threshold"] * sigma:
            unit = "pp" if g["quote"].iloc[0] == "yield_pct" else ""
            move = f"{r[-1]:+.3f}{unit}" if unit else f"{np.expm1(r[-1]):+.1%}"
            flagged.append(f"{iid} {move} ({r[-1] / sigma:+.1f} sigma)")
    if flagged:
        return ControlResult("return_outliers", WARN, len(flagged), _summary(flagged) + "; review before use")
    return ControlResult("return_outliers", PASS, 0, f"no move beyond {o['z_threshold']} EWMA sigma")


def cross_source(ctx: Context) -> ControlResult:
    """Primary vs second source on the latest date both have (sources publish on different lags)."""
    tol = ctx.cfg.controls["cross_source"]["tolerance_pct"]
    since = (ctx.as_of - pd.offsets.BDay(ctx.cfg.controls["staleness"]["fail_after_bdays"])).date()
    rows = ctx.con.execute("""
        SELECT r.instrument_id, r.source, r.date, r.value, m.close
        FROM reference_prices r
        JOIN market_data m ON m.instrument_id = r.instrument_id AND m.date = r.date
        WHERE r.purpose = 'cross_check' AND r.date BETWEEN ? AND ?
        QUALIFY row_number() OVER (PARTITION BY r.instrument_id, r.source ORDER BY r.date DESC) = 1
    """, [since, ctx.as_of.date()]).df()
    expected = {c["instrument"] for c in ctx.cfg.cross_checks}
    unchecked = sorted(expected - set(rows["instrument_id"]))
    breaks, checked = [], []
    for r in rows.itertuples():
        diff = abs(r.value - r.close) / r.close * 100
        checked.append(f"{r.instrument_id} {diff:.2f}% @{r.date:%m-%d}")
        if diff > tol:
            breaks.append(f"{r.instrument_id}@{r.date:%Y-%m-%d} primary {r.close:.4f} vs {r.source} {r.value:.4f} ({diff:.2f}%)")
    if breaks:
        return ControlResult("cross_source", WARN, len(breaks), _summary(breaks))
    if not checked:
        return ControlResult("cross_source", INFO, 0, "no overlapping second-source observations")
    detail = f"{len(checked)} of {len(expected)} within {tol}%: {_summary(checked)}"
    if unchecked:
        detail += f"; not checked: {_summary(unchecked)}"
    return ControlResult("cross_source", PASS, 0, detail)


def revisions(ctx: Context) -> ControlResult:
    grace = ctx.cfg.controls["revisions"]["grace_bdays"]
    if ctx.revisions.empty:
        return ControlResult("revisions", PASS, 0, "no stored history changed")
    cutoff = (ctx.as_of - pd.offsets.BDay(grace)).date()
    old = ctx.revisions[pd.to_datetime(ctx.revisions["date"]).dt.date < cutoff]
    if len(old):
        items = (f"{r.instrument_id}@{r.date} {r.old_close:.4f}->{r.new_close:.4f}" for r in old.itertuples())
        return ControlResult("revisions", WARN, len(old), f"history older than {grace}d changed: {_summary(items)}")
    return ControlResult("revisions", INFO, len(ctx.revisions),
                         f"{len(ctx.revisions)} recent values updated (within {grace} business days)")


def history_sufficiency(ctx: Context) -> ControlResult:
    h = ctx.cfg.controls["history"]
    since = (ctx.as_of - pd.DateOffset(years=h["window_years"])).date()
    counts = ctx.con.execute(
        "SELECT instrument_id, count(*) AS n FROM market_data WHERE date > ? AND date <= ? GROUP BY 1",
        [since, ctx.as_of.date()],
    ).df().set_index("instrument_id")["n"]
    short = [f"{i.id} ({int(counts.get(i.id, 0))})" for i in ctx.cfg.instruments
             if counts.get(i.id, 0) < h["min_observations"]]
    if short:
        return ControlResult("history_sufficiency", WARN, len(short),
                             f"fewer than {h['min_observations']} obs in {h['window_years']}y: {_summary(short)}")
    return ControlResult("history_sufficiency", PASS, 0,
                         f"at least {h['min_observations']} obs in {h['window_years']}y for every instrument")


def interior_gaps(ctx: Context) -> ControlResult:
    """A missing day in the middle of a series, while its peers have a price.

    The staleness controls only look at each series' latest date, so a gap
    that is later filled over (as GC=F on 28 Sep 2026) would pass them.
    """
    g = ctx.cfg.controls["gaps"]
    since = (ctx.as_of - pd.offsets.BDay(g["lookback_bdays"])).date()
    rows = ctx.con.execute("""
        SELECT instrument_id, date FROM market_data WHERE date > ? AND date <= ?
    """, [since, ctx.as_of.date()]).df()
    have = set(zip(rows.instrument_id, pd.to_datetime(rows.date).dt.date))   # plain dates, to match bdate_range
    groups = {}
    for i in ctx.cfg.instruments:
        groups.setdefault((i.source, i.currency), []).append(i.id)
    gaps = []
    for members in groups.values():
        for iid in members:
            mine = sorted(d for (j, d) in have if j == iid)
            peers = [p for p in members if p != iid]
            if len(mine) < 2 or len(peers) < g["min_peers"]:
                continue
            for d in pd.bdate_range(mine[0], mine[-1]).date:
                if (iid, d) in have:
                    continue
                share = sum((p, d) in have for p in peers) / len(peers)
                if share >= g["peer_share"]:
                    gaps.append(f"{iid}@{d} ({share:.0%} of peers priced)")
    if gaps:
        return ControlResult("interior_gaps", WARN, len(gaps), _summary(gaps) + "; carried forward")
    return ControlResult("interior_gaps", PASS, 0, f"no unexplained gaps in the last {g['lookback_bdays']} business days")


def futures_roll(ctx: Context) -> ControlResult:
    """Futures return vs its roll-free proxy on the latest common day."""
    thr = ctx.cfg.controls["futures_roll"]["threshold_pct"] / 100
    flagged, checked = [], []
    for inst in (i for i in ctx.cfg.instruments if i.returns_proxy):
        both = ctx.con.execute("""
            SELECT m.date, m.close AS fut, r.value AS proxy
            FROM market_data m JOIN reference_prices r
              ON r.instrument_id = m.instrument_id AND r.date = m.date AND r.purpose = 'return_proxy'
            WHERE m.instrument_id = ? AND m.date <= ? ORDER BY m.date DESC LIMIT 2
        """, [inst.id, ctx.as_of.date()]).df()
        if len(both) < 2:
            continue
        rf = both.fut.iloc[0] / both.fut.iloc[1] - 1
        rp = both.proxy.iloc[0] / both.proxy.iloc[1] - 1
        checked.append(f"{inst.id} {rf - rp:+.2%}")
        if abs(rf - rp) > thr:
            flagged.append(f"{inst.id}@{pd.Timestamp(both.date.iloc[0]):%Y-%m-%d}: future {rf:+.2%} vs {inst.returns_proxy} {rp:+.2%}")
    if flagged:
        return ControlResult("futures_roll", WARN, len(flagged),
                             _summary(flagged) + "; likely contract roll, risk uses proxy returns")
    return ControlResult("futures_roll", PASS, 0, f"futures within threshold of their proxies: {_summary(checked)}")


def dividend_adjustment(ctx: Context) -> ControlResult:
    """Our dividend-adjusted return vs Yahoo's, for US stocks (where Yahoo's is verified correct)."""
    from .returns import yahoo_style_return
    tol = ctx.cfg.controls["dividend_check"]["tolerance_bp"] / 1e4
    worst, n_days, n_div = [], 0, 0
    for inst in (i for i in ctx.cfg.instruments if i.asset_class == "equity" and i.currency == "USD"):
        px = ctx.con.execute(
            "SELECT date, close, adj_close FROM market_data WHERE instrument_id = ? AND date <= ? ORDER BY date",
            [inst.id, ctx.as_of.date()]).df()
        dv = ctx.con.execute("SELECT ex_date AS date, amount FROM dividends WHERE instrument_id = ?", [inst.id]).df()
        px = px.merge(dv, on="date", how="left").fillna({"amount": 0.0})
        ours = yahoo_style_return(px["close"], px["amount"])
        yahoo = np.log(px["adj_close"] / px["adj_close"].shift(1))
        with np.errstate(invalid="ignore"):
            diff = (ours - yahoo).abs().iloc[1:]
        # A gap we cannot compute (e.g. a dividend larger than the price) is a failure, not a pass.
        n_days += int(((diff > tol) | diff.isna()).sum())
        n_div += int((px["amount"] > 0).sum())
        worst.append(f"{inst.id} {diff.max() * 1e4:.2f}bp")
    detail = f"{n_div} US dividends; largest daily gap: {_summary(worst)}"
    if n_days:
        return ControlResult("dividend_adjustment", FAIL, n_days, f"{n_days} days beyond {tol * 1e4:.1f}bp; {detail}")
    return ControlResult("dividend_adjustment", PASS, 0, detail)


# --- Positions --------------------------------------------------------------

def position_reconciliation(ctx: Context) -> ControlResult:
    """Positions(T) must equal Positions(T-1) + Trades(T), for every instrument."""
    tol = ctx.cfg.controls["reconciliation"]["tolerance"]
    breaks = ctx.con.execute("""
        WITH prev_day AS (SELECT max(as_of) AS d FROM positions WHERE as_of < ?),
        prev AS (SELECT instrument_id, quantity FROM positions WHERE as_of = (SELECT d FROM prev_day)),
        cur  AS (SELECT instrument_id, quantity FROM positions WHERE as_of = ?),
        trd  AS (SELECT instrument_id, sum(quantity) AS quantity FROM trades
                 WHERE trade_date > coalesce((SELECT d FROM prev_day), DATE '1900-01-01')
                   AND trade_date <= ? GROUP BY 1)
        SELECT coalesce(c.instrument_id, p.instrument_id, t.instrument_id) AS instrument_id,
               coalesce(p.quantity, 0) AS prev, coalesce(t.quantity, 0) AS traded, coalesce(c.quantity, 0) AS cur
        FROM cur c
        FULL JOIN prev p USING (instrument_id)
        FULL JOIN trd t USING (instrument_id)
        WHERE abs(coalesce(p.quantity, 0) + coalesce(t.quantity, 0) - coalesce(c.quantity, 0))
              > ? * greatest(1, abs(coalesce(c.quantity, 0)))
    """, [ctx.as_of.date()] * 3 + [tol]).df()
    n_pos = ctx.con.execute("SELECT count(*) FROM positions WHERE as_of = ?", [ctx.as_of.date()]).fetchone()[0]
    if n_pos == 0:
        return ControlResult("position_reconciliation", FAIL, 0, f"no position snapshot for {ctx.as_of.date()}")
    if len(breaks):
        items = (f"{r.instrument_id}: {r.prev:,.0f} + {r.traded:,.0f} != {r.cur:,.0f}" for r in breaks.itertuples())
        return ControlResult("position_reconciliation", FAIL, len(breaks), _summary(items))
    return ControlResult("position_reconciliation", PASS, 0, f"{n_pos} positions agree with prior day + trades")


def position_coverage(ctx: Context, panel: pd.DataFrame) -> ControlResult:
    held = ctx.con.execute(
        "SELECT instrument_id FROM positions WHERE as_of = ? AND quantity <> 0", [ctx.as_of.date()]
    ).df()["instrument_id"]
    known = {i.id for i in ctx.cfg.instruments}
    row = panel.loc[ctx.as_of] if ctx.as_of in panel.index else pd.Series(dtype=float)
    unpriced = [i for i in held if i not in known or pd.isna(row.get(i))]
    if unpriced:
        return ControlResult("position_coverage", FAIL, len(unpriced), f"positions with no usable price: {_summary(unpriced)}")
    return ControlResult("position_coverage", PASS, 0, f"all {len(held)} held instruments priced")


# --- Process ----------------------------------------------------------------

def lineage_complete(ctx: Context) -> ControlResult:
    orphans = ctx.con.execute("""
        SELECT count(*) FROM market_data m LEFT JOIN lineage l USING (fetch_id)
        WHERE m.date <= ? AND l.fetch_id IS NULL
    """, [ctx.as_of.date()]).fetchone()[0]
    if orphans:
        return ControlResult("lineage_complete", FAIL, orphans, "market data rows with no lineage record")
    sources = ctx.con.execute("""
        SELECT l.source, l.sha256 FROM lineage l
        WHERE l.fetch_id IN (SELECT DISTINCT fetch_id FROM market_data WHERE date <= ?)
        ORDER BY 1
    """, [ctx.as_of.date()]).df()
    return ControlResult("lineage_complete", PASS, 0,
                         "every row traced to raw file: " + ", ".join(f"{r.source} {r.sha256[:8]}" for r in sources.itertuples()))


def sla(as_of: pd.Timestamp, finished_at: datetime, cfg) -> ControlResult:
    s = cfg.controls["sla"]
    tz = ZoneInfo(s["timezone"])
    hh, mm = (int(x) for x in s["deadline_local"].split(":"))
    due_day = (as_of + pd.offsets.BDay(1)).date()
    deadline = datetime.combine(due_day, time(hh, mm), tzinfo=tz)
    done = finished_at.astimezone(tz)
    if done <= deadline:
        return ControlResult("reporting_sla", PASS, 0, f"finished {done:%Y-%m-%d %H:%M} {tz.key}, due {deadline:%Y-%m-%d %H:%M}")
    if done - deadline > timedelta(days=1):
        return ControlResult("reporting_sla", INFO, 0, f"backfill run (finished {done:%Y-%m-%d %H:%M}); SLA not applicable")
    late = int((done - deadline).total_seconds() // 60)
    return ControlResult("reporting_sla", FAIL, 1, f"finished {done:%H:%M}, {late} min after the {deadline:%H:%M} deadline")


DATA_CONTROLS = [raw_duplicates, invalid_values, missing_prices, stale_prices, interior_gaps, return_outliers,
                 cross_source, futures_roll, dividend_adjustment, revisions, history_sufficiency,
                 position_reconciliation, lineage_complete]


def run_controls(ctx: Context, panel: pd.DataFrame) -> list[ControlResult]:
    results = [c(ctx) for c in DATA_CONTROLS]
    results.insert(-1, position_coverage(ctx, panel))
    return results


def overall_status(results: list[ControlResult]) -> str:
    worst = max((SEVERITY[r.status] for r in results), default=0)
    return {0: PASS, 1: WARN, 2: FAIL}[worst]
