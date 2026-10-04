from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text(encoding="utf-8"))
    return con


def upsert(con, table: str, df: pd.DataFrame, replace: bool = True) -> None:
    if df.empty:
        return
    cols = ", ".join(df.columns)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    con.register("_incoming", df)
    try:
        con.execute(f"{verb} INTO {table} ({cols}) SELECT {cols} FROM _incoming")
    finally:
        con.unregister("_incoming")


def load_instruments(con, cfg) -> None:
    rows = pd.DataFrame([
        {"instrument_id": i.id, "name": i.name, "asset_class": i.asset_class, "desk": i.desk,
         "currency": i.currency, "quote": i.quote, "source": i.source, "ticker": i.ticker,
         "tenor_years": i.tenor_years}
        for i in cfg.instruments
    ])
    con.execute("DELETE FROM dim_instrument")
    upsert(con, "dim_instrument", rows)


def replace_book(con, positions: pd.DataFrame, trades: pd.DataFrame, as_of) -> None:
    """The book is regenerated deterministically each run, so replace up to as_of."""
    con.execute("DELETE FROM positions WHERE as_of <= ?", [as_of])
    con.execute("DELETE FROM trades WHERE trade_date <= ?", [as_of])
    upsert(con, "positions", positions)
    upsert(con, "trades", trades)
