"""
Headless two-tier market scan — no Streamlit required.

Two tiers, two jobs, ONE merged report:

  Tier 1  ── Yahoo-only, EVERY stock (~7k), NO Finnhub, NO market-cap floor.
             Fast and complete.  fetch_fundamentals(use_finnhub=False) makes
             zero Finnhub calls, so it is not throttled by the 55/min cap.
             Scores the whole market on yfinance data + analyst targets.
             Publishes to JobState("growth_report").

  Tier 2  ── Yahoo + Finnhub enrichment, ONLY for the big, analyst-covered names
             (market_cap >= $2B AND num_analyst_opinions >= 1) — ~2k stocks.
             The analyst-coverage gate means no Finnhub call is ever wasted on a
             stock Finnhub has no recommendation data for.  Re-scores those rows
             with the richer Finnhub signal and UPGRADES them in place inside the
             same growth_report result.  Progress tracked separately in
             JobState("growth_report_enrich").  Runs long (~3.5-4.5 hr) because of
             the Finnhub cap; the app keeps showing the Tier-1 report meanwhile and
             the big-cap rows improve as enrichment lands.

Why two jobs?  Tier 1 finishes in minutes and the user sees a full-market report
immediately.  Tier 2 grinds through the Finnhub budget in the background and the
sidebar shows its own progress bar without ever blanking the Tier-1 results.

Run manually:
    venv/bin/python scan_market_job.py both          # Tier 1 then Tier 2 (default)
    venv/bin/python scan_market_job.py tier1         # full market, Yahoo-only, fast
    venv/bin/python scan_market_job.py tier2         # enrich whatever tier1 published

Test on a few tickers (--dry-run skips publishing to JobState):
    venv/bin/python scan_market_job.py both AAPL MSFT NVDA
    venv/bin/python scan_market_job.py --dry-run both AAPL MSFT NVDA

Via cron (9 AM ET weekdays — installed separately):
    0 9 * * 1-5 /…/venv/bin/python /…/scan_market_job.py both >> /tmp/politiquant_scan.log 2>&1
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

from fundamentals import fetch_fundamentals
from scorer import score_stock_detailed, signal_label
from score_history import save_scores
from scraper import load_cache
from job_state import JobState

# ── Configuration ──────────────────────────────────────────────────────────────
MIN_PRICE          = 1.0                # skip sub-$1 penny stocks (Growth Report default)
TIER2_MIN_CAP      = 2_000_000_000      # $2B floor — Tier-2 (Finnhub) gate
TIER2_MIN_ANALYSTS = 1                  # require >=1 analyst opinion so no Finnhub call is wasted
WORKERS            = 4                  # kept low to avoid macOS fd exhaustion
_CHECKPOINT        = 250                # Tier 2: republish the merged report every N enrichments

_GROWTH_JOB = JobState("growth_report")          # Tier 1 — full market, Yahoo-only
_ENRICH_JOB = JobState("growth_report_enrich")   # Tier 2 — Finnhub enrichment of big caps
_LOCK = threading.Lock()


# ── fd limit (macOS default is 256; full scan opens many sockets) ───────────────
def _raise_fd_limit():
    try:
        import resource as _res
        _s, _h = _res.getrlimit(_res.RLIMIT_NOFILE)
        _t = min(65536, _h) if _h > 0 else 65536
        if _s < _t:
            _res.setrlimit(_res.RLIMIT_NOFILE, (_t, _h))
    except Exception:
        pass


# ── Universe — full US market from NASDAQ trader directory (free, official) ─────
def _all_us_stocks() -> list:
    _h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    def _parse_nasdaq(text: str) -> list:
        out = []
        for line in text.strip().split("\n")[1:]:          # skip header
            p = line.split("|")
            if len(p) < 7:
                continue
            sym, test, etf = p[0].strip(), p[3].strip(), p[6].strip()
            if test == "Y" or etf == "Y":
                continue
            if sym and len(sym) <= 5 and not sym.startswith("$"):
                out.append(sym.upper())
        return out

    def _parse_other(text: str) -> list:
        out = []
        for line in text.strip().split("\n")[1:]:
            p = line.split("|")
            if len(p) < 7:
                continue
            sym, etf, test = p[0].strip(), p[4].strip(), p[6].strip()
            if test == "Y" or etf == "Y":
                continue
            if sym and len(sym) <= 5 and not sym.startswith("$"):
                out.append(sym.upper())
        return out

    r1 = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                      headers=_h, timeout=20)
    r1.raise_for_status()
    r2 = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
                      headers=_h, timeout=20)
    r2.raise_for_status()

    tickers = sorted(set(_parse_nasdaq(r1.text) + _parse_other(r2.text)))
    if not tickers:
        raise ValueError("Ticker list came back empty — unexpected parse error.")
    return tickers


# ── Phase-1 price-only pre-filter (Tier 1 has NO market-cap floor) ──────────────
def _passes_price_filter(ticker: str) -> tuple:
    try:
        fi = yf.Ticker(ticker).fast_info
        price = fi.get("lastPrice") or fi.get("last_price") or 0
        return (price >= MIN_PRICE), float(price)
    except Exception:
        return True, 0.0      # can't check → don't skip, let the full fetch decide


def _momentum_from_fund(fund: dict) -> dict:
    return {
        "rsi":          fund.get("rsi"),
        "mom_1m_pct":   fund.get("mom_1m_pct"),
        "mom_3m_pct":   fund.get("mom_3m_pct"),
        "vs_50ma_pct":  fund.get("vs_50ma_pct"),
        "vs_200ma_pct": fund.get("vs_200ma_pct"),
    }


def _safe(v):
    try:
        return round(float(v), 1)
    except Exception:
        return None


def _load_political_df() -> pd.DataFrame:
    """Congressional trades, normalised — for the politician-buys scoring factor."""
    trades_raw = load_cache()
    df_all = pd.DataFrame(trades_raw) if trades_raw else pd.DataFrame()
    if not df_all.empty:
        df_all["ticker"]           = df_all["ticker"].fillna("").str.upper().str.strip()
        df_all["transaction_type"] = df_all["transaction_type"].fillna("")
        df_all["transaction_date"] = pd.to_datetime(df_all["transaction_date"], errors="coerce")
    return df_all


# ── Row builder — same shape as _fetch_one() in the Growth Report page ──────────
def _build_row(ticker: str, fund: dict, df_all: pd.DataFrame, cutoff_30d) -> dict:
    mom = _momentum_from_fund(fund)

    if not df_all.empty:
        pol_mask = (
            (df_all["ticker"] == ticker) &
            (df_all["transaction_type"].str.contains("Purchase", case=False, na=False)) &
            (df_all["transaction_date"] >= cutoff_30d)
        )
        pol_buys_30d = int(pol_mask.sum())
    else:
        pol_buys_30d = 0

    merged = dict(fund); merged.update(mom)
    score, reasons, factor_pts = score_stock_detailed(merged, pol_buys_30d)
    signal, colour = signal_label(score)

    target = fund.get("analyst_target")
    cp     = fund.get("current_price")
    try:    upside = (target - cp) / cp * 100 if target and cp else None
    except Exception: upside = None

    return {
        "ticker": ticker, "company": fund.get("company_name", ticker),
        "sector": fund.get("sector") or "—", "score": round(score, 1),
        "signal": signal, "colour": colour, "factor_pts": factor_pts,
        "price": cp, "analyst_target": target, "upside_pct": upside,
        "analyst_rating": fund.get("analyst_rating") or "N/A",
        "num_analyst_opinions": fund.get("num_analyst_opinions"),
        "rsi": _safe(mom.get("rsi")),
        "mom_1m_pct": _safe(mom.get("mom_1m_pct")),
        "mom_3m_pct": _safe(mom.get("mom_3m_pct")),
        "mom_6m_pct": _safe(mom.get("mom_6m_pct")),
        "vs_50ma_pct": _safe(mom.get("vs_50ma_pct")),
        "positive_months_6":  fund.get("positive_months_6"),
        "positive_months_12": fund.get("positive_months_12"),
        "revenue_trend": _safe(fund.get("revenue_trend")),
        "ni_trend":      _safe(fund.get("ni_trend")),
        "monthly_returns": fund.get("monthly_returns", []),
        "pol_buys_30d":       pol_buys_30d,
        "eps_yoy_pct":        fund.get("eps_yoy_pct"),
        "fcf":                fund.get("free_cash_flow"),
        "pe_ratio":           fund.get("pe_ratio"),
        "forward_pe":         fund.get("forward_pe"),
        "market_cap":         fund.get("market_cap"),
        "reasons":            reasons,
        "insider_buy_score":  fund.get("insider_buy_score", 0),
        "insider_buy_count":  fund.get("insider_buy_count", 0),
        "insider_ceo_bought": fund.get("insider_ceo_bought", False),
        "insider_cfo_bought": fund.get("insider_cfo_bought", False),
        "insider_buys":       fund.get("insider_buys", []),
        "inst_score":         fund.get("inst_score", 0),
        "inst_buyer_count":   fund.get("inst_buyer_count", 0),
        "inst_tier1_buying":  fund.get("inst_tier1_buying", False),
        "inst_tier1_buyers":  fund.get("inst_tier1_buyers", []),
        "inst_buyers":        fund.get("inst_buyers", []),
        "inst_new_positions": fund.get("inst_new_positions", []),
        "inst_pct_held":      fund.get("inst_pct_held", 0.0),
        "earnings_days_until": fund.get("earnings_days_until"),
        "volume_ratio":        fund.get("volume_ratio"),
        "today_change_pct":    fund.get("today_change_pct"),
        "pct_from_52w_high":   fund.get("pct_from_52w_high"),
        "pct_from_52w_low":    fund.get("pct_from_52w_low"),
        "week_52_high":        fund.get("week_52_high"),
        "week_52_low":         fund.get("week_52_low"),
        "analyst_upgrades_30d":   fund.get("analyst_upgrades_30d", 0),
        "analyst_downgrades_30d": fund.get("analyst_downgrades_30d", 0),
        "analyst_upgrade_firms":  fund.get("analyst_upgrade_firms", []),
        "analyst_downgrade_firms":fund.get("analyst_downgrade_firms", []),
        # Bookkeeping: True once a Finnhub-enriched (Tier 2) fetch backed this row.
        "finnhub_attempted":  fund.get("finnhub_attempted", True),
    }


def _history_batch(rows: list) -> list:
    return [
        {"ticker": r["ticker"], "price": r["price"], "score": r["score"],
         "factor_pts": r.get("factor_pts", {})}
        for r in rows if r.get("price")
    ]


# ── Tier 1: Yahoo-only, full market ─────────────────────────────────────────────
def run_tier1(universe: list, publish: bool = True, save_history: bool = True) -> list:
    """
    Score EVERY stock on Yahoo data alone (no Finnhub, no market-cap floor).
    Publishes to JobState("growth_report").  Returns the scored rows.

    save_history=False is used by run_both() so only the FINAL merged rows (after
    Tier-2 enrichment) feed the ML history — save_scores() dedupes per ticker/day,
    so recording the weaker Tier-1 score first would block the better Tier-2 score.
    """
    _raise_fd_limit()
    started = datetime.now()
    print(f"\n{'='*64}")
    print(f"  TIER 1 — Yahoo-only full-market scan  ·  {started.strftime('%Y-%m-%d %H:%M')}")
    print(f"  (min ${MIN_PRICE:.0f}, no cap floor, {WORKERS} workers, 0 Finnhub calls)")
    print(f"{'='*64}")
    print(f"  Universe: {len(universe)} tickers   publish={publish}")

    cutoff_30d = pd.Timestamp(date.today() - timedelta(days=30))
    df_all     = _load_political_df()

    skipped = {"penny": 0, "mktcap": 0, "error": 0}   # mktcap stays 0 — Tier 1 has no cap floor
    done    = [0]

    if publish:
        _GROWTH_JOB.reset()
        _GROWTH_JOB.start(total=len(universe),
                          meta={"universe_key": "scan_market_job", "tier": 1})

    # ── Phase 1: parallel price-only pre-filter ────────────────────────────────
    print("\n[1/2] Pre-filtering (fast_info price check — no Finnhub)…")
    passing = []

    def _filter_one(ticker):
        passes, price = _passes_price_filter(ticker)
        with _LOCK:
            done[0] += 1
            if publish:
                _GROWTH_JOB.update(done=done[0], current=f"[filtering] {ticker}")
        return ticker, passes, price

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_filter_one, t): t for t in universe}
        for fut in as_completed(futs):
            try:
                ticker, passes, price = fut.result()
                if passes:
                    passing.append(ticker)
                else:
                    with _LOCK:
                        skipped["penny"] += 1
            except Exception:
                with _LOCK:
                    skipped["error"] += 1

    print(f"      {len(passing)} passed  ·  {skipped['penny']} penny  ·  {skipped['error']} error")

    # ── Phase 2: parallel full fetch (Yahoo-only) + score ──────────────────────
    done[0] = 0
    if publish:
        _GROWTH_JOB.start(total=len(passing),
                          meta={"universe_key": "scan_market_job", "tier": 1})

    print(f"\n[2/2] Full fetch + score ({len(passing)} stocks, Yahoo-only)…")
    rows = []

    def _fetch_one(ticker):
        try:
            fund = fetch_fundamentals(ticker, use_finnhub=False)   # ← Yahoo-only, 0 Finnhub
            if fund.get("error") and not fund.get("current_price"):
                with _LOCK:
                    skipped["error"] += 1; done[0] += 1
                    if publish: _GROWTH_JOB.update(done=done[0], current=ticker)
                return None
            price = fund.get("current_price") or 0
            if price < MIN_PRICE:
                with _LOCK:
                    skipped["penny"] += 1; done[0] += 1
                    if publish: _GROWTH_JOB.update(done=done[0], current=ticker)
                return None

            row = _build_row(ticker, fund, df_all, cutoff_30d)
            with _LOCK:
                rows.append(row); done[0] += 1
                if publish: _GROWTH_JOB.update(done=done[0], current=ticker)
            return row
        except Exception:
            with _LOCK:
                skipped["error"] += 1; done[0] += 1
                if publish: _GROWTH_JOB.update(done=done[0], current=ticker)
            return None

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(_fetch_one, t): t for t in passing}
            for i, fut in enumerate(as_completed(futs), 1):
                fut.result()
                if i % 100 == 0:
                    print(f"      {i}/{len(passing)} fetched…  ({len(rows)} scored)")

        rows.sort(key=lambda r: r["score"], reverse=True)

        if save_history and publish:
            try:
                save_scores(_history_batch(rows))
            except Exception:
                pass
    finally:
        if publish:
            _GROWTH_JOB.finish(result={"rows": rows, "skipped": skipped,
                                       "universe_key": tuple(universe), "tier": 1})

    elapsed = (datetime.now() - started).total_seconds() / 60
    sb = sum(1 for r in rows if r["signal"] == "STRONG BUY")
    b  = sum(1 for r in rows if r["signal"] == "BUY")
    print(f"\n  TIER 1 done in {elapsed:.1f} min  ·  {len(rows)} scored  "
          f"·  {sb} Strong Buy  ·  {b} Buy")
    if publish:
        print("  Published to JobState('growth_report') — the app shows it instantly.")
    print()
    return rows


# ── Tier 2: Finnhub enrichment of the big, analyst-covered names ────────────────
def run_tier2(rows: list = None, skipped: dict = None, universe_key=None,
              publish: bool = True, save_history: bool = True) -> list:
    """
    Re-score the big caps with full Finnhub enrichment and merge them back into the
    growth_report report in place.  Gate: market_cap >= $2B AND >=1 analyst opinion.

    rows=None  → load the Tier-1 rows from JobState("growth_report") (tier2-only run).
    Otherwise  → operate on the rows passed in (chained from run_tier1 in run_both).
    Progress is tracked in JobState("growth_report_enrich"); the merged report is
    rewritten into JobState("growth_report") at checkpoints and at the end.
    """
    _raise_fd_limit()
    started = datetime.now()

    prev = _GROWTH_JOB.result() or {}
    if rows is None:
        rows = prev.get("rows")
        if not rows:
            print("\n  TIER 2 — no Tier-1 rows in JobState('growth_report'). "
                  "Run 'tier1' or 'both' first.\n")
            return []
    if skipped is None:
        skipped = prev.get("skipped", {})
    if universe_key is None:
        universe_key = prev.get("universe_key")

    df_all     = _load_political_df()
    cutoff_30d = pd.Timestamp(date.today() - timedelta(days=30))

    # Merge target — keyed by ticker so Tier 2 can upgrade rows in place.
    by_ticker = {r["ticker"]: r for r in rows}

    # Gate: big AND analyst-covered, so every Finnhub call earns its keep.
    candidates = sorted(
        r["ticker"] for r in rows
        if (r.get("market_cap") or 0) >= TIER2_MIN_CAP
        and (r.get("num_analyst_opinions") or 0) >= TIER2_MIN_ANALYSTS
    )

    est_min = len(candidates) / 11.0   # ~5 Finnhub calls/stock at 55/min ≈ 11 stocks/min
    print(f"\n{'='*64}")
    print(f"  TIER 2 — Finnhub enrichment  ·  {started.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Gate: cap >= ${TIER2_MIN_CAP/1e9:.0f}B AND >= {TIER2_MIN_ANALYSTS} analyst")
    print(f"{'='*64}")
    print(f"  {len(candidates)} of {len(rows)} rows qualify   "
          f"(~{est_min:.0f} min at 55 Finnhub/min)   publish={publish}")

    enriched = {"count": 0, "error": 0}
    done     = [0]

    if publish:
        _ENRICH_JOB.reset()
        _ENRICH_JOB.start(total=len(candidates),
                          meta={"universe_key": "scan_market_job", "tier": 2})

    def _publish_growth(final: bool):
        merged = sorted(by_ticker.values(), key=lambda r: r["score"], reverse=True)
        _GROWTH_JOB.finish(result={"rows": merged, "skipped": skipped,
                                   "universe_key": universe_key, "tier": 2,
                                   "enriching": not final})

    def _enrich_one(ticker):
        try:
            fund = fetch_fundamentals(ticker, use_finnhub=True)    # ← Finnhub on
            row  = _build_row(ticker, fund, df_all, cutoff_30d)
            with _LOCK:
                by_ticker[ticker] = row        # upgrade the Tier-1 row in place
                enriched["count"] += 1; done[0] += 1
                if publish: _ENRICH_JOB.update(done=done[0], current=ticker)
            return row
        except Exception:
            with _LOCK:
                enriched["error"] += 1; done[0] += 1
                if publish: _ENRICH_JOB.update(done=done[0], current=ticker)
            return None

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(_enrich_one, t): t for t in candidates}
            for i, fut in enumerate(as_completed(futs), 1):
                fut.result()
                if i % 50 == 0:
                    print(f"      {i}/{len(candidates)} enriched…")
                if publish and i % _CHECKPOINT == 0:
                    _publish_growth(final=False)   # live-update the merged report

        merged = sorted(by_ticker.values(), key=lambda r: r["score"], reverse=True)

        if publish:
            _publish_growth(final=True)            # final merged report
        if save_history and publish:
            try:
                save_scores(_history_batch(merged))   # record the best score per ticker
            except Exception:
                pass
    finally:
        if publish:
            _ENRICH_JOB.finish(result={"enriched": enriched,
                                       "candidates": len(candidates), "tier": 2})

    elapsed = (datetime.now() - started).total_seconds() / 60
    print(f"\n  TIER 2 done in {elapsed:.1f} min  ·  {enriched['count']} enriched  "
          f"·  {enriched['error']} error")
    if publish:
        print("  Merged report rewritten into JobState('growth_report').")
    print()
    return sorted(by_ticker.values(), key=lambda r: r["score"], reverse=True)


# ── Both tiers chained ──────────────────────────────────────────────────────────
def run_both(universe: list, publish: bool = True) -> list:
    """Tier 1 (fast, full market) then Tier 2 (slow, Finnhub on the big caps)."""
    rows = run_tier1(universe, publish=publish, save_history=False)
    return run_tier2(rows=rows, universe_key=tuple(universe),
                     publish=publish, save_history=True)


if __name__ == "__main__":
    args    = sys.argv[1:]
    dry_run = "--dry-run" in args

    mode = "both"
    for _m in ("tier1", "tier2", "both"):
        if _m in [a.lower() for a in args]:
            mode = _m
            break

    tickers = [a.upper() for a in args
               if not a.startswith("--") and a.lower() not in ("tier1", "tier2", "both")]

    publish = not dry_run

    if mode == "tier2":
        if tickers:
            print("  (note: explicit tickers are ignored in 'tier2' mode — it enriches "
                  "whatever 'tier1' already published. Use 'both <tickers>' instead.)")
        run_tier2(rows=None, publish=publish, save_history=True)
    else:
        if tickers:
            universe = sorted(set(tickers))
        else:
            print("Fetching full US universe from NASDAQ trader directory…")
            universe = _all_us_stocks()

        if mode == "tier1":
            run_tier1(universe, publish=publish, save_history=True)
        else:  # both
            run_both(universe, publish=publish)
