"""
FRED (Federal Reserve Economic Data) fetcher.
Free API key: https://fred.stlouisfed.org/docs/api/api_key.html

Fetches key macro indicators once per day and caches them.
Used by scorer.py to apply a macro-environment modifier to each stock.
"""

import json
import os
from datetime import date, timedelta

import requests

import cache_db as _cdb

_DIR        = os.path.dirname(os.path.abspath(__file__))
_KEYS_FILE  = os.path.join(_DIR, "api_keys.json")
_NAMESPACE  = "fred"
_CACHE_KEY  = "macro"
_BASE_URL   = "https://api.stlouisfed.org/fred/series/observations"

# Series to fetch
_SERIES = {
    "fed_funds_rate": "FEDFUNDS",   # Fed Funds Effective Rate (monthly)
    "treasury_10y":   "DGS10",      # 10-Year Treasury (daily)
    "treasury_2y":    "DGS2",       # 2-Year Treasury (daily)
    "yield_curve":    "T10Y2Y",     # 10Y-2Y spread — negative = inverted
    "cpi":            "CPIAUCSL",   # CPI All Urban (monthly, index)
    "unemployment":   "UNRATE",     # Unemployment Rate (monthly)
}


# ── API key helpers ────────────────────────────────────────────────────────────

def get_api_key() -> str:
    try:
        with open(_KEYS_FILE) as f:
            return json.load(f).get("fred_api_key", "")
    except Exception:
        return ""


def save_api_key(key: str, provider: str = "fred"):
    data = {}
    if os.path.exists(_KEYS_FILE):
        try:
            with open(_KEYS_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    data[f"{provider}_api_key"] = key
    with open(_KEYS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Fetch ──────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    return _cdb.get(_NAMESPACE, _CACHE_KEY) or {}


def _save_cache(data: dict):
    _cdb.set(_NAMESPACE, _CACHE_KEY, data)


def _fetch_series(series_id: str, api_key: str, limit: int = 3) -> list:
    """Fetch the N most recent observations for a FRED series."""
    try:
        r = requests.get(_BASE_URL, params={
            "series_id":        series_id,
            "api_key":          api_key,
            "file_type":        "json",
            "sort_order":       "desc",
            "limit":            limit,
            "observation_start": (date.today() - timedelta(days=120)).isoformat(),
        }, timeout=10)
        obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None, "")]
        return obs
    except Exception:
        return []


def fetch_macro(api_key: str = None) -> dict:
    """
    Fetch and cache key macro indicators from FRED.
    Falls back to cached data if API key missing or call fails.
    Returns dict with values + direction (rising/falling/stable).
    """
    cache = _load_cache()
    today = date.today().isoformat()

    if cache.get("cached_date") == today and cache.get("yield_curve") is not None:
        return cache

    key = api_key or get_api_key()
    if not key:
        if cache:
            cache["stale"] = True
            return cache
        return {"error": "No FRED API key — add one in Settings", "cached_date": None}

    result: dict = {"cached_date": today, "error": None, "stale": False}

    for field, series_id in _SERIES.items():
        obs = _fetch_series(series_id, key, limit=3)
        if obs:
            result[field]            = float(obs[0]["value"])
            result[f"{field}_date"]  = obs[0]["date"]
            result[f"{field}_prev"]  = float(obs[1]["value"]) if len(obs) > 1 else None
            result[f"{field}_prev2"] = float(obs[2]["value"]) if len(obs) > 2 else None
        else:
            result[field] = None

    # Compute CPI YoY % change (current vs 12m-ago)
    if result.get("cpi"):
        obs_cpi = _fetch_series("CPIAUCSL", key, limit=14)
        valid   = [o for o in obs_cpi if o.get("value") not in (".", None, "")]
        if len(valid) >= 13:
            now = float(valid[0]["value"])
            yr_ago = float(valid[12]["value"])
            result["cpi_yoy_pct"] = round((now - yr_ago) / yr_ago * 100, 2) if yr_ago else None
        else:
            result["cpi_yoy_pct"] = None
    else:
        result["cpi_yoy_pct"] = None

    # Rate direction: cutting / hiking / stable
    ff     = result.get("fed_funds_rate")
    ff_p   = result.get("fed_funds_rate_prev")
    ff_p2  = result.get("fed_funds_rate_prev2")
    if ff and ff_p:
        if ff < ff_p:
            result["rate_direction"] = "cutting"
        elif ff > ff_p:
            result["rate_direction"] = "hiking"
        else:
            result["rate_direction"] = "stable"
        # Detect multi-period trend
        if ff_p2:
            if ff < ff_p < ff_p2:
                result["rate_direction"] = "cutting_fast"
            elif ff > ff_p > ff_p2:
                result["rate_direction"] = "hiking_fast"
    else:
        result["rate_direction"] = "unknown"

    _save_cache(result)
    return result


# ── Macro score modifier ───────────────────────────────────────────────────────

def macro_score_modifier(macro: dict, fwd_pe: float = None) -> tuple:
    """
    Returns (pts, reasons) — a modifier applied to each stock's score.
    High-P/E growth stocks get a larger penalty when rates are high/rising.
    Range: -8 to +4
    """
    if not macro or macro.get("error"):
        return 0.0, []

    pts     = 0.0
    reasons = []

    # ── Yield curve ───────────────────────────────────────────────────────────
    yc = macro.get("yield_curve")
    if yc is not None:
        if yc >= 1.5:
            pts += 3
            reasons.append(f"🌍 Yield curve healthy ({yc:+.2f}%) — low recession risk (+3)")
        elif yc >= 0.5:
            pts += 2
        elif yc >= 0:
            pts += 1
        elif yc >= -0.3:
            pts -= 2
            reasons.append(f"🌍 Yield curve flat/inverted ({yc:+.2f}%) (-2)")
        elif yc >= -1.0:
            pts -= 4
            reasons.append(f"🌍 Yield curve inverted ({yc:+.2f}%) — recession signal (-4)")
        else:
            pts -= 6
            reasons.append(f"🌍 Yield curve deeply inverted ({yc:+.2f}%) (-6)")

    # ── Rate direction ────────────────────────────────────────────────────────
    direction = macro.get("rate_direction", "unknown")
    ff        = macro.get("fed_funds_rate") or 0
    if direction == "cutting_fast":
        pts += 4
        reasons.append(f"📉 Fed cutting rates aggressively ({ff:.2f}%) (+4)")
    elif direction == "cutting":
        pts += 2
        reasons.append(f"📉 Fed cutting rates ({ff:.2f}%) (+2)")
    elif direction == "hiking_fast":
        pe_penalty = 2 if (fwd_pe and fwd_pe > 30) else 0  # extra for growth stocks
        pts -= (3 + pe_penalty)
        reasons.append(f"📈 Fed hiking rates fast ({ff:.2f}%) (−{3+pe_penalty})")
    elif direction == "hiking":
        pe_penalty = 1 if (fwd_pe and fwd_pe > 30) else 0
        pts -= (1 + pe_penalty)
        reasons.append(f"📈 Fed hiking rates ({ff:.2f}%) (−{1+pe_penalty})")

    # ── Inflation ─────────────────────────────────────────────────────────────
    cpi = macro.get("cpi_yoy_pct")
    if cpi is not None:
        if cpi > 5.0:
            pts -= 2
            reasons.append(f"💸 High inflation ({cpi:.1f}% CPI YoY) (-2)")
        elif cpi > 3.5:
            pts -= 1
        elif cpi < 2.0:
            pts += 1
            reasons.append(f"💸 Inflation under control ({cpi:.1f}% CPI YoY) (+1)")

    return round(pts, 1), reasons
