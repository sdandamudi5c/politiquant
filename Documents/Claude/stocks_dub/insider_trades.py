"""
Insider trading data — sourced from yfinance (SEC Form 4 filings, free).

Fetches open-market purchases and sales by CEOs, CFOs, Directors and Officers.
Caches results for 7 days (insider data rarely changes intra-day).

Key insight: open-market CEO/CFO PURCHASES are a very strong bullish signal.
Option exercises and stock awards are excluded — those are compensation, not conviction.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta

import yfinance as yf

_DIR        = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_DIR, "insider_cache.json")
_CACHE_TTL  = 7 * 24 * 3600   # 7 days in seconds
_LOCK       = threading.Lock()

# Module-level in-memory cache — loaded once, avoids repeated full JSON reads
_MEM_CACHE: dict | None = None

# Position importance weights for buy scoring
_POSITION_WEIGHT = {
    "chief executive officer": 6,
    "ceo":                     6,
    "chief financial officer": 5,
    "cfo":                     5,
    "chief operating officer": 4,
    "coo":                     4,
    "president":               4,
    "director":                3,
    "officer":                 2,
    "vp":                      2,
    "vice president":          2,
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


def _save_cache(ticker: str, data: dict) -> None:
    cache = _load_cache()
    with _LOCK:
        cache[ticker] = data
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=_DIR, delete=False, suffix=".tmp"
            ) as tf:
                json.dump(cache, tf, indent=2)
                tmp_path = tf.name
            os.replace(tmp_path, _CACHE_FILE)
        except Exception:
            pass


def _position_weight(position: str) -> int:
    """Return importance weight for a given insider position string."""
    pos = (position or "").lower().strip()
    for key, weight in _POSITION_WEIGHT.items():
        if key in pos:
            return weight
    return 1


def _is_open_market_buy(text: str) -> bool:
    """Return True only for genuine open-market purchases (not awards/exercises)."""
    t = (text or "").lower()
    if "purchase" in t or "buy" in t or "acquisition" in t:
        # Exclude automatic/plan purchases and awards
        if "award" in t or "grant" in t or "exercise" in t or "gift" in t:
            return False
        return True
    return False


def _is_open_market_sell(text: str) -> bool:
    """Return True for open-market sales (not gifts or plan sales)."""
    t = (text or "").lower()
    if "sale" in t or "sell" in t or "sold" in t:
        if "gift" in t:
            return False
        return True
    return False


def fetch_insider_trades(ticker: str, days: int = 120) -> dict:
    """
    Fetch recent insider transactions for a ticker.

    Returns
    -------
    {
        "ticker":         str,
        "buys":           [{"name", "position", "shares", "value", "date", "weight"}],
        "sells":          [{"name", "position", "shares", "value", "date"}],
        "buy_score":      int,    # weighted buy signal (+ve = bullish)
        "net_shares_30d": int,    # net shares bought in last 30 days
        "ceo_bought":     bool,
        "cfo_bought":     bool,
        "buy_count_90d":  int,    # number of open-market buy transactions
        "sell_count_90d": int,
        "error":          None | str,
        "cached_ts":      str,
    }
    """
    # ── Check cache ───────────────────────────────────────────────────────────
    # Cache key includes days so different windows don't collide
    cache_key = f"{ticker}_{days}"
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
        "ticker": ticker, "buys": [], "sells": [],
        "buy_score": 0, "net_shares_30d": 0,
        "ceo_bought": False, "cfo_bought": False,
        "buy_count_90d": 0, "sell_count_90d": 0,
        "error": None,
        "cached_ts": datetime.utcnow().isoformat(),
    }

    try:
        t   = yf.Ticker(ticker)
        df  = t.insider_transactions
        if df is None or df.empty:
            _save_cache(cache_key, empty)
            return empty

        cutoff    = datetime.utcnow() - timedelta(days=days)
        cutoff_30 = datetime.utcnow() - timedelta(days=30)

        buys  = []
        sells = []
        buy_score     = 0
        net_shares_30 = 0
        ceo_bought    = False
        cfo_bought    = False

        for _, row in df.iterrows():
            text     = str(row.get("Text", "") or "")
            name     = str(row.get("Insider", "") or "")
            position = str(row.get("Position", "") or "")
            shares   = int(row.get("Shares", 0) or 0)
            value    = float(row.get("Value", 0) or 0)
            date_raw = row.get("Start Date")

            # Parse date
            try:
                if hasattr(date_raw, "to_pydatetime"):
                    trade_dt = date_raw.to_pydatetime().replace(tzinfo=None)
                else:
                    trade_dt = datetime.strptime(str(date_raw)[:10], "%Y-%m-%d")
            except Exception:
                continue

            if trade_dt < cutoff:
                continue

            weight = _position_weight(position)
            entry  = {
                "name":     name,
                "position": position,
                "shares":   shares,
                "value":    value,
                "date":     trade_dt.strftime("%Y-%m-%d"),
            }

            if _is_open_market_buy(text):
                buys.append({**entry, "weight": weight})
                buy_score += weight
                if trade_dt >= cutoff_30:
                    net_shares_30 += shares
                pos_lower = position.lower()
                if "chief executive" in pos_lower or "ceo" in pos_lower:
                    ceo_bought = True
                if "chief financial" in pos_lower or "cfo" in pos_lower:
                    cfo_bought = True

            elif _is_open_market_sell(text):
                sells.append(entry)
                if trade_dt >= cutoff_30:
                    net_shares_30 -= shares

        result = {
            "ticker":         ticker,
            "buys":           sorted(buys,  key=lambda x: x["date"], reverse=True),
            "sells":          sorted(sells, key=lambda x: x["date"], reverse=True),
            "buy_score":      buy_score,
            "net_shares_30d": net_shares_30,
            "ceo_bought":     ceo_bought,
            "cfo_bought":     cfo_bought,
            "buy_count_90d":  len(buys),
            "sell_count_90d": len(sells),
            "error":          None,
            "cached_ts":      datetime.utcnow().isoformat(),
        }
        _save_cache(cache_key, result)
        return result

    except Exception as e:
        empty["error"] = str(e)
        _save_cache(cache_key, empty)
        return empty
