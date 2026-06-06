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


# ── Basic financials (key ratios) ─────────────────────────────────────────────

def fetch_basic_financials(ticker: str, api_key: str = None) -> dict:
    """
    GET /stock/metric?symbol=X&metric=all

    Returns a rich set of financial ratios and growth rates — all in ONE call.
    Covers: PE, PB, PS, ROE, ROA, gross/net margins, current ratio,
    debt/equity, revenue & EPS growth (3yr/5yr), 52-week high/low, beta.

    Returns
    -------
    {
        "pe_ttm":            float | None,
        "pb":                float | None,   # price/book
        "ps_ttm":            float | None,   # price/sales TTM
        "roe_ttm":           float | None,   # % return on equity TTM
        "roa_ttm":           float | None,   # % return on assets
        "gross_margin_ttm":  float | None,   # %
        "net_margin_ttm":    float | None,   # %
        "current_ratio":     float | None,
        "debt_to_equity":    float | None,   # %
        "revenue_growth_3y": float | None,   # % CAGR
        "revenue_growth_5y": float | None,   # % CAGR
        "eps_growth_3y":     float | None,   # % CAGR
        "eps_growth_5y":     float | None,   # % CAGR
        "eps_ttm":           float | None,
        "week_52_high":      float | None,
        "week_52_low":       float | None,
        "beta":              float | None,
        "dividend_yield":    float | None,   # decimal (e.g. 0.0041)
        "error":             None | str,
    }
    """
    key = api_key or get_api_key()
    _empty = {
        "pe_ttm": None, "pb": None, "ps_ttm": None, "roe_ttm": None,
        "roa_ttm": None, "gross_margin_ttm": None, "net_margin_ttm": None,
        "current_ratio": None, "debt_to_equity": None,
        "revenue_growth_3y": None, "revenue_growth_5y": None,
        "eps_growth_3y": None, "eps_growth_5y": None, "eps_ttm": None,
        "week_52_high": None, "week_52_low": None, "beta": None,
        "dividend_yield": None,
    }
    if not key:
        return {**_empty, "error": "No Finnhub API key"}

    data = _get("stock/metric", {"symbol": ticker.upper(), "metric": "all"}, key)
    if isinstance(data, dict) and data.get("_error"):
        return {**_empty, "error": data["_error"]}

    m = (data.get("metric") or {}) if isinstance(data, dict) else {}

    def _v(key_name):
        val = m.get(key_name)
        return float(val) if val is not None else None

    return {
        "pe_ttm":            _v("peTTM") or _v("peBasicExclExtraTTM"),
        "pb":                _v("pbAnnual"),
        "ps_ttm":            _v("psTTM"),
        "roe_ttm":           _v("roeTTM") or _v("roeRfy"),
        "roa_ttm":           _v("roaRfy") or _v("roaTTM"),
        "gross_margin_ttm":  _v("grossMarginTTM") or _v("grossMarginAnnual"),
        "net_margin_ttm":    _v("netProfitMarginTTM") or _v("netProfitMarginAnnual"),
        "current_ratio":     _v("currentRatioAnnual") or _v("currentRatioQuarterly"),
        "debt_to_equity":    _v("totalDebt/totalEquityAnnual"),
        "revenue_growth_3y": _v("revenueGrowth3Y"),
        "revenue_growth_5y": _v("revenueGrowth5Y"),
        "eps_growth_3y":     _v("epsGrowth3Y"),
        "eps_growth_5y":     _v("epsGrowth5Y"),
        "eps_ttm":           _v("epsTTM") or _v("epsBasicExclExtraItemsTTM"),
        "week_52_high":      _v("52WeekHigh"),
        "week_52_low":       _v("52WeekLow"),
        "beta":              _v("beta"),
        "dividend_yield":    _v("dividendYieldIndicatedAnnual"),
        "error":             None,
    }


# ── Analyst recommendation trends ──────────────────────────────────────────────

