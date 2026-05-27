# PolitiQuant 🏛️

A free, self-hosted stock research platform that cross-references **congressional trading activity** with fundamental analysis, ML-powered scoring, sector rotation signals, and portfolio monitoring — all built on free data sources with no paid subscriptions required.

---

## Pages

### 🏛️ Home (`app.py`)
The main congressional trading dashboard.

- Live scan of House & Senate stock disclosure filings (Capitol Trades / EFTS)
- Filter by politician, ticker, date range, party, chamber, and transaction type
- Party badges, enriched trade details, and direct links to original filings
- **Email alerts** — configurable notifications when politicians buy stocks matching your criteria (min score, bipartisan consensus, high-conviction signals)
- Smart alert preview shows which stocks would have triggered before you save

---

### 📈 Stock Research (`pages/1_Fundamental_Analysis.py`)
Two views in one page via a toggle at the top.

**🔍 Single Stock**
- Price, RSI, P/E, Forward P/E, 52-week range, market cap, analyst target & rating, PEG
- Live TradingView chart (1D / 1W / 1M intervals)
- 5-year price history with annual return bars
- Revenue & net income growth (CAGR + YoY trend), EPS trajectory, free cash flow, LT debt ratio
- Finnhub news headlines scored with **FinBERT NLP sentiment** (falls back to keyword scoring if model not loaded)
- Insider buying activity (SEC Form 4) — CEO/CFO open-market purchases highlighted
- Institutional 13F holdings — tier-1 institutions (Berkshire, Vanguard, Bridgewater, etc.) flagged separately
- Analyst upgrade/downgrade activity (last 30 days)
- Upcoming earnings countdown badge, volume surge indicator (vs 10-day average)
- Google Trends interest chart with rising / falling / spike signal
- Downloadable PDF report

**⚖️ Compare Stocks**
- Score 2–3 tickers side-by-side with full signal badges and ML-predicted returns
- Every factor's point contribution in a colour-coded table (green = positive, red = negative)
- Key metrics comparison (RSI, P/E, upside %, short interest, upgrades/downgrades, sector)
- Google Trends chart per ticker
- Scoring reasons breakdown for each stock

---

### 🚀 Market Scanner (`pages/2_Growth_Report.py`)
Two views via toggle.

**🚀 Growth Report**
- Scans thousands of tickers using the composite Growth Score engine
- Configurable filters: min score, min revenue growth, min EPS growth, max P/E, sector, min market cap
- Parallel fetching with live progress bar and cancel button
- Results ranked by score with ML-predicted 30-day return for each
- Full reasoning section: every factor and its point contribution for every stock
- Background job with persistent state — survives tab switches and page refreshes
- Export results as CSV

**🔄 Sector Rotation**
- Live momentum for all 11 SPDR sector ETFs vs SPY (1d / 5d / 30d / 90d returns)
- Colour-coded heat map: 🔥 Hot → 📈 Warm → ➡️ Neutral → 📉 Cool → ❄️ Cold
- 14 sub-sector/theme ETFs: Semiconductors, AI/Robotics, Cloud/Software, Biotech, Regional Banks, Big Banks, Homebuilders, Airlines, Oil & Gas, Clean Energy, Retail, Cybersecurity
- "What's Kicking" — top movers within each hot/warm sector sorted by today's return
- Entire 26-ETF universe fetched in a **single bulk download** for fast refresh

---

### 🧠 Model Performance (`pages/3_Model_Performance.py`)
- Tracks every stock scored over the past 30 days, resolves actual 30-day returns from historical price data
- **Random Forest** model trains on score → return pairs once 30+ resolved outcomes exist
- Factor importance chart: which of the 29 scoring factors actually predicted returns in your universe
- Signal tier accuracy: how often STRONG BUY / BUY / WATCH / AVOID / NEUTRAL were correct
- Daily signals log table and recent resolved outcomes with actual vs predicted return
- Calibration over time: model performance improves as more outcomes accumulate

---

### 💼 My Portfolio (`pages/4_My_Portfolio.py`)
Four tabs in a single page.

**📊 Summary**
- Add tickers and share counts; tracks current value, cost basis, unrealised P&L per position
- Growth Score and signal badge for every holding
- ML-predicted 30-day return per position
- Total portfolio score and breakdown

**📡 Smart Money**
- 🏦 **Insider Buying** — recent CEO/CFO/Director open-market purchases on your holdings (SEC Form 4), weighted by position seniority
- 🏢 **Institutional Activity** — 13F quarterly position changes per holding: buyers, sellers, new positions opened; tier-1 institutions highlighted
- 📅 **Upcoming Earnings** — days-until-earnings countdown badge for every holding

