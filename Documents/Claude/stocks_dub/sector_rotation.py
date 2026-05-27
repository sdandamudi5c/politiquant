"""
Sector rotation signals — 100% free via yfinance.

Compares each SPDR sector ETF's recent performance against SPY (S&P 500).
When money is rotating *into* a sector, stocks in that sector get a small
score boost. When money is rotating *out*, a small penalty.

Also tracks popular sub-sector / theme ETFs (semiconductors, biotech, banks, etc.)
so intraday moves (e.g. memory stocks up today) are visible at a granular level.

Sector ETF → yfinance sector name mapping:
  XLK  Technology
  XLC  Communication Services
  XLY  Consumer Cyclical / Consumer Discretionary
  XLP  Consumer Defensive / Consumer Staples
  XLV  Healthcare
  XLF  Financial Services / Financials
  XLI  Industrials
  XLB  Basic Materials / Materials
  XLRE Real Estate
  XLU  Utilities
  XLE  Energy

Cache: 1-hour TTL (captures intraday moves).
"""

from datetime import datetime, timedelta

import yfinance as yf

import cache_db as _cdb

_NAMESPACE  = "sector"
_CACHE_KEY  = "rotation"
_CACHE_TTL  = 1 * 3600           # 1 hour — captures intraday moves

# ── Broad SPDR sector ETFs (mapped to yfinance sector names) ──────────────────
SECTOR_ETFS = {
    "Technology":                "XLK",
    "Communication Services":    "XLC",
    "Consumer Cyclical":         "XLY",
    "Consumer Defensive":        "XLP",
    "Healthcare":                "XLV",
    "Financial Services":        "XLF",
    "Industrials":               "XLI",
    "Basic Materials":           "XLB",
    "Real Estate":               "XLRE",
    "Utilities":                 "XLU",
    "Energy":                    "XLE",
}

# ── Sub-sector / theme ETFs (more granular — not mapped to yfinance sector) ───
# These are shown separately on the page so users can see e.g. semis up today
# even though broad XLK may look flat.
SUB_SECTOR_ETFS = {
    "Semiconductors":      {"etf": "SOXX",  "desc": "iShares Semiconductor (MU, NVDA, AMD, AVGO…)"},
    "Semiconductor Alt":   {"etf": "SMH",   "desc": "VanEck Semiconductor (TSMC, NVDA, ASML…)"},
    "AI / Robotics":       {"etf": "BOTZ",  "desc": "Global X Robotics & AI (NVDA, ABB, Intuitive…)"},
    "Cloud / Software":    {"etf": "IGV",   "desc": "iShares Software (MSFT, CRM, ORCL, ADBE…)"},
    "Biotech":             {"etf": "XBI",   "desc": "SPDR Biotech (small-cap heavy, high-beta)"},
    "Biotech Large Cap":   {"etf": "IBB",   "desc": "iShares Biotech (AMGN, GILD, VRTX…)"},
    "Regional Banks":      {"etf": "KRE",   "desc": "SPDR Regional Banks (SVB-era proxy)"},
    "Big Banks":           {"etf": "KBE",   "desc": "SPDR Bank ETF (JPM, BAC, WFC…)"},
    "Homebuilders":        {"etf": "XHB",   "desc": "SPDR Homebuilders (LEN, DHI, NVR…)"},
    "Airlines":            {"etf": "JETS",  "desc": "US Global Jets (AAL, DAL, UAL, LUV…)"},
    "Oil & Gas Explorers": {"etf": "XOP",   "desc": "SPDR Oil & Gas E&P (high-beta energy)"},
    "Clean Energy":        {"etf": "ICLN",  "desc": "iShares Clean Energy (solar, wind…)"},
    "Retail":              {"etf": "XRT",   "desc": "SPDR Retail (AMZN, TGT, WMT…)"},
    "Cybersecurity":       {"etf": "HACK",  "desc": "ETFMG Cybersecurity (PANW, CRWD, FTNT…)"},
}

# Aliases for variant sector names yfinance sometimes returns
_ALIASES = {
    "consumer discretionary":     "Consumer Cyclical",
    "consumer staples":           "Consumer Defensive",
    "financials":                 "Financial Services",
    "materials":                  "Basic Materials",
    "health care":                "Healthcare",
    "information technology":     "Technology",
    "telecom":                    "Communication Services",
    "telecommunication services": "Communication Services",
}

BENCHMARK = "SPY"

