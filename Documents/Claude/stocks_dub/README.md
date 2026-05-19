# US Political Stock Disclosure Tracker

A free, local Streamlit app that tracks U.S. politician stock disclosures, scores every stock in the market for short-term growth potential, and learns from its own predictions over time.

**No API keys. No paid services. Everything runs on your laptop.**

---

## What it does

| Page | Description |
|---|---|
| **Home** | Scan and browse House & Senate disclosure filings. Filter by politician, ticker, date, chamber. Email alerts when new purchases are filed. |
| **Fundamental Analysis** | Deep-dive any stock — TradingView chart, Buy/Hold/Sell signal, 0–100 growth score, 5-year financials, politician trading history, PDF report download. |
| **Growth Report** | Score and rank an entire universe of stocks (S&P 500, NASDAQ 100, all US stocks, or custom) by estimated 1-month growth potential. Export as CSV. |
| **Model Performance** | Tracks whether the growth score actually predicted real returns. Learns from outcomes over time and shows ML-predicted 30-day return per stock. |

---

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd stocks_dub

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
streamlit run app.py
```

Open **http://localhost:8501** in your browser.

---

## First use

1. Click **Run Disclosure Scan Now** on the Home page (takes a few minutes first time)
2. Browse disclosures or jump straight to **Fundamental Analysis** to research a stock
3. Go to **Growth Report**, select a universe, and click **Run Report**
4. Each report run automatically saves scores to the learning history
5. After 20+ outcomes (30 days per stock), **Model Performance** shows ML predictions

---

## How the scoring works

Each stock is scored 0–100 across 18 factors. **Theoretical maximum is exactly 100** — the system is designed so only genuinely exceptional stocks approach it.

| Category | Factors | Max pts |
|---|---|---|
| Value / Upside | Analyst price target upside, 5yr total return | 18 |
| Analyst consensus | Wall St recommendation | 12 |
| Momentum | 1m / 3m / 6m price return, vs 50-day MA | 21 |
| Technical | RSI (mean-reversion signal) | 7 |
| Consistency | Positive months out of last 6 and 12 | 10 |
| Fundamentals | Revenue trend, NI trend, CAGR, EPS growth, FCF, Forward P/E | 21 |
| Political signal | Politician purchases in last 30 days | 6 |
| Penalties | Negative FCF, sell consensus, overbought RSI, earnings shrinking, etc. | up to −121 |

**Signal bands:**

| Score | Signal |
|---|---|
| 75 – 100 | STRONG BUY |
| 58 – 74 | BUY |
| 40 – 57 | WATCH |
| 25 – 39 | NEUTRAL |
| 0 – 24 | AVOID |

---

## Self-learning predictions

Every time you run the Growth Report, each scored stock's price and factor contributions are saved to `score_history.json`. 30 days later, the app fetches the actual price from Yahoo Finance historical data (not the current price — so it doesn't matter when you open the app) and records the real return.

Once 20 outcomes are collected, a **ridge regression model** trains on the data, learning which of the 18 factors actually predicted returns in your specific universe. Over time:

- Factors that consistently preceded gains get higher weight
- Factors that were noise get pushed toward zero
- Each stock in the Growth Report and Fundamental Analysis shows: `ML Predicted 30-day return: +6.2% ± 4.8%`

**You don't need to keep your laptop on** — outcomes are resolved from historical price data whenever you next open the app, with no loss of accuracy.

### Automated daily job

A scheduled task runs `daily_job.py` every weekday at 9am (when Claude Code is open) to:
1. Resolve any overdue 30-day outcomes
2. Score the government-disclosed universe and save to learning history
3. Retrain the model if new outcomes exist

You can also run it manually:

```bash
python3 daily_job.py
```

To change which universe is scored daily, edit `DAILY_UNIVERSE` in `daily_job.py`:

```python
DAILY_UNIVERSE = "gov_purchased"   # only stocks politicians bought
DAILY_UNIVERSE = "gov_all"         # all disclosed stocks (buys + sells)
DAILY_UNIVERSE = ["AAPL", "MSFT"]  # custom list
```

---

## Data sources

| Data | Source | Cost |
|---|---|---|
| House disclosures | disclosures-clerk.house.gov | Free |
| Senate disclosures | efts.senate.gov | Free |
| Stock prices & fundamentals | Yahoo Finance via yfinance | Free |
| All US stock tickers | NASDAQ trader directory (nasdaqlisted + otherlisted) | Free |
| S&P 500 / NASDAQ 100 lists | Wikipedia | Free |

---

## Project structure

```
stocks_dub/
├── app.py                        # Home page — disclosure scanner + email alerts
├── scraper.py                    # House disclosure scraper
├── senate_scraper.py             # Senate disclosure scraper
├── fundamentals.py               # Yahoo Finance data fetcher + daily cache
├── scorer.py                     # 0-100 growth scoring model (shared)
├── score_history.py              # Learning engine — saves outcomes, trains model
├── pdf_report.py                 # PDF report generator (reportlab)
├── emailer.py                    # Gmail SMTP alert sender
├── daily_job.py                  # Headless daily automation script
├── pages/
│   ├── 1_Fundamental_Analysis.py # Per-stock deep analysis page
│   ├── 2_Growth_Report.py        # Universe screener + ranking page
│   └── 3_Model_Performance.py    # Learning model dashboard
└── requirements.txt
```

### Key cache files (git-ignored)

| File | Contents | Size |
|---|---|---|
| `fundamentals_cache.json` | Yahoo Finance data, refreshed daily | ~3KB per stock |
| `score_history.json` | Scored stocks + outcomes for ML training | grows over time |
| `cached_trades.json` | Scraped disclosure filings | varies |
| `email_config.json` | Gmail credentials | sensitive — never commit |

---

## Email alerts

To receive an email when politicians make new stock purchases:

1. Create a [Gmail App Password](https://myaccount.google.com/apppasswords) (requires 2FA enabled)
2. Open the app → expand **Email Alerts** on the Home page
3. Enter your Gmail address, app password, and recipient email
4. Click **Save** — alerts fire automatically after each disclosure scan

---

## Requirements

- Python 3.10+
- Internet connection for initial data fetch (cached after that)
- ~50MB disk space for cache files (full US market)

```
streamlit>=1.35.0
pandas>=2.0.0
requests>=2.31.0
pdfplumber>=0.11.0
defusedxml>=0.7.1
beautifulsoup4>=4.12.0
lxml>=5.0.0
reportlab>=4.0.0
yfinance
numpy
```

---

## Disclaimer

This tool is for research and educational purposes only. Nothing here is financial advice. Political disclosure data is public record but does not imply any stock recommendation. Always do your own research before making investment decisions.
