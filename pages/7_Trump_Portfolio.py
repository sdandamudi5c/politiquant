"""
PolitiQuant · Trump Family Portfolio
Tab 1 — Known holdings from OGE financial disclosures (manually maintained)
Tab 2 — Live news feed: any time Trump/family mentions a company or stock
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import resource as _r
    _s, _h = _r.getrlimit(_r.RLIMIT_NOFILE)
    _t = min(65536, _h) if _h > 0 else 65536
    if _s < _t:
        _r.setrlimit(_r.RLIMIT_NOFILE, (_t, _h))
except Exception:
    pass

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from sidebar_jobs import render as _render_sidebar
_render_sidebar()

st.set_page_config(
    page_title="PolitiQuant · Trump Portfolio",
    page_icon="🇺🇸",
    layout="wide",
)

# ── Persistence ────────────────────────────────────────────────────────────────
_DIR      = os.path.dirname(os.path.dirname(__file__))
_HOL_FILE = os.path.join(_DIR, "trump_holdings.json")

_DEFAULT_HOLDINGS = [
    # Pre-populated from SEC EDGAR (auto-updated) + OGE disclosures (manually maintained)
    {"ticker": "DJT",  "held_by": "Donald Trump",  "notes": "Trump Media & Technology Group — stake auto-fetched from SEC EDGAR", "source": "SEC 13D/A"},
    {"ticker": "GLD",  "held_by": "Donald Trump",  "notes": "Gold ETF — per OGE Annual Disclosure",             "source": "OGE 2025"},
    {"ticker": "WYNN", "held_by": "Donald Trump",  "notes": "Wynn Resorts — per OGE Annual Disclosure",         "source": "OGE 2025"},
]

def _load_holdings() -> list[dict]:
    try:
        with open(_HOL_FILE) as f:
            return json.load(f)
    except Exception:
        return list(_DEFAULT_HOLDINGS)

def _save_holdings(h: list[dict]) -> None:
    with open(_HOL_FILE, "w") as f:
        json.dump(h, f, indent=2)

if "trump_holdings" not in st.session_state:
    st.session_state.trump_holdings = _load_holdings()

holdings: list[dict] = st.session_state.trump_holdings

# ── Page header ────────────────────────────────────────────────────────────────
st.title("🇺🇸 Trump Family Portfolio & News")
st.caption(
    "Tab 1: Known stock holdings from OGE financial disclosures — you maintain this list "
    "as new disclosures are released.  "
    "Tab 2: Live feed of any news where Trump or his family mention a company or stock."
)

# ── Live SEC EDGAR data banner ─────────────────────────────────────────────────
try:
    from trump_edgar import fetch_djt_stake, fetch_djt_insider_transactions
    _stake = fetch_djt_stake()
    if _stake:
        _pct    = _stake.get("pct", 0)
        _shares = _stake.get("shares_owned")
        _sdate  = _stake.get("date", "")
        _sec_url = _stake.get("url", "")
        st.markdown(
            f"<div style='background:#0a1a0a; border:1px solid #2ecc7155; border-radius:8px; "
            f"padding:10px 16px; margin-bottom:12px; display:flex; gap:24px; align-items:center;'>"
            f"<div><span style='color:#888; font-size:0.75rem;'>📊 SEC EDGAR — Live</span><br>"
            f"<span style='font-size:1.1rem; font-weight:800; color:#2ecc71;'>DJT: {_pct}% stake</span>"
            f"<span style='color:#aaa; font-size:0.8rem;'>"
            + (f" &nbsp;·&nbsp; {_shares:,} shares" if _shares else "")
            + f"</span></div>"
            f"<div style='color:#555; font-size:0.75rem;'>Filed {_sdate} &nbsp;"
            + (f"<a href='{_sec_url}' target='_blank' style='color:#4a90d9;'>View SEC filing ↗</a>" if _sec_url else "")
            + "</div>"
            f"<div style='flex:1; text-align:right; font-size:0.72rem; color:#555;'>"
            f"Auto-fetched from SEC EDGAR · updated every 6h</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
except Exception:
    pass

tab1, tab2 = st.tabs(["📋 Holdings Tracker", "📰 Live News Feed"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Holdings Tracker
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Known Trump Family Holdings")
    # Data freshness panel
    c_oge, c_sec = st.columns(2)
    with c_oge:
        st.markdown(
            "**📄 OGE Annual Disclosures** *(manual — PDF)*\n\n"
            "Trump family files with the [Office of Government Ethics]"
            "(https://efts.usethis.oge.gov/public/search/#/?search=trump&filerType=annual) "
            "every year (~May). The **2026 report** (covering 2025 holdings) "
            "should be available now. Open the link, download the PDF, "
            "and add any new holdings below.\n\n"
            "**Covers:** All assets — stocks, real estate, funds, crypto."
        )
    with c_sec:
        st.markdown(
            "**🔄 SEC EDGAR** *(auto-fetched every 6h)*\n\n"
            "- **DJT stake** — Schedule 13D/A amendments filed by Trump directly\n"
            "- **DJT insiders** — Form 4 transactions by directors/officers\n"
            "- Latest: **41.5% (114.75M shares)** as of Dec 22, 2025\n\n"
            "**Covers:** Only publicly-traded company filings (DJT)."
        )
    st.divider()

    # ── Manage holdings ───────────────────────────────────────────────────────
    with st.expander("✏️ Add / Remove Holdings", expanded=not holdings):
        ac, rc = st.columns(2)

        with ac:
            st.markdown("**Add a holding**")
            new_ticker  = st.text_input("Ticker",   placeholder="e.g. AAPL",            key="th_ticker").upper().strip()
            new_held_by = st.selectbox("Held by",   ["Donald Trump", "Ivanka Trump",
                                                     "Jared Kushner", "Donald Trump Jr.",
                                                     "Eric Trump", "Lara Trump",
                                                     "Melania Trump", "Trump Organization"],
                                       key="th_held_by")
            new_notes   = st.text_input("Notes",    placeholder="Optional: source / context", key="th_notes")
            new_source  = st.text_input("Source",   placeholder="e.g. OGE 2025 Disclosure",   key="th_source")
            if st.button("➕ Add Holding", key="th_add", use_container_width=True):
                if new_ticker:
                    existing = [h["ticker"] for h in holdings if h.get("held_by") == new_held_by]
                    if new_ticker in existing:
                        st.warning(f"{new_ticker} already tracked for {new_held_by}.")
                    else:
                        holdings.append({"ticker": new_ticker, "held_by": new_held_by,
                                         "notes": new_notes, "source": new_source})
                        _save_holdings(holdings)
                        st.success(f"Added {new_ticker} ({new_held_by})")
                        st.rerun()
                else:
                    st.warning("Enter a ticker symbol.")

        with rc:
            st.markdown("**Remove a holding**")
            if holdings:
                options = [f"{h['ticker']} — {h['held_by']}" for h in holdings]
                to_del  = st.multiselect("Select to remove", options, key="th_del",
                                         label_visibility="collapsed")
                if st.button("🗑️ Remove", key="th_del_btn", use_container_width=True,
                             disabled=not to_del):
                    del_set = set(to_del)
                    holdings[:] = [h for h in holdings
                                   if f"{h['ticker']} — {h['held_by']}" not in del_set]
                    _save_holdings(holdings)
                    st.rerun()
            else:
                st.info("No holdings yet — add some on the left.")

    if not holdings:
        st.info("Add holdings above to start tracking.")
        st.stop()

    # ── Score holdings ─────────────────────────────────────────────────────────
    _scored_key = "trump_holdings_scored"
    _has_scores = bool(st.session_state.get(_scored_key))

    sc1, sc2 = st.columns([3, 1])
    with sc1:
        if st.button("⚡ Score All Holdings", type="primary", key="th_score"):
            st.session_state.pop(_scored_key, None)
            with st.spinner(f"Scoring {len(holdings)} holdings..."):
                from fundamentals import fetch_fundamentals
                from scorer import score_stock_detailed, signal_label
                from score_history import predict_return

                scored = []
                tickers = list({h["ticker"] for h in holdings})

                def _fetch_one(ticker):
                    try:
                        fund = fetch_fundamentals(ticker)
                        sc, reasons, fp = score_stock_detailed(fund)
                        sig, col = signal_label(sc)
                        pred_raw = predict_return(fp)
                        return {
                            "ticker":   ticker,
                            "company":  fund.get("company_name", ticker),
                            "score":    round(sc, 1),
                            "signal":   sig,
                            "colour":   col,
                            "price":    fund.get("current_price"),
                            "target":   fund.get("analyst_target"),
                            "rsi":      fund.get("rsi"),
                            "mom_1m":   fund.get("mom_1m_pct"),
                            "mom_3m":   fund.get("mom_3m_pct"),
                            "fh_tot":   fund.get("fh_rec_total", 0),
                            "fh_sb":    fund.get("fh_strong_buy", 0),
                            "fh_b":     fund.get("fh_buy", 0),
                            "fh_h":     fund.get("fh_hold", 0),
                            "fh_s":     fund.get("fh_sell", 0),
                            "pol_flag": fund.get("political_flag"),
                            "pol_mod":  fund.get("political_score_mod", 0),
                            "pol_heads":fund.get("political_headlines", []),
                            "reasons":  reasons,
                            "pred":     pred_raw[0] if pred_raw else None,
                            "pred_ci":  pred_raw[1] if pred_raw else None,
                        }
                    except Exception:
                        return {"ticker": ticker, "score": 0, "signal": "N/A", "colour": "#888",
                                "company": ticker, "price": None, "target": None, "reasons": [],
                                "pol_flag": None, "pol_mod": 0, "pol_heads": [], "pred": None, "pred_ci": None,
                                "rsi": None, "mom_1m": None, "mom_3m": None, "fh_tot": 0,
                                "fh_sb": 0, "fh_b": 0, "fh_h": 0, "fh_s": 0}

                with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as ex:
                    futs = {ex.submit(_fetch_one, t): t for t in tickers}
                    for fut in as_completed(futs):
                        r = fut.result()
                        if r:
                            scored.append(r)

                # Map scores back onto holdings list
                score_map = {r["ticker"]: r for r in scored}
                st.session_state[_scored_key] = score_map
                st.success(f"Scored {len(scored)} holdings")
                st.rerun()

    with sc2:
        if _has_scores and st.button("🗑️ Clear Scores", key="th_clear"):
            st.session_state.pop(_scored_key, None)
            st.rerun()

    # ── Holdings table ─────────────────────────────────────────────────────────
    score_map = st.session_state.get(_scored_key, {})

    _SIG_COLS = {
        "STRONG BUY": "#2ecc71", "BUY": "#27ae60", "WATCH": "#f39c12",
        "NEUTRAL": "#888", "AVOID": "#e74c3c",
    }

    # Group by holder
    holders = sorted(set(h.get("held_by", "Unknown") for h in holdings))
    for holder in holders:
        holder_holdings = [h for h in holdings if h.get("held_by") == holder]
        st.markdown(f"### 👤 {holder}")
        for h in holder_holdings:
            ticker = h["ticker"]
            r      = score_map.get(ticker, {})
            scored = bool(r)
            sig_col = _SIG_COLS.get(r.get("signal", ""), "#888") if scored else "#444"

            with st.container():
                c1, c2, c3, c4 = st.columns([2, 1, 1, 3])

                # Ticker + company
                c1.markdown(
                    f"<span style='font-size:1.1rem; font-weight:900;'>{ticker}</span>"
                    + (f"&nbsp;<span style='color:#888; font-size:0.82rem;'>{r.get('company','')[:30]}</span>" if scored else "")
                    + (f"<br><span style='color:#555; font-size:0.72rem;'>{h.get('notes','')[:60]}</span>" if h.get('notes') else ""),
                    unsafe_allow_html=True,
                )

                # Score badge
                if scored:
                    c2.markdown(
                        f"<div style='text-align:center;'>"
                        f"<div style='font-size:1.5rem; font-weight:900; color:{sig_col};'>{r['score']:.0f}</div>"
                        f"<div style='font-size:0.7rem; color:{sig_col};'>{r.get('signal','')}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    c2.markdown("<div style='color:#555; font-size:0.8rem; text-align:center;'>Not scored</div>",
                                unsafe_allow_html=True)

                # Price
                if scored and r.get("price"):
                    p, t = r["price"], r.get("target")
                    upside = (t - p) / p * 100 if t and p else None
                    c3.markdown(
                        f"<div style='text-align:center;'>"
                        f"<div style='font-size:1rem; font-weight:700;'>${p:.2f}</div>"
                        + (f"<div style='font-size:0.7rem; color:#888;'>Target ${t:.2f}</div>" if t else "")
                        + (f"<div style='font-size:0.7rem; color:#2ecc71;'>+{upside:.1f}%</div>" if upside and upside > 0 else
                           f"<div style='font-size:0.7rem; color:#e74c3c;'>{upside:.1f}%</div>" if upside and upside < 0 else "")
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                # ML forecast + political flag
                if scored:
                    pred, ci = r.get("pred"), r.get("pred_ci")
                    pol_flag = r.get("pol_flag")
                    pol_mod  = r.get("pol_mod", 0)
                    parts = []
                    if pred is not None:
                        pc = "#2ecc71" if pred > 2 else "#e74c3c" if pred < -2 else "#888"
                        parts.append(
                            f"<span style='font-size:0.8rem; color:#aaa;'>ML 30d: </span>"
                            f"<span style='color:{pc}; font-weight:700;'>{pred:+.1f}%"
                            + (f" <span style='font-size:0.7rem; color:#666;'>±{ci:.1f}%</span>" if ci else "")
                            + "</span>"
                        )
                    if pol_flag and pol_mod != 0:
                        pc2 = "#2ecc71" if pol_mod > 0 else "#e74c3c"
                        parts.append(f"<span style='color:{pc2}; font-size:0.78rem;'>{pol_flag}</span>")
                    if parts:
                        c4.markdown("<br>".join(parts), unsafe_allow_html=True)

                # Political headlines for this ticker
                pol_heads = r.get("pol_heads", []) if scored else []
                if pol_heads:
                    with st.expander(f"📰 Political headlines for {ticker}", expanded=False):
                        for headline in pol_heads:
                            st.markdown(f"<span style='font-size:0.8rem; color:#ccc;'>• {headline}</span>",
                                        unsafe_allow_html=True)

                # Score reasons
                reasons = r.get("reasons", []) if scored else []
                if reasons:
                    with st.expander(f"Why {ticker} scored {r.get('score',0):.0f} pts", expanded=False):
                        pos = [x for x in reasons if x.startswith("✅")]
                        neg = [x for x in reasons if x.startswith("❌")]
                        rc1, rc2 = st.columns(2)
                        for p in pos:
                            rc1.markdown(f"<span style='font-size:0.8rem;'>{p}</span>",
                                         unsafe_allow_html=True)
                        for n in neg:
                            rc2.markdown(f"<span style='font-size:0.8rem;'>{n}</span>",
                                         unsafe_allow_html=True)

                # Source tag
                if h.get("source"):
                    st.markdown(
                        f"<span style='background:#1a1a2e; border:1px solid #333; border-radius:10px; "
                        f"padding:2px 8px; font-size:0.68rem; color:#666;'>📄 {h['source']}</span>",
                        unsafe_allow_html=True,
                    )

                st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Live Trump Family News Feed
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Trump Family News — Stock Mentions (Last 48h)")
    st.caption(
        "Scans White House RSS, Finnhub general news, and NYT every hour. "
        "Shows articles where Trump or his family mentions or is linked to a company/ticker. "
        "Click **Refresh** to fetch the latest."
    )

    col_refresh, col_filter = st.columns([2, 3])
    if col_refresh.button("🔄 Refresh News", key="trump_news_refresh", type="primary"):
        from political_signal import clear_cache
        clear_cache()
        st.session_state.pop("trump_news_feed", None)

    # Fetch feed
    if "trump_news_feed" not in st.session_state:
        with st.spinner("Fetching Trump family news..."):
            try:
                from political_signal import get_trump_family_news_feed
                st.session_state.trump_news_feed = get_trump_family_news_feed(hours=48)
            except Exception as e:
                st.session_state.trump_news_feed = []
                st.error(f"Failed to fetch news: {e}")

    feed: list[dict] = st.session_state.get("trump_news_feed", [])

    # Filter controls
    with col_filter:
        sentiment_filter = st.multiselect(
            "Filter by sentiment",
            ["positive", "negative", "neutral"],
            default=["positive", "negative", "neutral"],
            key="trump_news_filter",
            label_visibility="collapsed",
        )

    filtered_feed = [f for f in feed if f.get("sentiment") in sentiment_filter]

    if not filtered_feed:
        st.info(
            "No Trump family news mentioning stocks found in the current feed. "
            "This is normal — it only fires when Trump/family explicitly mentions a company. "
            "Try clicking **Refresh** or check back later."
        )
    else:
        st.markdown(f"**{len(filtered_feed)} articles found** mentioning Trump family + company/stock")
        st.write("")

        for item in filtered_feed:
            sent      = item.get("sentiment", "neutral")
            score     = item.get("score", 0)
            tickers   = item.get("tickers", [])
            companies = item.get("companies", [])
            speaker   = item.get("speaker", "Trump family")
            title     = item.get("title", "")
            link      = item.get("link", "")

            sent_col = "#2ecc71" if sent == "positive" else "#e74c3c" if sent == "negative" else "#888"
            sent_icon = "📈" if sent == "positive" else "📉" if sent == "negative" else "➖"
            border_col = sent_col + "44"

            with st.container():
                st.markdown(
                    f"<div style='border-left:3px solid {sent_col}; padding:10px 16px; "
                    f"background:#0d0d1a; border-radius:0 6px 6px 0; margin-bottom:8px;'>"

                    # Speaker badge + sentiment
                    f"<div style='display:flex; align-items:center; gap:8px; margin-bottom:4px;'>"
                    f"<span style='background:#1a1a2e; border:1px solid #333; border-radius:10px; "
                    f"padding:2px 8px; font-size:0.72rem; color:#aaa;'>🇺🇸 {speaker}</span>"
                    f"<span style='color:{sent_col}; font-size:0.75rem; font-weight:700;'>"
                    f"{sent_icon} {sent.upper()}</span>"
                    f"</div>"

                    # Headline
                    f"<div style='font-size:0.92rem; font-weight:600; color:#e8e8e8; margin-bottom:6px;'>"
                    + (f"<a href='{link}' target='_blank' style='color:#e8e8e8; text-decoration:none;'>{title}</a>"
                       if link else title)
                    + "</div>"

                    # Mentioned tickers
                    + (
                        "<div style='margin-top:4px;'>"
                        + "".join(
                            f"<span style='background:#1a2a3a; border:1px solid #4a90d9; "
                            f"border-radius:10px; padding:2px 8px; font-size:0.75rem; "
                            f"font-weight:700; color:#4a90d9; margin-right:4px;'>{t}</span>"
                            for t in tickers
                        )
                        + "".join(
                            f"<span style='background:#1a1a1a; border:1px solid #555; "
                            f"border-radius:10px; padding:2px 8px; font-size:0.72rem; "
                            f"color:#888; margin-right:4px;'>{c}</span>"
                            for c in companies[:3] if c.lower() not in {t.lower() for t in tickers}
                        )
                        + "</div>"
                        if tickers or companies else ""
                    )

                    + "</div>",
                    unsafe_allow_html=True,
                )

            # If we have tickers from this article, offer to score them
            if tickers:
                score_key = f"trump_news_score_{title[:30]}"
                if st.button(f"⚡ Score mentioned stocks: {', '.join(tickers[:4])}",
                             key=score_key, use_container_width=False):
                    with st.spinner("Scoring..."):
                        from fundamentals import fetch_fundamentals
                        from scorer import score_stock_detailed, signal_label
                        for tk in tickers[:4]:
                            try:
                                fund = fetch_fundamentals(tk)
                                sc, _, _ = score_stock_detailed(fund)
                                sig, col = signal_label(sc)
                                st.markdown(
                                    f"**{tk}** ({fund.get('company_name',tk)}) — "
                                    f"<span style='color:{col}; font-weight:700;'>{sc:.0f} pts / {sig}</span>"
                                    f" · Price: ${fund.get('current_price') or '—'}",
                                    unsafe_allow_html=True,
                                )
                            except Exception:
                                st.markdown(f"**{tk}** — could not score")

    # ── About section ──────────────────────────────────────────────────────────
    with st.expander("ℹ️ How this works / data sources", expanded=False):
        st.markdown("""
**Sources scanned every hour (all free):**
- 🏛️ **White House RSS** — official press releases, executive orders, signing ceremonies
- 📰 **Finnhub General News** — top 100 financial headlines (free tier)
- 📰 **NYT Business & Politics RSS** — broader coverage

**What triggers a match:**
- Article must mention Trump, Ivanka, Kushner, Eric/Lara/Donald Jr., or Trump-related entities
- Article must reference a recognisable company name (Apple, Boeing, etc.) or stock ticker

**Sentiment scoring:**
- ✅ Positive: deal signed, tariff relief, approval, endorsement, soaring, partnership
- ❌ Negative: tariff threat, sanction, ban, investigation, antitrust, forced sale

**Limitations:**
- Truth Social posts are not directly accessible (no free API)
- Covers news articles *about* Trump statements, not the raw posts themselves
- Updates hourly — for breaking news, click Refresh
        """)
