# PolitiQuant — Project Context for Claude Code

## What this project is
A **Streamlit stock analysis app** that scores stocks based on politician trading disclosures, fundamentals, analyst consensus, insider activity, and momentum.
Runs locally on macOS. All data sources are **free** — no paid APIs allowed.

## Critical constraint: FREE ONLY
Every data source used must be completely free:
- ✅ yfinance (free, no key)
- ✅ Finnhub free tier (`api_keys.json` → `finnhub_api_key`) — 55 calls/min hard cap
- ✅ FRED API (`api_keys.json` → `fred_api_key`) — macro indicators
- ✅ SEC EDGAR (free public API)
- ✅ Reddit public API (no key, unauthenticated)
- ❌ Polygon.io — key present in `polygon_client.py` but NOT used (paid)
- ❌ Finnhub `/stock/price-target` — returns 403 on free tier, do NOT call it

## Git safety rules — READ BEFORE EVERY COMMIT
- **NEVER** `git add .` or `git add -A` from the home directory (`~/`)
  - The git repo root is `~/` (not just the project folder)
  - This would scoop up SSH keys, passport scans, and personal documents
  - Always add files by explicit path: `git add Documents/Claude/stocks_dub/file.py`
- Files that must **never** be committed:
  - `api_keys.json` — Finnhub + FRED API keys
  - `email_config.json` — Gmail sender credentials
  - `portfolio.json` — personal holdings
  - `cache.db`, `*.json` cache files
  - `.job_*.json` job state files

## App structure
```
app.py                      ← Streamlit main entry (Home page)
pages/
  1_Fundamental_Analysis.py ← Single-stock deep-dive
  2_Growth_Report.py        ← Market scanner (bulk scoring)
  3_Model_Performance.py    ← ML prediction history
  4_My_Portfolio.py         ← Personal portfolio tracker
  5_Politicians.py          ← Congressional trade browser
  6_Watchlist.py            ← Pinned stocks — score on demand in ~30s
watchlist.json              ← Persisted watchlist tickers (gitignored)
```

## Key source files
| File | Purpose |
|------|---------|
| `fundamentals.py` | Core data fetcher — yfinance + Finnhub enrichment. `CACHE_VERSION=10` |
| `finnhub_client.py` | Finnhub API wrapper with 55/min sliding-window rate limiter |
| `scorer.py` | 0–100 scoring model across 10 factors |
| `score_history.py` | SQLite score history + ML return prediction |
| `job_state.py` | Persistent background job state (`.job_*.json` files) |
| `sidebar_jobs.py` | Live job progress panel rendered on every page |
| `cache_db.py` | SQLite WAL-mode cache (replaces all the old JSON caches) |
| `scraper.py` | Loads `cached_trades.json` (congressional trade data) |
| `daily_job.py` | Headless daily scorer — run via cron or manually |
| `macro_data.py` | FRED macro indicators (yield curve, CPI, fed funds) |

## Architecture — fetch_fundamentals() flow
1. Check SQLite cache (24h TTL, `CACHE_VERSION=10` — bump version when adding new fields)
2. `yf.Ticker(ticker)` → fetch `t.info` (single object, REUSED for all calls)
3. Parallel yfinance fetches via `ThreadPoolExecutor(max_workers=3)`:
   - `t.history`, `t.income_stmt`, `t.cashflow`, `t.balance_sheet`
   - `t.upgrades_downgrades`, `t.news`, `t.earnings_history`
   - `fetch_institutional_data`, `fetch_insider_trades` (both cached separately)
4. Parallel Finnhub enrichment via `ThreadPoolExecutor(max_workers=4)`:
   - `/company-news` → sentiment
   - `/stock/earnings` → EPS surprise
   - `/stock/metric` → PE, PB, ROE, margins, growth rates
   - `/stock/recommendation` → analyst buy/hold/sell counts
   - `/stock/insider-transactions` → Form 4 open-market purchases/sales
   - **NOT called**: `/stock/price-target` (403 on free tier)
5. Save to SQLite cache + in-memory `_MEM_CACHE`

