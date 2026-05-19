import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from fundamentals import fetch_fundamentals, fmt_large, fmt_pct, fmt_ratio
from scraper import load_cache
from scorer import score_stock, score_stock_detailed, signal_label
from score_history import save_scores, predict_return

st.set_page_config(page_title="1-Month Growth Report", page_icon="🚀", layout="wide")

st.title("🚀 1-Month Growth Report")
st.caption(
    "Scores every disclosed stock across momentum, fundamentals, analyst consensus, "
    "and politician buying activity. Ranks by estimated short-term growth potential. "
    "Not financial advice — use as a research starting point."
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _momentum_from_fund(fund: dict) -> dict:
    """Read momentum fields already computed inside fetch_fundamentals() — no extra API call."""
    return {
        "rsi":          fund.get("rsi"),
        "mom_1m_pct":   fund.get("mom_1m_pct"),
        "mom_3m_pct":   fund.get("mom_3m_pct"),
        "vs_50ma_pct":  fund.get("vs_50ma_pct"),
        "vs_200ma_pct": fund.get("vs_200ma_pct"),
    }


def _score(fund: dict, mom: dict, pol_buys_30d: int) -> tuple[float, list[str]]:
    # Thin wrapper: merge mom fields into fund copy, then delegate to shared scorer
    merged = dict(fund)
    merged.update(mom)
    return score_stock(merged, pol_buys_30d)



# ── Load political trade data ──────────────────────────────────────────────────
trades_raw = load_cache()
df_all = pd.DataFrame(trades_raw) if trades_raw else pd.DataFrame()

if not df_all.empty:
    df_all["ticker"]           = df_all["ticker"].fillna("").str.upper().str.strip()
    df_all["transaction_type"] = df_all["transaction_type"].fillna("")
    df_all["transaction_date"] = pd.to_datetime(df_all["transaction_date"], errors="coerce")

gov_all       = sorted(t for t in df_all["ticker"].unique() if t not in ("N/A", "--", "")) if not df_all.empty else []
gov_purchased = sorted(
    t for t in df_all.loc[df_all["transaction_type"].str.contains("Purchase", case=False, na=False), "ticker"].unique()
    if t not in ("N/A", "--", "")
) if not df_all.empty else []


import requests as _requests
from io import StringIO

_WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

@st.cache_data(ttl=86400)
def _sp500() -> list[str]:
    try:
        html = _requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=_WIKI_HEADERS, timeout=15,
        ).text
        tbl = pd.read_html(StringIO(html))[0]
        col = "Symbol" if "Symbol" in tbl.columns else tbl.columns[0]
        return sorted(tbl[col].str.replace(".", "-", regex=False).dropna().tolist())
    except Exception:
        return []

@st.cache_data(ttl=86400)
def _nasdaq100() -> list[str]:
    try:
        html = _requests.get(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            headers=_WIKI_HEADERS, timeout=15,
        ).text
        tables = pd.read_html(StringIO(html))
        for tbl in tables:
            for col in tbl.columns:
                if "ticker" in str(col).lower() or "symbol" in str(col).lower():
                    vals = tbl[col].dropna().tolist()
                    if len(vals) > 50:
                        return sorted(str(v).strip() for v in vals if str(v).strip())
        return []
    except Exception:
        return []

@st.cache_data(ttl=86400)
def _dow30() -> list[str]:
    return sorted([
        "AAPL","AMGN","AXP","BA","CAT","CRM","CSCO","CVX","DIS","DOW",
        "GS","HD","HON","IBM","INTC","JNJ","JPM","KO","MCD","MMM",
        "MRK","MSFT","NKE","PG","TRV","UNH","V","VZ","WBA","WMT",
    ])

