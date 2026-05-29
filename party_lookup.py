"""
Party affiliation lookup for U.S. legislators.

Source: https://unitedstates.github.io/congress-legislators/legislators-current.json
        Official open-source dataset (GitHub Pages mirror) — no API key, updated continuously.
        Manual overrides cover senators not yet in the dataset (recent elections, dataset lag).

Caches locally for 7 days (party affiliations rarely change).
Provides fuzzy name matching since PTR names sometimes differ slightly
from official names (e.g. "Mike" vs "Michael").
"""

import json
import os
import difflib
from datetime import date, timedelta

import requests

import cache_db as _cdb

_DIR        = os.path.dirname(os.path.abspath(__file__))
_NAMESPACE  = "party"
_CACHE_KEY  = "legislators"
_URL        = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
_TTL_DAYS   = 7

# Party display config
PARTY_STYLE = {
    "D": {"label": "Democrat",    "short": "D", "colour": "#3b82f6", "emoji": "🔵"},
    "R": {"label": "Republican",  "short": "R", "colour": "#ef4444", "emoji": "🔴"},
    "I": {"label": "Independent", "short": "I", "colour": "#a855f7", "emoji": "🟣"},
    "?": {"label": "Unknown",     "short": "?", "colour": "#6b7280", "emoji": "⚪"},
}


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    return _cdb.get(_NAMESPACE, _CACHE_KEY) or {}


def _save_cache(data: dict):
    _cdb.set(_NAMESPACE, _CACHE_KEY, data)


# ── Fetch + build lookup ───────────────────────────────────────────────────────

def _fetch_legislators() -> dict:
    """
    Fetch current legislators and build a name → party lookup.
    Returns {
        "fetched_date": "YYYY-MM-DD",
        "by_full_name": {"Nancy Pelosi": "D", ...},
        "by_last_name": {"Pelosi": ["D"], ...},   # list in case of same last name
    }
    """
    try:
        r = requests.get(_URL, timeout=15)
        r.raise_for_status()
        legislators = r.json()
    except Exception as e:
        return {"error": str(e), "by_full_name": {}, "by_last_name": {}}

    by_full  = {}
    by_last  = {}

    for leg in legislators:
        names = leg.get("name", {})
        terms = leg.get("terms", [])
        if not terms:
            continue

        # Most recent term
        latest = terms[-1]
        party_full = latest.get("party", "")
        if "Democrat" in party_full:
            party = "D"
        elif "Republican" in party_full:
            party = "R"
        elif "Independent" in party_full or "Independen" in party_full:
            party = "I"
        else:
            party = "?"

        # Store by multiple name variants for fuzzy matching
        full = names.get("official_full", "").strip()
        first = names.get("first", "").strip()
        last  = names.get("last",  "").strip()
        nick  = names.get("nickname", "").strip()

        for name in filter(None, [
            full,
            f"{first} {last}".strip(),
            f"{nick} {last}".strip() if nick else None,
        ]):
            by_full[name.lower()] = party

        if last:
            by_last.setdefault(last.lower(), [])
            if party not in by_last[last.lower()]:
                by_last[last.lower()].append(party)

    return {
        "fetched_date": date.today().isoformat(),
        "by_full_name": by_full,
        "by_last_name": by_last,
        "error": None,
    }


def get_lookup() -> dict:
    """Return the party lookup, fetching fresh data if cache is stale."""
    cache = _load_cache()
    fetched = cache.get("fetched_date", "")
    try:
        stale = not fetched or (
            date.today() - date.fromisoformat(fetched)
        ).days >= _TTL_DAYS
    except Exception:
        stale = True

    if stale or not cache.get("by_full_name"):
        fresh = _fetch_legislators()
        if not fresh.get("error"):
            _save_cache(fresh)
            return fresh
        # Return stale cache if fetch failed
        return cache if cache.get("by_full_name") else fresh

    return cache


# ── Public API ─────────────────────────────────────────────────────────────────

