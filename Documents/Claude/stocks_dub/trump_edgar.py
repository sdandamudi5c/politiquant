"""
trump_edgar.py — Live SEC EDGAR data for Trump family filings

Fetches from SEC EDGAR (100% free, public) every 6 hours:
  - Trump's latest Schedule 13D/A amendments (DJT stake changes)
  - DJT company insider Form 4 transactions
  - Any new filings by known Trump family CIKs

Trump family CIKs on EDGAR:
  0000947033 — Donald J. Trump (personal)
  0001849635 — Trump Media & Technology Group Corp. (DJT issuer)

OGE Annual Disclosures (non-DJT holdings — PDFs, manually entered):
  https://efts.usethis.oge.gov/public/search/  → search "Trump"
  2025 annual report (covers 2024 holdings) filed ~May 2025
  2026 annual report (covers 2025 holdings) filing due May 2026
"""

import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from threading import Lock

_CACHE: dict = {}
_LOCK = Lock()
_TTL  = 6 * 3600   # 6-hour cache

_HEADERS = {"User-Agent": "PolitiQuant research@politiquant.local"}

# Known Trump family CIKs on SEC EDGAR
_TRUMP_CIK      = "0000947033"   # Donald J. Trump (personal)
_DJT_ISSUER_CIK = "0001849635"   # Trump Media & Technology Group Corp.

# Direct OGE disclosure search links (PDF — manually reviewed)
OGE_SEARCH_URL = (
    "https://efts.usethis.oge.gov/public/search/"
    "#/?search=trump&filerType=annual"
)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    return urllib.request.urlopen(req, timeout=10).read()


def _get_json(url: str) -> dict:
    return json.loads(_get(url))


# ── Trump personal EDGAR filings ───────────────────────────────────────────────
def fetch_trump_filings(since_year: int = 2025) -> list[dict]:
    """
    Return recent SEC filings by Donald J. Trump (CIK 0000947033).
    Focuses on Schedule 13D/A amendments that reveal DJT stake changes.
    """
    cache_key = f"trump_filings_{since_year}"
    with _LOCK:
        c = _CACHE.get(cache_key)
        if c and time.time() - c["ts"] < _TTL:
            return c["data"]

    try:
        sub = _get_json(
            f"https://data.sec.gov/submissions/CIK{_TRUMP_CIK}.json"
        )
        recent     = sub.get("filings", {}).get("recent", {})
        forms      = recent.get("form", [])
        dates      = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        cutoff = str(since_year)
        filings = []
        for f, dt, acc in zip(forms, dates, accessions):
            if dt >= cutoff:
                filings.append({"form": f, "date": dt, "accession": acc})

        with _LOCK:
            _CACHE[cache_key] = {"ts": time.time(), "data": filings}
        return filings

    except Exception:
        return []


def fetch_djt_stake() -> dict:
    """
    Parse Trump's most recent Schedule 13D/A to get his current DJT ownership %.
    Returns {"pct": float, "date": str, "filer": str, "url": str} or {}.
    """
    cache_key = "djt_stake"
    with _LOCK:
        c = _CACHE.get(cache_key)
        if c and time.time() - c["ts"] < _TTL:
            return c["data"]

    result = {}
    try:
        filings = fetch_trump_filings(since_year=2024)
        # Find latest 13D/A
        for f in filings:
            if "13D" in f["form"].upper():
                acc       = f["accession"]
                acc_clean = acc.replace("-", "")
                idx_url   = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{_TRUMP_CIK.lstrip('0')}/{acc_clean}/{acc}-index.htm"
                )
                # Get document list from filing index
                try:
                    html  = _get(idx_url).decode("utf-8", "ignore")
                    cik_n = _TRUMP_CIK.lstrip("0")
                    # Match any XML docs in this filing (with or without namespace prefix)
                    docs  = re.findall(
                        r'href="(/Archives/edgar/data/\d+/[^"]+\.xml)"',
                        html,
                    )
                    # Prefer plain XML over XSLT-prefixed paths
                    docs.sort(key=lambda d: (1 if d.startswith("/Archives/edgar/data") and "xsl" not in d.lower() else 2))
                    for doc in docs:
                        if "primary_doc" in doc or "13d" in doc.lower():
                            xml_url = f"https://www.sec.gov{doc}"
                            xml_data = _get(xml_url).decode("utf-8", "ignore")
                            root = ET.fromstring(xml_data)

                            pct_el = None
                            name_el = None
                            for el in root.iter():
                                tag = el.tag.split("}")[-1]
                                if tag in ("percentOfClass",
                                           "percentOfClassBeneficiallyOwned"):
                                    pct_el = el
                                if tag in ("nameOfReportingPerson",
                                           "reportingPersonName") and el.text and "trump" in el.text.lower():
                                    name_el = el

                            # Also grab shares owned
                            shares_el = None
                            for el2 in root.iter():
                                tag2 = el2.tag.split("}")[-1]
                                if tag2 in ("aggregateAmountOwned",
                                            "sharesOwnedFollowingTransaction"):
                                    shares_el = el2
                                    break

                            if pct_el is not None and pct_el.text:
                                try:
                                    pct = float(pct_el.text.strip().replace("%", ""))
                                except ValueError:
                                    pct = 0.0
                                try:
                                    shares_owned = int(float(shares_el.text.strip().replace(",", ""))) if shares_el is not None and shares_el.text else None
                                except Exception:
                                    shares_owned = None
                                result = {
                                    "pct":          pct,
                                    "shares_owned": shares_owned,
                                    "date":         f["date"],
                                    "filer":        "Donald J. Trump",
                                    "url":          xml_url,
                                    "accession":    acc,
                                    "issuer":       "Trump Media & Technology Group Corp. (DJT)",
                                }
                                break
                except Exception:
                    pass
                if result:
                    break

    except Exception:
        pass

    with _LOCK:
        _CACHE[cache_key] = {"ts": time.time(), "data": result}
    return result