## Concurrency limits (macOS fd exhaustion fix)
- `_MAX_CONCURRENT = 3` in `fundamentals.py` — outer semaphore
- Inner yfinance TPE: `max_workers=3`
- Inner Finnhub TPE: `max_workers=4`
- Outer Growth Report workers: `_WORKERS = 4` in `pages/2_Growth_Report.py`
- Total worst-case concurrent connections: ~21 (well under macOS 256 default)
- Both the page module AND the background daemon thread raise the fd limit to 65,536

## Finnhub rate limiter
`finnhub_client.py` uses a **sliding-window** rate limiter:
- Hard cap: 55 calls/minute
- Uses `_CALL_TIMES` deque + `_RATE_LOCK`
- Any thread that would exceed 55/min **blocks** (not dropped) until window clears
- With 4 Finnhub calls per stock × 3 concurrent stocks = 12 calls bursting out at once
- At 55/min cap, a 3-concurrent run takes ~0.65s/call-batch (fine)

## Scorer factors (scorer.py)
| Factor | Max pts |
|--------|---------|
| Analyst upside (price vs target) | 25 |
| Analyst rating (Finnhub weighted or yfinance string) | 20 |
| RSI oversold bounce | 15 |
| 1-month momentum | 10 |
| 3-month momentum | 10 |
| Price vs 50-day MA | 10 |
| Politician purchases (30d) | 10 |
| EPS growth | 5 |
| Free cash flow | 5 |
| Earnings acceleration | 5 |

Signal thresholds: ≥65 = **STRONG BUY**, ≥50 = **BUY**, ≥35 = **WATCH**, ≥20 = **NEUTRAL**, <20 = **AVOID**

## Finnhub recommendation scoring (scorer.py)
When `fh_rec_total >= 3`, uses weighted score:
```python
_weighted = (_fh_sb * 2 + _fh_b - _fh_s - _fh_ss * 2) / _fh_tot
# +12 for >= 1.5 (very bullish), +8 for >= 0.8, +5 for >= 0.2
# +2 for >= -0.2, -8 for >= -0.8, -15 otherwise (very bearish)
```
Falls back to yfinance `analyst_rating` string when `fh_rec_total < 3`.

## Job state system
- `JobState("growth_report")` / `JobState("portfolio")` — persistent job tracking
- `.job_growth_report.json` / `.job_portfolio.json` — state files (gitignored)
- States: `idle → running → done/cancelled/cancelling`
- Stale detection: job is stale if no `last_updated` within 10 minutes
- Always use `try/finally` around `_JOB.finish()` to prevent stuck "Running" state

## Email / notifications
- `email_config.json` — Gmail sender credentials (`sender_email`, `app_password`, `recipient_email`)
- `emailer.py` + `send_daily_report.py` — PDF report generation + SMTP send
- Cron-based daily schedule set via the Growth Report UI

## Cache strategy
- Main cache: `cache.db` (SQLite, WAL mode) via `cache_db.py`
- In-memory layer: `_MEM_CACHE` dict in `fundamentals.py` (per-process, no size cap currently)
- Cache TTL: 24 hours for fundamentals; 7 days for institutional/insider
- Prune: `delete_old()` called 1-in-50 chance per write (deletes entries > 48h)
- **To force re-fetch**: bump `CACHE_VERSION` in `fundamentals.py`

## Test suite
```
tests/
  test_fundamentals_cache.py   ← patch paths: institutional_trades.fetch_institutional_data
  test_job_state.py            ← was_cancelled != is_done
  test_reddit_sentiment.py     ← error is swallowed in _search_subreddit (no assertIsNotNone)
  test_scorer.py
  test_finnhub_client.py
```
Run with: `python -m pytest tests/ -v`

## Common pitfalls to avoid
1. **Never call `/stock/price-target`** — it's 403 on the free Finnhub plan
2. **Always bump `CACHE_VERSION`** when adding new fields to `fetch_fundamentals()`
3. **Always use `try/finally` for `_JOB.finish()`** — otherwise the sidebar shows "Running" forever
4. **Never share `yf.Ticker()` instances across different stocks** — but DO reuse the same instance for multiple properties of the same stock (saves 6 extra HTTP sessions per stock)
5. **`except Exception: pass` in Finnhub block** is intentional (graceful degradation when no key) but means new endpoint failures are silent — test new endpoints directly before adding them
6. **`_WORKERS` comment** says "4×3=12 connections" — update the comment if you change `max_workers` in the inner TPE