# ── Representative stocks per broad sector ────────────────────────────────────
# ~10 well-known names each — used to show "what's kicking" in hot sectors
SECTOR_STOCKS = {
    "Technology": [
        "NVDA", "MSFT", "AAPL", "AVGO", "AMD", "CRM", "ORCL", "ACN", "ADBE", "INTC",
    ],
    "Communication Services": [
        "META", "GOOGL", "NFLX", "DIS", "SNAP", "RDDT", "T", "VZ", "CMCSA", "TTWO",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "F", "GM",
    ],
    "Consumer Defensive": [
        "WMT", "PG", "KO", "PEP", "COST", "MDLZ", "CL", "GIS", "TSN", "MO",
    ],
    "Healthcare": [
        "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "ISRG", "VRTX", "LLY",
    ],
    "Financial Services": [
        "JPM", "BAC", "WFC", "GS", "MS", "BLK", "AXP", "SCHW", "CB", "USB",
    ],
    "Industrials": [
        "CAT", "HON", "UNP", "GE", "LMT", "RTX", "DE", "MMM", "BA", "ETN",
    ],
    "Basic Materials": [
        "LIN", "APD", "SHW", "ECL", "NEM", "FCX", "ALB", "DD", "CF", "MOS",
    ],
    "Real Estate": [
        "AMT", "PLD", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "VTR", "AVB",
    ],
    "Utilities": [
        "NEE", "SO", "DUK", "D", "AEP", "EXC", "SRE", "PCG", "XEL", "ED",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "OXY", "VLO", "HES",
    ],
}

# ── Representative stocks per sub-sector / theme ──────────────────────────────
SUB_SECTOR_STOCKS = {
    "Semiconductors":    ["MU", "NVDA", "AMD", "AVGO", "INTC", "QCOM", "AMAT", "LRCX", "KLAC", "ON"],
    "AI / Robotics":     ["NVDA", "MSFT", "GOOGL", "META", "ARM", "PLTR", "AI", "SOUN", "BBAI", "PATH"],
    "Cloud / Software":  ["MSFT", "CRM", "ORCL", "NOW", "SNOW", "DDOG", "NET", "ZS", "MDB", "TEAM"],
    "Biotech":           ["MRNA", "BNTX", "GILD", "REGN", "BIIB", "SGEN", "EXAS", "RARE", "PCVX", "RXRX"],
    "Biotech Large Cap": ["AMGN", "GILD", "VRTX", "REGN", "BIIB", "BMY", "ALNY", "INCY", "SRRK", "NTRA"],
    "Regional Banks":    ["WAL", "PACW", "FHN", "ZION", "CMA", "RF", "HBAN", "KEY", "CFG", "FNB"],
    "Big Banks":         ["JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC", "TFC", "AXP"],
    "Homebuilders":      ["LEN", "DHI", "NVR", "PHM", "TOL", "MTH", "TMHC", "LGIH", "MHO", "SKY"],
    "Airlines":          ["DAL", "UAL", "AAL", "LUV", "JBLU", "ALK", "HA", "SAVE", "RYAAY", "SKYW"],
    "Oil & Gas Explorers":["XOM", "OXY", "COP", "EOG", "DVN", "MRO", "APA", "FANG", "AR", "EQT"],
    "Clean Energy":      ["ENPH", "FSLR", "SEDG", "RUN", "PLUG", "BLDP", "BE", "MAXN", "ARRY", "CSIQ"],
    "Retail":            ["AMZN", "WMT", "TGT", "COST", "TJX", "ROST", "DG", "DLTR", "M", "KSS"],
    "Cybersecurity":     ["PANW", "CRWD", "FTNT", "ZS", "OKTA", "S", "CYBR", "TENB", "VRNS", "QLYS"],
}

_STOCKS_CACHE_TTL  = 1 * 3600   # 1 hour


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    return _cdb.get(_NAMESPACE, _CACHE_KEY) or {}


def _save_cache(data: dict) -> None:
    _cdb.set(_NAMESPACE, _CACHE_KEY, data)


# ── Core data fetch ────────────────────────────────────────────────────────────

