"""
Google News RSS — free, no-key news aggregation for a SINGLE ticker.

Google's news aggregation returns far more headlines than yfinance or Finnhub
(≈100 vs ≈10 for a large-cap over 7 days) from a much wider publisher set.  The
RSS search endpoint is public and meant to be consumed by readers:

    https://news.google.com/rss/search?q=...

⚠️  ON-DEMAND, SINGLE-STOCK USE ONLY (Fundamental Analysis, Watchlist, etc.).
    Do NOT call this inside the bulk market scan — thousands of requests from one
    IP would get throttled/blocked, and Google gives no rate-limit guarantees.

Sentiment reuses the project's FinBERT scorer (finbert_sentiment.score_headlines)
when the model is loaded, with a compact keyword fallback otherwise (the same
word lists used by finnhub_client.fetch_news_sentiment).

Results are cached in cache_db (namespace "google_news", 30-min TTL) so Streamlit
reruns don't re-hit Google on every widget interaction.
"""

import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
import defusedxml.ElementTree as ET

import cache_db

_NAMESPACE = "google_news"
_TTL       = 1800   # 30 minutes
_UA        = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
_SENT_CAP  = 10     # headlines fed to FinBERT — matches finnhub_client; signal saturates here

# Keyword fallback (mirrors finnhub_client.fetch_news_sentiment) — only used when FinBERT is off.
_POS = {
    "beat", "beats", "surge", "surges", "record", "upgrade", "upgraded",
    "approved", "approval", "strong", "exceeds", "raises", "raised",
    "profit", "growth", "bullish", "partnership", "deal", "wins", "win",
    "outperforms", "outperform", "positive", "recovery", "accelerates",
    "rallies", "rally", "soars", "soar", "jumps", "jump", "boosts", "boost",
    "breakthrough", "expands", "expansion", "dividend", "buyback", "high",
}
_NEG = {
    "miss", "misses", "missed", "decline", "declines", "loss", "losses",
    "cut", "cuts", "downgrade", "downgraded", "lawsuit", "sue", "sued",
    "investigation", "recall", "disappoints", "disappointing", "layoffs",
    "layoff", "bankruptcy", "fraud", "warning", "weak", "bearish", "crash",
    "plunge", "plunges", "probe", "suspended", "halted", "tumbles", "falls",
    "slump", "slumps", "concern", "concerns", "risk", "penalty", "fine",
    "breach", "hack", "default", "shortfall", "slowdown", "sliding", "slides",
}

_SUFFIX_RE = re.compile(
    r"\b(Inc\.?|Corp\.?|Corporation|Company|Co\.?|Ltd\.?|L\.?P\.?|PLC|"
    r"Holdings?|Group|Incorporated|Class\s+[A-Z])\b",
    re.IGNORECASE,
)


def _keyword_sentiment(titles: list) -> dict:
    pos = neg = 0
    for h in titles:
        words = set(h.lower().replace("-", " ").split())
        p, n = len(words & _POS), len(words & _NEG)
        if p > n:
            pos += 1
        elif n > p:
            neg += 1
    return {
        "sentiment_score": pos - neg,
        "positive_count":  pos,
        "negative_count":  neg,
        "neutral_count":   len(titles) - pos - neg,
        "total_scored":    len(titles),
        "method":          "keywords",
        "error":           None,
    }


def _sentiment(titles: list) -> dict:
    titles = [t for t in titles if t][:_SENT_CAP]
    if not titles:
        return {"sentiment_score": 0, "positive_count": 0, "negative_count": 0,
                "neutral_count": 0, "total_scored": 0, "method": "none", "error": None}
    try:
        from finbert_sentiment import score_headlines, is_available
        if is_available():
            res = score_headlines(titles)
            if not res.get("error"):
                res.setdefault("method", "finbert")
                return res
    except Exception:
        pass
    return _keyword_sentiment(titles)


def _parse_pubdate(s: str):
    """RFC-822 pubDate -> ('YYYY-MM-DD HH:MM', epoch_seconds). ('', 0.0) on failure."""
    if not s:
        return "", 0.0
    try:
        dt = parsedate_to_datetime(s)
        return dt.strftime("%Y-%m-%d %H:%M"), dt.timestamp()
    except Exception:
        return s[:16], 0.0


def _clean_company(name: str) -> str:
    """Trim corporate suffixes so the news query matches more broadly."""
    if not name:
        return ""
    return _SUFFIX_RE.sub("", name).strip(" ,.&")


def fetch_google_news(ticker: str, company_name: str = None, days: int = 7,
                      limit: int = 30, use_cache: bool = True) -> dict:
    """
    Fetch recent Google News headlines for one ticker.

    Returns
    -------
    {
        "items": [ {"title","source","url","published","published_ts"}, ... ],
        "count": int,
        "sentiment": { sentiment_score, positive_count, negative_count, method, ... },
        "fetched_at": iso-str,
        "error": None | str,
    }
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"items": [], "count": 0, "sentiment": _sentiment([]),
                "fetched_at": None, "error": "no ticker"}

    if use_cache:
        cached = cache_db.get(_NAMESPACE, ticker)
        if cached and (time.time() - cached.get("_ts", 0) < _TTL):
            return cached

    cn = _clean_company(company_name)
    query = (f'"{cn}" {ticker} stock when:{days}d' if cn
             else f'{ticker} stock when:{days}d')
    url = (f"https://news.google.com/rss/search?q={quote_plus(query)}"
           f"&hl=en-US&gl=US&ceid=US:en")

    try:
        r = requests.get(url, headers=_UA, timeout=12)
        if r.status_code != 200:
            return {"items": [], "count": 0, "sentiment": _sentiment([]),
                    "fetched_at": None, "error": f"HTTP {r.status_code}"}
        root = ET.fromstring(r.content)
    except Exception as e:
        return {"items": [], "count": 0, "sentiment": _sentiment([]),
                "fetched_at": None, "error": str(e)}

    items, seen = [], set()
    for it in root.findall("./channel/item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        source = (it.findtext("source") or "").strip()
        # Google appends " - Publisher" to each title — strip it for a clean headline.
        if source and title.endswith(source):
            title = title[: -len(source)].rstrip(" -–|")
        dedupe_key = title.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        published, pub_ts = _parse_pubdate(it.findtext("pubDate") or "")
        items.append({
            "title":        title,
            "source":       source or "—",
            "url":          (it.findtext("link") or "").strip(),
            "published":    published,
            "published_ts": pub_ts,
        })

    items.sort(key=lambda x: x["published_ts"], reverse=True)
    items = items[:limit]

    result = {
        "items":      items,
        "count":      len(items),
        "sentiment":  _sentiment([i["title"] for i in items]),
        "fetched_at": datetime.utcnow().isoformat(),
        "error":      None,
        "_ts":        time.time(),
    }
    if use_cache and items:
        cache_db.set(_NAMESPACE, ticker, result)
    return result
