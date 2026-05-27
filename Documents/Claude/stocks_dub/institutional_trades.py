"""
Institutional holdings data — sourced from yfinance (SEC 13F filings, free).

Fetches the top 10 institutional holders and their quarterly position changes.
pctChange > 0  → institution increased position (bullish signal)
pctChange < 0  → institution decreased position (bearish signal)
pctChange = 1  → brand new position opened (very bullish)

Caches results for 7 days (13F filings are quarterly — data barely changes).

Key signal: multiple well-known institutions ADDING to their position
= "smart money consensus" which historically predicts outperformance.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta

import yfinance as yf

_DIR        = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_DIR, "institutional_cache.json")
_CACHE_TTL  = 7 * 24 * 3600   # 7 days
_LOCK       = threading.Lock()

# Module-level in-memory cache — loaded once, avoids repeated full JSON reads
_MEM_CACHE: dict | None = None

# Well-known institutions — carry extra weight in scoring
_TIER1 = {
    "berkshire hathaway", "blackrock", "vanguard", "state street",
    "fidelity", "jpmorgan", "morgan stanley", "goldman sachs",
    "bridgewater", "citadel", "renaissance", "aqr", "two sigma",
    "point72", "millennium", "viking global", "coatue", "tiger global",
    "druckenmiller", "baupost", "third point",
}


def _load_cache() -> dict:
    global _MEM_CACHE
    if _MEM_CACHE is None:
        with _LOCK:
            if _MEM_CACHE is None:
                _MEM_CACHE = {}
                if os.path.exists(_CACHE_FILE):
                    try:
                        with open(_CACHE_FILE) as f:
                            _MEM_CACHE = json.load(f)
                    except Exception:
                        pass
    return _MEM_CACHE


def _save_cache(key: str, data: dict) -> None:
    cache = _load_cache()
    with _LOCK:
        cache[key] = data
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=_DIR, delete=False, suffix=".tmp"
            ) as tf:
                json.dump(cache, tf, indent=2)
                tmp_path = tf.name
            os.replace(tmp_path, _CACHE_FILE)
        except Exception:
            pass


def _is_tier1(name: str) -> bool:
    n = name.lower()
    return any(t in n for t in _TIER1)


def fetch_institutional_data(ticker: str) -> dict:
    """
    Fetch institutional holdings and quarter-over-quarter changes.

    Returns
    -------
    {
        "ticker":           str,
        "total_institutions": int,       # total number of institutions holding
        "inst_pct_held":    float,       # % of float held by institutions
        "buyers":           [{"name", "shares", "value", "pct_change", "is_tier1"}],
        "sellers":          [{"name", "shares", "value", "pct_change", "is_tier1"}],
        "new_positions":    [{"name", "shares", "value", "is_tier1"}],
        "buyer_count":      int,         # institutions that increased >5%
        "seller_count":     int,         # institutions that decreased >5%
        "tier1_buying":     bool,        # any tier-1 institution adding?
        "tier1_buyers":     [str],       # names of tier-1 buyers
        "inst_score":       int,         # composite buy signal score
        "error":            None | str,
        "cached_ts":        str,
    }
    """
    cache_key = ticker
    cache = _load_cache()
    cached = cache.get(cache_key, {})
    if cached.get("cached_ts"):
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(cached["cached_ts"])).total_seconds()
            if age < _CACHE_TTL:
                return cached
        except Exception:
            pass

    empty = {
        "ticker": ticker, "total_institutions": 0, "inst_pct_held": 0.0,
        "buyers": [], "sellers": [], "new_positions": [],
        "buyer_count": 0, "seller_count": 0,
        "tier1_buying": False, "tier1_buyers": [],
        "inst_score": 0, "error": None,
        "cached_ts": datetime.utcnow().isoformat(),
    }

    try:
        t = yf.Ticker(ticker)

        # Major holders summary
        major = t.major_holders
        total_insts  = 0
        inst_pct     = 0.0
        if major is not None and not major.empty:
            try:
                idx = major.index if hasattr(major, 'index') else []
                val = major["Value"] if "Value" in major.columns else major.iloc[:, 0]
                for label, v in zip(idx, val):
                    l = str(label).lower()
                    if "institutionscount" in l.replace(" ", "") or "institutionscount" in l:
                        total_insts = int(float(v))
                    if "institutionspercent" in l.replace(" ", "") and "float" not in l:
                        inst_pct = round(float(v) * 100, 1)
            except Exception:
                pass

        # Top institutional holders
        df = t.institutional_holders
        if df is None or df.empty:
            empty["total_institutions"] = total_insts
            empty["inst_pct_held"]      = inst_pct
            _save_cache(cache_key, empty)
            return empty

        buyers       = []
        sellers      = []
        new_positions= []
        tier1_buyers = []

        for _, row in df.iterrows():
            name       = str(row.get("Holder", "") or "")
            shares     = int(row.get("Shares", 0)    or 0)
            value      = float(row.get("Value", 0)   or 0)
            pct_change = float(row.get("pctChange", 0) or 0)
            is_t1      = _is_tier1(name)

            entry = {
                "name":       name,
                "shares":     shares,
                "value":      value,
                "pct_change": round(pct_change * 100, 1),  # convert to %
                "is_tier1":   is_t1,
            }

            if pct_change >= 0.99:          # new position (100% increase)
                new_positions.append(entry)
                buyers.append(entry)
                if is_t1:
                    tier1_buyers.append(name)
            elif pct_change >= 0.05:        # added 5%+ to position
                buyers.append(entry)
                if is_t1:
                    tier1_buyers.append(name)
            elif pct_change <= -0.05:       # reduced 5%+ from position
                sellers.append(entry)

        # Composite score
        inst_score = 0
        n_buyers = len(buyers)
        n_sellers = len(sellers)
        t1_count  = len(tier1_buyers)

        if t1_count >= 2:
            inst_score += 8
        elif t1_count == 1:
            inst_score += 5
        elif n_buyers >= 5:
            inst_score += 4
        elif n_buyers >= 3:
            inst_score += 3
        elif n_buyers >= 1:
            inst_score += 2

        if n_sellers >= 5 and n_buyers == 0:
            inst_score -= 4
        elif n_sellers >= 3 and n_buyers == 0:
            inst_score -= 2

        result = {
            "ticker":             ticker,
            "total_institutions": total_insts,
            "inst_pct_held":      inst_pct,
            "buyers":             sorted(buyers,  key=lambda x: abs(x["value"]), reverse=True),
            "sellers":            sorted(sellers, key=lambda x: abs(x["value"]), reverse=True),
            "new_positions":      new_positions,
            "buyer_count":        n_buyers,
            "seller_count":       n_sellers,
            "tier1_buying":       len(tier1_buyers) > 0,
            "tier1_buyers":       tier1_buyers,
            "inst_score":         inst_score,
            "error":              None,
            "cached_ts":          datetime.utcnow().isoformat(),
        }
        _save_cache(cache_key, result)
        return result

    except Exception as e:
        empty["error"] = str(e)
        _save_cache(cache_key, empty)
        return empty
