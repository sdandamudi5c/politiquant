import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime

from fundamentals import fetch_fundamentals, fmt_large, fmt_pct, fmt_ratio, recommend, screen
from pdf_report import generate_stock_pdf
from scraper import load_cache
from scorer import score_stock, score_stock_detailed, signal_label
from score_history import predict_return
from sidebar_jobs import render as _render_sidebar

st.set_page_config(page_title="PolitiQuant · Stock Research", page_icon="📈", layout="wide")
_render_sidebar()

st.title("📈 Stock Research")

_research_view = st.radio(
    "", ["🔍 Single Stock", "⚖️ Compare Stocks"],
    horizontal=True, label_visibility="collapsed", key="research_view",
)
st.divider()

# ── COMPARE VIEW ──────────────────────────────────────────────────────────────
if _research_view == "⚖️ Compare Stocks":
    from scorer import FACTOR_NAMES as _FN
    from google_trends import fetch_trends as _ft_cmp

    st.subheader("⚖️ Compare Stocks")
    st.caption("Score 2–3 stocks side-by-side — every factor, ML prediction, Google Trends.")

    _ci1, _ci2, _ci3, _cb = st.columns([1.5, 1.5, 1.5, 1])
    _ct1 = _ci1.text_input("Stock 1", value="NVDA", key="cmp1").upper().strip()
    _ct2 = _ci2.text_input("Stock 2", value="AMD",  key="cmp2").upper().strip()
    _ct3 = _ci3.text_input("Stock 3 (optional)", value="", key="cmp3").upper().strip()
    _crun = _cb.button("⚖️ Compare", type="primary", use_container_width=True)
    _ctickers = [t for t in [_ct1, _ct2, _ct3] if t]

    if _crun and _ctickers:
        _cresults = {}
        _cprog = st.progress(0.0)
        for _ci_idx, _ctk in enumerate(_ctickers):
            _cprog.progress((_ci_idx+0.5)/len(_ctickers), text=f"Scoring {_ctk}…")
            _cfund = fetch_fundamentals(_ctk)
            _cscore, _creasons, _cfp = score_stock_detailed(_cfund)
            _cpred = predict_return(_cfp)
            _cresults[_ctk] = {"fund": _cfund, "score": _cscore, "reasons": _creasons, "fp": _cfp, "pred": _cpred}
        _cprog.empty()
        st.session_state["cmp_cache"] = _cresults
        st.session_state["cmp_tickers"] = _ctickers

    _cresults  = st.session_state.get("cmp_cache", {})
    _ctickers  = st.session_state.get("cmp_tickers", _ctickers)

    if not _cresults:
        st.info("Enter 2–3 tickers above and click Compare.")
        st.stop()

    _SIG_C = {"STRONG BUY":"#2ecc71","BUY":"#27ae60","WATCH":"#f39c12","NEUTRAL":"#aaa","AVOID":"#e74c3c"}
    _ncols = len(_ctickers)
    _hhcols = st.columns(_ncols)
    for _i, _tk in enumerate(_ctickers):
        _cr = _cresults[_tk]; _cscore = _cr["score"]
        _csig, _ccol = signal_label(_cscore)
        _cfund = _cr["fund"]; _cprice = _cfund.get("current_price"); _cpred = _cr["pred"]
        _cpred_html = ""
        if _cpred:
            _pret,_pci,_ntr,_dacc = _cpred; _pcol2 = "#2ecc71" if _pret>0 else "#e74c3c"
            _cpred_html = f"<div style='font-size:0.82rem;margin-top:6px;'><span style='color:{_pcol2};font-weight:700;'>ML: {_pret:+.1f}%</span><span style='color:#888;'> ±{_pci:.1f}%</span></div>"
        _hhcols[_i].markdown(
            f"<div style='background:{_ccol}22;border:2px solid {_ccol};border-radius:10px;padding:16px;text-align:center;'>"
            f"<div style='font-size:1.8rem;font-weight:900;color:#fff;'>{_tk}</div>"
            f"<div style='color:#aaa;font-size:0.82rem;'>{_cfund.get('company_name','')[:28]}</div>"
            f"<div style='font-size:2rem;font-weight:900;color:{_ccol};margin:8px 0;'>{_cscore:.0f}</div>"
            f"<div style='font-weight:700;color:{_ccol};'>{_csig}</div>"
            + (f"<div style='color:#aaa;font-size:0.82rem;'>${_cprice:.2f}</div>" if _cprice else "")
            + _cpred_html + "</div>", unsafe_allow_html=True)

    st.divider()
    st.subheader("📐 Factor Breakdown")
    import pandas as _cpd
    _crow_data = []
    for _fn in _FN:
        _crow = {"Factor": _fn.replace("_"," ").title()}
        for _tk in _ctickers: _crow[_tk] = round(_cresults[_tk]["fp"].get(_fn, 0.0), 2)
        _crow_data.append(_crow)
    _cdf = _cpd.DataFrame(_crow_data)
    def _cstyle(v):
        if not isinstance(v,(int,float)): return ""
        return "color:#2ecc71;font-weight:bold" if v>0 else "color:#e74c3c;font-weight:bold" if v<0 else "color:#666"
    st.dataframe(_cdf.style.applymap(_cstyle, subset=_ctickers), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📊 Key Metrics")
    _cm_rows = []
    _CMETRICS = [
        ("Price",         lambda f: f"${f.get('current_price'):.2f}" if f.get("current_price") else "N/A"),
        ("Analyst Target",lambda f: f"${f.get('analyst_target'):.2f}" if f.get("analyst_target") else "N/A"),
        ("Wall St Rating",lambda f: f.get("analyst_rating","N/A")),
        ("RSI",           lambda f: f"{f.get('rsi'):.0f}" if f.get("rsi") else "N/A"),
        ("P/E (TTM)",     lambda f: f"{f.get('pe_ratio'):.1f}" if f.get("pe_ratio") else "N/A"),
        ("Fwd P/E",       lambda f: f"{f.get('forward_pe'):.1f}" if f.get("forward_pe") else "N/A"),
        ("Market Cap",    lambda f: fmt_large(f.get("market_cap"))),
        ("52w High %",    lambda f: f"{f.get('pct_from_52w_high'):+.1f}%" if f.get("pct_from_52w_high") is not None else "N/A"),
        ("Volume Ratio",  lambda f: f"{f.get('volume_ratio'):.1f}×" if f.get("volume_ratio") else "N/A"),
        ("Earnings In",   lambda f: f"{f.get('earnings_days_until')}d" if f.get("earnings_days_until") is not None else "—"),
        ("Upgrades 30d",  lambda f: str(f.get("analyst_upgrades_30d",0))),
        ("Inst Score",    lambda f: str(f.get("inst_score",0))),
        ("Sector",        lambda f: f.get("sector","N/A")),
    ]
    for _lbl, _fn2 in _CMETRICS:
        _row = {"Metric": _lbl}
        for _tk in _ctickers:
            try: _row[_tk] = _fn2(_cresults[_tk]["fund"])
            except: _row[_tk] = "N/A"
        _cm_rows.append(_row)
    st.dataframe(_cpd.DataFrame(_cm_rows), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📈 Google Trends")
    _gt_cols = st.columns(_ncols)
    for _i, _tk in enumerate(_ctickers):
        with _gt_cols[_i]:
            with st.spinner(f"Trends {_tk}…"):
                _td = _ft_cmp(_tk)
            if _td.get("error"): st.caption(f"No data: {_td['error']}"); continue
            _tc = {"spike":"#f39c12","rising":"#2ecc71","falling":"#e74c3c","stable":"#888"}.get(_td["trend"],"#888")
            _tl = {"spike":"🔥 Spike","rising":"📈 Rising","falling":"📉 Falling","stable":"➡️ Stable"}.get(_td["trend"],"—")
            st.markdown(f"<div style='background:{_tc}18;border:1px solid {_tc}55;border-radius:8px;padding:10px;text-align:center;'>"
                        f"<div style='color:{_tc};font-weight:700;'>{_tl}</div>"
                        f"<div style='color:#aaa;font-size:0.8rem;'>{_td['pct_change']:+.0f}% vs prior 4wk</div></div>",unsafe_allow_html=True)
            if _td.get("weekly_data"):
                import pandas as _pd3
                _wdf = _pd3.DataFrame(_td["weekly_data"]).rename(columns={"date":"Date","value":"Interest"})
                _wdf["Date"] = _pd3.to_datetime(_wdf["Date"])
                st.line_chart(_wdf.set_index("Date")["Interest"], height=120, use_container_width=True)

    st.divider()
    st.subheader("💬 Scoring Reasons")
    _rc = st.columns(_ncols)
    for _i, _tk in enumerate(_ctickers):
        with _rc[_i]:
            st.markdown(f"**{_tk}**")
            for _rsn in _cresults[_tk]["reasons"]: st.caption(_rsn)

    st.stop()  # ← don't render Single Stock view below

# ── SINGLE STOCK VIEW ─────────────────────────────────────────────────────────
st.caption("Buy / Hold / Sell signals + detailed metrics for any stock. Data via Yahoo Finance (free, cached daily).")

# ── Ticker selection ───────────────────────────────────────────────────────────
trades = load_cache()
df = pd.DataFrame(trades) if trades else pd.DataFrame()

if not df.empty:
    df["ticker"]           = df["ticker"].fillna("").str.upper().str.strip()
    df["transaction_type"] = df["transaction_type"].fillna("")
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")

all_disclosed_tickers: list[str] = []
if not df.empty and "ticker" in df.columns:
    all_disclosed_tickers = sorted(
        t for t in df["ticker"].dropna().unique()
        if t not in ("N/A", "--", "")
    )


def _pol_buys_30d(ticker: str) -> int:
    """Count distinct purchase transactions for ticker in last 30 days."""
    if df.empty:
        return 0
    cutoff = pd.Timestamp.today() - pd.Timedelta(days=30)
    mask = (
        (df["ticker"] == ticker) &
        df["transaction_type"].str.contains("Purchase", case=False, na=False) &
        (df["transaction_date"] >= cutoff)
    )
    return int(mask.sum())

st.subheader("Select Stocks")
col1, col2, col3 = st.columns([3, 2, 1])

with col1:
    selected = st.multiselect(
        "From politicians' disclosed tickers (all transactions)",
        options=all_disclosed_tickers,
        default=st.session_state.get("fa_tickers_sel", []),
        key="fa_multiselect",
        placeholder="Search or pick tickers…",
    )
with col2:
    custom = st.text_input(
        "Add any ticker (comma-separated)",
        placeholder="e.g. TSLA, MSFT, AMZN",
        key="fa_custom",
    )
with col3:
    st.write("")
    st.write("")
    run = st.button("Analyse", type="primary", use_container_width=True)

custom_list = [t.strip().upper() for t in custom.split(",") if t.strip()]
all_tickers = sorted(set(selected + custom_list))

if not all_tickers and "fa_results" not in st.session_state:
    st.info("Pick tickers from the dropdown or type your own to get Buy/Hold/Sell signals.")
    st.stop()

# ── Fetch data ─────────────────────────────────────────────────────────────────
has_cached = "fa_results" in st.session_state
tickers_changed = has_cached and st.session_state.get("fa_tickers_key") != all_tickers and bool(all_tickers)

if tickers_changed and not run:
    st.info("Ticker selection changed — click **Analyse** to refresh results.")

if run:
    st.session_state.fa_tickers_key = all_tickers
    st.session_state.fa_tickers_sel = selected
    results = {}
    bar = st.progress(0, text="Fetching data…")
    for i, ticker in enumerate(all_tickers):
        bar.progress((i + 1) / len(all_tickers), text=f"Fetching {ticker}…")
        results[ticker] = fetch_fundamentals(ticker)
    bar.empty()
    st.session_state.fa_results = results

if "fa_results" in st.session_state:
    results = st.session_state.fa_results

    # ── Summary cards (one per stock) ─────────────────────────────────────────
    st.divider()
    st.subheader(f"Results — {len(results)} stock(s) analysed")

    cols_per_row = 3
    tickers_list = list(results.keys())
    for row_start in range(0, len(tickers_list), cols_per_row):
        cols = st.columns(cols_per_row)
        for col_idx, ticker in enumerate(tickers_list[row_start:row_start + cols_per_row]):
            data = results[ticker]
            signal, colour, _ = recommend(data)
            criteria = screen(data)
            passes = sum(1 for v in criteria.values() if v is True)
            price = data.get("current_price")
            target = data.get("analyst_target")
            upside = ((target - price) / price * 100) if target and price else None

            # Growth score
            g_score, _, _fp = score_stock_detailed(data, _pol_buys_30d(ticker))
            g_label, g_colour = signal_label(g_score)

            with cols[col_idx]:
                # Analyst rating badge colour
                rating_raw = (data.get("analyst_rating") or "N/A").lower()
                if "strong buy" in rating_raw or rating_raw == "buy":
                    r_colour, r_bg = "#2ecc71", "#2ecc7122"
                elif "strong sell" in rating_raw or rating_raw == "sell":
                    r_colour, r_bg = "#e74c3c", "#e74c3c22"
                elif "hold" in rating_raw or "neutral" in rating_raw:
                    r_colour, r_bg = "#f39c12", "#f39c1222"
                else:
                    r_colour, r_bg = "#aaaaaa", "#33333355"

                price_str  = f"${price:.2f}" if price else "N/A"
                target_str = f"${target:.2f} ({upside:+.0f}%)" if target and upside is not None else "N/A"
                n_analysts = data.get("num_analyst_opinions") or "?"
                rating_label_str = data.get("analyst_rating") or "N/A"

                score_bar_filled = int(g_score)
                score_bar_empty  = 100 - score_bar_filled

                st.markdown(
                    f"""
                    <div style="border:1px solid #333; border-radius:10px; padding:16px; margin-bottom:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                            <div>
                                <div style="font-size:1.4rem; font-weight:bold;">{ticker}</div>
                                <div style="font-size:0.82rem; color:#aaa;">{data.get('company_name','')}</div>
                                <div style="font-size:0.78rem; color:#888;">{data.get('sector') or ''}</div>
                            </div>
                            <div style="text-align:right;">
                                <div style="font-size:1.6rem; font-weight:bold; color:{colour};">{signal}</div>
                                <div style="font-size:0.75rem; color:#888;">Buy/Hold/Sell</div>
                            </div>
                        </div>
                        <div style="margin-top:10px; display:flex; align-items:center; gap:10px;">
                            <div style="flex:1; background:#222; border-radius:4px; height:8px; overflow:hidden;">
                                <div style="width:{score_bar_filled}%; height:100%; background:{g_colour};"></div>
                            </div>
                            <span style="color:{g_colour}; font-weight:bold; font-size:0.95rem; white-space:nowrap;">
                                {g_score:.0f}/100 &nbsp;{g_label}
                            </span>
                        </div>
                        <div style="margin-top:8px; padding:6px 10px; background:{r_bg}; border:1px solid {r_colour}; border-radius:6px; display:inline-block;">
                            <span style="color:{r_colour}; font-weight:bold; font-size:0.9rem;">⭐ Wall St: {rating_label_str}</span>
                            <span style="color:#aaa; font-size:0.78rem;"> &nbsp;({n_analysts} analysts)</span>
                        </div>
                        <div style="margin-top:10px; font-size:0.88rem;">
                            💵 <b>{price_str}</b> &nbsp;|&nbsp; 🎯 Target: <b>{target_str}</b>
                        </div>
                        <div style="margin-top:6px; font-size:0.82rem; color:#aaa;">
                            Screen: {passes}/4 &nbsp;|&nbsp;
                            P/E: {fmt_ratio(data.get('pe_ratio'))} &nbsp;|&nbsp;
                            FCF: {fmt_large(data.get('free_cash_flow'))}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    # ── Detailed cards ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Detailed Analysis")

    for ticker, data in results.items():
        signal, colour, reasons = recommend(data)
        criteria = screen(data)
        passes = sum(1 for v in criteria.values() if v is True)
        g_score, g_reasons, g_factor_pts = score_stock_detailed(data, _pol_buys_30d(ticker))
        g_label, g_colour  = signal_label(g_score)

        with st.expander(f"{'✅' if signal=='BUY' else '⚠️' if signal=='HOLD' else '❌'}  {ticker} — {data.get('company_name', ticker)}  |  Signal: {signal}  |  Score: {g_score:.0f}/100", expanded=True):

            if data.get("error"):
                st.error(f"Could not fetch data: {data['error']}")
                continue

            # Top row
            price  = data.get("current_price")
            target = data.get("analyst_target")
            upside = ((target - price) / price * 100) if target and price else None
            w52h   = data.get("week_52_high")
            w52l   = data.get("week_52_low")
            pct_from_high = ((price - w52h) / w52h * 100) if price and w52h else None

            h1, h2, h3, h4, h5 = st.columns(5)
            h1.metric("Price", f"${price:.2f}" if price else "N/A",
                      delta=f"{pct_from_high:+.1f}% from 52w high" if pct_from_high else None)
            h2.metric("Analyst Target", f"${target:.2f}" if target else "N/A",
                      delta=f"{upside:+.0f}% upside" if upside else None)
            h3.metric("Market Cap", fmt_large(data.get("market_cap")))
            h4.metric("Sector", data.get("sector") or "N/A")
            h5.metric("52w Range", f"${w52l:.0f} – ${w52h:.0f}" if w52h and w52l else "N/A")

            st.divider()

            # ── TradingView chart ──────────────────────────────────────────────
            st.markdown("##### TradingView Chart")
            tv_interval_map = {"1D": "D", "1W": "W", "1M": "M"}
            tv_interval = st.radio(
                "Interval",
                list(tv_interval_map.keys()),
                index=0,
                horizontal=True,
                key=f"tv_interval_{ticker}",
            )
            tv_html = f"""
            <div id="tv_{ticker}" style="height:500px;">
              <script src="https://s3.tradingview.com/tv.js"></script>
              <script>
                new TradingView.widget({{
                  "autosize": true,
                  "symbol": "{ticker}",
                  "interval": "{tv_interval_map[tv_interval]}",
                  "timezone": "America/New_York",
                  "theme": "dark",
                  "style": "1",
                  "locale": "en",
                  "toolbar_bg": "#1e1e1e",
                  "enable_publishing": false,
                  "hide_top_toolbar": false,
                  "hide_legend": false,
                  "save_image": false,
                  "studies": ["RSI@tv-basicstudies", "MACD@tv-basicstudies"],
                  "container_id": "tv_{ticker}",
                  "width": "100%",
                  "height": 500
                }});
              </script>
            </div>
            """
            components.html(tv_html, height=520, scrolling=False)

            st.divider()

            # ── Signal box (Buy/Hold/Sell) ────────────────────────────────────
            st.markdown(
                f"<div style='background-color:{colour}22; border-left:5px solid {colour}; "
                f"padding:12px 16px; border-radius:6px; margin-bottom:12px;'>"
                f"<span style='font-size:1.5rem; font-weight:bold; color:{colour};'>{signal}</span>"
                f"<span style='margin-left:12px; color:#ccc;'>— based on {len(reasons)} factors</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Reasons two-column
            pos = [r for r in reasons if r.startswith("✅")]
            neg = [r for r in reasons if r.startswith("❌")]
            rc1, rc2 = st.columns(2)
            with rc1:
                st.markdown("**Why to buy / keep:**")
                for r in pos:
                    st.markdown(r)
            with rc2:
                st.markdown("**Risks / concerns:**")
                for r in neg:
                    st.markdown(r)

            st.divider()

            # ── Growth Score (0-100) ──────────────────────────────────────────
            st.markdown("##### Growth Score")
            gs_col1, gs_col2 = st.columns([1, 3])
            with gs_col1:
                st.markdown(
                    f"<div style='background:{g_colour}22; border:2px solid {g_colour}; border-radius:10px; "
                    f"padding:14px; text-align:center;'>"
                    f"<div style='font-size:2.4rem; font-weight:bold; color:{g_colour};'>{g_score:.0f}</div>"
                    f"<div style='font-size:0.75rem; color:#aaa;'>out of 100</div>"
                    f"<div style='margin-top:4px; font-size:1rem; font-weight:bold; color:{g_colour};'>{g_label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with gs_col2:
                st.markdown(
                    f"<div style='background:#222; border-radius:6px; height:18px; overflow:hidden; margin-top:8px;'>"
                    f"<div style='width:{g_score:.0f}%; height:100%; background:linear-gradient(90deg,{g_colour}88,{g_colour});'></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                pos_g = [r for r in g_reasons if not r.startswith("⚠️") and not r.startswith("🔴") and not r.startswith("📉")]
                neg_g = [r for r in g_reasons if r.startswith("⚠️") or r.startswith("🔴") or r.startswith("📉")]
                gc1, gc2 = st.columns(2)
                with gc1:
                    for r in pos_g:
                        st.caption(r)
                with gc2:
                    for r in neg_g:
                        st.caption(r)

            # ML prediction
            try:
                pred = predict_return(g_factor_pts)
                if pred:
                    p_ret, p_ci, n_train, r2 = pred
                    p_col = "#2ecc71" if p_ret > 0 else "#e74c3c"
                    conf  = "High" if n_train >= 100 else "Medium" if n_train >= 50 else "Early"
                    st.markdown(
                        f"<div style='background:#1a2a1a; border:1px solid {p_col}55; border-radius:8px; "
                        f"padding:12px 16px; margin:8px 0;'>"
                        f"<div style='font-size:0.78rem; color:#aaa; margin-bottom:4px;'>🧠 ML Predicted 30-day return</div>"
                        f"<div style='font-size:1.6rem; font-weight:bold; color:{p_col};'>"
                        f"{p_ret:+.1f}% <span style='font-size:0.85rem; font-weight:normal; color:#aaa;'>± {p_ci:.1f}%</span></div>"
                        f"<div style='font-size:0.75rem; color:#888; margin-top:4px;'>"
                        f"Based on {n_train} historical outcomes · {conf} confidence · R²={r2:.2f}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("🧠 ML prediction: not enough data yet — run Growth Report daily to build history.")
            except Exception:
                pass

            st.divider()

            # Screening metrics
            st.markdown("##### Growth Screening (threshold: > 5%)")
            sc1, sc2, sc3, sc4 = st.columns(4)
            def _badge(val):
                if val is None: return "❓ No data"
                return "✅ Pass" if val else "❌ Fail"

            sc1.metric("5yr Total Return", fmt_pct(data["total_return_5yr_pct"]))
            sc1.caption(_badge(criteria["5yr Total Return > 5%"]))
            sc2.metric("5yr Revenue CAGR", fmt_pct(data["revenue_cagr_5yr_pct"]))
            sc2.caption(_badge(criteria["5yr Revenue CAGR > 5%"]))
            sc3.metric("EPS YoY Growth", fmt_pct(data["eps_yoy_pct"]))
            sc3.caption(_badge(criteria["EPS YoY Growth > 5%"]))
            sc4.metric("5yr Net Income CAGR", fmt_pct(data["net_income_cagr_5yr_pct"]))
            sc4.caption(_badge(criteria["5yr Net Income CAGR > 5%"]))

            st.markdown("##### Valuation & Financial Health")
            v1, v2, v3, v4, v5 = st.columns(5)
            v1.metric("Free Cash Flow",    fmt_large(data["free_cash_flow"]),
                      help="Operating CF minus CapEx (TTM). Positive = company generates real cash.")
            v2.metric("LT Debt / Capital", fmt_pct(data["lt_debt_to_capital_pct"]),
                      help="< 30% = low risk. > 60% = heavy debt burden.")
            v3.metric("P/S Ratio",         fmt_ratio(data["price_to_sales"]),
                      help="Price-to-sales. Lower = cheaper relative to revenue.")
            v4.metric("P/E Ratio",         fmt_ratio(data["pe_ratio"]),
                      help="Trailing price-to-earnings.")
            v5.metric("Forward P/E",       fmt_ratio(data["forward_pe"]),
                      help="Based on next 12 months estimated earnings.")

            v6, v7, v8, v9, v10 = st.columns(5)
            v6.metric("PEG Ratio",         fmt_ratio(data.get("peg_ratio")),
                      help="P/E divided by growth rate. < 1 = undervalued vs growth.")
            v7.metric("Return on Equity",  fmt_pct(data.get("return_on_equity")),
                      help="Net income as % of shareholder equity. Higher = more efficient.")
            v8.metric("Profit Margin",     fmt_pct(data.get("profit_margin")),
                      help="Net income as % of revenue.")
            v9.metric("Beta",              f"{data.get('beta'):.2f}" if data.get("beta") else "N/A",
                      help="< 1 = less volatile than market. > 1 = more volatile.")
            v10.metric("Dividend Yield",
                       f"{data['dividend_yield']*100:.2f}%" if data.get("dividend_yield") else "None",
                       help="Annual dividend as % of price.")

            # ── 5-Year Annual Breakdown ───────────────────────────────────────
            annual_returns   = data.get("annual_returns", [])
            annual_financials = data.get("annual_financials", [])

            if annual_returns or annual_financials:
                st.divider()
                st.markdown("##### 5-Year Annual Breakdown")

                # Merge annual_returns and annual_financials by year
                yr_map: dict = {}
                for row in annual_returns:
                    yr = row["year"]
                    yr_map.setdefault(yr, {})["Price Return %"] = row.get("price_return_pct")
                for row in annual_financials:
                    yr = row["year"]
                    yr_map.setdefault(yr, {})["Revenue"]    = row.get("revenue")
                    yr_map.setdefault(yr, {})["Net Income"] = row.get("net_income")
                    yr_map.setdefault(yr, {})["EPS"]        = row.get("eps")

                table_rows = []
                for yr in sorted(yr_map.keys()):
                    d = yr_map[yr]
                    ret = d.get("Price Return %")
                    rev = d.get("Revenue")
                    ni  = d.get("Net Income")
                    eps = d.get("EPS")
                    table_rows.append({
                        "Year":           yr,
                        "Price Return":   f"{ret:+.1f}%" if ret is not None else "N/A",
                        "Revenue":        fmt_large(rev) if rev is not None else "N/A",
                        "Net Income":     fmt_large(ni)  if ni  is not None else "N/A",
                        "EPS":            f"${eps:.2f}"  if eps is not None else "N/A",
                    })

                if table_rows:
                    ann_df = pd.DataFrame(table_rows).set_index("Year")
                    st.dataframe(ann_df, use_container_width=True)

                # Price return bar chart
                chart_rows = [(r["year"], r.get("price_return_pct")) for r in annual_returns if r.get("price_return_pct") is not None]
                if chart_rows:
                    chart_rows.sort(key=lambda x: x[0])
                    chart_data = pd.DataFrame(chart_rows, columns=["Year", "Price Return %"]).set_index("Year")
                    st.bar_chart(chart_data, color="#4a90d9", height=220)

            # Politicians who traded this stock (all transactions)
            if not df.empty:
                pol_trades = df[df["ticker"] == ticker][
                    ["name", "chamber", "transaction_type", "transaction_date", "disclosure_date", "amount"]
                ].copy()
                pol_trades["transaction_date"] = pd.to_datetime(
                    pol_trades["transaction_date"], errors="coerce"
                ).dt.strftime("%Y-%m-%d")
                pol_trades["disclosure_date"] = pd.to_datetime(
                    pol_trades["disclosure_date"], errors="coerce"
                ).dt.strftime("%Y-%m-%d")
                pol_trades = pol_trades.rename(columns={
                    "name":             "Politician",
                    "chamber":          "Chamber",
                    "transaction_type": "Transaction",
                    "transaction_date": "Trade Date",
                    "disclosure_date":  "Disclosed",
                    "amount":           "Amount Range",
                }).sort_values("Trade Date", ascending=False)

                if not pol_trades.empty:
                    st.divider()
                    buys_n  = pol_trades["Transaction"].str.contains("Purchase", case=False, na=False).sum()
                    sells_n = pol_trades["Transaction"].str.contains("Sale",     case=False, na=False).sum()
                    st.markdown(
                        f"##### Politician Trading History &nbsp; "
                        f"<span style='color:#2ecc71; font-size:0.9rem;'>▲ {buys_n} purchase(s)</span> &nbsp; "
                        f"<span style='color:#e74c3c; font-size:0.9rem;'>▼ {sells_n} sale(s)</span>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(
                        pol_trades,
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Transaction": st.column_config.TextColumn(width="medium"),
                        },
                    )

            # ── Google Trends ─────────────────────────────────────────────────
            st.divider()
            with st.expander("📈 Google Trends Interest", expanded=False):
                st.caption("Rising public search interest in a stock often precedes price moves.")
                with st.spinner(f"Fetching Google Trends for {ticker}…"):
                    try:
                        from google_trends import fetch_trends as _ft
                        _td = _ft(ticker)
                        if _td.get("error"):
                            st.caption(f"No trend data available: {_td['error']}")
                        else:
                            _trend   = _td["trend"]
                            _pct_chg = _td["pct_change"]
                            _spike   = _td["spike_ratio"]
                            _recent  = _td["recent_avg"]
                            _weekly  = _td.get("weekly_data", [])
                            _tcol    = {"spike": "#f39c12", "rising": "#2ecc71",
                                        "falling": "#e74c3c", "stable": "#888"}.get(_trend, "#888")
                            _tlbl    = {"spike": "🔥 Spiking", "rising": "📈 Trending Up",
                                        "falling": "📉 Trending Down", "stable": "➡️ Stable"}.get(_trend, "—")
                            st.markdown(
                                f"<span style='color:{_tcol}; font-weight:700; font-size:1.1rem;'>"
                                f"{_tlbl}</span>"
                                f"<span style='color:#888; font-size:0.85rem; margin-left:10px;'>"
                                f"Recent avg interest: {_recent:.0f}/100 &nbsp;·&nbsp; "
                                f"4-week change: {_pct_chg:+.0f}%"
                                + (f" &nbsp;·&nbsp; <b>Spike ratio: {_spike:.1f}×</b>" if _spike >= 2 else "")
                                + "</span>",
                                unsafe_allow_html=True,
                            )
                            if _weekly:
                                import pandas as _pd2
                                _wdf = _pd2.DataFrame(_weekly).rename(
                                    columns={"date": "Date", "value": "Search Interest"})
                                _wdf["Date"] = _pd2.to_datetime(_wdf["Date"])
                                st.line_chart(_wdf.set_index("Date")["Search Interest"],
                                              height=160, use_container_width=True)
                    except Exception as _te:
                        st.caption(f"Trends unavailable: {_te}")

            # ── PDF download ──────────────────────────────────────────────────
            st.divider()
            try:
                pdf_bytes = generate_stock_pdf(data, score=g_score, reasons=g_reasons)
                st.download_button(
                    label=f"Download {ticker} Full Report (PDF)",
                    data=pdf_bytes,
                    file_name=f"{ticker}_report_{datetime.today().strftime('%Y-%m-%d')}.pdf",
                    mime="application/pdf",
                    key=f"pdf_{ticker}",
                )
            except Exception as e:
                st.caption(f"PDF generation failed: {e}")