def _fetch_returns(ticker: str) -> dict:
    """
    Fetch 1d, 5d, 30d, 90d returns for a ticker in a single yf.download call.
    Returns dict with ret_1d, ret_5d, ret_30d, ret_90d (None on error).
    """
    try:
        end   = datetime.utcnow()
        start = end - timedelta(days=100)   # covers 90d + buffer
        df    = yf.download(ticker,
                            start=start.strftime("%Y-%m-%d"),
                            end=end.strftime("%Y-%m-%d"),
                            progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 2:
            return {}
        closes = df["Close"].dropna()
        n = len(closes)
        last = float(closes.iloc[-1])

        def _ret(periods: int) -> "float | None":
            idx = -periods if periods < n else 0
            base = float(closes.iloc[idx])
            return round((last - base) / base * 100, 2) if base else None

        return {
            "ret_1d":  _ret(1),
            "ret_5d":  _ret(5),
            "ret_30d": _ret(21),   # ~21 trading days ≈ 30 calendar days
            "ret_90d": _ret(63),   # ~63 trading days ≈ 90 calendar days
        }
    except Exception:
        return {}


def _classify(vs_spy_30d: "float | None") -> tuple[str, int]:
    """Return (momentum, score_mod) based on 30d vs SPY spread."""
    if vs_spy_30d is None:
        return "neutral", 0
    elif vs_spy_30d >= 5:
        return "hot", 4
    elif vs_spy_30d >= 2:
        return "warm", 2
    elif vs_spy_30d >= -2:
        return "neutral", 0
    elif vs_spy_30d >= -5:
        return "cool", -2
    else:
        return "cold", -4


def fetch_sector_rotation() -> dict:
    """
    Fetch/cache sector + sub-sector momentum data.
    Uses a single bulk yf.download for all 26 ETFs instead of 26 sequential calls.
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
        "sectors":    {},
        "sub_sectors": {},
        "spy_1d":     None,
        "spy_30d":    None,
        "spy_90d":    None,
        "cached_ts":  datetime.utcnow().isoformat(),
        "error":      None,
    }

    try:
        # ── Single bulk download for SPY + all 25 sector/sub-sector ETFs ──────
        all_etfs = (["SPY"]
                    + list(SECTOR_ETFS.values())
                    + [m["etf"] for m in SUB_SECTOR_ETFS.values()])
        end   = datetime.utcnow()
        start = end - timedelta(days=110)   # 90d + trading-day buffer
        raw   = yf.download(
            all_etfs,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True, group_by="ticker",
        )

        def _rets(tk: str) -> dict:
            """Extract 1d/5d/30d/90d returns for one ticker from the bulk result."""
            try:
                closes = raw[tk]["Close"].dropna()
                if closes is None or len(closes) < 2:
                    return {}
                n    = len(closes)
                last = float(closes.iloc[-1])

                def _r(p: int) -> "float | None":
                    idx  = max(n - p, 0)
                    base = float(closes.iloc[idx])
                    return round((last - base) / base * 100, 2) if base else None

                return {
                    "ret_1d":  _r(2),    # iloc[-2] → yesterday's close
                    "ret_5d":  _r(5),
                    "ret_30d": _r(21),   # ~21 trading days ≈ 30 calendar days
                    "ret_90d": _r(63),
                }
            except Exception:
                return {}

        spy_rets = _rets("SPY")
        spy_1d   = spy_rets.get("ret_1d")
        spy_30   = spy_rets.get("ret_30d")
        spy_90   = spy_rets.get("ret_90d")
        result["spy_1d"]  = spy_1d
        result["spy_30d"] = spy_30
        result["spy_90d"] = spy_90

        # ── Broad sectors ──────────────────────────────────────────────────────
        for sector, etf in SECTOR_ETFS.items():
            rets = _rets(etf)
            r1  = rets.get("ret_1d")
            r5  = rets.get("ret_5d")
            r30 = rets.get("ret_30d")
            r90 = rets.get("ret_90d")

            vs1  = round(r1  - spy_1d, 2) if (r1  is not None and spy_1d is not None) else None
            vs30 = round(r30 - spy_30, 2) if (r30 is not None and spy_30 is not None) else None
            vs90 = round(r90 - spy_90, 2) if (r90 is not None and spy_90 is not None) else None

            momentum, score_mod = _classify(vs30)

            result["sectors"][sector] = {
                "etf":        etf,
                "ret_1d":     round(r1,  2) if r1  is not None else None,
                "ret_5d":     round(r5,  2) if r5  is not None else None,
                "ret_30d":    round(r30, 2) if r30 is not None else None,
                "ret_90d":    round(r90, 2) if r90 is not None else None,
                "vs_spy_1d":  vs1,
                "vs_spy_30d": vs30,
                "vs_spy_90d": vs90,
                "momentum":   momentum,
                "score_mod":  score_mod,
            }

        # ── Sub-sectors / themes ───────────────────────────────────────────────
        for name, meta in SUB_SECTOR_ETFS.items():
            etf  = meta["etf"]
            desc = meta["desc"]
            rets = _rets(etf)
            r1  = rets.get("ret_1d")
            r5  = rets.get("ret_5d")
            r30 = rets.get("ret_30d")

            vs1  = round(r1  - spy_1d, 2) if (r1  is not None and spy_1d is not None) else None
            vs30 = round(r30 - spy_30, 2) if (r30 is not None and spy_30 is not None) else None

            momentum, _ = _classify(vs30)

            result["sub_sectors"][name] = {
                "etf":        etf,
                "desc":       desc,
                "ret_1d":     round(r1,  2) if r1  is not None else None,
                "ret_5d":     round(r5,  2) if r5  is not None else None,
                "ret_30d":    round(r30, 2) if r30 is not None else None,
                "vs_spy_1d":  vs1,
                "vs_spy_30d": vs30,
                "momentum":   momentum,
            }

    except Exception as e:
        result["error"] = str(e)

    _save_cache(result)
    return result


def get_sector_signal(sector_name: str) -> dict:
    """
    Return the sector data for a given stock's sector name.
    Handles variant names. Returns neutral defaults if not found.
    """
    normalised = _ALIASES.get(sector_name.lower(), sector_name)
    data    = fetch_sector_rotation()
    sectors = data.get("sectors", {})

    if normalised in sectors:
        return sectors[normalised]
    for k, v in sectors.items():
        if k.lower() == normalised.lower():
            return v

    return {"momentum": "neutral", "score_mod": 0, "etf": None,
            "ret_1d": None, "ret_30d": None, "vs_spy_1d": None, "vs_spy_30d": None}


# ── Convenience labels / colours ──────────────────────────────────────────────

MOMENTUM_LABEL = {
    "hot":     ("🔥 Hot",     "#e74c3c"),
    "warm":    ("📈 Warm",    "#f39c12"),
    "neutral": ("➡️  Neutral", "#aaaaaa"),
    "cool":    ("📉 Cool",    "#5dade2"),
    "cold":    ("❄️  Cold",    "#2980b9"),
}

MOMENTUM_COLOUR = {
    "hot":     "#e74c3c",
    "warm":    "#f39c12",
    "neutral": "#aaaaaa",
    "cool":    "#5dade2",
    "cold":    "#2980b9",
}


# ── Stock movers fetch ────────────────────────────────────────────────────────

def _load_stocks_cache() -> dict:
    return _cdb.get("sector_stocks", "movers") or {}


def _save_stocks_cache(data: dict) -> None:
    _cdb.set("sector_stocks", "movers", data)


def fetch_stock_movers(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch 1d, 5d, 30d returns for a list of tickers in a single bulk download.
    Uses a 1-hour cache keyed on the sorted ticker list.

    Returns { "NVDA": {"ret_1d": 3.2, "ret_5d": 8.1, "ret_30d": 22.4}, ... }
    """
    cache_key = ",".join(sorted(tickers))
    cache     = _load_stocks_cache()
    cached    = cache.get(cache_key, {})
    if cached.get("cached_ts"):
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(cached["cached_ts"])).total_seconds()
            if age < _STOCKS_CACHE_TTL:
                return {k: v for k, v in cached.items() if k != "cached_ts"}
        except Exception:
            pass

    result: dict[str, dict] = {}
    try:
        end   = datetime.utcnow()
        start = end - timedelta(days=50)   # enough for 30d trading days
        all_tickers = list(tickers) + (["SPY"] if "SPY" not in tickers else [])
        raw   = yf.download(
            all_tickers, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True, group_by="ticker",
        )
        if raw is None or raw.empty:
            return result

        # Compute SPY 1d return from bulk download instead of a separate fetch
        try:
            _spy_closes = (
                raw["SPY"]["Close"].dropna()
                if "SPY" in raw.columns.get_level_values(0)
                else raw["Close"].dropna()
                if len(all_tickers) == 1
                else None
            )
            if _spy_closes is not None and len(_spy_closes) >= 2:
                _spy_last = float(_spy_closes.iloc[-1])
                _spy_prev = float(_spy_closes.iloc[-2])
                spy_1d = round((_spy_last - _spy_prev) / _spy_prev * 100, 2) if _spy_prev else None
            else:
                spy_1d = None
        except Exception:
            spy_1d = None

        for tk in tickers:
            try:
                # Handle single vs multi ticker DataFrame structure
                if len(tickers) == 1:
                    closes = raw["Close"].dropna()
                else:
                    closes = raw[tk]["Close"].dropna() if tk in raw.columns.get_level_values(0) else None
                if closes is None or len(closes) < 2:
                    continue
                n    = len(closes)
                last = float(closes.iloc[-1])

                def _r(p: int) -> "float | None":
                    idx  = max(n - p, 0)
                    base = float(closes.iloc[idx])
                    return round((last - base) / base * 100, 2) if base else None

                r1  = _r(2)    # 1 trading day
                r5  = _r(5)
                r30 = _r(21)
                vs1 = round(r1 - spy_1d, 2) if (r1 is not None and spy_1d is not None) else None

                result[tk] = {
                    "ret_1d":    r1,
                    "ret_5d":    r5,
                    "ret_30d":   r30,
                    "vs_spy_1d": vs1,
                }
            except Exception:
                continue

    except Exception:
        pass

    # Cache with timestamp
    to_cache = dict(result)
    to_cache["cached_ts"] = datetime.utcnow().isoformat()
    cache[cache_key] = to_cache
    _save_stocks_cache(cache)

    return result


