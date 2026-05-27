"""
Senate PTR scraper — efdsearch.senate.gov (free, public STOCK Act data).

Key design decisions:
  - Incremental: tracks processed report URLs in senate_index_cache.json so
    we never re-parse a filing we've already seen.
  - First run goes back FIRST_RUN_DAYS (365) to build historical depth;
    subsequent runs only look back INCREMENTAL_DAYS (60) for new filings.
  - Handles both the standard <table> layout and the newer
    <div class="table-responsive"> layout some senators use.
  - Normalises names to Title Case and removes duplicate whitespace.
  - Returns trades in the same schema as scraper.py (House).
"""

import json
import os
import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE  = "https://efdsearch.senate.gov"
HOME  = f"{BASE}/search/home/"
DATA  = f"{BASE}/search/report/data/"
DELAY = 0.35     # seconds between requests — polite crawl rate
BATCH = 100

PTR_TYPE = 11

FIRST_RUN_DAYS   = 365   # how far back on a fresh cache
INCREMENTAL_DAYS = 60    # lookback once cache exists

_DIR        = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_DIR, "senate_index_cache.json")

TRANSACTION_MAP = {
    "purchase":       "Purchase",
    "sale":           "Sale",
    "sale (full)":    "Sale",
    "sale (partial)": "Sale (Partial)",
    "exchange":       "Exchange",
}


# ── Name helpers ───────────────────────────────────────────────────────────────

def _normalise_name(raw: str) -> str:
    """
    'RICHARD  BLUMENTHAL' → 'Richard Blumenthal'
    Preserves camelCase in names like 'McCormick' by only capitalising
    fully-uppercase words (names from the Senate feed are all-caps).
    """
    words = raw.strip().rstrip(",").split()
    out = []
    for w in words:
        # Strip trailing comma from each word too
        w = w.rstrip(",")
        if not w:
            continue
        # If word is all-uppercase (or uppercase+punctuation) → title-case it
        if w.replace(".", "").replace("-", "").isupper():
            out.append(w.capitalize())
        else:
            # Already mixed-case — leave as-is (e.g. "McCormick" already correct)
            out.append(w)
    return " ".join(out)


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"processed_urls": [], "last_updated": None}


