"""
Finnhub client — free tier.
Free API key: https://finnhub.io  (register → API Dashboard)

Free tier limit : 60 calls / minute
Our hard cap    : 55 calls / minute  ← enforced with a sliding-window lock.
                  Any thread that would push us past 55 is blocked (not dropped)
                  until the window clears.

Endpoints used (all free tier):
  GET /api/v1/company-news    → recent headlines (keyword sentiment applied)
  GET /api/v1/stock/earnings  → quarterly EPS actual vs estimate (surprise %)

Note: /api/v1/news-sentiment (pre-computed NLP) requires a paid Finnhub plan.
      We get real Finnhub headlines and apply keyword scoring — still better than
      yfinance because Finnhub returns far more articles (100–250 vs ~15).
"""

import json
import os
import threading
import time
from collections import deque
from datetime import date, timedelta

import requests

_DIR       = os.path.dirname(os.path.abspath(__file__))
_KEYS_FILE = os.path.join(_DIR, "api_keys.json")
_BASE      = "https://finnhub.io/api/v1"

# ── Hard rate-limit: 55 calls per 60 seconds ──────────────────────────────────
#
#  Sliding-window approach:
#    _CALL_TIMES holds the monotonic timestamp of every call made in the
#    last 60 s.  Before each request we:
#      1. Purge entries older than 60 s.
#      2. If len < 55  → record timestamp and proceed immediately.
#      3. If len == 55 → compute when the oldest entry falls out of the
#         window, sleep exactly that long (+ 50 ms buffer), then retry.
#  The lock is released during the sleep so other threads can check/queue.

_RATE_LOCK   = threading.Lock()
_CALL_TIMES  = deque()   # monotonic timestamps of recent API calls
_MAX_PER_MIN = 55        # HARD STOP — never exceed this


def _acquire_slot():
    """
    Block the caller until a call slot is available within the 55/min budget.
    Returns only after the slot has been recorded (i.e., caller is clear to go).
    Thread-safe; multiple threads share the same sliding window.
    """
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            # Remove calls that left the 60-second window
            while _CALL_TIMES and now - _CALL_TIMES[0] >= 60.0:
                _CALL_TIMES.popleft()

            if len(_CALL_TIMES) < _MAX_PER_MIN:
                _CALL_TIMES.append(now)
                return          # ← slot secured, proceed

            # Window is full — find how long until the oldest slot expires
            wait_until = _CALL_TIMES[0] + 60.0

        # Release the lock while sleeping so other threads can also check
        sleep_for = max(0.0, wait_until - time.monotonic()) + 0.05
        time.sleep(sleep_for)
        # Re-enter the loop and re-check under the lock


# ── API key helpers ────────────────────────────────────────────────────────────

def get_api_key() -> str:
    try:
        with open(_KEYS_FILE) as f:
            return json.load(f).get("finnhub_api_key", "")
    except Exception:
        return ""


# ── Internal request wrapper ───────────────────────────────────────────────────

def _get(endpoint: str, params: dict, api_key: str):
    """
    Rate-limited GET to Finnhub.
    Returns parsed JSON (dict or list) on success, or {"_error": str} on failure.
    On a 429 response we back off 15 s and retry once (belt-and-suspenders).
    """
    _acquire_slot()
    try:
        r = requests.get(
            f"{_BASE}/{endpoint}",
            params={**params, "token": api_key},
            timeout=10,
        )
        if r.status_code == 429:
            # Shouldn't happen with our limiter, but handle defensively
            time.sleep(15)
            _acquire_slot()
            r = requests.get(
                f"{_BASE}/{endpoint}",
                params={**params, "token": api_key},
                timeout=10,
            )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"_error": str(exc)}


# ── News sentiment ─────────────────────────────────────────────────────────────

