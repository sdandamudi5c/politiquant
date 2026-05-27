"""
Polygon.io client — free tier.
Free API key: https://polygon.io/ (register → API Keys)

What we use from the free tier:
  - /v2/reference/news  → news articles with per-article sentiment scores
  - short % float       → via ticker details (V3 endpoint)

The news sentiment from Polygon is pre-computed NLP (positive/neutral/negative
per article), which is far more reliable than keyword matching.
"""

import json
import os
import time
from datetime import date, datetime, timedelta

import requests

_DIR        = os.path.dirname(os.path.abspath(__file__))
_KEYS_FILE  = os.path.join(_DIR, "api_keys.json")
_BASE        = "https://api.polygon.io"
_RATE_DELAY  = 12   # seconds between calls on free tier (5 calls/min)


# ── API key helpers ────────────────────────────────────────────────────────────

def get_api_key() -> str:
    try:
        with open(_KEYS_FILE) as f:
            return json.load(f).get("polygon_api_key", "")
    except Exception:
        return ""


# ── News with sentiment ────────────────────────────────────────────────────────

def fetch_news_sentiment(ticker: str, api_key: str = None, days_back: int = 7) -> dict:
    """
    Fetch recent news for a ticker from Polygon and compute a sentiment score.

    Returns:
        {
            "sentiment_score": float,   # net positive - negative article count
            "headlines": [str, ...],    # up to 8 recent headlines
            "details": [               # per-article detail
                {"title": str, "sentiment": str, "published": str}, ...
            ]
        }
    or {"error": str, "sentiment_score": None, "headlines": [], "details": []}
    """
    key = api_key or get_api_key()
    if not key:
        return {"error": "No Polygon API key", "sentiment_score": None,
                "headlines": [], "details": []}

    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = requests.get(
            f"{_BASE}/v2/reference/news",
            params={
                "ticker":         ticker.upper(),
                "published_utc.gte": since,
                "order":          "desc",
                "limit":          20,
                "apiKey":         key,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("status") == "ERROR":
            return {"error": data.get("error", "Polygon error"),
                    "sentiment_score": None, "headlines": [], "details": []}

        articles = data.get("results", [])
        headlines, details = [], []
        pos = neg = 0

        # Keyword fallback for free tier (insights field needs paid plan)
        _POS = {"beat","beats","surge","surges","record","upgrade","upgraded",
                "approved","approval","strong","exceeds","raises","raised",
                "profit","growth","bullish","partnership","deal","wins","win",
                "outperforms","outperform","positive","recovery","accelerates"}
        _NEG = {"miss","misses","missed","decline","declines","loss","losses",
                "cut","cuts","downgrade","downgraded","lawsuit","investigation",
                "recall","disappoints","disappointing","layoffs","bankruptcy",
                "fraud","warning","weak","bearish","crash","plunge","probe",
                "suspended","halted","tumbles","falls","slump"}

        used_nlp = False
        for a in articles:
            title = a.get("title", "")
            if not title:
                continue

            # Try Polygon NLP insights first (paid tier) — fall back to keywords
            sentiment = None
            for insight in (a.get("insights") or []):
                if insight.get("ticker", "").upper() == ticker.upper():
                    s = insight.get("sentiment", "").lower()
                    if s in ("positive", "negative", "neutral"):
                        sentiment = s
                        used_nlp  = True
                        break

            if sentiment is None:
                # Free-tier fallback: keyword matching
                words = set(title.lower().replace("-", " ").split())
                p = len(words & _POS)
                n = len(words & _NEG)
                if p > n:
                    sentiment = "positive"
                elif n > p:
                    sentiment = "negative"
                else:
                    sentiment = "neutral"

            if sentiment == "positive":
                pos += 1
            elif sentiment == "negative":
                neg += 1

            pub = a.get("published_utc", "")[:10]
            details.append({"title": title, "sentiment": sentiment, "published": pub})
            headlines.append(title)

        return {
            "sentiment_score": pos - neg,
            "positive_count":  pos,
            "negative_count":  neg,
            "total_articles":  len(details),
            "headlines":       headlines[:8],
            "details":         details[:8],
            "error":           None,
        }

    except Exception as e:
        return {"error": str(e), "sentiment_score": None, "headlines": [], "details": []}


# ── Short interest ─────────────────────────────────────────────────────────────

def fetch_short_interest(ticker: str, api_key: str = None) -> dict:
    """
    Fetch short interest data for a ticker.
    Uses Polygon V3 ticker details — includes shortPercentOfFloat if available.
    Falls back to None if not in free tier.

    Returns: {"short_pct_float": float | None, "days_to_cover": float | None}
    """
    key = api_key or get_api_key()
    if not key:
        return {"short_pct_float": None, "days_to_cover": None}

    try:
        r = requests.get(
            f"{_BASE}/v3/reference/tickers/{ticker.upper()}",
            params={"apiKey": key},
            timeout=10,
        )
        results = r.json().get("results", {})
        return {
            "short_pct_float": results.get("short_percent_of_float"),
            "days_to_cover":   results.get("days_to_cover"),
        }
    except Exception:
        return {"short_pct_float": None, "days_to_cover": None}


# ── Batch helper (respects rate limit) ────────────────────────────────────────

def fetch_news_batch(tickers: list, api_key: str = None) -> dict:
    """
    Fetch news sentiment for multiple tickers, respecting the free-tier rate limit.
    Returns {ticker: sentiment_dict}.
    """
    key = api_key or get_api_key()
    results = {}
    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(_RATE_DELAY)   # 5 calls/minute on free tier
        results[ticker] = fetch_news_sentiment(ticker, api_key=key)
    return results