@st.cache_data(ttl=86400, show_spinner=False)
def _all_us_stocks() -> list[str]:
    """
    Fetch all US stock tickers from NASDAQ trader directory (official, free).
    Returns ~7,000 real stocks — ETFs and test issues excluded.
    Raises on failure so st.cache_data does NOT cache an empty result.
    """
    _h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    def _parse_nasdaq(text: str) -> list[str]:
        out = []
        for line in text.strip().split("\n")[1:]:  # skip header
            p = line.split("|")
            if len(p) < 7:
                continue
            sym, test, etf = p[0].strip(), p[3].strip(), p[6].strip()
            if test == "Y" or etf == "Y":
                continue
            if sym and len(sym) <= 5 and not sym.startswith("$"):
                out.append(sym.upper())
        return out

    def _parse_other(text: str) -> list[str]:
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

    r1 = _requests.get(
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
        headers=_h, timeout=20,
    )
    r1.raise_for_status()

    r2 = _requests.get(
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
        headers=_h, timeout=20,
    )
    r2.raise_for_status()

    tickers = sorted(set(_parse_nasdaq(r1.text) + _parse_other(r2.text)))
    if not tickers:
        raise ValueError("Ticker list came back empty — unexpected parse error.")
    return tickers


# ── Controls ───────────────────────────────────────────────────────────────────
with st.expander("Report settings", expanded=True):
    rc1, rc2 = st.columns([2, 1])

    with rc1:
        st.markdown("**Select stock universe(s) — pick one or more**")
        use_all_us   = st.checkbox("🌎 ALL US stocks — SEC EDGAR full market (~10,000 tickers)", value=False, key="u_all_us")
        use_sp500    = st.checkbox("S&P 500  (~500 stocks)",                                  value=False, key="u_sp500")
        use_nasdaq   = st.checkbox("NASDAQ 100",                                              value=False, key="u_nasdaq")
        use_dow      = st.checkbox("Dow Jones 30",                                            value=False, key="u_dow")
        use_gov_buy  = st.checkbox("Government disclosed — purchased only",                   value=False, key="u_gov_buy")
        use_gov_all  = st.checkbox("Government disclosed — all (purchases+sales)",            value=False, key="u_gov_all")
        use_custom   = st.checkbox("Custom tickers",                                          value=False, key="u_custom")

        if use_all_us:
            st.warning(
                "⏱️ **First run is slow** — ~7,000 stocks × 1-2 sec each = 2–4 hours. "
                "After that every stock is cached daily and re-runs take under 5 minutes. "
                "Tip: combine with the **min price $5** and **$300M+ market cap** filters to "
                "skip small/micro caps instantly — cuts the time significantly. "
                "Leave this tab open and let it run overnight."
            )

    with rc2:
        top_n = st.slider("Show top N in chart", min_value=5, max_value=30, value=15)

    if use_custom:
        custom_input = st.text_input(
            "Enter tickers (comma-separated)",
            placeholder="e.g. AAPL, TSLA, NVDA, MSFT",
            key="custom_tickers_input",
        )
        custom_list = sorted(set(t.strip().upper() for t in custom_input.split(",") if t.strip()))
    else:
        custom_list = []

    # Build combined universe
    combined: set[str] = set()
    fetch_errors: list[str] = []

    if use_dow:     combined.update(_dow30())
    if use_custom:  combined.update(custom_list)
    if use_gov_buy: combined.update(gov_purchased)
    if use_gov_all: combined.update(gov_all)

    if use_sp500:
        with st.spinner("Loading S&P 500 list…"):
            sp = _sp500()
        if sp:
            combined.update(sp)
        else:
            fetch_errors.append("S&P 500 list could not be fetched — check your internet connection.")

    if use_nasdaq:
        with st.spinner("Loading NASDAQ 100 list…"):
            nq = _nasdaq100()
        if nq:
            combined.update(nq)
        else:
            fetch_errors.append("NASDAQ 100 list could not be fetched — check your internet connection.")

    if use_all_us:
        with st.spinner("Loading full US stock list from NASDAQ directory (~7,000 stocks)…"):
            try:
                all_us = _all_us_stocks()
                combined.update(all_us)
                st.success(f"Loaded {len(all_us):,} tickers from NASDAQ directory (ETFs excluded).")
            except Exception as _e:
                fetch_errors.append(
                    f"Full US stock list could not be fetched: {_e}. "
                    "Click **Clear ticker cache** below and try again."
                )
                all_us = []

    for err in fetch_errors:
        st.warning(err)

    if st.button("🔄 Clear ticker cache (force re-fetch lists)", key="clear_ticker_cache"):
        _all_us_stocks.clear()
        _sp500.clear()
        _nasdaq100.clear()
        st.success("Ticker cache cleared — uncheck and re-check your universe to reload.")
        st.rerun()

    candidate_tickers = sorted(t for t in combined if t not in ("N/A", "--", ""))

    st.divider()
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        min_price = st.number_input(
            "Minimum stock price ($)",
            min_value=0.0, max_value=500.0, value=5.0, step=1.0,
            help="Stocks below this price are skipped. $5 is the standard penny-stock cutoff.",
            key="min_price_filter",
        )
    with fc2:
        min_market_cap = st.selectbox(
            "Minimum market cap",
            options=[0, 50e6, 300e6, 2e9, 10e9],
            index=2,
            format_func=lambda v: "No filter" if v == 0 else (
                f"${v/1e9:.0f}B+" if v >= 1e9 else f"${v/1e6:.0f}M+"
            ),
            help="Small cap < $300M, Mid cap $300M–$2B, Large cap > $2B",
            key="min_mktcap_filter",
        )
    with fc3:
        st.write("")
        if candidate_tickers:
            st.success(f"**{len(candidate_tickers)} stocks** ready to analyse.")
        else:
            st.warning("No universe selected — tick at least one box above.")

