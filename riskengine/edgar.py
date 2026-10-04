"""Goldman Sachs 10-K / 10-Q filings from SEC EDGAR, cached with hashes.

SEC's fair-access policy asks automated tools to declare a contact in the
User-Agent header and to stay under 10 requests a second. The contact is read
from the SEC_USER_AGENT environment variable and is never stored in the repo.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

GS_CIK = "0000886982"
BASE = "https://www.sec.gov"
PAUSE_S = 0.25                      # well under SEC's 10 requests per second


@dataclass
class Filing:
    form: str
    filed: str
    accession: str
    index_url: str
    doc_url: str | None = None
    raw_path: Path | None = None
    sha256: str | None = None


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        raise RuntimeError("Set SEC_USER_AGENT to 'Your Name your@email' (SEC requires a declared contact).")
    return ua


def _get(url: str) -> str:
    time.sleep(PAUSE_S)
    r = requests.get(url, headers={"User-Agent": user_agent()}, timeout=120)
    r.raise_for_status()
    if "Undeclared Automated Tool" in r.text[:2000]:
        raise RuntimeError("SEC rejected the request: User-Agent not accepted")
    return r.text


def list_filings(form: str, cik: str = GS_CIK) -> list[Filing]:
    atom = _get(f"{BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include"
                f"&count=100&output=atom")
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", atom, re.S):
        ftype = re.search(r"<filing-type>(.*?)<", entry).group(1)
        if ftype != form:                                    # skip amendments such as 10-Q/A
            continue
        href = re.search(r"<filing-href>(.*?)<", entry).group(1)
        acc = re.search(r"<accession-number>(.*?)<", entry).group(1)
        out.append(Filing(form, re.search(r"<filing-date>(.*?)<", entry).group(1), acc, href))
    return out


def fetch(filing: Filing, cache: Path) -> Filing:
    """Download the filing's main document once; later runs read the cached bytes."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{filing.accession}.htm"
    if not path.exists():
        index = _get(filing.index_url)
        # The first document row of type 10-Q/10-K in the index table is the main document.
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", index, re.S)
        doc = None
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(cells) >= 4 and re.sub(r"<[^>]+>", "", cells[3]).strip() == filing.form:
                m = re.search(r'href="([^"]+)"', cells[2])
                if m:
                    doc = m.group(1).replace("/ix?doc=", "")
                    break
        if doc is None:
            raise RuntimeError(f"No {filing.form} document found in {filing.index_url}")
        filing.doc_url = BASE + doc if doc.startswith("/") else doc
        path.write_bytes(_get(filing.doc_url).encode("utf-8"))
        (cache / f"{filing.accession}.url.txt").write_text(filing.doc_url, encoding="utf-8")
    else:
        url_file = cache / f"{filing.accession}.url.txt"
        filing.doc_url = url_file.read_text(encoding="utf-8") if url_file.exists() else None
    payload = path.read_bytes()
    filing.raw_path, filing.sha256 = path, hashlib.sha256(payload).hexdigest()
    return filing
