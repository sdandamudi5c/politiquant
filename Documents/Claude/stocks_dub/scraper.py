"""
Political Stock Disclosure Tracker — scraper module.

Data source: U.S. House of Representatives Clerk
  - Index:  disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
  - PDFs:   disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf

Incremental: only parses PDFs not already in the cache.
"""

import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta

import defusedxml.ElementTree as ET
import pdfplumber
import requests

CACHE_FILE = "cached_trades.json"
INDEX_CACHE_FILE = "filing_index_cache.json"
BASE_URL = "https://disclosures-clerk.house.gov"
HEADERS = {"User-Agent": "political-disclosure-tracker/1.0 (public STOCK Act data)"}
SCAN_YEARS = [2024, 2025, 2026]
DELAY = 0.2  # seconds between PDF requests

TRANSACTION_MAP = {
    "P":          "Purchase",
    "S":          "Sale",
    "S (partial)": "Sale (Partial)",
    "E":          "Exchange",
    "O":          "Other",
}


# ── index ─────────────────────────────────────────────────────────────────────

def _get_filing_index(year: int) -> list[dict]:
    """Download the annual filing index ZIP and return all PTR entries."""
    url = f"{BASE_URL}/public_disc/financial-pdfs/{year}FD.zip"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()

    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(f"{year}FD.xml") as f:
        tree = ET.parse(f)
    root = tree.getroot()

    ptrs = []
    for member in root:
        if member.findtext("FilingType") != "P":
            continue
        first = member.findtext("First", "") or ""
        last = member.findtext("Last", "") or ""
        ptrs.append({
            "year":         year,
            "name":         f"{first} {last}".strip(),
            "state_dst":    member.findtext("StateDst", "") or "",
            "filing_date":  member.findtext("FilingDate", "") or "",
            "doc_id":       member.findtext("DocID", "") or "",
        })
    return ptrs


