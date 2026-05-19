"""
Price-change calculator using yfinance (free, no API key).
Caches all fetched closes to price_cache.json so repeated lookups are instant.
"""

import json
import os
from datetime import datetime, timedelta, date

import pandas as pd
import yfinance as yf

PRICE_CACHE_FILE = "price_cache.json"


def _load_price_cache() -> dict:
    if os.path.exists(PRICE_CACHE_FILE):
        try:
            with open(PRICE_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_price_cache(cache: dict) -> None:
    with open(PRICE_CACHE_FILE, "w") as f:
        json.dump(cache, f)


def _nearest_close(hist: pd.DataFrame, target: date) -> float | None:
    """Return the closing price on or just after target date (skips weekends/holidays)."""
    if hist.empty:
        return None
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    target_ts = pd.Timestamp(target)
    future = hist[hist.index >= target_ts]
    if future.empty:
        future = hist  # fall back to last available
    return float(future["Close"].iloc[0])


def _to_date_str(val) -> str:
    """Normalise any date-like value to YYYY-MM-DD string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val[:10]
    try:
        return pd.Timestamp(val).strftime("%Y-%m-%d")
    except Exception:
        return str(val)[:10]


def fetch_pct_changes(trades: list[dict], progress_callback=None) -> dict[str, float]:
    """
    For each trade, compute % change from transaction_date close to today's close.
    Returns dict keyed by doc_id+ticker: {"pct": float, "buy_price": float, "now_price": float}.
    Uses local cache aggressively to avoid redundant yfinance calls.
    """
    cache = _load_price_cache()
    today = date.today()
    today_str = today.isoformat()
    results: dict[str, float] = {}

    # Group trades by ticker to batch yfinance requests
    ticker_dates: dict[str, set[str]] = {}
    for t in trades:
        ticker = t.get("ticker", "").upper().strip()
        tx_date = _to_date_str(t.get("transaction_date", ""))
        if not ticker or not tx_date or ticker in ("N/A", "--", ""):
            continue
        ticker_dates.setdefault(ticker, set()).add(tx_date)

    total = len(ticker_dates)
    done = 0

    for ticker, dates_needed in ticker_dates.items():
        if progress_callback and done % 20 == 0:
            progress_callback(f"Fetching prices: {done}/{total} tickers…")

        # Find oldest date we need so we fetch one range
        try:
            oldest = min(dates_needed)
            start = (datetime.fromisoformat(oldest) - timedelta(days=5)).strftime("%Y-%m-%d")
            end = (today + timedelta(days=1)).strftime("%Y-%m-%d")

            cache_key = ticker
            if cache_key not in cache or cache[cache_key].get("fetched_date") != today_str:
                hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
                if hist.empty:
                    cache[cache_key] = {"fetched_date": today_str, "closes": {}}
                else:
                    hist.index = pd.to_datetime(hist.index).tz_localize(None)
                    closes = {str(ts.date()): float(row["Close"]) for ts, row in hist.iterrows()}
                    cache[cache_key] = {"fetched_date": today_str, "closes": closes}

            closes = cache[cache_key].get("closes", {})

            # Get today's price (latest available)
            if closes:
                now_price = closes[max(closes.keys())]
            else:
                now_price = None

            # Store per-date close for this ticker (dates_needed already normalised)
            for tx_date in dates_needed:
                buy_price = _get_close_on_or_after(closes, tx_date)
                if buy_price and now_price and buy_price > 0:
                    pct = (now_price - buy_price) / buy_price * 100
                    results[f"{ticker}|{tx_date}"] = {
                        "pct": round(pct, 2),
                        "buy_price": round(buy_price, 2),
                        "now_price": round(now_price, 2),
                    }

        except Exception:
            pass

        done += 1

    _save_price_cache(cache)
    return results


def _get_close_on_or_after(closes: dict, target_date_str: str) -> float | None:
    """Find the closing price on or just after the target date."""
    if not closes:
        return None
    sorted_dates = sorted(closes.keys())
    for d in sorted_dates:
        if d >= target_date_str:
            return closes[d]
    return closes[sorted_dates[-1]]  # fall back to latest


def add_pct_change_to_df(df: pd.DataFrame, price_data: dict) -> pd.DataFrame:
    """Add a pct_change column to the trades dataframe."""
    def lookup(row):
        date_str = _to_date_str(row["transaction_date"])
        key = f"{row['ticker']}|{date_str}"
        info = price_data.get(key)
        return info["pct"] if info else None

    df = df.copy()
    df["pct_change"] = df.apply(lookup, axis=1)
    return df
