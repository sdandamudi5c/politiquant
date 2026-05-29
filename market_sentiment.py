"""
Market Sentiment / Fear & Greed proxy — 100% free via yfinance.

Builds a composite sentiment score (0–100) from 5 signals, similar to
CNN's Fear & Greed Index but using freely available data:

  1. VIX level       — CBOE Volatility Index (^VIX). High = fear.
  2. SPY momentum    — SPY vs its 125-day SMA. Falling = fear.
  3. Market breadth  — % of S&P 500 ETF (SPY) rolling 20d highs proxy
                       using equal-weight vs cap-weight spread (RSP vs SPY)
  4. Junk bond demand— HYG (high-yield) vs LQD (investment-grade) spread.
                       When investors buy junk = greed.
  5. Safe haven flow — TLT (long bonds) vs SPY. Flight to bonds = fear.

Each signal scored 0–100, averaged to get composite.

Interpretation:
  0–25   Extreme Fear    → historically good time to buy
  26–45  Fear
  46–55  Neutral
  56–75  Greed
  76–100 Extreme Greed   → historically risky

Cache: 1-hour TTL.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import yfinance as yf

import cache_db as _cdb

_NAMESPACE  = "sentiment"
_CACHE_KEY  = "fear_greed"
_CACHE_TTL  = 3600   # 1 hour


def _load_cache() -> dict:
    return _cdb.get(_NAMESPACE, _CACHE_KEY) or {}


def _save_cache(data: dict) -> None:
    _cdb.set(_NAMESPACE, _CACHE_KEY, data)


def _bulk_download_all() -> dict:
    """Download all required tickers in one yf.download call. Returns dict keyed by ticker."""
    _ALL_TICKERS = ["^VIX", "SPY", "RSP", "HYG", "LQD", "TLT"]
    try:
        end   = datetime.utcnow()
        start = end - timedelta(days=200)
        raw = yf.download(
            _ALL_TICKERS,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True, group_by="ticker",
        )
        if raw is None or raw.empty:
            return {}
        result = {}
        for tk in _ALL_TICKERS:
            try:
                df = raw[tk] if tk in raw.columns.get_level_values(0) else None
                if df is not None and not df.empty:
                    result[tk] = df
            except Exception:
                pass
        return result
    except Exception:
        return {}


def _score_vix(dfs: dict) -> tuple[float, str, float]:
    """VIX: low = greed (high score), high = fear (low score)."""
    df = dfs.get("^VIX")
    if df is None or len(df) < 2:
        return 50.0, "N/A", None
    vix = float(df["Close"].iloc[-1])
    # VIX <12 = extreme complacency (greed=90), >35 = extreme fear (score=5)
    if vix <= 12:   score = 90
    elif vix <= 15: score = 75
    elif vix <= 20: score = 60
    elif vix <= 25: score = 45
    elif vix <= 30: score = 30
    elif vix <= 35: score = 15
    else:           score = 5
    label = f"VIX {vix:.1f}"
    return float(score), label, vix


def _score_spy_momentum(dfs: dict) -> tuple[float, str, float]:
    """SPY vs 125-day SMA: above = greed, below = fear."""
    df = dfs.get("SPY")
    if df is None or len(df) < 50:
        return 50.0, "N/A", None
    closes = df["Close"].dropna()
    spy    = float(closes.iloc[-1])
    sma125 = float(closes.tail(125).mean()) if len(closes) >= 125 else float(closes.mean())
    pct    = (spy - sma125) / sma125 * 100
    if pct >= 8:    score = 90
    elif pct >= 4:  score = 75
    elif pct >= 1:  score = 60
    elif pct >= -1: score = 45
    elif pct >= -4: score = 30
    elif pct >= -8: score = 15
    else:           score = 5
    label = f"SPY {pct:+.1f}% vs 125d SMA"
    return float(score), label, round(pct, 2)


def _score_breadth(dfs: dict) -> tuple[float, str, float]:
    """RSP (equal-weight) vs SPY (cap-weight) 20d return spread.
    When small/equal stocks outperform → broad participation → greed."""
    df_rsp = dfs.get("RSP")
    df_spy = dfs.get("SPY")
    if df_rsp is None or df_spy is None or len(df_rsp) < 22 or len(df_spy) < 22:
        return 50.0, "N/A", None
    def _ret(df):
        c = df["Close"].dropna()
        return (float(c.iloc[-1]) - float(c.iloc[-21])) / float(c.iloc[-21]) * 100
    spread = _ret(df_rsp) - _ret(df_spy)
    if spread >= 3:    score = 85
    elif spread >= 1:  score = 65
    elif spread >= -1: score = 50
    elif spread >= -3: score = 35
    else:              score = 15
    label = f"Breadth spread {spread:+.1f}%"
    return float(score), label, round(spread, 2)


def _score_junk_demand(dfs: dict) -> tuple[float, str, float]:
    """HYG/LQD 20d return spread: high-yield outperforming = risk appetite = greed."""
    df_hyg = dfs.get("HYG")
    df_lqd = dfs.get("LQD")
    if df_hyg is None or df_lqd is None or len(df_hyg) < 22 or len(df_lqd) < 22:
        return 50.0, "N/A", None
    def _ret(df):
        c = df["Close"].dropna()
        return (float(c.iloc[-1]) - float(c.iloc[-21])) / float(c.iloc[-21]) * 100
    spread = _ret(df_hyg) - _ret(df_lqd)
    if spread >= 2:    score = 85
    elif spread >= 0.5: score = 65
    elif spread >= -0.5: score = 50
    elif spread >= -2: score = 30
    else:              score = 10
    label = f"Junk vs IG spread {spread:+.2f}%"
    return float(score), label, round(spread, 3)


def _score_safe_haven(dfs: dict) -> tuple[float, str, float]:
    """TLT vs SPY 20d: bonds outperforming stocks = fear (low score)."""
    df_tlt = dfs.get("TLT")
    df_spy = dfs.get("SPY")
    if df_tlt is None or df_spy is None or len(df_tlt) < 22 or len(df_spy) < 22:
        return 50.0, "N/A", None
    def _ret(df):
        c = df["Close"].dropna()
        return (float(c.iloc[-1]) - float(c.iloc[-21])) / float(c.iloc[-21]) * 100
    # Bonds - Stocks: positive means flight to safety = fear
    spread = _ret(df_tlt) - _ret(df_spy)
    if spread >= 4:    score = 10    # heavy flight to bonds
    elif spread >= 1:  score = 30
    elif spread >= -1: score = 50
    elif spread >= -4: score = 70
    else:              score = 85    # stocks crushing bonds = greed
    label = f"Bonds vs Stocks spread {spread:+.1f}%"
    return float(score), label, round(spread, 2)


def _label(score: float) -> tuple[str, str]:
    """(text label, hex colour) for a 0–100 score."""
    if score <= 25:   return "😱 Extreme Fear",  "#e74c3c"
    elif score <= 45: return "😨 Fear",           "#e67e22"
    elif score <= 55: return "😐 Neutral",        "#aaaaaa"
    elif score <= 75: return "😏 Greed",          "#2ecc71"
    else:             return "🤑 Extreme Greed",  "#27ae60"


def fetch_market_sentiment() -> dict:
    """
    Returns composite Fear & Greed score + individual signal breakdown.

    {
      "score":      float (0–100),
      "label":      str,
      "colour":     str (hex),
      "signals": [
          {"name": str, "score": float, "detail": str, "raw": float|None},
          ...
      ],
      "cached_ts":  str,
      "error":      None | str,
    }
    """
    cache = _load_cache()
    if cache.get("cached_ts"):
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(cache["cached_ts"])).total_seconds()
            if age < _CACHE_TTL:
                return cache
        except Exception:
            pass

    result = {
        "score":     50.0,
        "label":     "Neutral",
        "colour":    "#aaaaaa",
        "signals":   [],
        "cached_ts": datetime.utcnow().isoformat(),
        "error":     None,
    }

    try:
        # Single bulk download for all tickers
        dfs = _bulk_download_all()

        checks = [
            ("VIX (Volatility)",       _score_vix),
            ("SPY Momentum",           _score_spy_momentum),
            ("Market Breadth",         _score_breadth),
            ("Junk Bond Demand",       _score_junk_demand),
            ("Safe Haven Demand",      _score_safe_haven),
        ]

        def _run_check(name_fn):
            name, fn = name_fn
            try:
                s, detail, raw = fn(dfs)
                return {"name": name, "score": round(s, 1), "detail": detail, "raw": raw}, s
            except Exception:
                return {"name": name, "score": 50.0, "detail": "Error", "raw": None}, 50.0

        scores = []
        signals = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = list(pool.map(_run_check, checks))
        for sig, s in futures:
            signals.append(sig)
            scores.append(s)

        composite = round(sum(scores) / len(scores), 1)
        lbl, col  = _label(composite)
        result["score"]   = composite
        result["label"]   = lbl
        result["colour"]  = col
        result["signals"] = signals

    except Exception as e:
        result["error"] = str(e)

    _save_cache(result)
    return result
