"""
political_signal.py — Presidential / political news signal for PolitiQuant

Scans three FREE sources every hour for mentions of a stock near presidential
or political keywords, then returns a sentiment score + matching headline snippets.

Sources (all 100% free, no key required):
  1. White House official press releases  (https://www.whitehouse.gov/news/feed/)
  2. Finnhub general news                 (/news?category=general — free tier)
  3. NYT Business RSS                     (https://rss.nytimes.com/...)

Typical use:
    from political_signal import get_political_signal
    sig = get_political_signal("AAPL", "Apple")
    # {"score_mod": 5, "flag": "🏛️ Presidential mention", "headlines": [...], ...}
"""

import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from threading import Lock

# ── Cache (in-memory, 1-hour TTL — political news is fast-moving) ─────────────
_CACHE: dict[str, dict] = {}
_CACHE_LOCK = Lock()
_CACHE_TTL_SECS = 3600          # 1 hour
_NEWS_CACHE_KEY = "__political_news__"

# ── Company name → ticker mapping (top ~120 most news-mentioned companies) ────
_COMPANY_TO_TICKER: dict[str, str] = {
    # Tech
    "apple":         "AAPL", "microsoft":    "MSFT", "google":       "GOOGL",
    "alphabet":      "GOOGL","amazon":        "AMZN", "meta":         "META",
    "facebook":      "META", "nvidia":        "NVDA", "tesla":        "TSLA",
    "intel":         "INTC", "amd":           "AMD",  "qualcomm":     "QCOM",
    "broadcom":      "AVGO", "oracle":        "ORCL", "salesforce":   "CRM",
    "ibm":           "IBM",  "cisco":         "CSCO", "hp":           "HPQ",
    "dell":          "DELL", "palantir":      "PLTR", "snowflake":    "SNOW",
    "uber":          "UBER", "lyft":          "LYFT", "airbnb":       "ABNB",
    "netflix":       "NFLX", "spotify":       "SPOT", "twitter":      "X",
    "x corp":        "X",    "tiktok":        "BDNCE","bytedance":    "BDNCE",
    "openai":        "MSFT", "spacex":        "SPCE", "starlink":     "SPCE",
    # Finance
    "jpmorgan":      "JPM",  "jp morgan":     "JPM",  "goldman sachs":"GS",
    "goldman":       "GS",   "morgan stanley":"MS",   "bank of america":"BAC",
    "citigroup":     "C",    "citi":          "C",    "wells fargo":  "WFC",
    "blackrock":     "BLK",  "berkshire":     "BRK-B","visa":         "V",
    "mastercard":    "MA",   "paypal":        "PYPL", "coinbase":     "COIN",
    # Healthcare / Pharma
    "pfizer":        "PFE",  "moderna":       "MRNA", "johnson":      "JNJ",
    "j&j":           "JNJ",  "unitedhealth":  "UNH",  "cvs":          "CVS",
    "eli lilly":     "LLY",  "lilly":         "LLY",  "abbvie":       "ABBV",
    "merck":         "MRK",  "novo nordisk":  "NVO",  "novartis":     "NVS",
    # Energy / Industrial
    "exxon":         "XOM",  "chevron":       "CVX",  "shell":        "SHEL",
    "bp":            "BP",   "boeing":        "BA",   "lockheed":     "LMT",
    "raytheon":      "RTX",  "northrop":      "NOC",  "general electric":"GE",
    "ge":            "GE",   "ford":          "F",    "gm":           "GM",
    "general motors":"GM",   "caterpillar":   "CAT",  "deere":        "DE",
    "john deere":    "DE",   "3m":            "MMM",  "honeywell":    "HON",
    # Retail / Consumer
    "walmart":       "WMT",  "costco":        "COST", "target":       "TGT",
    "home depot":    "HD",   "starbucks":     "SBUX", "mcdonalds":    "MCD",
    "mcdonald's":    "MCD",  "nike":          "NKE",  "disney":       "DIS",
    "comcast":       "CMCSA","at&t":          "T",    "verizon":      "VZ",
    "t-mobile":      "TMUS",
    # China / Geopolitical flash points
    "huawei":        "HUAWEI","tencent":      "TCEHY","alibaba":      "BABA",
    "baidu":         "BIDU",  "jd.com":       "JD",   "xiaomi":       "XIACF",
    "semiconductor": None,    # sector — handled separately
    "chip":          None,
}