**🏛️ Politicians**
- Which politicians traded your holdings this week
- Full politician activity table filtered to your holdings
- Your returns vs politician returns on the same stocks side-by-side

**🌍 Market & Alerts**
- 🥧 **Sector Concentration** — bar chart of your portfolio's exposure by sector
- 😱 **Fear & Greed Index** — 5-signal composite score (VIX level, SPY vs 125d SMA, market breadth RSP/SPY spread, junk bond demand HYG/LQD, safe-haven flow TLT/SPY) with individual signal breakdown
- 🔔 **Portfolio Alerts** — detects and flags signal changes (new STRONG BUY, dropped to AVOID), imminent earnings (≤ 3 days), and volume surges (3× normal); one-click email delivery of all active alerts

---

### 🏛️ Politicians (`pages/5_Politicians.py`)
Two views via toggle.

**🔍 Cross Reference**
- All stocks bought by politicians ranked by composite Growth Score
- 🔥 Hot Picks — high-score stocks bought by multiple politicians recently; bipartisan filter available
- Full ranked table: score, signal, ML prediction, politician count, days since last buy
- Drill-down: which politicians bought each stock, when, and for how much
- Export table as CSV

**👤 Politician Profile**
- Search any senator or representative by name
- Full trade history: date, ticker, transaction type, amount range, asset type
- Trade performance chart: price at trade date vs today (realised/unrealised gain)
- Cross-reference with Growth Scores: did they buy before the stock scored well?
- Compare two politicians side-by-side

---

## Scoring System

Each stock receives a **Growth Score (0–100)** from 29 factors across 6 categories:

| Category | Factors |
|---|---|
| **Price / Momentum** | RSI, 1m / 3m / 6m returns, vs 50-day MA, 52-week high proximity, volume surge |
| **Fundamentals** | Revenue CAGR, revenue trend, net income CAGR, net income trend, EPS growth, free cash flow, forward P/E |
| **Analyst** | Upside to price target, analyst rating, upgrades/downgrades (30d) |
| **Smart Money** | Insider buy score (CEO/CFO weighted), institutional score (tier-1 weighted), political buys (30d) |
| **Sentiment / News** | FinBERT news sentiment, earnings surprise %, earnings timing penalty |
| **Macro / Sector** | FRED macro environment modifier, sector rotation momentum, short interest |

**Signal bands:**

| Score | Signal |
|---|---|
| 75 – 100 | 🚀 STRONG BUY |
| 58 – 74 | 📈 BUY |
| 42 – 57 | 👀 WATCH |
| 25 – 41 | 😐 NEUTRAL |
| 0 – 24 | 🔴 AVOID |

---

## Setup