def get_hot_sector_picks(top_n: int = 8) -> list[dict]:
    """
    Return a list of dicts for display on the sector rotation page.
    Each dict has: sector, momentum, stocks (sorted by today's return).
    Only includes hot/warm sectors.
    """
    sector_data = fetch_sector_rotation()
    sectors     = sector_data.get("sectors", {})

    picks = []
    for sector_name, sinfo in sectors.items():
        mom = sinfo.get("momentum", "neutral")
        if mom not in ("hot", "warm"):
            continue
        tickers = SECTOR_STOCKS.get(sector_name, [])
        if not tickers:
            continue
        movers = fetch_stock_movers(tickers)
        stocks = []
        for tk, ret in movers.items():
            stocks.append({
                "ticker":    tk,
                "ret_1d":    ret.get("ret_1d"),
                "ret_5d":    ret.get("ret_5d"),
                "ret_30d":   ret.get("ret_30d"),
                "vs_spy_1d": ret.get("vs_spy_1d"),
            })
        # Sort: best today first
        stocks.sort(key=lambda x: (x["ret_1d"] or -999), reverse=True)
        picks.append({
            "sector":    sector_name,
            "etf":       sinfo.get("etf", ""),
            "momentum":  mom,
            "vs_spy_30d": sinfo.get("vs_spy_30d"),
            "stocks":    stocks[:top_n],
        })

    # Hot before warm
    picks.sort(key=lambda x: 0 if x["momentum"] == "hot" else 1)
    return picks