def _load_index_cache() -> dict:
    if os.path.exists(INDEX_CACHE_FILE):
        try:
            with open(INDEX_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed_ids": [], "last_updated": None}


def _save_index_cache(cache: dict) -> None:
    with open(INDEX_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


# ── PDF parsing ───────────────────────────────────────────────────────────────

# Approximate x-boundaries for each column in House PTR PDFs (points).
# These are stable across filings since the form template is standardised.
_COL_ASSET_MIN   = 90
_COL_TX_MIN      = 250
_COL_DATE_MIN    = 315
_COL_NDATE_MIN   = 370
_COL_AMT_MIN     = 435
_COL_AMT_MAX     = 530


def _group_words_into_lines(words: list[dict], y_gap: float = 6.0) -> list[list[dict]]:
    """Group word-dicts from pdfplumber into horizontal lines by y-proximity."""
    if not words:
        return []
    lines: list[list[dict]] = []
    current: list[dict] = [words[0]]
    for w in words[1:]:
        if abs(w["top"] - current[0]["top"]) <= y_gap:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
    lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


def _words_in_band(words: list[dict], x_min: float, x_max: float) -> str:
    return " ".join(w["text"] for w in words if x_min <= w["x0"] < x_max)


def _parse_ptr_pdf(pdf_bytes: bytes, filing_meta: dict) -> list[dict]:
    """
    Extract stock transactions from a House PTR PDF using word-position parsing.

    Layout: each trade spans 2-3 lines.
    Line 1 (anchor): asset words | tx-type | date | notif-date | amount
    Line 2:          (TICKER) [ST] or (TICKER) [OP]
    Line 3 (opt):    footnote text (filing type, status)

    Strategy: collect all words, group into lines, find anchor lines (those with
    a date in the Date column), then look ahead to the next anchor to gather all
    words belonging to this trade.
    """
    trades = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_words: list[dict] = []
            for page in pdf.pages:
                page_words = page.extract_words(x_tolerance=3, y_tolerance=3)
                all_words.extend(page_words)

        lines = _group_words_into_lines(all_words)

        # Identify anchor indices (lines that contain a date in the Date column)
        anchor_indices: list[int] = []
        for idx, line_words in enumerate(lines):
            date_text = _words_in_band(line_words, _COL_DATE_MIN, _COL_NDATE_MIN)
            if re.match(r"\d{1,2}/\d{1,2}/\d{4}", date_text):
                anchor_indices.append(idx)

        # For each anchor, gather the anchor line + following non-anchor lines
        for a_pos, anchor_idx in enumerate(anchor_indices):
            end_idx = anchor_indices[a_pos + 1] if a_pos + 1 < len(anchor_indices) else len(lines)

            anchor_words = lines[anchor_idx]
            trailing_words: list[dict] = []
            for i in range(anchor_idx + 1, min(end_idx, anchor_idx + 4)):
                trailing_words.extend(lines[i])

            # Extract fields from anchor line
            date_text  = _words_in_band(anchor_words, _COL_DATE_MIN, _COL_NDATE_MIN)
            ndate_text = _words_in_band(anchor_words, _COL_NDATE_MIN, _COL_AMT_MIN)
            tx_text = _words_in_band(anchor_words, _COL_TX_MIN, _COL_DATE_MIN).strip()

            # Amount may span anchor + trailing (e.g. "$15,001 -" on anchor, "$50,000" below)
            anchor_amt = _words_in_band(anchor_words, _COL_AMT_MIN, _COL_AMT_MAX + 30)
            trailing_amt = " ".join(
                w["text"] for w in trailing_words
                if _COL_AMT_MIN <= w["x0"] < _COL_AMT_MAX + 30
            )
            amount_text = f"{anchor_amt} {trailing_amt}".strip()

            # Asset = anchor asset words + trailing asset-column words (ticker is in trailing)
            anchor_asset = _words_in_band(anchor_words, _COL_ASSET_MIN, _COL_TX_MIN)
            trailing_asset = " ".join(
                w["text"] for w in trailing_words if _COL_ASSET_MIN <= w["x0"] < _COL_TX_MIN
            )
            full_asset = f"{anchor_asset} {trailing_asset}".strip()
            full_asset = re.sub(r"\s+", " ", full_asset)

            ticker_match = re.search(
                r"\(([A-Z]{1,5}(?:\.[A-Z])?)\)\s*\[(ST|OP)\]", full_asset
            )
            if not ticker_match:
                continue

            ticker = ticker_match.group(1)
            company = re.sub(r"\s*\([A-Z.]+\)\s*\[.*?\].*", "", full_asset).strip()
            company = re.sub(r"[\x00-\x08\x0b-\x1f]", "", company).strip()
            company = re.sub(r"\s+", " ", company)

            trades.append({
                "chamber":          "House",
                "name":             filing_meta["name"],
                "state":            filing_meta["state_dst"],
                "ticker":           ticker,
                "asset_name":       company,
                "transaction_type": TRANSACTION_MAP.get(tx_text, tx_text or "Unknown"),
                "transaction_date": _fmt_date(date_text),
                "disclosure_date":  _fmt_date(filing_meta["filing_date"]),
                "amount":           amount_text.strip(),
                "doc_id":           filing_meta["doc_id"],
            })

    except Exception:
        pass
    return trades


def _fmt_date(date_str: str) -> str:
    """Convert M/D/YYYY or MM/DD/YYYY to YYYY-MM-DD."""
    for fmt in ("%m/%d/%Y", "%#m/%#d/%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Try with single-digit month/day
    parts = date_str.split("/")
    if len(parts) == 3:
        try:
            m, d, y = [p.strip() for p in parts]
            return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return date_str


# ── cache I/O ─────────────────────────────────────────────────────────────────

def load_cache() -> list[dict]:
    if not os.path.exists(CACHE_FILE):
        return []
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        return data.get("trades", [])
    except Exception:
        return []


def _save_cache(trades: list[dict]) -> None:
    with open(CACHE_FILE, "w") as f:
        json.dump({"fetched_at": datetime.utcnow().isoformat(), "trades": trades}, f, indent=2)


def get_cache_age() -> str | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        delta = datetime.utcnow() - fetched_at
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"{hours}h {minutes}m ago" if hours else f"{minutes}m ago"
    except Exception:
        return None


# ── main scan ─────────────────────────────────────────────────────────────────

def run_scan(
    years: list[int] | None = None,
    days_back: int = 90,
    progress_callback=None,
) -> tuple[list[dict], list[str]]:
    """
    Fetch new PTR filings from the House Clerk.
    Only processes filings not already in the index cache.

    Returns (all_trades, warnings).
    Raises RuntimeError on total failure.
    """
    if years is None:
        years = SCAN_YEARS

    cutoff = datetime.utcnow() - timedelta(days=days_back)
    index_cache = _load_index_cache()
    processed_ids: set[str] = set(index_cache.get("processed_ids", []))

    existing_trades = load_cache()
    existing_by_id: dict[str, list[dict]] = {}
    for t in existing_trades:
        existing_by_id.setdefault(t.get("doc_id", ""), []).append(t)

    all_warnings: list[str] = []
    new_trades: list[dict] = []

    for year in years:
        try:
            if progress_callback:
                progress_callback(f"Fetching {year} filing index…")
            filings = _get_filing_index(year)
        except requests.exceptions.ConnectionError:
            all_warnings.append(f"{year}: No internet — could not fetch filing index.")
            continue
        except requests.exceptions.Timeout:
            all_warnings.append(f"{year}: Timeout fetching filing index.")
            continue
        except Exception as e:
            all_warnings.append(f"{year}: Index error — {e}")
            continue

        # Filter to recent, unprocessed filings
        pending = []
        for f in filings:
            if f["doc_id"] in processed_ids:
                continue
            try:
                fdate = datetime.strptime(f["filing_date"], "%m/%d/%Y")
                if fdate < cutoff:
                    processed_ids.add(f["doc_id"])  # too old, skip forever
                    continue
            except Exception:
                pass
            pending.append(f)

        total = len(pending)
        if progress_callback:
            progress_callback(f"{year}: {total} new PTR filing(s) to parse…")

        for i, filing in enumerate(pending):
            doc_id = filing["doc_id"]
            try:
                pdf_url = f"{BASE_URL}/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
                r = requests.get(pdf_url, headers=HEADERS, timeout=25)
                r.raise_for_status()
                trades = _parse_ptr_pdf(r.content, filing)
                new_trades.extend(trades)
                processed_ids.add(doc_id)

                if progress_callback and (i + 1) % 10 == 0:
                    progress_callback(f"{year}: parsed {i + 1}/{total} filings…")

                time.sleep(DELAY)
            except requests.exceptions.Timeout:
                all_warnings.append(f"Timeout on filing {doc_id} — skipped.")
            except Exception:
                pass

    # ── Senate PTRs ────────────────────────────────────────────────────────────
    try:
        from senate_scraper import fetch_senate_trades
        senate_trades, senate_warnings = fetch_senate_trades(
            days_back=days_back,
            progress_callback=progress_callback,
        )
        new_trades.extend(senate_trades)
        all_warnings.extend(senate_warnings)
    except Exception as e:
        all_warnings.append(f"Senate: skipped — {e}")

    # ── Merge with cache (de-duplicate by doc_id) ──────────────────────────────
    merged_ids = {t["doc_id"] for t in new_trades}
    kept_old = [t for t in existing_trades if t.get("doc_id") not in merged_ids]
    all_trades = kept_old + new_trades
    all_trades.sort(key=lambda t: t.get("transaction_date", ""), reverse=True)

    if all_trades:
        _save_cache(all_trades)

    index_cache["processed_ids"] = list(processed_ids)
    index_cache["last_updated"] = datetime.utcnow().isoformat()
    _save_index_cache(index_cache)

    if not all_trades and all_warnings:
        raise RuntimeError("\n".join(all_warnings))

    return all_trades, all_warnings
