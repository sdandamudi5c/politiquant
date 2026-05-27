"""
PolitiQuant — Smart Alert Engine

Filters new politician trades down to high-signal events worth emailing.

Three alert types (in priority order):
  1. CONSENSUS     — 3+ politicians bought the same stock in the last 30 days
  2. HIGH_SCORE    — politician bought a stock scoring ≥ threshold on Growth Report
  3. PORTFOLIO     — politician bought a stock you currently hold
  4. BIPARTISAN    — both Democrat and Republican bought same stock (rare, strong signal)

Each returned alert dict has everything needed to render a rich email card.
"""

import json
import os
from datetime import date, timedelta

_DIR             = os.path.dirname(os.path.abspath(__file__))
_FUND_CACHE_FILE = os.path.join(_DIR, "fundamentals_cache.json")


# ── Score helpers ──────────────────────────────────────────────────────────────

def _load_gr_scores() -> dict:
    """
    Return {ticker: score_row} from the latest Growth Report result.
    Falls back to fundamentals_cache for tickers not yet scored.
    """
    try:
        from job_state import JobState
        result = JobState("growth_report").result()
        if result and result.get("rows"):
            return {r["ticker"]: r for r in result["rows"]}
    except Exception:
        pass
    return {}


def _score_from_cache(ticker: str, fund_cache: dict) -> dict:
    """Score a ticker directly from fundamentals_cache (no API call)."""
    fund = fund_cache.get(ticker)
    if not fund or fund.get("error") or not fund.get("current_price"):
        return {}
    try:
        from scorer import score_stock, signal_label
        score, _  = score_stock(fund)
        label, col = signal_label(score)
        return {
            "ticker":  ticker,
            "score":   round(score, 1),
            "signal":  label,
            "colour":  col,
            "company": fund.get("company_name", ticker),
            "price":   fund.get("current_price"),
        }
    except Exception:
        return {}


def _get_score(ticker: str, gr_scores: dict, fund_cache: dict) -> dict:
    if ticker in gr_scores:
        return gr_scores[ticker]
    return _score_from_cache(ticker, fund_cache)


# ── Main engine ────────────────────────────────────────────────────────────────

