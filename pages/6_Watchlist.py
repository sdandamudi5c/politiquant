"""
PolitiQuant · Watchlist
Persistent list of specific stocks you want to track.
Scores them on demand in ~30 seconds — no full universe scan needed.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Raise fd limit early (same guard as Growth Report) ────────────────────────
try:
    import resource as _resource
    _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    _target = min(65536, _hard) if _hard > 0 else 65536
    if _soft < _target:
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (_target, _hard))
except Exception:
    pass

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from fundamentals import fetch_fundamentals, fmt_large
from scorer import score_stock_detailed, signal_label
from score_history import predict_return
from job_state import JobState
from sidebar_jobs import render as _render_sidebar

_JOB = JobState("watchlist")
_render_sidebar()

st.set_page_config(
    page_title="PolitiQuant · Watchlist",
    page_icon="⭐",
    layout="wide",
)

st.title("⭐ My Watchlist")
st.caption(
    "Stocks you specifically want to track. Score them in ~30 seconds anytime "
    "— no need to run a full universe scan. Results stay cached for the day."
)

# ── Watchlist persistence ──────────────────────────────────────────────────────
_WL_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "watchlist.json")

def _load_watchlist() -> list[str]:
    try:
        with open(_WL_FILE) as f:
            data = json.load(f)
            return sorted(set(t.upper().strip() for t in data if t.strip()))
    except Exception:
        return []

def _save_watchlist(tickers: list[str]) -> None:
    with open(_WL_FILE, "w") as f:
        json.dump(sorted(set(tickers)), f, indent=2)

if "watchlist" not in st.session_state:
    st.session_state.watchlist = _load_watchlist()

watchlist: list[str] = st.session_state.watchlist

# ── Add / Remove controls ──────────────────────────────────────────────────────
with st.expander("✏️ Manage Watchlist", expanded=not watchlist):
    add_col, remove_col = st.columns(2)

    with add_col:
        st.markdown("**Add tickers**")
        new_input = st.text_input(
            "Ticker(s) to add (comma-separated)",
            placeholder="e.g. NVDA, TSLA, AAPL",
            key="wl_add_input",
            label_visibility="collapsed",
        )
        if st.button("➕ Add to Watchlist", key="wl_add_btn", use_container_width=True):
            new_tickers = [t.strip().upper() for t in new_input.split(",") if t.strip()]
            if new_tickers:
                updated = sorted(set(watchlist + new_tickers))
                st.session_state.watchlist = updated
                _save_watchlist(updated)
                st.success(f"Added: {', '.join(new_tickers)}")
                st.rerun()
            else:
                st.warning("Enter at least one ticker symbol.")

    with remove_col:
        st.markdown("**Remove tickers**")
        if watchlist:
            to_remove = st.multiselect(
                "Select to remove",
                options=watchlist,
                key="wl_remove_select",
                label_visibility="collapsed",
            )
            if st.button("🗑️ Remove selected", key="wl_remove_btn",
                         use_container_width=True, disabled=not to_remove):
                updated = sorted(t for t in watchlist if t not in to_remove)
                st.session_state.watchlist = updated
                _save_watchlist(updated)
                st.success(f"Removed: {', '.join(to_remove)}")
                st.rerun()
        else:
            st.info("Watchlist is empty — add tickers on the left.")

if not watchlist:
    st.info("Your watchlist is empty. Add some tickers above to get started.")
    st.stop()

# Show current watchlist as chips
st.markdown(
    " ".join(
        f"<span style='background:#1a1a2e; border:1px solid #4a90d9; "
        f"border-radius:12px; padding:3px 10px; font-size:0.85rem; "
        f"font-weight:700; color:#4a90d9; margin-right:4px;'>{t}</span>"
        for t in watchlist
    ),
    unsafe_allow_html=True,
)
st.write("")

# ── Background scoring ─────────────────────────────────────────────────────────
def _run_watchlist_score(tickers: list[str], trades_df: pd.DataFrame):
    """Score watchlist tickers in a background thread."""
    try:
        import resource as _res
        _s, _h = _res.getrlimit(_res.RLIMIT_NOFILE)
        _t = min(65536, _h) if _h > 0 else 65536
        if _s < _t:
            _res.setrlimit(_res.RLIMIT_NOFILE, (_t, _h))
    except Exception:
        pass

    _JOB.start(total=len(tickers), meta={"tickers": tickers})
    rows   = []
    _lock  = threading.Lock()
    _done  = [0]
    cutoff_30d = pd.Timestamp(date.today() - timedelta(days=30))

    def _fetch_one(ticker):
        if _JOB.is_cancelling():
            with _lock:
                _done[0] += 1
                _JOB.update(done=_done[0], current=ticker)
            return None
        try:
            fund  = fetch_fundamentals(ticker)
            score, reasons, factor_pts = score_stock_detailed(fund, pol_buys_30d=_pol_buys(ticker, trades_df, cutoff_30d))
            signal, colour = signal_label(score)
            pred_raw = predict_return(factor_pts)
            pred     = pred_raw[0] if pred_raw else None   # extract predicted return %
            pred_ci  = pred_raw[1] if pred_raw else None
            pred_n   = pred_raw[2] if pred_raw else None
            pred_acc = pred_raw[3] if pred_raw else None

            cp     = fund.get("current_price")
            target = fund.get("analyst_target")
            try:
                upside = (target - cp) / cp * 100 if target and cp else None
            except Exception:
                upside = None

            row = {
                "ticker":          ticker,
                "company":         fund.get("company_name", ticker),
                "sector":          fund.get("sector") or "—",
                "score":           round(score, 1),
                "signal":          signal,
                "colour":          colour,
                "price":           cp,
                "analyst_target":  target,
                "upside_pct":      round(upside, 1) if upside is not None else None,
                "analyst_rating":  fund.get("analyst_rating") or "N/A",
                "fh_rec_total":    fund.get("fh_rec_total", 0),
                "fh_strong_buy":   fund.get("fh_strong_buy", 0),
                "fh_buy":          fund.get("fh_buy", 0),
                "fh_hold":         fund.get("fh_hold", 0),
                "fh_sell":         fund.get("fh_sell", 0),
                "fh_pe_ttm":       fund.get("fh_pe_ttm"),
                "fh_roe_ttm":      fund.get("fh_roe_ttm"),
                "fh_gross_margin": fund.get("fh_gross_margin"),
                "rsi":             fund.get("rsi"),
                "mom_1m_pct":      fund.get("mom_1m_pct"),
                "mom_3m_pct":      fund.get("mom_3m_pct"),
                "vs_50ma_pct":     fund.get("vs_50ma_pct"),
                "eps_yoy_pct":     fund.get("eps_yoy_pct"),
                "earnings_surprise_pct": fund.get("earnings_surprise_pct"),
                "news_sentiment_score":  fund.get("news_sentiment_score"),
                "recent_headlines":      fund.get("recent_headlines", []),
                "free_cash_flow":        fund.get("free_cash_flow"),
                "market_cap":            fund.get("market_cap"),
                "volume_ratio":          fund.get("volume_ratio"),
                "pct_from_52w_high":     fund.get("pct_from_52w_high"),
                "earnings_days_until":   fund.get("earnings_days_until"),
                "insider_buy_count":     fund.get("insider_buy_count", 0),
                "insider_sell_count":    fund.get("insider_sell_count", 0),
                "inst_tier1_buying":     fund.get("inst_tier1_buying", False),
                "inst_buyer_count":      fund.get("inst_buyer_count", 0),
                "analyst_upgrades_30d":  fund.get("analyst_upgrades_30d", 0),
                "analyst_downgrades_30d":fund.get("analyst_downgrades_30d", 0),
                "political_score_mod":   fund.get("political_score_mod", 0),
                "political_flag":        fund.get("political_flag"),
                "political_sentiment":   fund.get("political_sentiment", "neutral"),
                "political_headlines":   fund.get("political_headlines", []),
                "reasons":         reasons,
                "factor_pts":      factor_pts,
                "pred":            pred,
                "pred_ci":         pred_ci,
                "pred_n":          pred_n,
                "pred_acc":        pred_acc,
                "error":           fund.get("error"),
            }
            with _lock:
                rows.append(row)
                _done[0] += 1
                _JOB.update(done=_done[0], current=ticker)
            return row
        except Exception as e:
            with _lock:
                _done[0] += 1
                _JOB.update(done=_done[0], current=ticker)
            return None

    try:
        # Use up to 4 workers — watchlists are small so this is fast
        with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as ex:
            futs = {ex.submit(_fetch_one, t): t for t in tickers}
            for fut in as_completed(futs):
                fut.result()
        rows.sort(key=lambda r: r["score"], reverse=True)
    finally:
        _JOB.finish(result={"rows": rows, "scored_at": date.today().isoformat()})


def _pol_buys(ticker: str, trades_df: pd.DataFrame, cutoff: pd.Timestamp) -> int:
    if trades_df.empty:
        return 0
    try:
        mask = (
            (trades_df["ticker"] == ticker) &
            (trades_df["transaction_type"].str.contains("Purchase", case=False, na=False)) &
            (trades_df["transaction_date"] >= cutoff)
        )
        return int(mask.sum())
    except Exception:
        return 0


# ── Load trades for politician signal ─────────────────────────────────────────
try:
    from scraper import load_cache as _load_trades
    _trades_raw = _load_trades()
    _df_trades  = pd.DataFrame(_trades_raw) if _trades_raw else pd.DataFrame()
    if not _df_trades.empty:
        _df_trades["ticker"]           = _df_trades["ticker"].fillna("").str.upper().str.strip()
        _df_trades["transaction_type"] = _df_trades["transaction_type"].fillna("")
        _df_trades["transaction_date"] = pd.to_datetime(_df_trades["transaction_date"], errors="coerce")
except Exception:
    _df_trades = pd.DataFrame()

# ── Score / progress buttons ───────────────────────────────────────────────────
st.divider()
_has_results = bool(
    st.session_state.get("watchlist_results")
    or (_JOB.is_done() and _JOB.result())
)

btn1, btn2, btn3 = st.columns([2, 2, 1])

score_now = btn1.button(
    f"⚡ Score Watchlist  ({len(watchlist)} stocks)",
    type="primary",
    disabled=_JOB.is_running(),
    help="Fetches live data and scores every stock on your watchlist. Usually takes 20–60 seconds.",
)

if btn2.button(
    "🔄 Use Cached Scores",
    disabled=not _has_results,
    help="Load scores from the last run without re-fetching data.",
    key="wl_use_cache",
):
    if _JOB.is_done() and _JOB.result() and not st.session_state.get("watchlist_results"):
        st.session_state.watchlist_results = _JOB.result().get("rows", [])
    st.rerun()

if _has_results and btn3.button("🗑️ Clear", key="wl_clear", help="Clear cached scores"):
    st.session_state.pop("watchlist_results", None)
    _JOB.reset()
    st.rerun()

if score_now:
    st.session_state.pop("watchlist_results", None)
    _JOB.reset()
    t = threading.Thread(
        target=_run_watchlist_score,
        args=(watchlist, _df_trades.copy() if not _df_trades.empty else pd.DataFrame()),
        daemon=True,
    )
    t.start()
    st.rerun()

# ── Progress bar ───────────────────────────────────────────────────────────────
if _JOB.is_running():
    done, total, current = _JOB.progress()
    pct = min(0.99, done / total) if total > 0 else 0.5
    st.progress(pct, text=f"Scoring {current}… ({done}/{total})")
    import time; time.sleep(1.5); st.rerun()
    st.stop()

# ── Load results into session state ───────────────────────────────────────────
if _JOB.is_done() and _JOB.result() and not st.session_state.get("watchlist_results"):
    st.session_state.watchlist_results = _JOB.result().get("rows", [])
    scored_at = _JOB.result().get("scored_at", "")
    if scored_at:
        st.success(f"✅ Scored {len(st.session_state.watchlist_results)} stocks · {scored_at}")

if not st.session_state.get("watchlist_results"):
    st.info("Click **⚡ Score Watchlist** above to fetch live scores for your tracked stocks.")
    st.stop()

results: list[dict] = st.session_state.watchlist_results

# ── Summary bar ────────────────────────────────────────────────────────────────
st.divider()
_sb = sum(1 for r in results if r["signal"] == "STRONG BUY")
_b  = sum(1 for r in results if r["signal"] == "BUY")
_w  = sum(1 for r in results if r["signal"] == "WATCH")
_n  = sum(1 for r in results if r["signal"] in ("NEUTRAL", "AVOID"))

mc1, mc2, mc3, mc4, mc5 = st.columns(5)
mc1.metric("Tracked",      len(results))
mc2.metric("Strong Buy",   _sb)
mc3.metric("Buy",          _b)
mc4.metric("Watch",        _w)
mc5.metric("Neutral/Avoid",_n)

# ── Score chart ────────────────────────────────────────────────────────────────
if len(results) > 1:
    _chart_df = pd.DataFrame({
        "Stock": [r["ticker"] for r in results],
        "Score": [r["score"]  for r in results],
    }).set_index("Stock").sort_values("Score", ascending=True)
    st.bar_chart(_chart_df, color="#f39c12", height=max(200, len(results) * 32))

# ── Detailed cards ─────────────────────────────────────────────────────────────
st.subheader("Watchlist Scores")

_SIG_COLOURS = {
    "STRONG BUY": "#2ecc71",
    "BUY":        "#27ae60",
    "WATCH":      "#f39c12",
    "NEUTRAL":    "#888888",
    "AVOID":      "#e74c3c",
}

for r in results:
    sig_col  = _SIG_COLOURS.get(r["signal"], "#888")
    err      = r.get("error")
    price    = r.get("price")
    target   = r.get("analyst_target")
    upside   = r.get("upside_pct")
    pred     = r.get("pred")
    pred_ci  = r.get("pred_ci")
    pred_n   = r.get("pred_n")
    pred_acc = r.get("pred_acc")
    rsi      = r.get("rsi")
    m1       = r.get("mom_1m_pct")
    m3       = r.get("mom_3m_pct")
    ma50     = r.get("vs_50ma_pct")
    earn_d   = r.get("earnings_days_until")
    vol_r    = r.get("volume_ratio")
    fh_tot   = r.get("fh_rec_total", 0)
    fh_sb    = r.get("fh_strong_buy", 0)
    fh_b     = r.get("fh_buy", 0)
    fh_h     = r.get("fh_hold", 0)
    fh_s     = r.get("fh_sell", 0) + r.get("fh_strong_sell", 0) if r.get("fh_sell") is not None else 0
    gross_m  = r.get("fh_gross_margin")
    roe      = r.get("fh_roe_ttm")
    pe       = r.get("fh_pe_ttm")

    def _pf(v, fmt="%.1f%%", fallback="—"):
        try: return fmt % v if v is not None else fallback
        except: return fallback

    with st.container():
        # Header row
        hc1, hc2, hc3, hc4 = st.columns([2, 1, 1, 3])
        hc1.markdown(
            f"<span style='font-size:1.2rem; font-weight:900;'>⭐ {r['ticker']}</span>"
            f"&nbsp;&nbsp;<span style='color:#888; font-size:0.85rem;'>{r['company'][:35]}</span>"
            f"<br><span style='color:#666; font-size:0.75rem;'>{r['sector']}</span>",
            unsafe_allow_html=True,
        )
        hc2.markdown(
            f"<div style='text-align:center;'>"
            f"<div style='font-size:1.6rem; font-weight:900; color:{sig_col};'>{r['score']:.0f}</div>"
            f"<div style='font-size:0.7rem; color:{sig_col}; font-weight:700;'>{r['signal']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        hc3.markdown(
            f"<div style='text-align:center;'>"
            f"<div style='font-size:1.1rem; font-weight:700;'>"
            f"{'${:.2f}'.format(price) if price else '—'}</div>"
            + (f"<div style='font-size:0.72rem; color:#888;'>Target: ${target:.2f}</div>" if target else "")
            + (f"<div style='font-size:0.72rem; color:#2ecc71; font-weight:700;'>+{upside:.1f}% upside</div>" if upside and upside > 0 else
               f"<div style='font-size:0.72rem; color:#e74c3c;'>{upside:.1f}% downside</div>" if upside and upside < 0 else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        # ML prediction badge
        if pred is not None:
            pred_col  = "#2ecc71" if pred > 2 else "#e74c3c" if pred < -2 else "#888"
            conf_str  = "High conf" if (pred_n or 0) >= 500 else "Med conf" if (pred_n or 0) >= 100 else "Early"
            ci_str    = f" ± {pred_ci:.1f}%" if pred_ci is not None else ""
            acc_str   = f" · {pred_acc:.0f}% dir. acc" if pred_acc is not None else ""
            hc4.markdown(
                f"<div style='background:#1a1a2e; border-radius:6px; padding:8px 12px;'>"
                f"<div style='color:#888; font-size:0.72rem;'>🧠 ML 30d forecast</div>"
                f"<div style='color:{pred_col}; font-weight:700; font-size:1.1rem;'>"
                f"{pred:+.1f}%<span style='font-size:0.78rem; color:#aaa;'>{ci_str}</span></div>"
                f"<div style='color:#666; font-size:0.68rem;'>{conf_str}{acc_str}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Metrics row
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("RSI",        f"{rsi:.1f}" if rsi else "—",
                    delta="oversold" if rsi and rsi < 35 else "overbought" if rsi and rsi > 70 else None,
                    delta_color="normal" if rsi and rsi < 35 else "inverse")
        col2.metric("1M Return",  _pf(m1))
        col3.metric("3M Return",  _pf(m3))
        col4.metric("vs 50-MA",   _pf(ma50))
        col5.metric("Gross Margin", _pf(gross_m) if gross_m else "—")
        col6.metric("ROE",         _pf(roe) if roe else "—")

        # Finnhub analyst consensus bar
        if fh_tot > 0:
            _sb_pct = fh_sb / fh_tot * 100
            _b_pct  = fh_b  / fh_tot * 100
            _h_pct  = fh_h  / fh_tot * 100
            _s_pct  = fh_s  / fh_tot * 100
            st.markdown(
                f"<div style='margin:6px 0 2px 0;'>"
                f"<span style='font-size:0.72rem; color:#888;'>Analyst consensus ({fh_tot} analysts):</span>"
                f"</div>"
                f"<div style='display:flex; height:8px; border-radius:4px; overflow:hidden; margin-bottom:4px;'>"
                f"<div style='width:{_sb_pct:.0f}%; background:#27ae60;' title='Strong Buy {fh_sb}'></div>"
                f"<div style='width:{_b_pct:.0f}%;  background:#2ecc71;' title='Buy {fh_b}'></div>"
                f"<div style='width:{_h_pct:.0f}%;  background:#888;'    title='Hold {fh_h}'></div>"
                f"<div style='width:{_s_pct:.0f}%;  background:#e74c3c;' title='Sell/Strong Sell {fh_s}'></div>"
                f"</div>"
                f"<div style='font-size:0.7rem; color:#888;'>"
                f"<span style='color:#27ae60;'>▮ SB {fh_sb}</span>&nbsp;"
                f"<span style='color:#2ecc71;'>▮ Buy {fh_b}</span>&nbsp;"
                f"<span style='color:#888;'>▮ Hold {fh_h}</span>&nbsp;"
                f"<span style='color:#e74c3c;'>▮ Sell {fh_s}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Political / presidential flag (shown prominently above other flags)
        _pol_mod  = r.get("political_score_mod", 0)
        _pol_flag = r.get("political_flag")
        _pol_heads = r.get("political_headlines", [])
        if _pol_flag and _pol_mod != 0:
            _pol_col = "#2ecc71" if _pol_mod > 0 else "#e74c3c"
            _pol_bg  = "#0a2a0a" if _pol_mod > 0 else "#2a0a0a"
            st.markdown(
                f"<div style='background:{_pol_bg}; border:1px solid {_pol_col}55; "
                f"border-radius:6px; padding:8px 12px; margin:6px 0;'>"
                f"<span style='color:{_pol_col}; font-weight:700; font-size:0.85rem;'>"
                f"{_pol_flag}</span>"
                + (f"<br><span style='color:#aaa; font-size:0.75rem; font-style:italic;'>"
                   f"\"{_pol_heads[0][:100]}...\"</span>" if _pol_heads else "")
                + "</div>",
                unsafe_allow_html=True,
            )
            if len(_pol_heads) > 1:
                with st.expander("More political headlines", expanded=False):
                    for h in _pol_heads[1:]:
                        st.markdown(f"<span style='font-size:0.8rem; color:#aaa;'>• {h}</span>",
                                    unsafe_allow_html=True)

        # Flags row
        flags = []
        if earn_d is not None:
            earn_col = "#e74c3c" if earn_d <= 2 else "#f39c12" if earn_d <= 7 else "#888"
            flags.append(f"<span style='color:{earn_col};'>📅 Earnings in {earn_d}d</span>")
        if vol_r and vol_r >= 1.5:
            flags.append(f"<span style='color:#f39c12;'>📊 Volume {vol_r:.1f}× avg</span>")
        if r.get("inst_tier1_buying"):
            flags.append("<span style='color:#9b59b6;'>⭐ Tier-1 inst. buying</span>")
        if r.get("insider_buy_count", 0) > 0:
            flags.append(f"<span style='color:#2ecc71;'>👤 {r['insider_buy_count']} insider buy(s)</span>")
        if r.get("analyst_upgrades_30d", 0) > 0:
            flags.append(f"<span style='color:#2ecc71;'>⬆️ {r['analyst_upgrades_30d']} upgrade(s)</span>")
        if r.get("analyst_downgrades_30d", 0) > 0:
            flags.append(f"<span style='color:#e74c3c;'>⬇️ {r['analyst_downgrades_30d']} downgrade(s)</span>")
        if flags:
            st.markdown(
                "<div style='margin:4px 0; font-size:0.8rem;'>"
                + "&nbsp;&nbsp;·&nbsp;&nbsp;".join(flags)
                + "</div>",
                unsafe_allow_html=True,
            )

        # Score reasons expander
        reasons = r.get("reasons", [])
        if reasons:
            with st.expander(f"Why {r['ticker']} scored {r['score']:.0f} pts", expanded=False):
                pos = [x for x in reasons if x.startswith("✅")]
                neg = [x for x in reasons if x.startswith("❌")]
                nc1, nc2 = st.columns(2)
                with nc1:
                    for p in pos:
                        st.markdown(f"<span style='font-size:0.82rem;'>{p}</span>", unsafe_allow_html=True)
                with nc2:
                    for n in neg:
                        st.markdown(f"<span style='font-size:0.82rem;'>{n}</span>", unsafe_allow_html=True)

        if err:
            st.warning(f"⚠️ Data incomplete for {r['ticker']}: {err}")

        st.divider()

# ── Download ───────────────────────────────────────────────────────────────────
if results:
    _dl_data = []
    for r in results:
        _dl_data.append({
            "Ticker":         r["ticker"],
            "Company":        r["company"],
            "Score":          r["score"],
            "Signal":         r["signal"],
            "Price":          r["price"],
            "Analyst Target": r["analyst_target"],
            "Upside %":       r["upside_pct"],
            "RSI":            r["rsi"],
            "1M %":           r["mom_1m_pct"],
            "3M %":           r["mom_3m_pct"],
            "Analysts":       r["fh_rec_total"],
            "Strong Buy":     r["fh_strong_buy"],
            "Buy":            r["fh_buy"],
            "Hold":           r["fh_hold"],
            "Gross Margin":   r["fh_gross_margin"],
            "ROE":            r["fh_roe_ttm"],
            "PE (TTM)":       r["fh_pe_ttm"],
            "ML 30d Forecast":r["pred"],
        })
    _csv = pd.DataFrame(_dl_data).to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download Watchlist as CSV",
        data=_csv,
        file_name=f"watchlist_{date.today()}.csv",
        mime="text/csv",
    )
