"""
Senate PTR scraper — efdsearch.senate.gov (free, public STOCK Act data).
Returns trades in the same schema as scraper.py (House).
"""

import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://efdsearch.senate.gov"
HOME = f"{BASE}/search/home/"
DATA = f"{BASE}/search/report/data/"
DELAY = 0.3
BATCH = 100
PTR_TYPE = 11

TRANSACTION_MAP = {
    "purchase":        "Purchase",
    "sale":            "Sale",
    "sale (full)":     "Sale",
    "sale (partial)":  "Sale (Partial)",
    "exchange":        "Exchange",
}


def _make_session() -> tuple[requests.Session, str]:
    s = requests.Session()
    s.headers.update({"User-Agent": "political-disclosure-tracker/1.0 (public STOCK Act data)"})
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


def _get_filing_index(
    session: requests.Session,
    csrf: str,
    start_date: str,
) -> list[dict]:
    """Page through all Senate PTR filings since start_date."""
    filings = []
    offset = 0
    while True:
        r = session.post(
            DATA,
            data={
                "csrfmiddlewaretoken": csrf,
                "draw": "1",
                "start": str(offset),
                "length": str(BATCH),
                "report_types": f"[{PTR_TYPE}]",
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
                "name":         f"{row[0]} {row[1]}".strip(),
                "report_url":   BASE + link_match.group(1),
                "filing_date":  row[4],
            })
        offset += len(rows)
        if offset >= data.get("recordsTotal", 0):
            break
        time.sleep(DELAY)
    return filings


def _parse_report(
    session: requests.Session,
    filing: dict,
) -> list[dict]:
    """Fetch one Senate PTR HTML page and extract transactions."""
    trades = []
    try:
        r = session.get(filing["report_url"], timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        for row in table.find_all("tr")[1:]:
            cells = [td.get_text(separator=" ", strip=True) for td in row.find_all("td")]
            if len(cells) < 8:
                continue

            tx_date_raw   = cells[1]
            ticker        = cells[3].strip().upper()
            asset_name    = cells[4].strip()
            tx_type_raw   = cells[6].strip().lower()
            amount        = cells[7].strip()

            if not re.match(r"\d{2}/\d{2}/\d{4}", tx_date_raw):
                continue

            tx_type = TRANSACTION_MAP.get(tx_type_raw, tx_type_raw.capitalize())

            try:
                tx_date = datetime.strptime(tx_date_raw, "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                tx_date = tx_date_raw

            try:
                disc_date = datetime.strptime(filing["filing_date"], "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                disc_date = filing["filing_date"]

            trades.append({
                "chamber":          "Senate",
                "name":             filing["name"],
                "state":            "",
                "ticker":           ticker if ticker not in ("--", "") else "N/A",
                "asset_name":       asset_name,
                "transaction_type": tx_type,
                "transaction_date": tx_date,
                "disclosure_date":  disc_date,
                "amount":           amount,
                "doc_id":           filing["report_url"].split("/")[-2],
            })
    except Exception:
        pass
    return trades


def fetch_senate_trades(
    days_back: int = 90,
    progress_callback=None,
) -> tuple[list[dict], list[str]]:
    """
    Fetch all Senate PTR filings within the last `days_back` days.
    Returns (trades, warnings).
    """
    warnings: list[str] = []
    trades: list[dict] = []

    start_dt = datetime.utcnow() - timedelta(days=days_back)
    start_date = start_dt.strftime("%m/%d/%Y 00:00:00")

    try:
        if progress_callback:
            progress_callback("Senate: establishing session…")
        session, csrf = _make_session()
    except Exception as e:
        return [], [f"Senate: could not connect — {e}"]

    try:
        if progress_callback:
            progress_callback("Senate: fetching filing index…")
        filings = _get_filing_index(session, csrf, start_date)
    except Exception as e:
        return [], [f"Senate: index fetch failed — {e}"]

    if progress_callback:
        progress_callback(f"Senate: parsing {len(filings)} PTR report(s)…")

    for i, filing in enumerate(filings):
        try:
            t = _parse_report(session, filing)
            trades.extend(t)
            time.sleep(DELAY)
        except Exception:
            pass
        if progress_callback and (i + 1) % 20 == 0:
            progress_callback(f"Senate: parsed {i + 1}/{len(filings)}…")

    return trades, warnings
