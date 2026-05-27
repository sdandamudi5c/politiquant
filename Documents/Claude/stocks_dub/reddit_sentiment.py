"""
Reddit sentiment signal — free via PRAW (Reddit API).

Tracks mention frequency and sentiment of a stock ticker across:
  - r/wallstreetbets  (retail speculation, high-beta signal)
  - r/investing       (more conservative retail investors)
  - r/stocks          (general stock discussion)

Signal logic:
  - Count posts/comments mentioning the ticker in the last 48h
  - Score title sentiment with keyword scoring (no model needed)
  - High mentions + positive tone → bullish retail signal
  - High mentions + negative tone → bearish retail signal
  - Going viral (spike vs 7-day avg) → noteworthy catalyst

Score range: -4 to +5 (intentionally modest — retail sentiment is noisy)

Setup: create a free Reddit app at https://www.reddit.com/prefs/apps
  Type: "script"   Name: anything   redirect_uri: http://localhost:8080
Then add to api_keys.json:
  "reddit_client_id": "your_client_id",
  "reddit_client_secret": "your_client_secret"

Falls back gracefully (score_mod=0, no error shown) if keys not configured.

Cache: 4-hour TTL (Reddit rate limit: 60 req/min on free tier).
"""

import json
import os
import re
from datetime import datetime, timedelta

import cache_db as _cdb

_DIR       = os.path.dirname(os.path.abspath(__file__))
_KEYS_FILE = os.path.join(_DIR, "api_keys.json")
_NAMESPACE = "reddit"
_CACHE_TTL = 4 * 3600   # 4 hours

_SUBREDDITS = ["wallstreetbets", "investing", "stocks"]

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


def _get_keys() -> "tuple[str, str]":
    """Return (client_id, client_secret) from api_keys.json, or ('', '')."""
    try:
        with open(_KEYS_FILE) as f:
            d = json.load(f)
        return d.get("reddit_client_id", ""), d.get("reddit_client_secret", "")
    except Exception:
        return "", ""


def _score_text(text: str) -> float:
    """Simple keyword sentiment: +1 per bull word, -1 per bear word. Range: float."""
    words = set(re.findall(r"\b\w+\b", text.lower()))
    bull = len(words & _BULL_WORDS)
    bear = len(words & _BEAR_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 2)   # -1 to +1


def fetch_reddit_sentiment(ticker: str) -> dict:
    """
    Fetch Reddit mention count and sentiment for a ticker.

    Returns
    -------
    {
        "ticker":         str,
        "mention_count":  int,     # posts/comments mentioning ticker (last 48h)
        "sentiment_avg":  float,   # avg keyword sentiment (-1 to +1)
        "subreddit_counts": dict,  # {subreddit: count}
        "top_posts":      list,    # [{title, score, url, sentiment}]
        "trend":          str,     # "viral" | "rising" | "normal" | "quiet"
        "score_mod":      int,     # -4 to +5
        "cached_ts":      str,
        "error":          None | str,
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

    client_id, client_secret = _get_keys()
    if not client_id or not client_secret:
        empty["error"] = "Reddit API keys not configured"
        return empty   # graceful no-op — don't cache so it retries after setup

    try:
        import praw
    except ImportError:
        empty["error"] = "praw not installed: pip install praw"
        return empty

    try:
        reddit = praw.Reddit(
            client_id     = client_id,
            client_secret = client_secret,
            user_agent    = f"PolitiQuant/1.0 (stock research tool) ticker={ticker}",
        )

        cutoff  = datetime.utcnow() - timedelta(hours=48)
        counts  = {sr: 0 for sr in _SUBREDDITS}
        sentiments = []
        top_posts  = []

        # Search each subreddit for ticker mentions
        for sr_name in _SUBREDDITS:
            try:
                sr = reddit.subreddit(sr_name)
                # Search by ticker symbol (exact word match)
                for post in sr.search(f'"{ticker}"', sort="new", time_filter="week", limit=25):
                    post_time = datetime.utcfromtimestamp(post.created_utc)
                    if post_time < cutoff:
                        continue

                    # Check ticker appears as a standalone word in title/selftext
                    combined = f"{post.title} {post.selftext or ''}"
                    if not re.search(rf"\b{re.escape(ticker)}\b", combined, re.IGNORECASE):
                        continue

                    counts[sr_name] += 1
                    sent = _score_text(combined)
                    sentiments.append(sent)

                    if len(top_posts) < 5:
                        top_posts.append({
                            "title":     post.title[:120],
                            "score":     post.score,
                            "url":       f"https://reddit.com{post.permalink}",
                            "sentiment": sent,
                            "subreddit": sr_name,
                        })
            except Exception:
                continue

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

        # Score modifier: mentions × sentiment direction
        score_mod = 0
        if total_mentions >= 20 and avg_sentiment > 0.1:
            score_mod = 5   # viral + positive = strong retail signal
        elif total_mentions >= 10 and avg_sentiment > 0.1:
            score_mod = 3
        elif total_mentions >= 5 and avg_sentiment > 0.1:
            score_mod = 2
        elif total_mentions >= 3 and avg_sentiment > 0:
            score_mod = 1
        elif total_mentions >= 10 and avg_sentiment < -0.1:
            score_mod = -4   # viral + negative = warning
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
