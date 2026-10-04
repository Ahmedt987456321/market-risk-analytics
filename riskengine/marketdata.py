"""Turn raw source frames into normalised market data, and read it back as panels."""
from __future__ import annotations

import pandas as pd


def normalise(fetches: dict, cfg) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Map (source, ticker) to instrument ids and fix units.

    Returns (market_data, reference_prices, dividends). Market data duplicates
    are kept here on purpose: the duplicate control needs to see them.
    """
    by_key = {(i.source, i.ticker): i for i in cfg.instruments}
    refs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for c in cfg.cross_checks:
        refs.setdefault((c["source"], c["ticker"]), []).append((c["instrument"], "cross_check"))
    for i in cfg.instruments:
        if i.returns_proxy:
            refs.setdefault(("yahoo", i.returns_proxy), []).append((i.id, "return_proxy"))
        if i.stress_proxy:
            src, tkr = i.stress_proxy.split(":", 1)
            refs.setdefault((src, tkr), []).append((i.id, "stress_proxy"))

    md_parts, ref_parts, div_parts = [], [], []
    for source, fetch in fetches.items():
        for ticker, rows in fetch.frame.groupby("ticker", sort=False):
            dates = rows["date"].dt.date.values
            inst = by_key.get((source, ticker))
            if inst is not None:
                md_parts.append(pd.DataFrame({
                    "instrument_id": inst.id,
                    "date": dates,
                    "close": rows["close"].values * inst.price_scale,
                    "adj_close": rows["adj_close"].values * inst.price_scale,
                    "fetch_id": fetch.fetch_id,
                }))
                if "dividend" in rows.columns:
                    paid = rows["dividend"].fillna(0).values > 0
                    if paid.any():
                        div_parts.append(pd.DataFrame({
                            "instrument_id": inst.id,
                            "ex_date": dates[paid],
                            "amount": rows["dividend"].values[paid] * inst.price_scale,  # Yahoo lists LSE dividends in pence
                            "fetch_id": fetch.fetch_id,
                        }))
            for instrument_id, purpose in refs.get((source, ticker), []):
                ref_parts.append(pd.DataFrame({
                    "instrument_id": instrument_id,
                    "purpose": purpose,
                    "source": source,
                    "ticker": ticker,
                    "date": dates,
                    "value": rows["close"].values,
                    "fetch_id": fetch.fetch_id,
                }))

    def cat(parts, cols):
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)

    md = cat(md_parts, ["instrument_id", "date", "close", "adj_close", "fetch_id"])
    ref = cat(ref_parts, ["instrument_id", "purpose", "source", "ticker", "date", "value", "fetch_id"])
    div = cat(div_parts, ["instrument_id", "ex_date", "amount", "fetch_id"])
    return md, ref, div


def dedupe(md: pd.DataFrame) -> pd.DataFrame:
    return md.drop_duplicates(subset=["instrument_id", "date"], keep="last").reset_index(drop=True)


def find_revisions(con, md: pd.DataFrame, tolerance_pct: float) -> pd.DataFrame:
    """Rows whose close differs from what is already stored for the same date."""
    con.register("_incoming", md[["instrument_id", "date", "close"]])
    try:
        return con.execute("""
            SELECT n.instrument_id, n.date, o.close AS old_close, n.close AS new_close
            FROM _incoming n
            JOIN market_data o USING (instrument_id, date)
            WHERE abs(n.close - o.close) > abs(o.close) * ? / 100
        """, [tolerance_pct]).df()
    finally:
        con.unregister("_incoming")


def price_panel(con, end, field: str = "close", ffill_limit: int = 5) -> pd.DataFrame:
    """Wide panel (business days x instruments), forward-filled across short gaps.

    Forward-filling covers local holidays (e.g. a UK bank holiday when US
    markets are open). Anything longer is left as NaN and caught by controls.
    """
    long = con.execute(
        f"SELECT date, instrument_id, {field} AS value FROM market_data WHERE date <= ? ORDER BY date",
        [end],
    ).df()
    wide = long.pivot(index="date", columns="instrument_id", values="value")
    wide.index = pd.to_datetime(wide.index)
    days = pd.bdate_range(wide.index.min(), pd.Timestamp(end))
    return wide.reindex(days).ffill(limit=ffill_limit)