# ── Sentiment keywords ─────────────────────────────────────────────────────────
# Presidential positive: deal-making, deregulation, endorsement, tariff relief
_POSITIVE_PATTERNS = [
    r"\bgreat deal\b", r"\bfantastic\b", r"\btremendous\b", r"\bincredible\b",
    r"\bbeautiful\b",  r"\bwinning\b",   r"\bno tariff\b",  r"\btariff.{0,20}remov",
    r"\btariff.{0,20}exempt",            r"\btariff.{0,20}relief",
    r"\btariff.{0,20}cut",               r"\btariff.{0,20}reduc",
    r"\bdeal.{0,30}sign",                r"\bagreement.{0,30}sign",
    r"\bapprove[sd]?\b", r"\bend(?:ors|ed)\b", r"\bpartnership\b",
    r"\binvest.{0,20}america\b",         r"\bjobs?.{0,20}creat",
    r"\bopen.{0,20}market",              r"\btrade deal\b",
    r"\bsupport\b",    r"\bchampion\b",  r"\bbuy american\b",
    r"\bderegulat",    r"\btax cut\b",   r"\bmanufactur.{0,20}america",
    r"\breshoring\b",  r"\bonshoring\b", r"\bdomestic.{0,20}product",
    r"\bexempt",       r"\bwaiver\b",    r"\bpause.{0,20}tariff",
    r"\bceasefire\b",  r"\bpeace.{0,20}deal",
    r"\bhistoric.{0,20}deal",            r"\bsoaring\b",
    r"\bamerica first\b",               r"\bprove.{0,30}work",
    r"\b(?:sign|ink).{0,20}deal",       r"\bdeal\b",   # any deal mention near company is bullish
    r"\bsecures?\b",  r"\brebuild\b",   r"\bboost\b",
]

_NEGATIVE_PATTERNS = [
    r"\btariff\b",     r"\bsanction\b",  r"\bban\b",        r"\bblock\b",
    r"\binvestigat\b", r"\bfine\b",      r"\bpenalt",       r"\bproscut",
    r"\bterrible\b",   r"\bdisaster\b",  r"\bunfair\b",     r"\bcheat\b",
    r"\bsue\b",        r"\blawsuit\b",   r"\bnational security\b",
    r"\bbreak.?up\b",  r"\bantitrust\b", r"\bmonopol\b",    r"\bforced.{0,20}sell",
    r"\bdivestiture\b",r"\bblacklist\b", r"\bexport.{0,20}control",
    r"\bchina.{0,30}threat",             r"\bembargo\b",
    r"\btariff.{0,20}increas",           r"\btariff.{0,20}hike",
    r"\btariff.{0,20}rais",
]

# Political source keywords (must appear near company name to count)
_POLITICAL_ACTORS = [
    r"\btrump\b", r"\bpresident\b", r"\bwhite house\b", r"\badministration\b",
    r"\bexecutive order\b", r"\btariff\b", r"\btrade war\b", r"\bsanction\b",
    r"\bcongress\b", r"\bsenate\b", r"\bhouse.{0,10}pass\b", r"\bfederal.{0,15}regulat",
    r"\bfcc\b", r"\bftc\b", r"\bdoj\b", r"\bsec\b(?! edgar)", r"\bfda\b",
    r"\bdepartment of\b",
]


