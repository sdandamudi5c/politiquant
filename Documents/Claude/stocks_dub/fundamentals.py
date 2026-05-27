"""
Fundamental data fetcher using yfinance (free, no API key).
Caches results to fundamentals_cache.json (refreshed once per day).
"""

import json
import os
import tempfile
import threading
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

FUND_CACHE_FILE = "fundamentals_cache.json"
SCREEN_THRESHOLD = 5.0  # percent
CACHE_VERSION = 8  # bump this whenever new fields are added

# Thread-safe cache lock — prevents parallel workers overwriting each other
_CACHE_LOCK = threading.Lock()

# Module-level in-memory cache — loaded once, avoids repeated full JSON reads
_MEM_CACHE: dict | None = None

# Global connection cap — prevents DNS thread exhaustion when many tickers are
# fetched in parallel.  Each fetch_fundamentals call holds one slot while it
# runs its inner ThreadPoolExecutor (max_workers=3), so at most
# _MAX_CONCURRENT × 3 = 12 yfinance connections are open at any one time.
_MAX_CONCURRENT = 4
_FETCH_SEMAPHORE = threading.Semaphore(_MAX_CONCURRENT)

_CACHE_DIR = os.path.dirname(os.path.abspath(FUND_CACHE_FILE)) or "."


def _load_cache() -> dict:
    global _MEM_CACHE
    if _MEM_CACHE is None:
        with _CACHE_LOCK:
            if _MEM_CACHE is None:
                _MEM_CACHE = {}
                if os.path.exists(FUND_CACHE_FILE):
                    try:
                        with open(FUND_CACHE_FILE) as f:
                            _MEM_CACHE = json.load(f)
                    except Exception:
                        # Corrupted cache — start fresh (will rebuild automatically)
                        _MEM_CACHE = {}
    return _MEM_CACHE


def _save_cache(ticker: str, result: dict) -> None:
    """
    Atomically add/update a single ticker in the cache file.
    Uses a temp-file + rename so a mid-write kill can't corrupt the cache.
    Prunes entries older than 48 hours to keep file size manageable.
    """
    cache = _load_cache()
    with _CACHE_LOCK:
        cache[ticker] = result
        # Prune stale entries (> 48 h) to cap file size
        cutoff = (datetime.utcnow() - timedelta(hours=48)).isoformat()
        cache = {k: v for k, v in cache.items()
                 if v.get("cached_ts", "9999") >= cutoff}
        cache[ticker] = result   # keep the entry we just wrote even if brand-new
        # Atomic write via temp file + rename — safe against mid-write crashes
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=_CACHE_DIR, delete=False, suffix=".tmp"
            ) as tf:
                json.dump(cache, tf, indent=2)
                tmp_path = tf.name
            os.replace(tmp_path, FUND_CACHE_FILE)
        except Exception:
            pass


def _cagr(start: float, end: float, years: int) -> float | None:
    """Compound annual growth rate."""
    if not start or not end or years <= 0:
        return None
    if start < 0 or end < 0:
        return None
    try:
        return ((end / start) ** (1 / years) - 1) * 100
    except Exception:
        return None


def _pct_change(old: float, new: float) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / abs(old) * 100