# ── DJT company insider Form 4 transactions ───────────────────────────────────
def fetch_djt_insider_transactions(limit: int = 20) -> list[dict]:
    """
    Return recent Form 4 insider transactions filed with DJT as the issuer.
    Includes any open-market buys/sells by Trump, directors, officers.
    """
    cache_key = "djt_insiders"
    with _LOCK:
        c = _CACHE.get(cache_key)
        if c and time.time() - c["ts"] < _TTL:
            return c["data"]

    txns = []
    try:
        sub    = _get_json(
            f"https://data.sec.gov/submissions/CIK{_DJT_ISSUER_CIK}.json"
        )
        recent = sub.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        dates  = recent.get("filingDate", [])
        accs   = recent.get("accessionNumber", [])

        form4s = [
            (dt, acc)
            for f, dt, acc in zip(forms, dates, accs)
            if f == "4" and dt >= "2025-01-01"
        ][:limit]

        for dt, acc in form4s[:10]:   # parse up to 10 for speed
            acc_clean = acc.replace("-", "")
            cik_n     = _DJT_ISSUER_CIK.lstrip("0")
            idx_url   = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik_n}/{acc_clean}/{acc}-index.htm"
            )
            try:
                html = _get(idx_url).decode("utf-8", "ignore")
                xml_links = re.findall(
                    rf'href="(/Archives/edgar/data/{cik_n}/[^"]+\.xml)"', html
                )
                for xlink in xml_links:
                    if "primary_doc" in xlink or "form4" in xlink.lower():
                        xml_url  = f"https://www.sec.gov{xlink}"
                        xml_data = _get(xml_url).decode("utf-8", "ignore")
                        root     = ET.fromstring(xml_data)

                        reporter = shares = price = code = sec_title = None
                        for el in root.iter():
                            tag = el.tag.split("}")[-1]
                            if tag in ("rptOwnerName",) and el.text:
                                reporter = el.text.strip()
                            if tag == "transactionShares" and el.text and el.text.strip():
                                try:
                                    shares = float(el.text.strip())
                                except ValueError:
                                    pass
                            if tag == "transactionPricePerShare" and el.text and el.text.strip():
                                try:
                                    price = float(el.text.strip())
                                except ValueError:
                                    pass
                            if tag == "transactionCode" and el.text:
                                code = el.text.strip()
                            if tag == "securityTitle" and el.text and el.text.strip():
                                sec_title = el.text.strip()

                        if reporter and (shares or code):
                            txns.append({
                                "date":      dt,
                                "reporter":  reporter,
                                "shares":    shares,
                                "price":     price,
                                "code":      code,          # P=purchase, S=sale, A=award, F=forfeiture
                                "security":  sec_title or "Common Stock",
                                "accession": acc,
                            })
                        break
            except Exception:
                pass

    except Exception:
        pass

    with _LOCK:
        _CACHE[cache_key] = {"ts": time.time(), "data": txns}
    return txns


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
