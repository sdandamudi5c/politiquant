"""
Reddit sentiment signal — no API key needed.

Uses Reddit's public JSON endpoints (available on every subreddit without
authentication). No PRAW, no OAuth, no app registration required.

Tracks mention frequency and sentiment across:
  - r/wallstreetbets  (retail speculation, high-beta signal)
  - r/investing       (more conservative retail investors)
  - r/stocks          (general stock discussion)

Signal logic:
  - Search each subreddit for the ticker symbol (last 7 days)
  - Keyword sentiment scoring on post titles
  - High mentions + positive tone → bullish retail signal
  - High mentions + negative tone → bearish retail signal

Score range: -4 to +5 (intentionally modest — retail sentiment is noisy)

Cache: 4-hour TTL.
"""

import re
import time
from datetime import datetime, timedelta

import requests

import cache_db as _cdb

_NAMESPACE  = "reddit"
_CACHE_TTL  = 4 * 3600   # 4 hours
_SUBREDDITS = ["wallstreetbets", "investing", "stocks"]

# Reddit requires a descriptive User-Agent — generic ones get rate-limited
_HEADERS = {
    "User-Agent": "PolitiQuant/1.0 (open-source stock research; no commercial use)",
    "Accept": "application/json",
}

# Keyword sentiment scoring
_BULL_WORDS = {
    "buy", "buying", "bull", "bullish", "calls", "long", "moon", "rocket",
    "upside", "breakout", "undervalued", "strong", "beat", "upgrade",
    "growth", "profit", "record", "surge", "rally", "squeeze",
}
_BEAR_WORDS = {
    "sell", "selling", "bear", "bearish", "puts", "short", "crash", "dump",
    "overvalued", "downgrade", "miss", "loss", "decline", "risk", "weak",
    "fraud", "lawsuit", "warning", "cut", "layoff", "bankrupt",
}


def _score_text(text: str) -> float:
    """Keyword sentiment: +1 per bull word, -1 per bear word → normalised -1 to +1."""
    words = set(re.findall(r"\b\w+\b", text.lower()))
    bull  = len(words & _BULL_WORDS)
    bear  = len(words & _BEAR_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 2)


def _search_subreddit(subreddit: str, ticker: str) -> list:
    """
    Search a subreddit for ticker mentions using the public JSON search API.
    Returns list of post dicts. No auth required.
    """
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    params = {
        "q":           ticker,
        "restrict_sr": "on",
        "sort":        "new",
        "t":           "week",
        "limit":       25,
    }
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        if resp.status_code == 429:   # rate limited — back off
            time.sleep(2)
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        posts = resp.json().get("data", {}).get("children", [])
        return [p["data"] for p in posts if p.get("data")]
    except Exception:
        return []


def fetch_reddit_sentiment(ticker: str) -> dict:
    """
    Fetch Reddit mention count and sentiment for a ticker.

    Returns
    -------
    {
        "ticker":           str,
        "mention_count":    int,     # posts mentioning ticker (last 7 days)
        "sentiment_avg":    float,   # avg keyword sentiment (-1 to +1)
        "subreddit_counts": dict,    # {subreddit: count}
        "top_posts":        list,    # [{title, score, url, sentiment, subreddit}]
        "trend":            str,     # "viral" | "rising" | "normal" | "quiet"
        "score_mod":        int,     # -4 to +5
        "cached_ts":        str,
        "error":            None | str,
    }
    """
    cached = _cdb.get(_NAMESPACE, ticker) or {}
    if cached.get("cached_ts"):
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(cached["cached_ts"])).total_seconds()
            if age < _CACHE_TTL:
                return cached
        except Exception:
            pass

    empty = {
        "ticker": ticker, "mention_count": 0, "sentiment_avg": 0.0,
        "subreddit_counts": {}, "top_posts": [],
        "trend": "unknown", "score_mod": 0,
        "cached_ts": datetime.utcnow().isoformat(), "error": None,
    }

    try:
        cutoff     = datetime.utcnow() - timedelta(days=7)
        counts     = {sr: 0 for sr in _SUBREDDITS}
        sentiments = []
        top_posts  = []

        for sr_name in _SUBREDDITS:
            posts = _search_subreddit(sr_name, ticker)
            for post in posts:
                # Filter to posts within the last 7 days
                created = datetime.utcfromtimestamp(post.get("created_utc", 0))
                if created < cutoff:
                    continue

                # Must contain ticker as a standalone word (not substring)
                title = post.get("title", "")
                if not re.search(rf"\b{re.escape(ticker)}\b", title, re.IGNORECASE):
                    continue

                counts[sr_name] += 1
                sent = _score_text(title)
                sentiments.append(sent)

                if len(top_posts) < 6:
                    top_posts.append({
                        "title":     title[:120],
                        "score":     post.get("score", 0),
                        "url":       f"https://reddit.com{post.get('permalink', '')}",
                        "sentiment": sent,
                        "subreddit": sr_name,
                    })

            # Small delay between subreddit requests to be polite
            time.sleep(0.5)

        total_mentions = sum(counts.values())
        avg_sentiment  = round(sum(sentiments) / len(sentiments), 2) if sentiments else 0.0

        # Classify trend
        if total_mentions >= 20:
            trend = "viral"
        elif total_mentions >= 8:
            trend = "rising"
        elif total_mentions >= 2:
            trend = "normal"
        else:
            trend = "quiet"

        # Score modifier: mention volume × sentiment direction
        score_mod = 0
        if total_mentions >= 20 and avg_sentiment > 0.1:
            score_mod = 5
        elif total_mentions >= 10 and avg_sentiment > 0.1:
            score_mod = 3
        elif total_mentions >= 5 and avg_sentiment > 0.1:
            score_mod = 2
        elif total_mentions >= 3 and avg_sentiment > 0:
            score_mod = 1
        elif total_mentions >= 10 and avg_sentiment < -0.1:
            score_mod = -4
        elif total_mentions >= 5 and avg_sentiment < -0.1:
            score_mod = -2
        elif total_mentions >= 3 and avg_sentiment < -0.1:
            score_mod = -1

        result = {
            "ticker":           ticker,
            "mention_count":    total_mentions,
            "sentiment_avg":    avg_sentiment,
            "subreddit_counts": counts,
            "top_posts":        sorted(top_posts, key=lambda x: x["score"], reverse=True),
            "trend":            trend,
            "score_mod":        score_mod,
            "cached_ts":        datetime.utcnow().isoformat(),
            "error":            None,
        }
        _cdb.set(_NAMESPACE, ticker, result)
        return result

    except Exception as e:
        empty["error"] = str(e)
        _cdb.set(_NAMESPACE, ticker, empty)
        return empty