def get_hot_subsector_picks(top_n: int = 6) -> list[dict]:
    """
    Same as get_hot_sector_picks but for sub-sectors (SOXX stocks, etc.).
    Only returns sub-sectors whose ETF 30d momentum is hot/warm.
    """
    sector_data = fetch_sector_rotation()
    sub_data    = sector_data.get("sub_sectors", {})

    picks = []
    for name, sinfo in sub_data.items():
        mom = sinfo.get("momentum", "neutral")
        if mom not in ("hot", "warm"):
            continue
        tickers = SUB_SECTOR_STOCKS.get(name, [])
        if not tickers:
            continue
        movers = fetch_stock_movers(tickers)
        stocks = []
        for tk, ret in movers.items():
            stocks.append({
                "ticker":    tk,
                "ret_1d":    ret.get("ret_1d"),
                "ret_5d":    ret.get("ret_5d"),
                "ret_30d":   ret.get("ret_30d"),
                "vs_spy_1d": ret.get("vs_spy_1d"),
            })
        stocks.sort(key=lambda x: (x["ret_1d"] or -999), reverse=True)
        picks.append({
            "sector":     name,
            "etf":        sinfo.get("etf", ""),
            "momentum":   mom,
            "vs_spy_30d": sinfo.get("vs_spy_30d"),
            "desc":       sinfo.get("desc", ""),
            "stocks":     stocks[:top_n],
        })

    picks.sort(key=lambda x: 0 if x["momentum"] == "hot" else 1)
    return picks
