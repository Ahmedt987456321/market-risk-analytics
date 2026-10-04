"""Market data sources.

Each source has a `download` (network -> raw text) and a `parse` (raw text ->
long frame with columns ticker, date, close, adj_close). Raw text is saved
exactly as received and hashed, so every loaded number can be traced back to
the bytes it came from.
"""
from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

USER_AGENT = "market-risk-engine/0.1 (personal research project)"
LONG_COLUMNS = ["ticker", "date", "close", "adj_close"]
YAHOO_COLUMNS = LONG_COLUMNS + ["dividend"]


@dataclass
class Fetch:
    source: str
    request: str
    fetched_at: datetime
    raw_path: Path
    sha256: str
    frame: pd.DataFrame

    @property
    def fetch_id(self) -> str:
        return f"{self.source}-{self.sha256[:12]}"


def _get(url: str, attempts: int = 3) -> str:
    last = None
    for i in range(attempts):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last = exc
            time.sleep(2 ** i)
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}") from last


# --- Bank of England ---------------------------------------------------------

def boe_url(series: list[str], start: str) -> str:
    d = pd.Timestamp(start)
    return (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
        f"?csv.x=yes&Datefrom={d:%d/%b/%Y}&Dateto=now&SeriesCodes={','.join(series)}"
        "&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )


def parse_boe(text: str) -> pd.DataFrame:
    if not text.lstrip().startswith("DATE"):
        raise ValueError("Bank of England response is not the expected CSV")
    wide = pd.read_csv(io.StringIO(text))
    wide["date"] = pd.to_datetime(wide.pop("DATE"), format="%d %b %Y")
    long = wide.melt(id_vars="date", var_name="ticker", value_name="close")
    long["close"] = pd.to_numeric(long["close"], errors="coerce")
    long["adj_close"] = long["close"]
    return long.dropna(subset=["close"])[LONG_COLUMNS]


# --- FRED --------------------------------------------------------------------

def fred_url(series: list[str], start: str) -> str:
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={','.join(series)}&cosd={start}"


def parse_fred(text: str, start: str | None = None) -> pd.DataFrame:
    wide = pd.read_csv(io.StringIO(text))
    date_col = "observation_date" if "observation_date" in wide.columns else "DATE"
    wide["date"] = pd.to_datetime(wide.pop(date_col))
    long = wide.melt(id_vars="date", var_name="ticker", value_name="close")
    long["close"] = pd.to_numeric(long["close"], errors="coerce")  # FRED uses "." or blank for gaps
    long["adj_close"] = long["close"]
    long = long.dropna(subset=["close"])
    if start is not None:
        long = long[long["date"] >= pd.Timestamp(start)]  # FRED ignores cosd for some series
    return long[LONG_COLUMNS]


# --- Yahoo Finance -----------------------------------------------------------

def download_yahoo(tickers: list[str], start: str) -> str:
    import yfinance as yf

    wide = yf.download(tickers, start=start, auto_adjust=False, actions=True, progress=False, threads=True)
    if wide.empty:
        raise RuntimeError("Yahoo returned no data")
    long = (
        wide[["Close", "Adj Close", "Dividends"]]
        .stack(level="Ticker", future_stack=True)
        .reset_index()
        .rename(columns={"Date": "date", "Ticker": "ticker", "Close": "close", "Adj Close": "adj_close",
                         "Dividends": "dividend"})
    )
    return long[YAHOO_COLUMNS].to_csv(index=False)


def parse_yahoo(text: str) -> pd.DataFrame:
    long = pd.read_csv(io.StringIO(text), parse_dates=["date"])
    if "dividend" not in long.columns:        # raw files saved before dividends were requested
        long["dividend"] = 0.0
    long["dividend"] = long["dividend"].fillna(0.0)
    return long.dropna(subset=["close"])[YAHOO_COLUMNS]


# --- Orchestration -----------------------------------------------------------

def _save(source: str, request: str, text: str, raw_dir: Path, frame: pd.DataFrame) -> Fetch:
    now = datetime.now(timezone.utc)
    folder = raw_dir / f"{now:%Y%m%dT%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{source}.csv"
    payload = text.encode("utf-8")
    path.write_bytes(payload)  # bytes, not text: Windows would otherwise rewrite line endings
    (folder / f"{source}.request.txt").write_text(request, encoding="utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    return Fetch(source, request, now, path, sha, frame)


def fetch_live(cfg, raw_dir: Path) -> dict[str, Fetch]:
    start = cfg.history_start
    fetches = {}

    tickers = cfg.tickers("yahoo")
    text = download_yahoo(tickers, start)
    fetches["yahoo"] = _save("yahoo", f"yfinance.download(tickers={tickers}, start={start}, auto_adjust=False, actions=True)",
                             text, raw_dir, parse_yahoo(text))

    url = boe_url(cfg.tickers("boe"), start)
    text = _get(url)
    fetches["boe"] = _save("boe", url, text, raw_dir, parse_boe(text))

    url = fred_url(cfg.tickers("fred"), start)
    text = _get(url)
    fetches["fred"] = _save("fred", url, text, raw_dir, parse_fred(text, start))
    return fetches


PARSERS = {"yahoo": parse_yahoo, "boe": parse_boe, "fred": parse_fred}


def fetch_cached(cfg, raw_dir: Path) -> dict[str, Fetch]:
    """Reload the most recent raw files from disk. Same bytes, same hashes."""
    fetches = {}
    for source, parse in PARSERS.items():
        candidates = sorted(raw_dir.glob(f"*/{source}.csv"))
        if not candidates:
            raise FileNotFoundError(f"No cached raw data for {source} in {raw_dir}; run once without --offline")
        path = candidates[-1]
        payload = path.read_bytes()
        text = payload.decode("utf-8")
        request_file = path.with_name(f"{source}.request.txt")
        request = request_file.read_text(encoding="utf-8") if request_file.exists() else "unknown"
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        sha = hashlib.sha256(payload).hexdigest()
        frame = parse(text, cfg.history_start) if source == "fred" else parse(text)
        fetches[source] = Fetch(source, request, fetched_at, path, sha, frame)
    return fetches