def fetch_fundamentals(ticker: str) -> dict:
    """
    Fetch fundamental metrics for a single ticker.
    Returns a dict with all metrics needed for screening and display.
    Uses daily cache to avoid repeated yfinance calls.
    """
    cache = _load_cache()
    today = date.today().isoformat()

    cached = cache.get(ticker, {})
    # Accept cache if fetched within the last 24 hours (not just same calendar day)
    try:
        from datetime import datetime as _dt
        _cached_ts = cached.get("cached_ts")
        _fresh = (
            _cached_ts is not None
            and (_dt.utcnow() - _dt.fromisoformat(_cached_ts)).total_seconds() < 86400
            and cached.get("cache_version") == CACHE_VERSION
        )
    except Exception:
        _fresh = cached.get("cached_date") == today and cached.get("cache_version") == CACHE_VERSION
    if _fresh:
        return cached

    result: dict = {
        "ticker": ticker,
        "cached_date": today,
        "cached_ts": __import__("datetime").datetime.utcnow().isoformat(),
        "cache_version": CACHE_VERSION,
        "error": None,
        # Screening metrics
        "total_return_5yr_pct":    None,
        "revenue_cagr_5yr_pct":    None,
        "eps_yoy_pct":             None,
        "net_income_cagr_5yr_pct": None,
        # Display metrics
        "free_cash_flow":          None,
        "lt_debt_to_capital_pct":  None,
        "price_to_sales":          None,
        "pe_ratio":                None,
        "forward_pe":              None,
        # Extra info
        "company_name":            ticker,
        "sector":                  None,
        "market_cap":              None,
        "current_price":           None,
        # News & earnings
        "news_sentiment_score":    None,
        "recent_headlines":        [],
        "earnings_surprise_pct":   None,
        # Short interest
        "short_pct_float":         None,
        "short_days_to_cover":     None,
    }

    with _FETCH_SEMAPHORE:   # cap total concurrent network fetches across all callers
      try:
        t = yf.Ticker(ticker)

        # ── Fetch t.info with its own try/except so a rate-limit error doesn't
        #    abort the entire function (Finnhub + parallel fetches can still run).
        try:
            info = t.info or {}
        except Exception:
            info = {}

        result["company_name"]  = info.get("longName") or info.get("shortName") or ticker
        result["sector"]        = info.get("sector")
        result["market_cap"]    = info.get("marketCap")
        result["current_price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        result["pe_ratio"]      = info.get("trailingPE")
        result["forward_pe"]    = info.get("forwardPE")
        result["price_to_sales"]= info.get("priceToSalesTrailing12Months")

        # ── Fetch all sub-data in parallel ─────────────────────────────────────
        # Cap at 5 workers to avoid flooding yfinance and triggering rate limits
        # when multiple tickers are being scanned simultaneously.
        from concurrent.futures import ThreadPoolExecutor as _TPE
        from institutional_trades import fetch_institutional_data as _fi_inst
        from insider_trades import fetch_insider_trades as _fi_ins

        _tk = ticker
        with _TPE(max_workers=5) as _pool:
            _fhist = _pool.submit(lambda: yf.Ticker(_tk).history(period="5y", auto_adjust=True))
            _ffin  = _pool.submit(lambda: yf.Ticker(_tk).income_stmt)
            _fcf   = _pool.submit(lambda: yf.Ticker(_tk).cashflow)
            _fbs   = _pool.submit(lambda: yf.Ticker(_tk).balance_sheet)
            _fud   = _pool.submit(lambda: yf.Ticker(_tk).upgrades_downgrades)
            _fnews = _pool.submit(lambda: yf.Ticker(_tk).news)
            _feh   = _pool.submit(lambda: yf.Ticker(_tk).earnings_history)
            _finst = _pool.submit(_fi_inst, _tk)
            _fins  = _pool.submit(_fi_ins,  _tk)
            _pre_hist = _fhist.result()
            _pre_fin  = _ffin.result()
            _pre_cf   = _fcf.result()
            _pre_bs   = _fbs.result()
            _pre_ud   = _fud.result()
            _pre_news = _fnews.result()
            _pre_eh   = _feh.result()
            _pre_inst = _finst.result() or {}
            _pre_ins  = _fins.result()  or {}

        # ── 5-year total return + annual returns + momentum ───────────────────
        hist = _pre_hist if _pre_hist is not None else pd.DataFrame()
        annual_returns: list[dict] = []
        if not hist.empty:
            p0 = float(hist["Close"].iloc[0])
            p1 = float(hist["Close"].iloc[-1])
            if p0 > 0:
                result["total_return_5yr_pct"] = (p1 - p0) / p0 * 100

            # Year-by-year price return
            hist.index = pd.to_datetime(hist.index).tz_localize(None)
            for yr in range(date.today().year - 4, date.today().year + 1):
                yr_data = hist[hist.index.year == yr]
                if not yr_data.empty:
                    yr_open  = float(yr_data["Close"].iloc[0])
                    yr_close = float(yr_data["Close"].iloc[-1])
                    yr_ret   = (yr_close - yr_open) / yr_open * 100 if yr_open else None
                    annual_returns.append({"year": yr, "price_return_pct": round(yr_ret, 1) if yr_ret else None})

            # Momentum & RSI — computed from already-downloaded history (no extra API call)
            closes = hist["Close"].dropna()
            def _mom(days):
                if len(closes) > days:
                    p_end = float(closes.iloc[-1])
                    p_s   = float(closes.iloc[-days])
                    return round((p_end - p_s) / p_s * 100, 2) if p_s else None
                return None

            result["mom_1m_pct"] = _mom(21)
            result["mom_3m_pct"] = _mom(63)
            result["mom_6m_pct"] = _mom(126)

            # RSI-14
            if len(closes) >= 15:
                delta    = closes.diff().dropna()
                gain     = delta.clip(lower=0).rolling(14).mean()
                loss     = (-delta.clip(upper=0)).rolling(14).mean()
                rs       = gain / loss.replace(0, float("nan"))
                rsi_val  = (100 - 100 / (1 + rs)).iloc[-1]
                result["rsi"] = round(float(rsi_val), 1) if not np.isnan(rsi_val) else None
            else:
                result["rsi"] = None

            # Price vs moving averages
            ma50  = info.get("fiftyDayAverage")
            ma200 = info.get("twoHundredDayAverage")
            price_now = float(closes.iloc[-1])
            result["vs_50ma_pct"]  = round((price_now - ma50)  / ma50  * 100, 2) if ma50  else None
            result["vs_200ma_pct"] = round((price_now - ma200) / ma200 * 100, 2) if ma200 else None

            # Month-by-month returns for the last 24 months
            monthly_returns: list[dict] = []
            monthly = closes.resample("ME").last()
            for i in range(1, min(25, len(monthly))):
                p_prev = float(monthly.iloc[-i - 1])
                p_curr = float(monthly.iloc[-i])
                if p_prev > 0:
                    ret = round((p_curr - p_prev) / p_prev * 100, 2)
                    label = monthly.index[-i].strftime("%Y-%m")
                    monthly_returns.append({"month": label, "return_pct": ret})
            monthly_returns.reverse()  # oldest first
            result["monthly_returns"] = monthly_returns

            # Consistency: how many of the last 6 / 12 months were positive
            last6  = [m["return_pct"] for m in monthly_returns[-6:]]
            last12 = [m["return_pct"] for m in monthly_returns[-12:]]
            result["positive_months_6"]  = sum(1 for r in last6  if r > 0)
            result["positive_months_12"] = sum(1 for r in last12 if r > 0)

        else:
            result["mom_1m_pct"]         = None
            result["mom_3m_pct"]         = None
            result["mom_6m_pct"]         = None
            result["rsi"]                = None
            result["vs_50ma_pct"]        = None
            result["vs_200ma_pct"]       = None
            result["monthly_returns"]    = []
            result["positive_months_6"]  = None
            result["positive_months_12"] = None

        result["annual_returns"] = annual_returns  # list of {year, price_return_pct}

        # ── Annual financials (revenue, net income, EPS) ───────────────────────
        fin = _pre_fin  # columns = most recent first
        annual_financials: list[dict] = []
        if fin is not None and not fin.empty:
            rev_row = fin.loc["Total Revenue"] if "Total Revenue" in fin.index else None
            ni_row  = fin.loc["Net Income"]    if "Net Income"    in fin.index else None

            if rev_row is not None:
                rev_vals = rev_row.dropna().values
                if len(rev_vals) >= 2:
                    result["revenue_cagr_5yr_pct"] = _cagr(
                        float(rev_vals[-1]), float(rev_vals[0]), min(len(rev_vals) - 1, 5)
                    )

            if ni_row is not None:
                ni_vals = ni_row.dropna().values
                if len(ni_vals) >= 2:
                    result["net_income_cagr_5yr_pct"] = _cagr(
                        float(ni_vals[-1]), float(ni_vals[0]), min(len(ni_vals) - 1, 5)
                    )

            # Build year-by-year financials table
            for col in fin.columns:
                try:
                    yr = pd.Timestamp(col).year
                except Exception:
                    continue
                row: dict = {"year": yr}
                if rev_row is not None and col in rev_row.index and not pd.isna(rev_row[col]):
                    row["revenue"] = float(rev_row[col])
                if ni_row is not None and col in ni_row.index and not pd.isna(ni_row[col]):
                    row["net_income"] = float(ni_row[col])
                annual_financials.append(row)

            annual_financials.sort(key=lambda x: x["year"])

        result["annual_financials"] = annual_financials  # list of {year, revenue, net_income}

        # ── Multi-year trend: is revenue / net income growth accelerating? ─────
        try:
            rev_by_yr = {af["year"]: af["revenue"] for af in annual_financials if "revenue" in af}
            ni_by_yr  = {af["year"]: af["net_income"] for af in annual_financials if "net_income" in af}

            def _yoy_growth_series(by_yr: dict) -> list[float]:
                yrs = sorted(by_yr.keys())
                growths = []
                for i in range(1, len(yrs)):
                    prev, curr = by_yr[yrs[i-1]], by_yr[yrs[i]]
                    if prev and prev != 0:
                        growths.append((curr - prev) / abs(prev) * 100)
                return growths

            rev_growths = _yoy_growth_series(rev_by_yr)
            ni_growths  = _yoy_growth_series(ni_by_yr)

            # Latest YoY growth rate (used for scoring)
            result["recent_rev_growth_pct"] = round(rev_growths[-1], 2) if rev_growths else None
            result["recent_ni_growth_pct"]  = round(ni_growths[-1],  2) if ni_growths  else None

            # Acceleration (kept for display; NOT used for primary scoring)
            if len(rev_growths) >= 2:
                result["revenue_trend"] = round(rev_growths[-1] - rev_growths[-2], 2)
            else:
                result["revenue_trend"] = None

            if len(ni_growths) >= 2:
                result["ni_trend"] = round(ni_growths[-1] - ni_growths[-2], 2)
            else:
                result["ni_trend"] = None

            # Store year-over-year growth rates for display
            result["revenue_yoy_growths"] = [round(g, 1) for g in rev_growths]
            result["ni_yoy_growths"]       = [round(g, 1) for g in ni_growths]
        except Exception:
            result["recent_rev_growth_pct"] = None
            result["recent_ni_growth_pct"]  = None
            result["revenue_trend"]         = None
            result["ni_trend"]              = None
            result["revenue_yoy_growths"]   = []
            result["ni_yoy_growths"]        = []

        # ── EPS YoY + annual EPS ──────────────────────────────────────────────
        try:
            inc = _pre_fin
            if inc is not None and not inc.empty:
                eps_row = None
                for candidate in ["Basic EPS", "Diluted EPS"]:
                    if candidate in inc.index:
                        eps_row = inc.loc[candidate]
                        break
                if eps_row is not None:
                    eps_vals = eps_row.dropna().values
                    if len(eps_vals) >= 2:
                        result["eps_yoy_pct"] = _pct_change(float(eps_vals[1]), float(eps_vals[0]))
                    # Attach EPS to annual_financials
                    for col in eps_row.dropna().index:
                        try:
                            yr = pd.Timestamp(col).year
                        except Exception:
                            continue
                        eps_val = float(eps_row[col])
                        for af in result.get("annual_financials", []):
                            if af["year"] == yr:
                                af["eps"] = round(eps_val, 2)
                                break
                        else:
                            result.setdefault("annual_financials", []).append(
                                {"year": yr, "eps": round(eps_val, 2)}
                            )
        except Exception:
            pass

        if result["eps_yoy_pct"] is None:
            eps_current = info.get("trailingEps")
            eps_forward = info.get("forwardEps")
            if eps_current and eps_forward:
                result["eps_yoy_pct"] = _pct_change(eps_current, eps_forward)

        # ── Free cash flow ─────────────────────────────────────────────────────
        try:
            cf = _pre_cf
            if cf is not None and not cf.empty:
                ocf_row  = cf.loc["Operating Cash Flow"] if "Operating Cash Flow" in cf.index else None
                capx_row = cf.loc["Capital Expenditure"] if "Capital Expenditure" in cf.index else None
                if ocf_row is not None:
                    ocf  = float(ocf_row.iloc[0])
                    capx = float(capx_row.iloc[0]) if capx_row is not None else 0
                    result["free_cash_flow"] = ocf + capx  # capex is negative in yfinance
        except Exception:
            result["free_cash_flow"] = info.get("freeCashflow")

        # ── LT debt / total capital ────────────────────────────────────────────
        try:
            bs = _pre_bs
            if bs is not None and not bs.empty:
                ltd_row = bs.loc["Long Term Debt"] if "Long Term Debt" in bs.index else None
                eq_row  = (bs.loc["Stockholders Equity"] if "Stockholders Equity" in bs.index
                           else bs.loc["Common Stock Equity"] if "Common Stock Equity" in bs.index
                           else None)
                if ltd_row is not None and eq_row is not None:
                    ltd = float(ltd_row.iloc[0]) if not pd.isna(ltd_row.iloc[0]) else 0
                    eq  = float(eq_row.iloc[0])  if not pd.isna(eq_row.iloc[0])  else 0
                    total_cap = ltd + eq
                    if total_cap > 0:
                        result["lt_debt_to_capital_pct"] = ltd / total_cap * 100
        except Exception:
            pass

        # ── Extra info ─────────────────────────────────────────────────────────
        result["dividend_yield"]       = info.get("dividendYield")
        result["beta"]                 = info.get("beta")
        result["week_52_high"]         = info.get("fiftyTwoWeekHigh")
        result["week_52_low"]          = info.get("fiftyTwoWeekLow")
        result["analyst_target"]       = info.get("targetMeanPrice")
        result["analyst_rating"]       = info.get("recommendationKey", "").replace("_", " ").title()
        result["num_analyst_opinions"] = info.get("numberOfAnalystOpinions")
        result["peg_ratio"]            = info.get("pegRatio")
        result["return_on_equity"]     = (info.get("returnOnEquity") or 0) * 100
        result["profit_margin"]        = (info.get("profitMargins") or 0) * 100
        result["revenue_ttm"]          = info.get("totalRevenue")
        result["earnings_date"]        = info.get("earningsTimestamp")

        # ── Analyst upgrades / downgrades (last 30 days) ──────────────────────
        try:
            _ud = _pre_ud
            if _ud is not None and not _ud.empty:
                _cutoff = datetime.utcnow() - timedelta(days=30)
                # Index is a DatetimeIndex
                if hasattr(_ud.index, 'tz_localize'):
                    try:
                        _ud.index = _ud.index.tz_localize(None)
                    except Exception:
                        try:
                            _ud.index = _ud.index.tz_convert(None)
                        except Exception:
                            pass
                _recent = _ud[_ud.index >= _cutoff] if len(_ud) else _ud.iloc[0:0]
                _upgrades   = 0
                _downgrades = 0
                _firms_up   = []
                _firms_down = []
                _UPGRADE_WORDS   = {"upgrade", "buy", "outperform", "overweight",
                                    "strong buy", "positive", "accumulate"}
                _DOWNGRADE_WORDS = {"downgrade", "sell", "underperform", "underweight",
                                    "reduce", "negative", "avoid"}
                for _, _row in _recent.iterrows():
                    _to = str(_row.get("ToGrade") or "").lower()
                    _fr = str(_row.get("FromGrade") or "").lower()
                    _action = str(_row.get("Action") or "").lower()
                    _firm   = str(_row.get("Firm") or "")
                    if "up" in _action or any(w in _to for w in _UPGRADE_WORDS):
                        _upgrades += 1
                        _firms_up.append(_firm)
                    elif "down" in _action or any(w in _to for w in _DOWNGRADE_WORDS):
                        _downgrades += 1
                        _firms_down.append(_firm)
                result["analyst_upgrades_30d"]   = _upgrades
                result["analyst_downgrades_30d"] = _downgrades
                result["analyst_upgrade_firms"]  = _firms_up[:3]
                result["analyst_downgrade_firms"]= _firms_down[:3]
            else:
                result["analyst_upgrades_30d"]   = 0
                result["analyst_downgrades_30d"] = 0
                result["analyst_upgrade_firms"]  = []
                result["analyst_downgrade_firms"]= []
        except Exception:
            result["analyst_upgrades_30d"]   = 0
            result["analyst_downgrades_30d"] = 0
            result["analyst_upgrade_firms"]  = []
            result["analyst_downgrade_firms"]= []

        # ── Volume surge ──────────────────────────────────────────────────────
        _vol     = info.get("volume") or info.get("regularMarketVolume")
        _avg_vol = info.get("averageVolume10days") or info.get("averageDailyVolume10Day") \
                   or info.get("averageVolume")
        result["volume"]         = int(_vol)    if _vol    else None
        result["avg_volume_10d"] = int(_avg_vol) if _avg_vol else None
        result["volume_ratio"]   = round(_vol / _avg_vol, 2) \
                                   if (_vol and _avg_vol and _avg_vol > 0) else None
        result["today_change_pct"] = info.get("regularMarketChangePercent")

        # ── 52-week proximity ─────────────────────────────────────────────────
        _price = result.get("current_price")
        _h52   = result.get("week_52_high")
        _l52   = result.get("week_52_low")
        result["pct_from_52w_high"] = round((_price - _h52) / _h52 * 100, 1) \
                                      if (_price and _h52 and _h52 > 0) else None
        result["pct_from_52w_low"]  = round((_price - _l52) / _l52 * 100, 1) \
                                      if (_price and _l52 and _l52 > 0) else None

        # ── Earnings countdown ────────────────────────────────────────────────
        _ets = result.get("earnings_date")
        result["earnings_days_until"] = None
        if _ets:
            try:
                from datetime import timezone as _tz
                _ed  = datetime.fromtimestamp(float(_ets), tz=_tz.utc)
                _now = datetime.now(tz=_tz.utc)
                _days = (_ed - _now).days
                # Only store if in the future (up to 180 days)
                if 0 <= _days <= 180:
                    result["earnings_days_until"] = _days
            except Exception:
                pass

        # ── Short interest (from yfinance info — no extra API call) ───────────
        try:
            spf = info.get("shortPercentOfFloat")
            result["short_pct_float"]     = round(float(spf) * 100, 2) if spf else None
            dtc = info.get("shortRatio")
            result["short_days_to_cover"] = round(float(dtc), 1) if dtc else None
        except Exception:
            pass

        # ── Finnhub: news sentiment + earnings surprise ───────────────────────────
        # Skip Finnhub if:
        #   1. Stock has no market cap (penny/shell/warrant — Finnhub won't have data)
        #   2. Previous run flagged no Finnhub coverage — but only respect that flag
        #      for 7 days; after that retry so transient errors self-heal.
        _finnhub_ok = False
        _mktcap = result.get("market_cap")   # may be None if t.info was rate-limited
        _prev_no_coverage = False
        _no_cov_ts = cached.get("finnhub_no_coverage_ts", "")
        if _no_cov_ts:
            try:
                _no_cov_age = (datetime.utcnow() - datetime.fromisoformat(_no_cov_ts)).total_seconds()
                _prev_no_coverage = _no_cov_age < 7 * 86400   # respect for 7 days only
            except Exception:
                pass
        # Try Finnhub if: market cap unknown (None = t.info was rate-limited, give benefit of doubt)
        # OR market cap ≥ $10 M (skip obvious penny stocks / shells Finnhub won't cover)
        _should_try_finnhub = (_mktcap is None or _mktcap >= 10_000_000) and not _prev_no_coverage

        if _should_try_finnhub:
            try:
                from finnhub_client import (
                    fetch_news_sentiment    as _fh_news,
                    fetch_earnings_surprise as _fh_earn,
                    get_api_key             as _fh_key,
                )
                fh_key = _fh_key()
                if fh_key:
                    fh_news = _fh_news(ticker, api_key=fh_key)
                    if not fh_news.get("error"):
                        result["news_sentiment_score"] = fh_news.get("sentiment_score")
                        result["recent_headlines"]     = fh_news.get("headlines", [])
                        result["news_source"]          = "finnhub"
                        result["news_total_articles"]  = fh_news.get("total_articles", 0)
                        _finnhub_ok = True
                    else:
                        # Timestamp the no-coverage flag so it auto-expires after 7 days
                        result["finnhub_no_coverage_ts"] = datetime.utcnow().isoformat()

                    fh_earn = _fh_earn(ticker, api_key=fh_key)
                    if not fh_earn.get("error") and fh_earn.get("surprise_pct") is not None:
                        result["earnings_surprise_pct"] = fh_earn.get("surprise_pct")
                        result["earnings_quarter"]       = fh_earn.get("quarter")
                        result["earnings_actual_eps"]    = fh_earn.get("actual_eps")
                        result["earnings_est_eps"]       = fh_earn.get("est_eps")
            except Exception:
                pass

        # ── Fallback: keyword-based sentiment from yfinance news ──────────────
        if not _finnhub_ok:
            _POS = {
                "beat", "beats", "surges", "surge", "record", "upgrade", "upgraded",
                "approved", "approval", "strong", "exceeds", "raises", "raised",
                "profit", "growth", "bullish", "partnership", "deal", "wins", "win",
                "outperforms", "outperform", "positive", "recovery", "accelerates",
            }
            _NEG = {
                "miss", "misses", "missed", "decline", "declines", "declined", "loss",
                "losses", "cut", "cuts", "downgrade", "downgraded", "lawsuit", "sue",
                "investigation", "recall", "disappoints", "disappointing", "disappointed",
                "layoffs", "layoff", "bankruptcy", "fraud", "warning", "weak", "concern",
                "concerns", "sell", "bearish", "crash", "collapsed", "slump", "slumps",
                "falls", "tumbles", "plunges", "plunge", "halted", "suspended", "probe",
            }
            try:
                raw_news = (_pre_news or [])
                headlines = []
                net = 0
                for item in raw_news[:15]:
                    title = (item.get("content", {}).get("title") or item.get("title") or "")
                    if not title:
                        continue
                    words = set(title.lower().replace("-", " ").split())
                    net += len(words & _POS) - len(words & _NEG)
                    headlines.append(title)
                result["news_sentiment_score"] = net
                result["recent_headlines"]     = headlines[:8]
                result["news_source"]          = "yfinance_keywords"
            except Exception:
                result["news_sentiment_score"] = None
                result["recent_headlines"]     = []

        # ── Fallback: earnings surprise from yfinance if Finnhub didn't provide ─
        if result.get("earnings_surprise_pct") is None:
            try:
                eh = _pre_eh
                if eh is not None and not (hasattr(eh, "empty") and eh.empty):
                    last   = eh.iloc[-1]
                    est    = float(last.get("epsEstimate") or last.get("EpsEstimate", 0) or 0)
                    actual = float(last.get("epsActual")   or last.get("EpsActual",   0) or 0)
                    if est and est != 0:
                        result["earnings_surprise_pct"] = round(
                            (actual - est) / abs(est) * 100, 2
                        )
            except Exception:
                pass

        # ── Institutional holdings (13F filings via yfinance — 7-day cache) ──
        try:
            inst = _pre_inst
            result["inst_score"]         = inst.get("inst_score", 0)
            result["inst_buyer_count"]   = inst.get("buyer_count", 0)
            result["inst_seller_count"]  = inst.get("seller_count", 0)
            result["inst_tier1_buying"]  = inst.get("tier1_buying", False)
            result["inst_tier1_buyers"]  = inst.get("tier1_buyers", [])
            result["inst_pct_held"]      = inst.get("inst_pct_held", 0.0)
            result["inst_total"]         = inst.get("total_institutions", 0)
            result["inst_buyers"]        = inst.get("buyers", [])[:5]
            result["inst_new_positions"] = inst.get("new_positions", [])[:3]
        except Exception:
            result["inst_score"]         = 0
            result["inst_buyer_count"]   = 0
            result["inst_seller_count"]  = 0
            result["inst_tier1_buying"]  = False
            result["inst_tier1_buyers"]  = []
            result["inst_pct_held"]      = 0.0
            result["inst_total"]         = 0
            result["inst_buyers"]        = []
            result["inst_new_positions"] = []

        # ── Insider trades (SEC Form 4 via yfinance — 7-day cache) ───────────
        try:
            ins = _pre_ins
            result["insider_buy_score"]   = ins.get("buy_score", 0)
            result["insider_buy_count"]   = ins.get("buy_count_90d", 0)
            result["insider_sell_count"]  = ins.get("sell_count_90d", 0)
            result["insider_ceo_bought"]  = ins.get("ceo_bought", False)
            result["insider_cfo_bought"]  = ins.get("cfo_bought", False)
            result["insider_net_30d"]     = ins.get("net_shares_30d", 0)
            result["insider_buys"]        = ins.get("buys", [])[:5]   # top 5 recent buys
        except Exception:
            result["insider_buy_score"]  = 0
            result["insider_buy_count"]  = 0
            result["insider_sell_count"] = 0
            result["insider_ceo_bought"] = False
            result["insider_cfo_bought"] = False
            result["insider_net_30d"]    = 0
            result["insider_buys"]       = []

      except Exception as e:
          result["error"] = str(e)

    _save_cache(ticker, result)
    return result


def recommend(data: dict) -> tuple[str, str, list[str]]:
    """
    Returns (signal, colour, reasons) where signal is BUY / HOLD / SELL.
    Based on fundamentals + valuation + analyst consensus.
    """
    positives: list[str] = []
    negatives: list[str] = []

    # Screening criteria
    if (data["total_return_5yr_pct"] or 0) >= 5:
        positives.append(f"Strong 5yr total return ({fmt_pct(data['total_return_5yr_pct'])})")
    else:
        negatives.append(f"Weak 5yr total return ({fmt_pct(data['total_return_5yr_pct'])})")

    if (data["revenue_cagr_5yr_pct"] or 0) >= 5:
        positives.append(f"Revenue growing at {fmt_pct(data['revenue_cagr_5yr_pct'])}/yr (5yr CAGR)")
    else:
        negatives.append(f"Slow revenue growth ({fmt_pct(data['revenue_cagr_5yr_pct'])}/yr)")

    if (data["eps_yoy_pct"] or 0) >= 5:
        positives.append(f"EPS up {fmt_pct(data['eps_yoy_pct'])} YoY")
    else:
        negatives.append(f"EPS growth weak ({fmt_pct(data['eps_yoy_pct'])} YoY)")

    if (data["net_income_cagr_5yr_pct"] or 0) >= 5:
        positives.append(f"Net income compounding at {fmt_pct(data['net_income_cagr_5yr_pct'])}/yr")
    else:
        negatives.append(f"Net income growth below threshold ({fmt_pct(data['net_income_cagr_5yr_pct'])}/yr)")

    # Valuation
    pe = data.get("pe_ratio")
    fpe = data.get("forward_pe")
    if pe and fpe and fpe < pe:
        positives.append(f"Forward P/E ({fmt_ratio(fpe)}) < Trailing P/E ({fmt_ratio(pe)}) — earnings expected to grow")
    elif pe and pe > 50:
        negatives.append(f"High P/E ratio ({fmt_ratio(pe)}) — expensive valuation")

    peg = data.get("peg_ratio")
    if peg and peg < 1:
        positives.append(f"PEG ratio {peg:.2f} — undervalued relative to growth")
    elif peg and peg > 2:
        negatives.append(f"PEG ratio {peg:.2f} — may be overpriced for its growth rate")

    # Balance sheet
    debt = data.get("lt_debt_to_capital_pct")
    if debt is not None and debt < 30:
        positives.append(f"Low debt ({fmt_pct(debt)} of capital)")
    elif debt is not None and debt > 60:
        negatives.append(f"High debt load ({fmt_pct(debt)} of capital)")

    # Cash flow
    fcf = data.get("free_cash_flow")
    if fcf and fcf > 0:
        positives.append(f"Positive free cash flow ({fmt_large(fcf)})")
    elif fcf and fcf < 0:
        negatives.append(f"Negative free cash flow ({fmt_large(fcf)}) — burning cash")

    # Analyst consensus
    rating = (data.get("analyst_rating") or "").lower()
    target = data.get("analyst_target")
    price  = data.get("current_price")
    if rating in ("buy", "strong buy"):
        positives.append(f"Analyst consensus: {data['analyst_rating']} ({data.get('num_analyst_opinions', '?')} analysts)")
    elif rating in ("sell", "strong sell"):
        negatives.append(f"Analyst consensus: {data['analyst_rating']} ({data.get('num_analyst_opinions', '?')} analysts)")
    if target and price and target > price * 1.1:
        upside = (target - price) / price * 100
        positives.append(f"Analyst target ${target:.2f} implies +{upside:.0f}% upside")
    elif target and price and target < price * 0.95:
        negatives.append(f"Analyst target ${target:.2f} is below current price")

    # Score
    score = len(positives) - len(negatives)
    if score >= 3:
        signal, colour = "BUY", "#2ecc71"
    elif score <= -2:
        signal, colour = "SELL", "#e74c3c"
    else:
        signal, colour = "HOLD", "#f39c12"

    reasons = [f"✅ {p}" for p in positives] + [f"❌ {n}" for n in negatives]
    return signal, colour, reasons


def screen(data: dict) -> dict[str, bool | None]:
    """Return pass/fail for each screening criterion."""
    def _pass(val, threshold=SCREEN_THRESHOLD):
        if val is None:
            return None
        return val >= threshold

    return {
        "5yr Total Return > 5%":    _pass(data["total_return_5yr_pct"]),
        "5yr Revenue CAGR > 5%":    _pass(data["revenue_cagr_5yr_pct"]),
        "EPS YoY Growth > 5%":      _pass(data["eps_yoy_pct"]),
        "5yr Net Income CAGR > 5%": _pass(data["net_income_cagr_5yr_pct"]),
    }


def fmt_large(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1e9:
        return f"${val/1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val/1e6:.2f}M"
    return f"${val:,.0f}"


def fmt_pct(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:+.1f}%"


def fmt_ratio(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:.2f}x"