# Show a soft warning if the universe differs from the last run — but keep results visible
_universe_key = tuple(candidate_tickers)
_last_universe = st.session_state.get("_last_universe")
_universe_changed = _last_universe is not None and _last_universe != _universe_key and len(_last_universe) > 0

run_report = st.button(
    f"Generate Report  ({len(candidate_tickers)} stocks)",
    type="primary",
    disabled=len(candidate_tickers) == 0,
)

if _universe_changed:
    st.warning("Universe selection changed — click **Generate Report** to refresh results with the new stock list.")

st.info(
    "**How the score works:** Each stock gets 0–100 points across 10 factors — "
    "analyst upside (25 pts), analyst rating (20 pts), RSI oversold bounce (15 pts), "
    "1-month & 3-month price momentum (10 pts each), price vs 50-day MA (10 pts), "
    "recent politician purchases (10 pts), EPS growth, FCF, and earnings acceleration (5 pts each). "
    "Score ≥ 65 = **Strong Buy**, ≥ 50 = **Buy**, ≥ 35 = **Watch**.",
    icon="ℹ️",
)

# ── Run ────────────────────────────────────────────────────────────────────────
if run_report or "growth_report_rows" in st.session_state:

    if run_report:
        cutoff_30d = pd.Timestamp(date.today() - timedelta(days=30))
        rows = []
        skipped_penny   = 0
        skipped_mktcap  = 0
        skipped_error   = 0
        progress = st.progress(0, text="Starting…")
        status   = st.empty()

        for i, ticker in enumerate(candidate_tickers):
            progress.progress(
                (i + 1) / len(candidate_tickers),
                text=f"Analysing {ticker}  ({i+1}/{len(candidate_tickers)}) — {len(rows)} scored so far",
            )

            # Fundamentals + momentum (both cached daily — one API call per stock)
            fund = fetch_fundamentals(ticker)
            if fund.get("error") and not fund.get("current_price"):
                skipped_error += 1
                continue

            # Skip penny stocks and small caps based on user filters
            price   = fund.get("current_price") or 0
            mkt_cap = fund.get("market_cap") or 0
            if price < min_price:
                skipped_penny += 1
                continue
            if min_market_cap and mkt_cap < min_market_cap:
                skipped_mktcap += 1
                continue

            mom = _momentum_from_fund(fund)

            # Recent politician purchase count (last 30 days)
            pol_mask = (
                (df_all["ticker"] == ticker) &
                (df_all["transaction_type"].str.contains("Purchase", case=False, na=False)) &
                (df_all["transaction_date"] >= cutoff_30d)
            )
            pol_buys_30d = int(pol_mask.sum())

            merged = dict(fund)
            merged.update(mom)
            score, reasons, factor_pts = score_stock_detailed(merged, pol_buys_30d)
            signal, colour = signal_label(score)

            def _safe(v):
                try: return round(float(v), 1)
                except: return None

            price  = fund.get("current_price")
            target = fund.get("analyst_target")
            try:
                upside = (target - price) / price * 100 if target and price else None
            except Exception:
                upside = None

            rows.append({
                "ticker":              ticker,
                "company":             fund.get("company_name", ticker),
                "sector":              fund.get("sector") or "—",
                "score":               round(score, 1),
                "signal":              signal,
                "colour":              colour,
                "factor_pts":          factor_pts,
                "price":               price,
                "analyst_target":      target,
                "upside_pct":          upside,
                "analyst_rating":      fund.get("analyst_rating") or "N/A",
                "rsi":                 _safe(mom.get("rsi")),
                "mom_1m_pct":          _safe(mom.get("mom_1m_pct")),
                "mom_3m_pct":          _safe(mom.get("mom_3m_pct")),
                "mom_6m_pct":          _safe(mom.get("mom_6m_pct")),
                "vs_50ma_pct":         _safe(mom.get("vs_50ma_pct")),
                "positive_months_6":   fund.get("positive_months_6"),
                "positive_months_12":  fund.get("positive_months_12"),
                "revenue_trend":       _safe(fund.get("revenue_trend")),
                "ni_trend":            _safe(fund.get("ni_trend")),
                "monthly_returns":     fund.get("monthly_returns", []),
                "pol_buys_30d":        pol_buys_30d,
                "eps_yoy_pct":         fund.get("eps_yoy_pct"),
                "fcf":                 fund.get("free_cash_flow"),
                "pe_ratio":            fund.get("pe_ratio"),
                "forward_pe":          fund.get("forward_pe"),
                "market_cap":          fund.get("market_cap"),
                "reasons":             reasons,
            })

        progress.empty()
        status.empty()
        rows.sort(key=lambda r: r["score"], reverse=True)
        st.session_state.growth_report_rows = rows
        st.session_state["_last_universe"] = _universe_key

        # Save to learning history (silently — never blocks the report)
        try:
            history_batch = [
                {"ticker": r["ticker"], "price": r["price"], "score": r["score"], "factor_pts": r.get("factor_pts", {})}
                for r in rows if r.get("price")
            ]
            added = save_scores(history_batch)
            if added:
                st.caption(f"🧠 {added} stock(s) added to learning history.")
        except Exception:
            pass

        # Show what was skipped so user understands the count
        skip_parts = []
        if skipped_penny:  skip_parts.append(f"{skipped_penny} penny stocks (price < ${min_price:.0f})")
        if skipped_mktcap: skip_parts.append(f"{skipped_mktcap} below market cap filter")
        if skipped_error:  skip_parts.append(f"{skipped_error} with no data from Yahoo Finance")
        if skip_parts:
            st.info(f"Skipped: {', '.join(skip_parts)}. **{len(rows)} stocks** made it into the report.")

    rows = st.session_state.growth_report_rows
    if not rows:
        st.warning("No results — try lowering the minimum price or market cap filter.")
        st.stop()

    # ── Summary metrics ────────────────────────────────────────────────────────
    st.divider()
    strong_buys = sum(1 for r in rows if r["signal"] == "STRONG BUY")
    buys        = sum(1 for r in rows if r["signal"] == "BUY")
    watches     = sum(1 for r in rows if r["signal"] == "WATCH")
    avoids      = sum(1 for r in rows if r["signal"] in ("AVOID", "NEUTRAL"))

    m1c, m2c, m3c, m4c, m5c = st.columns(5)
    m1c.metric("Stocks Analysed", len(rows))
    m2c.metric("Strong Buy",  strong_buys,  delta=None)
    m3c.metric("Buy",         buys)
    m4c.metric("Watch",       watches)
    m5c.metric("Neutral/Avoid", avoids)

    # ── Top N chart ───────────────────────────────────────────────────────────
    st.subheader(f"Top {top_n} Stocks by Growth Score")
    top_rows = rows[:top_n]
    chart_df = pd.DataFrame({
        "Stock": [f"{r['ticker']}" for r in top_rows],
        "Score": [r["score"]       for r in top_rows],
    }).set_index("Stock").sort_values("Score", ascending=True)
    st.bar_chart(chart_df, color="#4a90d9", height=max(300, top_n * 28))

    # ── Full ranked table ─────────────────────────────────────────────────────
    st.subheader("Full Ranked Report")

    filter_signal = st.multiselect(
        "Filter by signal",
        ["STRONG BUY", "BUY", "WATCH", "NEUTRAL", "AVOID"],
        default=["STRONG BUY", "BUY", "WATCH"],
        key="gr_signal_filter",
    )

    table_rows = [r for r in rows if r["signal"] in filter_signal]

    table_data = []
    for r in table_rows:
        fund_r = st.session_state.fa_results.get(r["ticker"], {}) if "fa_results" in st.session_state else {}
        table_data.append({
            "Score":          r["score"],
            "Signal":         r["signal"],
            "Ticker":         r["ticker"],
            "Company":        r["company"],
            "Sector":         r["sector"],
            "Price":          f"${r['price']:.2f}" if r["price"] else "N/A",
            "Analyst Target": f"${r['analyst_target']:.2f}" if r["analyst_target"] else "N/A",
            "Upside %":       round(r["upside_pct"], 1) if r["upside_pct"] is not None else None,
            "Wall St Rating": r["analyst_rating"],
            "RSI":            r["rsi"],
            "1M %":           r["mom_1m_pct"],
            "3M %":           r["mom_3m_pct"],
            "6M %":           r["mom_6m_pct"],
            "Pos Months/6":   r.get("positive_months_6"),
            "Pos Months/12":  r.get("positive_months_12"),
            "Rev Trend":      r.get("revenue_trend"),
            "NI Trend":       r.get("ni_trend"),
            "vs 50MA":        r["vs_50ma_pct"],
            "Pol Buys (30d)": r["pol_buys_30d"],
            "Market Cap":     fmt_large(r["market_cap"]),
        })

    tdf = pd.DataFrame(table_data)
    st.dataframe(
        tdf,
        use_container_width=True,
        hide_index=True,
        height=min(600, len(table_data) * 38 + 50),
        column_config={
            "Score":         st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
            "Upside %":      st.column_config.NumberColumn(format="%.1f%%"),
            "1M %":          st.column_config.NumberColumn("1M %",   format="%.1f%%"),
            "3M %":          st.column_config.NumberColumn("3M %",   format="%.1f%%"),
            "6M %":          st.column_config.NumberColumn("6M %",   format="%.1f%%"),
            "Rev Trend":     st.column_config.NumberColumn("Rev Trend (pp)", format="%.1f"),
            "NI Trend":      st.column_config.NumberColumn("NI Trend (pp)",  format="%.1f"),
            "vs 50MA":       st.column_config.NumberColumn("vs 50MA", format="%.1f%%"),
        },
    )

    # ── Download ──────────────────────────────────────────────────────────────
    csv = tdf.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download report as CSV",
        data=csv,
        file_name=f"growth_report_{date.today()}.csv",
        mime="text/csv",
    )

    # ── Detailed breakdown per stock ──────────────────────────────────────────
    st.divider()
    st.subheader("Why each stock scored the way it did")
    show_top = st.slider("Show top N detailed breakdowns", 3, min(20, len(rows)), 5, key="gr_detail_n")

    for r in rows[:show_top]:
        signal, colour = r["signal"], r["colour"]
        with st.expander(
            f"{'🟢' if 'BUY' in signal else '🟡' if signal == 'WATCH' else '🔴'}  "
            f"{r['ticker']} — {r['company']}  |  Score: {r['score']}/100  |  {signal}",
            expanded=False,
        ):
            dc1, dc2, dc3, dc4, dc5, dc6 = st.columns(6)
            dc1.metric("Score",      f"{r['score']}/100")
            dc2.metric("Price",      f"${r['price']:.2f}" if r["price"] else "N/A")
            dc3.metric("RSI",        f"{r['rsi']:.0f}" if r["rsi"] else "N/A")
            dc4.metric("1M / 3M / 6M",
                       f"{r['mom_1m_pct']:+.1f}% / {r['mom_3m_pct']:+.1f}% / {r['mom_6m_pct']:+.1f}%"
                       if all(v is not None for v in [r["mom_1m_pct"], r["mom_3m_pct"], r["mom_6m_pct"]])
                       else "N/A")
            pos6 = r.get("positive_months_6")
            dc5.metric("Positive months", f"{pos6}/6" if pos6 is not None else "N/A",
                       help="How many of the last 6 months had a positive return")
            rev_t = r.get("revenue_trend")
            dc6.metric("Rev trend", f"{rev_t:+.1f}pp" if rev_t is not None else "N/A",
                       help="Change in revenue growth rate vs prior year (positive = accelerating)")

            target_str = f"${r['analyst_target']:.2f}" if r["analyst_target"] else "N/A"
            st.markdown(
                f"<div style='background:{colour}22; border-left:4px solid {colour}; "
                f"padding:10px 14px; border-radius:6px; margin:8px 0;'>"
                f"<b style='color:{colour};'>{signal}</b> — {r['analyst_rating']} (Wall St) · "
                f"Target: {target_str} · Pol buys 30d: {r['pol_buys_30d']}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ML prediction (only shown once model is trained)
            try:
                pred = predict_return(r.get("factor_pts", {}))
                if pred:
                    p_ret, p_ci, n_train, r2 = pred
                    p_col = "#2ecc71" if p_ret > 0 else "#e74c3c"
                    conf  = "High" if n_train >= 100 else "Medium" if n_train >= 50 else "Early"
                    st.markdown(
                        f"<div style='background:#1a2a1a; border:1px solid {p_col}55; border-radius:8px; "
                        f"padding:10px 14px; margin:6px 0; display:flex; align-items:center; gap:16px;'>"
                        f"<div><div style='font-size:0.75rem; color:#aaa;'>🧠 ML Predicted 30-day return</div>"
                        f"<div style='font-size:1.4rem; font-weight:bold; color:{p_col};'>"
                        f"{p_ret:+.1f}% <span style='font-size:0.85rem; color:#aaa;'>± {p_ci:.1f}%</span></div></div>"
                        f"<div style='font-size:0.75rem; color:#888;'>"
                        f"Based on {n_train} outcomes · {conf} confidence · R²={r2:.2f}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            except Exception:
                pass

            # Month-by-month returns chart
            monthly = r.get("monthly_returns", [])
            if monthly:
                mdf = pd.DataFrame(monthly).set_index("month")
                st.caption("Month-by-month price returns (last 24 months)")
                st.bar_chart(mdf["return_pct"], height=180, color="#4a90d9")

            st.markdown("**Scoring breakdown:**")
            for reason in r["reasons"]:
                st.markdown(f"- {reason}")