# Manual overrides for politicians not yet in the legislators-current dataset
# (recent elections, dataset update lag, unusual name spellings).
# Keys are lowercase normalised names as they appear in the scraped data.
_OVERRIDES: dict = {
    # Senate — politicians not in or mismatched by the legislators-current dataset
    "markwayne mullin":           "R",  # OK Senator, missing from dataset
    "a. mitchell mcconnell jr.":  "R",  # Mitch McConnell (suffix stripped)
    "a. mitchell mcconnell, jr.": "R",  # Mitch McConnell (comma variant)
    "william f hagerty iv":       "R",  # Bill Hagerty, TN
    "william f hagerty, iv":      "R",  # comma variant
    "james conley justice ii":    "R",  # Jim Justice, WV
    "james conley justice, ii":   "R",  # comma variant
    "rafael e cruz":              "R",  # Ted Cruz, TX
    "jon a husted":               "R",  # Jon Husted, OH
    "jerry moran":                "R",  # Jerry Moran, KS
    "jerry moran,":               "R",  # trailing comma artifact
    "john r curtis":              "R",  # UT Senator
    "bernie moreno":              "R",  # OH Senator
    "adam b schiff":              "D",  # CA Senator
    "ron l wyden":                "D",  # OR Senator
    "mark r warner":              "D",  # VA Senator
    "angus s king jr.":           "I",  # Angus King, ME (suffix stripped)
    "david h mccormick":          "R",  # PA Senator (casing variant)
}

# Name-part suffixes to strip before last-name lookup
_SUFFIXES = {"jr.", "sr.", "ii", "iii", "iv", "v", "jr", "sr"}


def _clean_name(raw: str) -> str:
    """Lowercase, strip trailing commas/punctuation for matching."""
    return raw.strip().lower().rstrip(",.")


def get_party(name: str, lookup: dict = None) -> str:
    """
    Return party code ("D", "R", "I", "?") for a politician name.
    Uses fuzzy matching so minor name variations still resolve correctly.
    """
    if not name:
        return "?"

    if lookup is None:
        lookup = get_lookup()

    by_full    = lookup.get("by_full_name", {})
    by_last    = lookup.get("by_last_name", {})
    name_lower = _clean_name(name)

    # 0. Manual override — fastest, most accurate for known mismatches
    if name_lower in _OVERRIDES:
        return _OVERRIDES[name_lower]

    # 1. Exact full-name match
    if name_lower in by_full:
        return by_full[name_lower]

    # 2. Fuzzy full-name match (handles "Mike" vs "Michael", middle initials etc.)
    close = difflib.get_close_matches(name_lower, by_full.keys(), n=1, cutoff=0.82)
    if close:
        return by_full[close[0]]

    # 3. Last-name match — strip suffixes (Jr., II, IV…) before extracting last name
    parts = name_lower.rstrip(".,").split()
    # Walk backwards past any suffix tokens
    while parts and parts[-1].rstrip(".,") in _SUFFIXES:
        parts.pop()
    if parts:
        last = parts[-1].rstrip(".,")
        if last in by_last and len(by_last[last]) == 1:
            return by_last[last][0]

    return "?"


def enrich_trades(trades: list) -> list:
    """
    Add a "party" field to each trade dict.
    Fetches the lookup once and reuses it for all trades.
    """
    if not trades:
        return trades
    lookup = get_lookup()
    for trade in trades:
        if "party" not in trade:
            trade["party"] = get_party(trade.get("name", ""), lookup)
    return trades


def party_badge(party: str) -> str:
    """Return a coloured HTML badge for a party code."""
    style = PARTY_STYLE.get(party, PARTY_STYLE["?"])
    colour = style["colour"]
    label  = style["short"]
    return (
        f"<span style='background:{colour}22; color:{colour}; "
        f"border:1px solid {colour}55; border-radius:4px; "
        f"padding:1px 6px; font-size:0.78rem; font-weight:700;'>"
        f"{label}</span>"
    )