### 1. Clone and create virtual environment
```bash
git clone <repo-url>
cd stocks_dub
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### 2. Configure API keys
Create `api_keys.json` in the project root (already gitignored):
```json
{
  "finnhub_api_key": "your_key_here",
  "fred_api_key": "your_key_here"
}
```
- **Finnhub** free key: https://finnhub.io → register → API Dashboard
- **FRED** free key: https://fred.stlouisfed.org/docs/api/api_key.html

Both are free tier with generous rate limits. The app works without them (falls back to yfinance keywords for news and skips macro scoring) but works better with them.

### 3. Configure email alerts (optional)
Create `email_config.json` (already gitignored):
```json
{
  "sender_email": "you@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "recipient_email": "you@gmail.com",
  "alert_min_score": 60,
  "alert_min_pols": 1,
  "alert_consensus": false,
  "alert_bipartisan": false,
  "alert_high_score": true,
  "alert_portfolio": true
}
```
Use a Gmail **App Password** (not your regular password). Enable it at: Google Account → Security → 2-Step Verification → App Passwords.

### 4. Run
```bash
streamlit run app.py
```
Opens at **http://localhost:8501**

---

## Self-Learning ML Model

Every time the Growth Report runs, each scored stock's factor contributions are saved to `score_history.json`. 30 days later, the actual price change is resolved from Yahoo Finance historical data.

Once **30+ resolved outcomes** exist, a **Random Forest model** trains on the data and learns which factors actually predicted returns in your universe. Predictions appear as `ML: +6.2% ± 4.8%` on every scored stock.

To seed the model faster, run the daily job manually:
```bash
python3 daily_job.py
```

This scores the congressional-purchase universe, resolves overdue outcomes, and retrains the model. Configure the scoring universe in `daily_job.py`:
```python
DAILY_UNIVERSE = "gov_purchased"   # only stocks politicians bought (default)
DAILY_UNIVERSE = "gov_all"         # all disclosed stocks including sells
DAILY_UNIVERSE = ["AAPL", "MSFT"]  # custom list
```

---

## Data Sources (all free)

| Source | What it provides |
|---|---|
| **yfinance** | Price history, fundamentals, financials, insider trades (Form 4), institutional holders (13F), analyst data, earnings dates |
| **Finnhub** (free tier, 60 calls/min) | News headlines for FinBERT sentiment scoring, earnings surprise % |
| **FRED** (Federal Reserve) | Macro indicators: Fed Funds Rate, 10yr Treasury yield, unemployment, CPI |
| **Capitol Trades / EFTS Senate** | Congressional stock disclosure filings (scraped public records) |
| **Google Trends** (pytrends) | Search interest signals — rising retail interest precedes price moves |
| **SPDR sector ETFs** | Sector rotation momentum via XLK, XLE, XLF, XLV, SOXX, XBI, etc. |

---

## Architecture

```
stocks_dub/
├── app.py                          # Home — congressional trades + email alerts
├── daily_job.py                    # Headless scoring + ML retraining job
│
├── pages/
│   ├── 1_Fundamental_Analysis.py   # 📈 Stock Research  (single stock + compare)
│   ├── 2_Growth_Report.py          # 🚀 Market Scanner  (growth report + sector rotation)
│   ├── 3_Model_Performance.py      # 🧠 Model Performance
│   ├── 4_My_Portfolio.py           # 💼 My Portfolio    (4 tabs)
│   └── 5_Politicians.py            # 🏛️ Politicians     (cross-reference + profile)
│
├── Core data & scoring
│   ├── fundamentals.py             # yfinance fetcher (parallel, 24h cache)
│   ├── scorer.py                   # 29-factor Growth Score engine
│   ├── score_history.py            # ML training data + Random Forest model
│   ├── sector_rotation.py          # Sector ETF momentum (single bulk download)
│   ├── market_sentiment.py         # Fear & Greed composite (single bulk download)
│   ├── insider_trades.py           # SEC Form 4 insider transactions
│   ├── institutional_trades.py     # 13F institutional holdings
│   ├── google_trends.py            # pytrends search interest
│   ├── finnhub_client.py           # News + earnings surprise (rate-limited 55/min)
│   ├── macro_data.py               # FRED macro indicators
│   └── portfolio_alerts.py         # Signal-change alert engine
│
├── Infrastructure
│   ├── scraper.py                  # House disclosure scraper
│   ├── senate_scraper.py           # Senate disclosure scraper
│   ├── emailer.py                  # HTML email delivery (Gmail SMTP)
│   ├── pdf_report.py               # PDF report generator (ReportLab)
│   ├── job_state.py                # Background job state (persisted to disk)
│   ├── sidebar_jobs.py             # Job status sidebar widget
│   └── party_lookup.py             # Politician party enrichment
│
└── Config (gitignored)
    ├── api_keys.json               # Finnhub + FRED API keys
    └── email_config.json           # Gmail credentials + alert thresholds
```

---

## Cache Files (gitignored, auto-managed)

| File | TTL | Contents |
|---|---|---|
| `fundamentals_cache.json` | 24 hours | All yfinance data per ticker; auto-prunes entries > 48h |
| `insider_cache.json` | 7 days | SEC Form 4 insider trades |
| `institutional_cache.json` | 7 days | 13F institutional holdings |
| `sector_cache.json` | 1 hour | Sector ETF momentum for all 26 ETFs |
| `sector_stocks_cache.json` | 1 hour | Top stock movers within hot sectors |
| `sentiment_cache.json` | 1 hour | Fear & Greed composite score |
| `trends_cache.json` | 24 hours | Google Trends interest per ticker |
| `fred_cache.json` | 24 hours | FRED macro indicators |
| `score_history.json` | Permanent | ML training data — scored stocks + resolved 30-day outcomes |
| `cached_trades.json` | Until next scan | Congressional disclosure filings |

Cache writes use **atomic temp-file + rename** so a mid-write crash can never corrupt a cache file.

---

## Requirements

- Python 3.10+
- Internet connection (data is cached locally after first fetch)
- ~200 MB disk for cache + FinBERT model weights

Key packages (see `requirements.txt` for full list):
```
streamlit>=1.35
yfinance>=0.2
pandas>=2.0
numpy
scikit-learn
torch
transformers          # FinBERT NLP sentiment
reportlab             # PDF reports
requests
pytrends              # Google Trends (may need: pip3 install pytrends)
```

> **Note:** `pytrends` may need to be installed in your system Python as well as the venv:  
> `pip3 install pytrends`

---

## Disclaimer

This tool is for research and educational purposes only. Nothing here constitutes financial advice. Congressional disclosure data is public record and does not imply any stock recommendation. Always do your own research before making investment decisions.