def fetch_recommendation_trends(ticker: str, api_key: str = None) -> dict:
    """
    GET /stock/recommendation?symbol=X

    Returns analyst consensus as COUNTS (strong-buy / buy / hold / sell /
    strong-sell) for the most recent period.  Much richer than a single
    rating string — allows a continuous weighted score.

    Returns
    -------
    {
        "strong_buy":   int,
        "buy":          int,
        "hold":         int,
        "sell":         int,
        "strong_sell":  int,
        "total":        int,
        "period":       str,   # e.g. "2024-01-01"
        "error":        None | str,
    }
    """
    key = api_key or get_api_key()
    _empty = {"strong_buy": 0, "buy": 0, "hold": 0,
              "sell": 0, "strong_sell": 0, "total": 0, "period": ""}
    if not key:
        return {**_empty, "error": "No Finnhub API key"}

    data = _get("stock/recommendation", {"symbol": ticker.upper()}, key)
    if isinstance(data, dict) and data.get("_error"):
        return {**_empty, "error": data["_error"]}

    if not isinstance(data, list) or not data:
        return {**_empty, "error": None}

    latest = data[0]   # most recent period
    sb  = int(latest.get("strongBuy",   0) or 0)
    b   = int(latest.get("buy",         0) or 0)
    h   = int(latest.get("hold",        0) or 0)
    s   = int(latest.get("sell",        0) or 0)
    ss  = int(latest.get("strongSell",  0) or 0)

    return {
        "strong_buy":   sb,
        "buy":          b,
        "hold":         h,
        "sell":         s,
        "strong_sell":  ss,
        "total":        sb + b + h + s + ss,
        "period":       latest.get("period", ""),
        "error":        None,
    }


# ── Analyst price target ───────────────────────────────────────────────────────

def fetch_price_target(ticker: str, api_key: str = None) -> dict:
    """
    DISABLED — /stock/price-target is a PAID Finnhub endpoint (403 on the free tier).

    This stub returns immediately WITHOUT making any network request, so it can
    never hit the paid endpoint or consume a slot from the 55/min rate-limit
    budget. Analyst price targets come from yfinance (targetMeanPrice) instead —
    see fundamentals.py (result["analyst_target"] = info.get("targetMeanPrice")).

    To re-enable on a paid plan, restore the GET /stock/price-target?symbol=X call
    and parse targetHigh / targetLow / targetMean / targetMedian / lastUpdated.

    Returns
    -------
    {
        "target_high":    None,
        "target_low":     None,
        "target_mean":    None,
        "target_median":  None,
        "last_updated":   None,
        "error":          "price-target disabled (paid Finnhub endpoint)",
    }
    """
    return {
        "target_high":   None,
        "target_low":    None,
        "target_mean":   None,
        "target_median": None,
        "last_updated":  None,
        "error":         "price-target disabled (paid Finnhub endpoint)",
    }


# ── Insider transactions (Form 4) ─────────────────────────────────────────────

def fetch_insider_transactions(ticker: str, api_key: str = None) -> dict:
    """
    GET /stock/insider-transactions?symbol=X&from=YYYY-MM-DD

    Returns open-market insider purchases and sales (last 90 days).
    Excludes derivative transactions (options exercises).

    Returns
    -------
    {
        "buy_count":   int,            # open-market purchases
        "sell_count":  int,            # open-market sales
        "buy_value":   float,          # total $ value of purchases
        "executives":  list[str],      # names of buyers (up to 5)
        "error":       None | str,
    }
    """
    key = api_key or get_api_key()
    _empty = {"buy_count": 0, "sell_count": 0, "buy_value": 0.0, "executives": []}
    if not key:
        return {**_empty, "error": "No Finnhub API key"}

    from_date = (date.today() - timedelta(days=90)).isoformat()
    data = _get("stock/insider-transactions", {
        "symbol": ticker.upper(),
        "from":   from_date,
        "to":     date.today().isoformat(),
    }, key)

    if isinstance(data, dict) and data.get("_error"):
        return {**_empty, "error": data["_error"]}

    transactions = []
    if isinstance(data, dict):
        transactions = data.get("data") or []

    buy_count  = 0
    sell_count = 0
    buy_value  = 0.0
    executives: list = []

    for txn in transactions:
        if txn.get("isDerivative"):
            continue           # skip options exercises — not real money
        code   = txn.get("transactionCode", "")
        change = txn.get("change") or 0
        price  = txn.get("transactionPrice") or 0
        name   = txn.get("name", "")

        if code == "P" and change > 0:      # open-market PURCHASE
            buy_count  += 1
            buy_value  += float(change) * float(price)
            if name and name not in executives:
                executives.append(name)
        elif code == "S" and change < 0:    # open-market SALE
            sell_count += 1

    return {
        "buy_count":   buy_count,
        "sell_count":  sell_count,
        "buy_value":   round(buy_value, 2),
        "executives":  executives[:5],
        "error":       None,
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
            "sentiment":        fetch_news_sentiment(ticker,         api_key=key),
            "earnings":         fetch_earnings_surprise(ticker,      api_key=key),
            "basic_financials": fetch_basic_financials(ticker,       api_key=key),
            "recommendation":   fetch_recommendation_trends(ticker,  api_key=key),
            "insider":          fetch_insider_transactions(ticker,   api_key=key),
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