def fetch_news_sentiment(ticker: str, api_key: str = None) -> dict:
    """
    Fetch news headlines from Finnhub then score them with FinBERT
    (full-sentence NLP).  Falls back to keyword scoring if FinBERT
    is not loaded yet.

    Uses ONE Finnhub call: /company-news (last 7 days)

    Returns
    -------
    {
        "sentiment_score":  float,   # confidence-weighted net (FinBERT) or int (keywords)
        "positive_count":   int,
        "negative_count":   int,
        "total_articles":   int,
        "headlines":        [str],   # up to 8 recent headlines
        "sentiment_method": str,     # "finbert" | "keywords"
        "avg_confidence":   float,   # FinBERT only, else 0
        "error":            None | str,
    }
    """
    key = api_key or get_api_key()
    if not key:
        return {
            "error": "No Finnhub API key",
            "sentiment_score": None, "positive_count": 0,
            "negative_count": 0, "total_articles": 0,
            "headlines": [], "sentiment_method": None, "avg_confidence": 0,
        }

    today    = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    news = _get("company-news", {
        "symbol": ticker.upper(),
        "from": week_ago,
        "to": today,
    }, key)

    if isinstance(news, dict) and news.get("_error"):
        return {
            "error": news["_error"],
            "sentiment_score": None, "positive_count": 0,
            "negative_count": 0, "total_articles": 0,
            "headlines": [], "sentiment_method": None, "avg_confidence": 0,
        }

    # Cap at 10 headlines for FinBERT — 50 headlines × CPU inference = ~8s/stock
    # 10 headlines is sufficient for sentiment signal and 5× faster
    headlines = []
    for item in (news if isinstance(news, list) else [])[:10]:
        h = item.get("headline", "")
        if h:
            headlines.append(h)

    if not headlines:
        return {
            "error": None, "sentiment_score": 0, "positive_count": 0,
            "negative_count": 0, "total_articles": 0,
            "headlines": [], "sentiment_method": "none", "avg_confidence": 0,
        }

    # ── Try FinBERT first (full-sentence NLP) ─────────────────────────────────
    try:
        from finbert_sentiment import score_headlines, is_available
        if is_available():
            result = score_headlines(headlines)
            if not result.get("error"):
                return {
                    "sentiment_score":  result["sentiment_score"],
                    "positive_count":   result["positive_count"],
                    "negative_count":   result["negative_count"],
                    "total_articles":   result["total_scored"],
                    "headlines":        headlines[:8],
                    "sentiment_method": "finbert",
                    "avg_confidence":   result["avg_confidence"],
                    "error":            None,
                }
    except Exception:
        pass

    # ── Keyword fallback (if FinBERT not loaded yet) ──────────────────────────
    _POS = {
        "beat","beats","surge","surges","record","upgrade","upgraded",
        "approved","approval","strong","exceeds","raises","raised",
        "profit","growth","bullish","partnership","deal","wins","win",
        "outperforms","outperform","positive","recovery","accelerates",
        "rallies","rally","soars","soar","jumps","jump","boosts","boost",
        "breakthrough","expands","expansion","dividend","buyback",
    }
    _NEG = {
        "miss","misses","missed","decline","declines","loss","losses",
        "cut","cuts","downgrade","downgraded","lawsuit","sue","sued",
        "investigation","recall","disappoints","disappointing","layoffs",
        "layoff","bankruptcy","fraud","warning","weak","bearish","crash",
        "plunge","plunges","probe","suspended","halted","tumbles","falls",
        "slump","slumps","concern","concerns","risk","penalty","fine",
        "breach","hack","default","shortfall","slowdown",
    }
    pos = neg = 0
    for h in headlines:
        words = set(h.lower().replace("-", " ").split())
        p, n  = len(words & _POS), len(words & _NEG)
        if p > n:   pos += 1
        elif n > p: neg += 1

    return {
        "sentiment_score":  pos - neg,
        "positive_count":   pos,
        "negative_count":   neg,
        "total_articles":   len(headlines),
        "headlines":        headlines[:8],
        "sentiment_method": "keywords",
        "avg_confidence":   0,
        "error":            None,
    }


# ── Earnings surprise ──────────────────────────────────────────────────────────

def fetch_earnings_surprise(ticker: str, api_key: str = None) -> dict:
    """
    Fetch the most recent quarterly earnings surprise from Finnhub.

    Uses ONE call: /stock/earnings

    Returns
    -------
    {
        "surprise_pct": float | None,  # (actual − estimate) / |estimate| × 100
        "actual_eps":   float | None,
        "est_eps":      float | None,
        "quarter":      str | None,    # e.g. "2024-12-31"
        "error":        None | str,
    }
    """
    key = api_key or get_api_key()
    if not key:
        return {
            "error": "No Finnhub API key",
            "surprise_pct": None, "actual_eps": None,
            "est_eps": None, "quarter": None,
        }

    data = _get("stock/earnings", {"symbol": ticker.upper(), "limit": 4}, key)

    if isinstance(data, dict) and data.get("_error"):
        return {
            "error": data["_error"],
            "surprise_pct": None, "actual_eps": None,
            "est_eps": None, "quarter": None,
        }

    if not isinstance(data, list) or not data:
        return {
            "error": None,
            "surprise_pct": None, "actual_eps": None,
            "est_eps": None, "quarter": None,
        }

    latest  = data[0]
    actual  = latest.get("actual")
    est     = latest.get("estimate")
    period  = latest.get("period", "")

    surprise_pct = None
    if actual is not None and est is not None and est != 0:
        surprise_pct = round((float(actual) - float(est)) / abs(float(est)) * 100, 2)

    return {
        "surprise_pct": surprise_pct,
        "actual_eps":   actual,
        "est_eps":      est,
        "quarter":      period,
        "error":        None,
    }


# ── Batch helper ───────────────────────────────────────────────────────────────

def fetch_batch(tickers: list, api_key: str = None) -> dict:
    """
    Fetch sentiment + earnings surprise for a list of tickers.
    Rate limiting (55/min) is handled automatically per call.

    Returns
    -------
    {
        "AAPL": {
            "sentiment": { ... },   # from fetch_news_sentiment
            "earnings":  { ... },   # from fetch_earnings_surprise
        },
        ...
    }
    """
    key = api_key or get_api_key()
    results = {}
    for ticker in tickers:
        results[ticker] = {
            "sentiment": fetch_news_sentiment(ticker, api_key=key),
            "earnings":  fetch_earnings_surprise(ticker, api_key=key),
        }
    return results


# ── Rate-limit status (for diagnostics / UI) ──────────────────────────────────

def rate_limit_status() -> dict:
    """
    Return current rate-limit window status (useful for debugging or UI display).
    """
    with _RATE_LOCK:
        now = time.monotonic()
        while _CALL_TIMES and now - _CALL_TIMES[0] >= 60.0:
            _CALL_TIMES.popleft()
        used = len(_CALL_TIMES)
    return {
        "calls_used_last_60s": used,
        "calls_remaining":     _MAX_PER_MIN - used,
        "hard_limit":          _MAX_PER_MIN,
    }