def check_smart_alerts(
    new_trades:    list,
    all_trades:    list,
    my_tickers:    set  = None,
    min_score:     float = 58.0,
    min_pols:      int   = 3,
    lookback_days: int   = 30,
    alert_on_score:      bool = True,
    alert_on_consensus:  bool = True,
    alert_on_portfolio:  bool = True,
    alert_on_bipartisan: bool = True,
) -> list:
    """
    Evaluate new_trades against alert conditions.
    Returns a list of alert dicts sorted by priority (highest first).
    """
    if not new_trades:
        return []

    my_tickers = {t.upper() for t in (my_tickers or [])}

    # Load scoring data once
    gr_scores  = _load_gr_scores()
    fund_cache = {}
    try:
        with open(_FUND_CACHE_FILE) as f:
            fund_cache = json.load(f)
    except Exception:
        pass

    # Load party lookup once
    try:
        from party_lookup import get_lookup, get_party, PARTY_STYLE
        lookup = get_lookup()
        def _party(name): return get_party(name, lookup)
        def _pstyle(p):   return PARTY_STYLE.get(p, PARTY_STYLE["?"])
    except Exception:
        def _party(name): return "?"
        def _pstyle(p):   return {"emoji":"⚪","colour":"#888","label":"Unknown","short":"?"}

    # New purchases only
    new_buys = [
        t for t in new_trades
        if "purchase" in t.get("transaction_type", "").lower()
        and t.get("ticker") not in ("N/A", "--", "", None)
    ]

    if not new_buys:
        return []

    # Build recent-buys index (all trades, not just new, in the lookback window)
    cutoff = date.today() - timedelta(days=lookback_days)
    import pandas as pd
    try:
        df = pd.DataFrame(all_trades)
        df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
        recent_all_buys = df[
            df["transaction_type"].str.contains("Purchase", case=False, na=False) &
            (df["transaction_date"].dt.date >= cutoff)
        ]
    except Exception:
        recent_all_buys = pd.DataFrame()

    # Deduplicate: one alert per ticker (avoid N alerts for N new trades on same ticker)
    seen_tickers: set = set()
    events: list = []

    for trade in new_buys:
        ticker = trade["ticker"].upper().strip()
        if ticker in seen_tickers:
            continue

        # Score lookup
        score_row = _get_score(ticker, gr_scores, fund_cache)
        score     = score_row.get("score")
        signal    = score_row.get("signal", "N/A")
        colour    = score_row.get("colour", "#888888")
        company   = score_row.get("company", ticker)

        # Recent buys of this ticker by any politician
        if not recent_all_buys.empty and "ticker" in recent_all_buys.columns:
            tk_recent = recent_all_buys[
                recent_all_buys["ticker"].str.upper().str.strip() == ticker
            ]
        else:
            tk_recent = pd.DataFrame()

        unique_buyers = tk_recent["name"].nunique() if not tk_recent.empty else 1
        buyer_names   = tk_recent["name"].unique().tolist() if not tk_recent.empty else [trade.get("name","")]

        # Party breakdown
        buyer_parties = {n: _party(n) for n in buyer_names}
        d_count = sum(1 for p in buyer_parties.values() if p == "D")
        r_count = sum(1 for p in buyer_parties.values() if p == "R")
        bipartisan = d_count >= 1 and r_count >= 1

        # Buyer detail list for email cards
        buyer_list = []
        for _, row in (tk_recent.iterrows() if not tk_recent.empty else iter([])):
            p  = _party(row.get("name", ""))
            ps = _pstyle(p)
            buyer_list.append({
                "name":    row.get("name", ""),
                "party":   p,
                "emoji":   ps["emoji"],
                "colour":  ps["colour"],
                "label":   ps["label"],
                "date":    str(row.get("transaction_date", ""))[:10],
                "amount":  row.get("amount", "N/A"),
                "chamber": row.get("chamber", ""),
            })
        if not buyer_list:
            ps = _pstyle(_party(trade.get("name", "")))
            buyer_list.append({
                "name":    trade.get("name", ""),
                "party":   _party(trade.get("name", "")),
                "emoji":   ps["emoji"],
                "colour":  ps["colour"],
                "label":   ps["label"],
                "date":    str(trade.get("transaction_date", ""))[:10],
                "amount":  trade.get("amount", "N/A"),
                "chamber": trade.get("chamber", ""),
            })

        # ── Evaluate conditions ────────────────────────────────────────────────
        triggered: list = []

        if alert_on_consensus and unique_buyers >= min_pols:
            triggered.append({
                "type":     "CONSENSUS",
                "priority": 1,
                "headline": f"🏛️ {unique_buyers} politicians bought {ticker} in the last {lookback_days} days",
            })

        if alert_on_bipartisan and bipartisan and unique_buyers >= 2:
            triggered.append({
                "type":     "BIPARTISAN",
                "priority": 2,
                "headline": f"🤝 Bipartisan buy — 🔵{d_count}D + 🔴{r_count}R on {ticker}",
            })

        if alert_on_score and score is not None and score >= min_score:
            triggered.append({
                "type":     "HIGH_SCORE",
                "priority": 3,
                "headline": f"📈 {ticker} scores {score:.0f}/100 ({signal}) — {trade.get('name','')} just bought",
            })

        if alert_on_portfolio and ticker in my_tickers:
            triggered.append({
                "type":     "PORTFOLIO",
                "priority": 4,
                "headline": f"💼 {trade.get('name','')} bought {ticker} — you hold this stock",
            })

        if not triggered:
            continue

        # Highest-priority trigger for this ticker
        triggered.sort(key=lambda x: x["priority"])
        best = triggered[0]

        events.append({
            "ticker":        ticker,
            "company":       company,
            "score":         score,
            "signal":        signal,
            "colour":        colour,
            "unique_buyers": unique_buyers,
            "d_count":       d_count,
            "r_count":       r_count,
            "bipartisan":    bipartisan,
            "buyers":        buyer_list,
            "triggers":      triggered,         # all matched conditions
            "type":          best["type"],
            "priority":      best["priority"],
            "headline":      best["headline"],
            "in_portfolio":  ticker in my_tickers,
            "trade_date":    str(trade.get("transaction_date", ""))[:10],
        })
        seen_tickers.add(ticker)

    # Sort: consensus first, then bipartisan, high score, portfolio
    events.sort(key=lambda e: (e["priority"], -(e["score"] or 0)))
    return events