def _save_cache(data: dict) -> None:
    with open(_CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Session ────────────────────────────────────────────────────────────────────

def _make_session() -> tuple:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "political-disclosure-tracker/1.0 (public STOCK Act data)"
    })
    r = s.get(HOME, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    if not csrf_input:
        raise RuntimeError("Senate: could not find CSRF token")
    csrf = csrf_input["value"]
    s.post(
        HOME,
        data={"csrfmiddlewaretoken": csrf, "prohibition_agreement": "1"},
        headers={"Referer": HOME},
        timeout=15,
    )
    return s, csrf


# ── Filing index ───────────────────────────────────────────────────────────────

def _get_filing_index(session, csrf: str, start_date: str) -> list:
    """Page through Senate PTR filings since start_date."""
    filings = []
    offset  = 0
    while True:
        r = session.post(
            DATA,
            data={
                "csrfmiddlewaretoken": csrf,
                "draw":               "1",
                "start":              str(offset),
                "length":             str(BATCH),
                "report_types":       f"[{PTR_TYPE}]",
                "submitted_start_date": start_date,
            },
            headers={"Referer": f"{BASE}/search/"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", [])
        if not rows:
            break
        for row in rows:
            link_match = re.search(r'href="([^"]+)"', row[3])
            if not link_match:
                continue
            filings.append({
                "name":        _normalise_name(f"{row[0]} {row[1]}".strip()),
                "report_url":  BASE + link_match.group(1),
                "filing_date": row[4],
            })
        offset += len(rows)
        if offset >= data.get("recordsTotal", 0):
            break
        time.sleep(DELAY)
    return filings


# ── Report parser ──────────────────────────────────────────────────────────────

def _parse_report(session, filing: dict) -> list:
    """
    Fetch one Senate PTR page and extract transactions.
    Handles two layouts:
      1. Standard <table> — most senators
      2. <div class="table-responsive"> — some newer filings
    """
    trades = []
    try:
        r = session.get(filing["report_url"], timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        try:
            disc_date = datetime.strptime(
                filing["filing_date"], "%m/%d/%Y"
            ).strftime("%Y-%m-%d")
        except ValueError:
            disc_date = filing["filing_date"]

        doc_id = filing["report_url"].rstrip("/").split("/")[-1]

        # ── Try standard <table> first ────────────────────────────────────────
        table = soup.find("table")
        if table:
            trades = _parse_table(table, filing["name"], disc_date, doc_id)
            if trades:
                return trades

        # ── Fallback: scan all divs/tables in table-responsive wrappers ───────
        for wrapper in soup.find_all("div", class_=re.compile("table")):
            t = wrapper.find("table")
            if t:
                trades = _parse_table(t, filing["name"], disc_date, doc_id)
                if trades:
                    return trades

    except Exception:
        pass
    return trades


def _parse_table(table, senator_name: str, disc_date: str, doc_id: str) -> list:
    """Extract trades from a BeautifulSoup <table> element."""
    trades = []
    rows   = table.find_all("tr")
    if len(rows) < 2:
        return []

    # Detect header row to find column indices
    header_cells = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]

    def _col(keywords):
        for i, h in enumerate(header_cells):
            if any(k in h for k in keywords):
                return i
        return None

    tx_date_col = _col(["transaction date", "transaction_date", "date of transaction"])
    ticker_col  = _col(["ticker", "symbol"])
    asset_col   = _col(["asset", "security", "description"])
    tx_type_col = _col(["type", "transaction type"])
    amount_col  = _col(["amount"])

    # If we can't find key columns, fall back to positional heuristic
    if tx_date_col is None and len(header_cells) >= 7:
        tx_date_col, ticker_col, asset_col, tx_type_col, amount_col = 1, 3, 4, 6, 7

    if tx_date_col is None:
        return []

    for row in rows[1:]:
        cells = [td.get_text(separator=" ", strip=True) for td in row.find_all("td")]
        if not cells or len(cells) <= max(
            filter(lambda x: x is not None,
                   [tx_date_col, ticker_col or 0, tx_type_col or 0])
        ):
            continue

        def _cell(idx):
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx].strip()

        tx_date_raw = _cell(tx_date_col)
        if not re.match(r"\d{1,2}/\d{1,2}/\d{4}", tx_date_raw):
            continue

        try:
            tx_date = datetime.strptime(tx_date_raw, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            tx_date = tx_date_raw

        ticker     = _cell(ticker_col).upper() if ticker_col is not None else "N/A"
        asset_name = _cell(asset_col)  if asset_col  is not None else ""
        tx_type_raw= _cell(tx_type_col).lower() if tx_type_col is not None else ""
        amount     = _cell(amount_col) if amount_col is not None else ""

        # Clean ticker
        ticker = re.sub(r"[^A-Z.\-]", "", ticker)
        if not ticker or ticker in ("--", ""):
            ticker = "N/A"

        tx_type = TRANSACTION_MAP.get(tx_type_raw, tx_type_raw.capitalize() if tx_type_raw else "Unknown")

        trades.append({
            "chamber":          "Senate",
            "name":             senator_name,
            "state":            "",
            "ticker":           ticker,
            "asset_name":       asset_name,
            "transaction_type": tx_type,
            "transaction_date": tx_date,
            "disclosure_date":  disc_date,
            "amount":           amount or "N/A",
            "doc_id":           f"senate_{doc_id}",
        })

    return trades


# ── Public entry point ─────────────────────────────────────────────────────────

def fetch_senate_trades(
    days_back: int = 90,
    progress_callback=None,
) -> tuple:
    """
    Fetch Senate PTR filings incrementally.

    - First run (empty cache): goes back FIRST_RUN_DAYS to build depth.
    - Subsequent runs: only fetches INCREMENTAL_DAYS of filings, but skips
      any report URL already processed (so no duplicate work).

    Returns (trades, warnings).
    """
    warnings: list = []
    trades:   list = []

    idx_cache     = _load_cache()
    processed_urls: set = set(idx_cache.get("processed_urls", []))

    # If cache is empty go back further to build history
    is_first_run = not processed_urls
    lookback     = FIRST_RUN_DAYS if is_first_run else max(days_back, INCREMENTAL_DAYS)

    start_dt    = datetime.utcnow() - timedelta(days=lookback)
    start_date  = start_dt.strftime("%m/%d/%Y 00:00:00")

    if progress_callback:
        progress_callback(
            f"Senate: {'first-time setup — fetching ' + str(lookback) + ' days of history' if is_first_run else 'checking for new filings'}…"
        )

    try:
        session, csrf = _make_session()
    except Exception as e:
        return [], [f"Senate: could not connect — {e}"]

    try:
        filings = _get_filing_index(session, csrf, start_date)
    except Exception as e:
        return [], [f"Senate: index fetch failed — {e}"]

    # Only process filings we haven't seen before
    new_filings = [f for f in filings if f["report_url"] not in processed_urls]

    if progress_callback:
        progress_callback(
            f"Senate: {len(new_filings)} new filing(s) out of {len(filings)} total…"
        )

    for i, filing in enumerate(new_filings):
        try:
            t = _parse_report(session, filing)
            trades.extend(t)
            processed_urls.add(filing["report_url"])
            time.sleep(DELAY)
        except Exception:
            pass
        if progress_callback and (i + 1) % 10 == 0:
            progress_callback(f"Senate: parsed {i + 1}/{len(new_filings)} filing(s)…")

    # Persist updated index
    idx_cache["processed_urls"] = list(processed_urls)
    idx_cache["last_updated"]   = datetime.utcnow().isoformat()
    _save_cache(idx_cache)

    return trades, warnings