def _fetch_rss(url: str, timeout: int = 6) -> list[dict]:
    """Fetch an RSS feed and return list of {title, description, published}."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 PolitiQuant/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        out = []
        for item in root.findall("./channel/item"):
            out.append({
                "title":       item.findtext("title", ""),
                "description": item.findtext("description", ""),
                "link":        item.findtext("link", ""),
                "pubDate":     item.findtext("pubDate", ""),
            })
        return out
    except Exception:
        return []


def _fetch_finnhub_general(api_key: str) -> list[dict]:
    """Fetch Finnhub general news (/news?category=general)."""
    if not api_key:
        return []
    try:
        url = f"https://finnhub.io/api/v1/news?category=general&token={api_key}"
        with urllib.request.urlopen(url, timeout=6) as resp:
            items = json.loads(resp.read())
        return [
            {
                "title":       i.get("headline", ""),
                "description": i.get("summary", ""),
                "link":        i.get("url", ""),
                "pubDate":     "",
            }
            for i in items
        ]
    except Exception:
        return []


def _load_api_key() -> str:
    try:
        _dir = os.path.dirname(__file__)
        with open(os.path.join(_dir, "api_keys.json")) as f:
            return json.load(f).get("finnhub_api_key", "")
    except Exception:
        return ""


def _fetch_all_political_news() -> list[dict]:
    """
    Aggregate political/presidential news from all sources.
    Results are cached in-memory for 1 hour.
    """
    with _CACHE_LOCK:
        cached = _CACHE.get(_NEWS_CACHE_KEY)
        if cached and time.time() - cached["ts"] < _CACHE_TTL_SECS:
            return cached["items"]

    items: list[dict] = []

    # 1. White House official press releases
    items += _fetch_rss("https://www.whitehouse.gov/news/feed/")

    # 2. Finnhub general news
    items += _fetch_finnhub_general(_load_api_key())

    # 3. NYT Business RSS (free, no key)
    items += _fetch_rss("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml")

    # 4. NYT Politics RSS (free, no key)
    items += _fetch_rss("https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml")

    # Deduplicate by title
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        key = item["title"].strip().lower()[:80]
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)

    with _CACHE_LOCK:
        _CACHE[_NEWS_CACHE_KEY] = {"ts": time.time(), "items": deduped}

    return deduped


def _text_of(item: dict) -> str:
    """Combine title + description into one searchable string."""
    return (item.get("title", "") + " " + item.get("description", "")).lower()


def _has_political_actor(text: str) -> bool:
    return any(re.search(pat, text, re.I) for pat in _POLITICAL_ACTORS)


def _sentiment_score(text: str) -> int:
    """
    Return a raw sentiment integer for political content about a company.
    Positive = good for stock, Negative = bad.
    """
    pos = sum(1 for p in _POSITIVE_PATTERNS if re.search(p, text, re.I))
    neg = sum(1 for p in _NEGATIVE_PATTERNS if re.search(p, text, re.I))
    return pos - neg


def get_political_signal(ticker: str, company_name: str = "") -> dict:
    """
    Scan recent political news for mentions of `ticker` or `company_name`.

    Returns:
        {
          "score_mod":   int,          # points to add/subtract in scorer
          "flag":        str | None,   # display badge e.g. "🏛️ Presidential +deal"
          "sentiment":   str,          # "positive" | "negative" | "neutral"
          "headlines":   list[str],    # matching headline snippets (≤3)
          "mention_count": int,
        }
    """
    all_news = _fetch_all_political_news()

    ticker_lc      = ticker.lower()
    company_lc     = (company_name or "").lower().strip()
    # Short company name (drop "Inc", "Corp", "Ltd", etc.)
    short_co       = re.sub(r"\s+(inc|corp|ltd|llc|co|plc|group|holdings?)\.?$",
                            "", company_lc, flags=re.I).strip()

    # Also check common aliases from our map
    aliases: set[str] = {ticker_lc}
    if short_co:
        aliases.add(short_co)
    # Reverse-lookup: any known company name that maps to this ticker
    for name, tk in _COMPANY_TO_TICKER.items():
        if tk and tk.upper() == ticker.upper():
            aliases.add(name.lower())

    matched_items: list[tuple[int, str, str]] = []  # (score, title, link)

    for item in all_news:
        text = _text_of(item)

        # Must mention the company/ticker — use word-boundary for short tickers
        # to avoid "ba" matching "obama", "cuba", etc.
        def _alias_match(alias: str, text: str) -> bool:
            if not alias:
                return False
            # Always use word boundaries — prevents "meta" matching "metal",
            # "apple" matching "applet", "amazon" matching "amazonian", etc.
            return bool(re.search(r'\b' + re.escape(alias) + r'\b', text, re.I))

        if not any(_alias_match(alias, text) for alias in aliases):
            continue

        # Must have a political context
        if not _has_political_actor(text):
            continue

        score = _sentiment_score(text)
        matched_items.append((score, item["title"], item.get("link", "")))

    if not matched_items:
        return {
            "score_mod":     0,
            "flag":          None,
            "sentiment":     "neutral",
            "headlines":     [],
            "mention_count": 0,
        }

    total_score    = sum(s for s, _, _ in matched_items)
    mention_count  = len(matched_items)
    top_headlines  = [t for _, t, _ in sorted(matched_items, key=lambda x: abs(x[0]), reverse=True)][:3]

    # Convert raw score to scorer point modifier
    if total_score >= 4:
        score_mod = 6
        sentiment = "positive"
        flag      = f"🏛️ Presidential +deal ({mention_count} mention{'s' if mention_count > 1 else ''})"
    elif total_score >= 2:
        score_mod = 4
        sentiment = "positive"
        flag      = f"🏛️ Positive political mention ({mention_count})"
    elif total_score == 1:
        score_mod = 2
        sentiment = "positive"
        flag      = f"🏛️ Political mention — slight positive"
    elif total_score == 0:
        score_mod = 0
        sentiment = "neutral"
        flag      = f"🏛️ Political mention — neutral ({mention_count})"
    elif total_score >= -2:
        score_mod = -4
        sentiment = "negative"
        flag      = f"🏛️ Political headwind — tariff/sanction risk (−4)"
    elif total_score >= -4:
        score_mod = -7
        sentiment = "negative"
        flag      = f"🏛️ Political threat — investigation/tariff (−7)"
    else:
        score_mod = -10
        sentiment = "negative"
        flag      = f"🏛️ Major political risk — ban/sanction/antitrust (−10)"

    return {
        "score_mod":     score_mod,
        "flag":          flag,
        "sentiment":     sentiment,
        "headlines":     top_headlines,
        "mention_count": mention_count,
    }


def clear_cache() -> None:
    """Force-refresh news cache on next call."""
    with _CACHE_LOCK:
        _CACHE.pop(_NEWS_CACHE_KEY, None)


# ── Trump family name patterns ─────────────────────────────────────────────────
_TRUMP_FAMILY = [
    r"\btrump\b",
    r"\bivanka\b",
    r"\bjared\s+kushner\b", r"\bkushner\b",
    r"\bdonald\s+trump\s+jr\b", r"\btrump\s+jr\b",
    r"\beric\s+trump\b",
    r"\blara\s+trump\b",
    r"\bmelania\b",
    r"\bbarron\s+trump\b",
    r"\btrump\s+media\b",   r"\btruth\s+social\b",
    r"\bdjt\b",             r"\btrump\s+organization\b",
]

# ── All known tickers (superset used for extraction from headlines) ────────────
_ALL_KNOWN_TICKERS: set[str] = set(
    v for v in _COMPANY_TO_TICKER.values() if v
)
# Add extra well-known tickers not in company map
_ALL_KNOWN_TICKERS.update([
    "DJT", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "INTC", "AMD", "QCOM", "AVGO", "ORCL", "CRM", "IBM", "CSCO",
    "JPM", "GS", "MS", "BAC", "C", "WFC", "BLK", "V", "MA",
    "PFE", "MRNA", "JNJ", "UNH", "LLY", "MRK", "ABBV",
    "XOM", "CVX", "BA", "LMT", "RTX", "NOC", "GE", "F", "GM",
    "WMT", "COST", "TGT", "HD", "SBUX", "MCD", "NKE", "DIS",
    "BABA", "BIDU", "JD", "TCEHY", "NVO", "SHEL", "BP",
    "COIN", "PYPL", "SHOP", "SQ", "PLTR", "SNOW", "UBER",
    "SPY", "QQQ", "GLD", "SLV", "USO",
])


def get_trump_family_news_feed(hours: int = 48) -> list[dict]:
    """
    Return recent news articles that mention Trump or his family AND reference
    any recognisable stock/company.  Results are sorted newest-first.

    Each item:
        {
          "title":     str,
          "link":      str,
          "pubDate":   str,
          "tickers":   list[str],    # extracted tickers / company mentions
          "companies": list[str],    # human-readable company names
          "sentiment": str,          # "positive" | "negative" | "neutral"
          "score":     int,          # raw sentiment score
          "speaker":   str,          # which family member is mentioned
        }
    """
    all_news = _fetch_all_political_news()
    results  = []

    for item in all_news:
        text  = _text_of(item)
        title = item.get("title", "")

        # Must mention a Trump family member
        speakers = [
            pat.strip(r"\b").replace(r"\s+", " ").replace("\\", "")
            for pat in _TRUMP_FAMILY
            if re.search(pat, text, re.I)
        ]
        if not speakers:
            continue

        # Determine the most specific speaker name
        speaker = "Trump family"
        for pat, label in [
            (r"\bivanka\b",            "Ivanka Trump"),
            (r"\bkushner\b",           "Jared Kushner"),
            (r"\btrump\s+jr\b",        "Donald Trump Jr."),
            (r"\beric\s+trump\b",      "Eric Trump"),
            (r"\blara\s+trump\b",      "Lara Trump"),
            (r"\bmelania\b",           "Melania Trump"),
            (r"\btrump\b",             "Donald Trump"),
        ]:
            if re.search(pat, text, re.I):
                speaker = label
                break

        # Extract mentioned companies / tickers
        found_tickers:  list[str] = []
        found_companies: list[str] = []
        for name, ticker in _COMPANY_TO_TICKER.items():
            if not name or len(name) <= 2:
                continue
            # Word-boundary match — prevents "meta" → "metal", "apple" → "applet", etc.
            if re.search(r'\b' + re.escape(name) + r'\b', text, re.I):
                if ticker and ticker not in found_tickers:
                    found_tickers.append(ticker)
                if name.title() not in found_companies:
                    found_companies.append(name.title())

        # Also scan for bare ticker symbols near the title text
        # (only in ALL-CAPS sequences 2-5 chars that are known tickers)
        for match in re.finditer(r'\b([A-Z]{2,5})\b', title):
            sym = match.group(1)
            if sym in _ALL_KNOWN_TICKERS and sym not in found_tickers:
                found_tickers.append(sym)

        # Score sentiment
        score = _sentiment_score(text)
        if score > 0:
            sentiment = "positive"
        elif score < 0:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        results.append({
            "title":     title,
            "link":      item.get("link", ""),
            "pubDate":   item.get("pubDate", ""),
            "tickers":   found_tickers[:6],
            "companies": found_companies[:6],
            "sentiment": sentiment,
            "score":     score,
            "speaker":   speaker,
        })

    # Sort: highest absolute sentiment score first, then by position in feed
    results.sort(key=lambda x: abs(x["score"]), reverse=True)
    return results
