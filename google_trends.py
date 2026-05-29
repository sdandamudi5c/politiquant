"""
Google Trends interest for a stock ticker — free via pytrends.

Rising search interest in a company often precedes price moves,
especially for consumer-facing or retail-investor-heavy stocks.

Signal logic:
  - Compare last 4 weeks avg vs prior 4 weeks avg
  - "Trending up"   (>25% increase) → small positive signal
  - "Trending down" (>25% decrease) → small negative signal
  - Spike (last week vs 8-week avg > 2×) → noteworthy catalyst

Cache: 24-hour TTL (trends data is daily, not real-time).
"""

from datetime import datetime

import cache_db as _cdb

_NAMESPACE  = "trends"
_CACHE_TTL  = 24 * 3600   # 24 hours


def _load_cache(key: str) -> "dict | None":
    return _cdb.get(_NAMESPACE, key)


def _save_cache(key: str, data: dict) -> None:
    _cdb.set(_NAMESPACE, key, data)


def fetch_trends(ticker: str) -> dict:
    """
    Fetch Google Trends interest for a ticker over the last 90 days.

    Returns
    -------
    {
        "ticker":        str,
        "trend":         str,   # "rising" | "falling" | "stable" | "spike" | "unknown"
        "pct_change":    float, # last 4wk avg vs prior 4wk avg
        "spike_ratio":  float,  # last week vs 8-week avg
        "recent_avg":   float,  # last 4-week average interest (0–100)
        "weekly_data":  list,   # list of {date, value} for charting
        "score_mod":    int,    # +2 rising, -2 falling, +4 spike, 0 stable
        "cached_ts":    str,
        "error":        None | str,
    }
    """
    cached = _load_cache(ticker) or {}
    if cached.get("cached_ts"):
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(cached["cached_ts"])).total_seconds()
            if age < _CACHE_TTL:
                return cached
        except Exception:
            pass

    empty = {
        "ticker": ticker, "trend": "unknown", "pct_change": 0.0,
        "spike_ratio": 1.0, "recent_avg": 0.0, "weekly_data": [],
        "score_mod": 0, "cached_ts": datetime.utcnow().isoformat(), "error": None,
    }

    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25), retries=2, backoff_factor=0.5)
        pt.build_payload([ticker], cat=0, timeframe="today 3-m", geo="US")
        df = pt.interest_over_time()

        if df is None or df.empty or ticker not in df.columns:
            empty["error"] = "No Google Trends data available for this ticker"
            _save_cache(ticker, empty)
            return empty

        series = df[ticker].dropna().astype(float)
        if len(series) < 4:
            empty["error"] = "Insufficient trend data"
            _save_cache(ticker, empty)
            return empty

        vals = list(series.values)
        n    = len(vals)

        recent4  = sum(vals[max(n-4, 0):]) / min(4, n)
        prior4   = sum(vals[max(n-8, 0):max(n-4, 0)]) / min(4, max(n-4, 0)) if n > 4 else recent4
        last1    = vals[-1]
        avg8     = sum(vals[max(n-8, 0):]) / min(8, n)

        pct_change  = round((recent4 - prior4) / max(prior4, 1) * 100, 1)
        spike_ratio = round(last1 / max(avg8, 1), 2)

        if spike_ratio >= 2.0:
            trend     = "spike"
            score_mod = 4
        elif pct_change >= 25:
            trend     = "rising"
            score_mod = 2
        elif pct_change <= -25:
            trend     = "falling"
            score_mod = -2
        else:
            trend     = "stable"
            score_mod = 0

        # Weekly data for chart
        weekly = []
        for idx, val in zip(df.index[-12:], vals[-12:]):
            weekly.append({"date": str(idx)[:10], "value": int(val)})

        result = {
            "ticker":      ticker,
            "trend":       trend,
            "pct_change":  pct_change,
            "spike_ratio": spike_ratio,
            "recent_avg":  round(recent4, 1),
            "weekly_data": weekly,
            "score_mod":   score_mod,
            "cached_ts":   datetime.utcnow().isoformat(),
            "error":       None,
        }
        _save_cache(ticker, result)
        return result

    except Exception as e:
        empty["error"] = str(e)
        _save_cache(ticker, empty)
        return empty
