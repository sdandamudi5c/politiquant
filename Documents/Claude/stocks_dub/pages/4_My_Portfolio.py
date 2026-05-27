import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import threading
from datetime import datetime
import pandas as pd
import streamlit as st

from fundamentals import fetch_fundamentals, fmt_large, fmt_pct, fmt_ratio
from scorer import score_stock_detailed, signal_label
from score_history import predict_return
from job_state import JobState
from sidebar_jobs import render as _render_sidebar

_JOB = JobState("portfolio")
_render_sidebar()

st.set_page_config(page_title="PolitiQuant · Portfolio", page_icon="💼", layout="wide")

st.title("💼 My Portfolio")
st.caption("Analyses your holdings using live fundamentals and the growth scoring model. Not financial advice.")

# ── Default holdings from Robinhood statement April 30, 2026 ─────────────────
_DEFAULT = [
    {"Ticker": "AMZN",  "Qty": 9.310109,   "Stmt Price": 265.06,  "Stmt Value": 2467.74},
    {"Ticker": "BRK-B", "Qty": 1.218961,   "Stmt Price": 473.60,  "Stmt Value": 577.30},
    {"Ticker": "CMBT",  "Qty": 30.739459,  "Stmt Price": 13.77,   "Stmt Value": 423.28},
    {"Ticker": "GOOGL", "Qty": 10.389884,  "Stmt Price": 384.80,  "Stmt Value": 3998.03},
    {"Ticker": "HIMS",  "Qty": 20.903167,  "Stmt Price": 27.17,   "Stmt Value": 567.94},
    {"Ticker": "LBRA",  "Qty": 1.0,        "Stmt Price": 0.0099,  "Stmt Value": 0.01},
    {"Ticker": "MSFT",  "Qty": 0.790537,   "Stmt Price": 407.78,  "Stmt Value": 322.37},
    {"Ticker": "NBIS",  "Qty": 12.724108,  "Stmt Price": 173.47,  "Stmt Value": 2207.22},
    {"Ticker": "NVDA",  "Qty": 13.099176,  "Stmt Price": 199.57,  "Stmt Value": 2614.20},
    {"Ticker": "SMCI",  "Qty": 6.3229,     "Stmt Price": 27.40,   "Stmt Value": 173.25},
    {"Ticker": "SOFI",  "Qty": 260.18027,  "Stmt Price": 16.10,   "Stmt Value": 4188.90},
    {"Ticker": "SUUN",  "Qty": 248.598689, "Stmt Price": 0.561,   "Stmt Value": 139.46},
    {"Ticker": "TSLA",  "Qty": 3.25938,    "Stmt Price": 381.63,  "Stmt Value": 1243.88},
    {"Ticker": "VOO",   "Qty": 5.496172,   "Stmt Price": 660.58,  "Stmt Value": 3630.66},
    {"Ticker": "VOOG",  "Qty": 17.680848,  "Stmt Price": 78.03,   "Stmt Value": 1379.64},
    {"Ticker": "VYNE",  "Qty": 22.333333,  "Stmt Price": 0.6367,  "Stmt Value": 14.22},
    {"Ticker": "GREE",  "Qty": 1.0,        "Stmt Price": 1.16,    "Stmt Value": 1.16},
]
_STMT_TOTAL = 22624.85   # Apr 30 base + $250 UNCOMMONX cash credit (May 15)
_STMT_DATE  = "May 15, 2026"

# ETFs — scored but flagged separately
_ETFS = {"VOO", "VOOG", "SPY", "QQQ", "IWM", "VTI", "ARKK"}

# ── Portfolio persistence ─────────────────────────────────────────────────────
_PORT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portfolio.json")

def _load_portfolio() -> list:
    try:
        with open(_PORT_FILE) as f:
            return json.load(f)
    except Exception:
        return _DEFAULT

def _save_portfolio(rows: list):
    import json as _json
    with open(_PORT_FILE, "w") as f:
        _json.dump(rows, f, indent=2)

import json

# Load saved portfolio (falls back to _DEFAULT on first run)
if "portfolio_rows" not in st.session_state:
    st.session_state.portfolio_rows = _load_portfolio()

# ── Quick Buy / Sell form ─────────────────────────────────────────────────────
with st.expander("➕ Record a Trade", expanded=False):
    tc1, tc2, tc3, tc4, tc5 = st.columns([1, 1, 1, 1, 1])
    t_action = tc1.selectbox("Action", ["Buy", "Sell"], key="trade_action")
    t_ticker = tc2.text_input("Ticker", placeholder="e.g. NVDA", key="trade_ticker").upper().strip()
    t_qty    = tc3.number_input("Quantity", min_value=0.0, step=0.01, format="%.6f", key="trade_qty")
    t_price  = tc4.number_input("Price per share ($)", min_value=0.0, step=0.01, format="%.4f", key="trade_price")
    tc5.write("")
    tc5.write("")
    record_trade = tc5.button("✅ Record", key="record_trade_btn", use_container_width=True)

    if record_trade:
        if not t_ticker:
            st.error("Enter a ticker symbol.")
        elif t_qty <= 0:
            st.error("Quantity must be greater than 0.")
        elif t_price <= 0:
            st.error("Price must be greater than 0.")
        else:
            rows = st.session_state.portfolio_rows
            existing = next((r for r in rows if r["Ticker"].upper() == t_ticker), None)

            if t_action == "Buy":
                if existing:
                    # Average down/up the cost basis
                    old_qty   = existing["Qty"]
                    old_val   = existing["Stmt Value"]
                    new_qty   = old_qty + t_qty
                    new_val   = old_val + (t_qty * t_price)
                    existing["Qty"]        = round(new_qty, 6)
                    existing["Stmt Price"] = round(new_val / new_qty, 4)
                    existing["Stmt Value"] = round(new_val, 2)
                    st.success(f"✅ Added {t_qty} shares of **{t_ticker}** @ ${t_price:.2f} — new avg cost ${existing['Stmt Price']:.4f}, total {existing['Qty']:.4f} shares.")
                else:
                    rows.append({
                        "Ticker":     t_ticker,
                        "Qty":        round(t_qty, 6),
                        "Stmt Price": round(t_price, 4),
                        "Stmt Value": round(t_qty * t_price, 2),
                    })
                    st.success(f"✅ Added **{t_ticker}** — {t_qty} shares @ ${t_price:.2f}")

            elif t_action == "Sell":
                if not existing:
                    st.error(f"{t_ticker} not found in your portfolio.")
                elif t_qty > existing["Qty"]:
                    st.error(f"You only hold {existing['Qty']:.6f} shares of {t_ticker}.")
                else:
                    existing["Qty"] = round(existing["Qty"] - t_qty, 6)
                    existing["Stmt Value"] = round(existing["Qty"] * existing["Stmt Price"], 2)
                    if existing["Qty"] <= 0:
                        rows = [r for r in rows if r["Ticker"].upper() != t_ticker]
                        st.success(f"✅ Sold all shares of **{t_ticker}** — removed from portfolio.")
                    else:
                        st.success(f"✅ Sold {t_qty} shares of **{t_ticker}** — {existing['Qty']:.6f} shares remaining.")

            st.session_state.portfolio_rows = rows
            _save_portfolio(rows)
            st.rerun()

# ── Holdings editor ───────────────────────────────────────────────────────────
with st.expander("📋 Holdings — edit to match your current portfolio", expanded=True):
    st.caption("Changes are saved automatically. Use the ➕ Record a Trade form above for quick buys/sells.")
    edited = st.data_editor(
        pd.DataFrame(st.session_state.portfolio_rows),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Ticker":     st.column_config.TextColumn("Ticker",      width="small"),
            "Qty":        st.column_config.NumberColumn("Qty",       format="%.6f", width="small"),
            "Stmt Price": st.column_config.NumberColumn("Stmt Price ($)", format="$%.4f", width="small"),
            "Stmt Value": st.column_config.NumberColumn("Stmt Value ($)", format="$%.2f", width="small"),
        },
        key="portfolio_editor",
    )

    sv1, sv2 = st.columns([1, 4])
    if sv1.button("💾 Save changes", key="save_portfolio_btn"):
        rows = edited.to_dict("records")
        # Clean up — remove rows with no ticker
        rows = [r for r in rows if str(r.get("Ticker", "")).strip()]
        st.session_state.portfolio_rows = rows
        _save_portfolio(rows)
        st.success(f"✅ Portfolio saved — {len(rows)} holdings.")
        st.rerun()
    sv2.caption("Or edit directly in the table above and click Save changes.")

analyse = st.button("Analyse Portfolio", type="primary", use_container_width=False)

# ── Background fetch function ─────────────────────────────────────────────────
def _run_portfolio(holdings_rows):
    for i, row in enumerate(holdings_rows):
        ticker = row["Ticker"]
        _JOB.update(done=i, current=ticker)
        try:
            fund  = fetch_fundamentals(ticker)
            score, reasons, fp = score_stock_detailed(fund, pol_buys_30d=0)
            label, colour      = signal_label(score)
            pred               = predict_return(fp)
            cp         = fund.get("current_price")
            stmt_price = row.get("Stmt Price") or 0
            qty        = row.get("Qty") or 0
            stmt_value = row.get("Stmt Value") or (qty * stmt_price)
            curr_value = qty * cp if cp else None
            chg_pct    = ((cp - stmt_price) / stmt_price * 100) if cp and stmt_price > 0 else None
            _JOB.result()  # touch — keep state fresh
            holdings_rows[i]["_result"] = {
                "ticker": ticker, "company": fund.get("company_name", ticker),
                "sector": fund.get("sector") or "—", "qty": qty,
                "stmt_price": stmt_price, "stmt_value": stmt_value,
                "current_price": cp, "current_value": curr_value,
                "price_chg_pct": chg_pct, "score": score, "label": label,
                "colour": colour, "reasons": reasons, "fp": fp, "pred": pred,
                "is_etf": ticker in _ETFS,
                "is_penny": bool(cp and cp < 5),
                "error": fund.get("error"), "fund": fund,
            }
        except Exception as e:
            holdings_rows[i]["_result"] = {"ticker": ticker, "error": str(e),
                "score": 0, "label": "AVOID", "colour": "#e74c3c",
                "company": ticker, "sector": "—", "qty": 0,
                "stmt_price": 0, "stmt_value": 0, "current_price": None,
                "current_value": None, "price_chg_pct": None,
                "reasons": [], "fp": {}, "pred": None,
                "is_etf": False, "is_penny": False, "fund": {}}
    results = [r["_result"] for r in holdings_rows if "_result" in r]
    _JOB.finish(result=results)

if analyse:
    holdings = edited.dropna(subset=["Ticker"]).copy()
    holdings["Ticker"] = holdings["Ticker"].str.upper().str.strip()
    holdings = holdings[holdings["Ticker"] != ""]
    rows = holdings.to_dict("records")
    # Keep session state in sync with what's being analysed
    st.session_state.portfolio_rows = rows
    _save_portfolio(rows)
    _JOB.reset()
    _JOB.start(total=len(rows))
    # Pass a deep copy so the thread's _result mutations don't pollute portfolio_rows
    import copy as _copy
    threading.Thread(target=_run_portfolio, args=(_copy.deepcopy(rows),), daemon=True).start()
    st.rerun()

# ── Progress while running ────────────────────────────────────────────────────
if _JOB.is_running():
    done, total, current = _JOB.progress()
    st.progress(done / max(total, 1), text=f"Fetching {current}… ({done}/{total})")
    st.caption(f"⏱ Started {_JOB.started_at()} · Switch tabs freely — analysis continues in the background.")
    import time; time.sleep(1.5); st.rerun()
    st.stop()

if not _JOB.is_done() and "portfolio_results" not in st.session_state:
    st.info("Click **Analyse Portfolio** to fetch live data and score each holding.")
    st.stop()

if _JOB.is_done():
    result = _JOB.result()
    if result:
        st.session_state.portfolio_results = result
        st.success(f"✅ Analysis complete ({len(result)} holdings) — finished {_JOB.completed_at()}")

results = st.session_state.portfolio_results

# ── Portfolio summary ─────────────────────────────────────────────────────────
st.divider()
total_current = sum(r["current_value"] for r in results if r["current_value"])
total_stmt    = sum(r["stmt_value"]    for r in results if r["stmt_value"])
total_chg     = total_current - total_stmt if total_current and total_stmt else None
total_chg_pct = (total_chg / total_stmt * 100) if total_chg and total_stmt else None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Holdings", len(results))
c2.metric("Statement Value", f"${total_stmt:,.2f}", f"(as of {_STMT_DATE})")
c3.metric(
    "Current Value",
    f"${total_current:,.2f}" if total_current else "N/A",
    delta=f"{total_chg:+.2f} ({total_chg_pct:+.1f}% since stmt)" if total_chg else None,
    delta_color="normal",
)
scores = [r["score"] for r in results if not r["error"]]
c4.metric("Avg Growth Score", f"{sum(scores)/len(scores):.0f}/100" if scores else "N/A")

# ── Categorise ────────────────────────────────────────────────────────────────
good    = [r for r in results if r["score"] >= 58  and not r["is_etf"]]
ok      = [r for r in results if 40 <= r["score"] < 58 and not r["is_etf"]]
bad     = [r for r in results if 25 <= r["score"] < 40 and not r["is_etf"]]
sell    = [r for r in results if r["score"] < 25 and not r["is_etf"]]
etfs    = [r for r in results if r["is_etf"]]

# Sort each tier by score descending
for grp in (good, ok, bad, sell, etfs):
    grp.sort(key=lambda r: r["score"], reverse=True)


def _holding_card(r: dict, expanded: bool = True):
    ticker  = r["ticker"]
    colour  = r["colour"]
    score   = r["score"]
    chg     = r["price_chg_pct"]
    val     = r["current_value"]
    stmt_v  = r["stmt_value"]
    label   = r["label"]

    chg_str   = f"{chg:+.1f}% since stmt" if chg is not None else ""
    val_str   = f"${val:,.2f}" if val else "N/A"
    pct_total = (val / total_current * 100) if val and total_current else 0

    with st.expander(
        f"{ticker}  —  {r['company']}  |  Score: {score:.0f}/100  |  {label}  |  {val_str}  ({pct_total:.1f}%)",
        expanded=expanded,
    ):
        if r.get("error") and not r["current_price"]:
            st.error(f"Could not fetch data: {r['error']}")
            return

        col1, col2, col3, col4 = st.columns(4)
        col1.metric(
            "Current Price",
            f"${r['current_price']:.2f}" if r["current_price"] else "N/A",
            delta=chg_str if chg_str else None,
            delta_color="normal",
        )
        col2.metric("Holding Value", val_str)
        col2.caption(f"Stmt: ${stmt_v:,.2f}")
        col3.metric("Qty", f"{r['qty']:.4f}")
        col4.metric("Sector", r["sector"] or "N/A")

        # Score bar
        bar_pct = int(score)
        st.markdown(
            f"<div style='display:flex; align-items:center; gap:12px; margin:8px 0;'>"
            f"<div style='flex:1; background:#222; border-radius:4px; height:10px; overflow:hidden;'>"
            f"<div style='width:{bar_pct}%; height:100%; background:{colour};'></div></div>"
            f"<span style='color:{colour}; font-weight:bold; white-space:nowrap;'>"
            f"{score:.0f}/100 — {label}</span></div>",
            unsafe_allow_html=True,
        )

        # ML prediction
        pred = r.get("pred")
        if pred:
            p_ret, p_ci, n_train, dir_acc = pred
            p_col = "#2ecc71" if p_ret > 0 else "#e74c3c"
            st.markdown(
                f"<div style='background:#1a2a1a; border:1px solid {p_col}44; border-radius:6px; "
                f"padding:8px 12px; margin:4px 0; font-size:0.85rem;'>"
                f"🧠 ML predicted 30-day return: "
                f"<b style='color:{p_col};'>{p_ret:+.1f}% ± {p_ci:.1f}%</b>"
                f" &nbsp;·&nbsp; based on {n_train} outcomes · {dir_acc:.1f}% direction accuracy</div>",
                unsafe_allow_html=True,
            )

        # Top reasons
        pos_r = [x for x in r["reasons"] if not any(x.startswith(p) for p in ("⚠️","🔴","📉"))]
        neg_r = [x for x in r["reasons"] if any(x.startswith(p) for p in ("⚠️","🔴","📉"))]
        rc1, rc2 = st.columns(2)
        with rc1:
            for reason in pos_r[:4]:
                st.caption(reason)
        with rc2:
            for reason in neg_r[:4]:
                st.caption(reason)

        headlines = (r.get("fund") or {}).get("recent_headlines", [])
        if headlines:
            with st.expander("📰 Recent news", expanded=False):
                for h in headlines:
                    st.caption(f"• {h}")

        if r["is_penny"]:
            st.warning(f"Penny stock (< $5) — high risk, low liquidity.")


def _section(title: str, colour: str, subtitle: str, group: list):
    if not group:
        return
    val = sum(r["current_value"] for r in group if r["current_value"])
    st.divider()
    st.markdown(
        f"## {title} &nbsp; <span style='color:{colour}; font-size:1rem;'>"
        f"{len(group)} stock(s) · ${val:,.0f}</span>",
        unsafe_allow_html=True,
    )
    st.caption(subtitle)
    for r in group:
        _holding_card(r, expanded=False)


_section(
    "🟢 HOLD / ADD MORE", "#2ecc71",
    "Score ≥ 58 — strong fundamentals and/or momentum. Click a card to expand.",
    good,
)
_section(
    "🟡 MONITOR", "#f39c12",
    "Score 40–57 — mixed signals. Keep holding but watch for deterioration.",
    ok,
)
_section(
    "🟠 CONSIDER TRIMMING", "#e67e22",
    "Score 25–39 — weak fundamentals or fading momentum. Consider reducing.",
    bad,
)
_section(
    "🔴 CONSIDER SELLING", "#e74c3c",
    "Score < 25 — poor fundamentals or negligible value.",
    sell,
)

if etfs:
    val = sum(r["current_value"] for r in etfs if r["current_value"])
    st.divider()
    st.markdown(
        f"## 📦 ETFs &nbsp; <span style='color:#4a90d9; font-size:1rem;'>"
        f"{len(etfs)} ETF(s) · ${val:,.0f}</span>",
        unsafe_allow_html=True,
    )
    st.caption("ETFs score low because P/E, revenue, and EPS metrics don't apply. VOO and VOOG are safe long-term holds regardless of score.")
    for r in etfs:
        _holding_card(r, expanded=False)

# ── Summary table ─────────────────────────────────────────────────────────────
st.divider()

# ── Internal section tabs ────────────────────────────────────────────────────
_tab_sum, _tab_sig, _tab_pol, _tab_mkt = st.tabs([
    "📊 Summary", "📡 Smart Money", "🏛️ Politicians", "🌍 Market & Alerts"
])

with _tab_sum:
    st.subheader("Full Summary")

    rows = []
    for r in sorted(results, key=lambda x: x["score"], reverse=True):
        val  = r["current_value"]
        pct  = (val / total_current * 100) if val and total_current else 0
        chg  = r["price_chg_pct"]
        rows.append({
            "Ticker":      r["ticker"],
            "Company":     r["company"][:28],
            "Score":       f"{r['score']:.0f}",
            "Signal":      r["label"],
            "Holding $":   f"${val:,.2f}"  if val else "N/A",
            "% Portfolio": f"{pct:.1f}%",
            "Since Stmt":  f"{chg:+.1f}%" if chg is not None else "N/A",
            "Category":    "ETF" if r["is_etf"] else (
                            "HOLD/ADD" if r["score"] >= 58 else
                            "MONITOR"  if r["score"] >= 40 else
                            "TRIM"     if r["score"] >= 25 else "SELL"),
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

with _tab_sig:

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Insider Buying on Your Holdings
    # ══════════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🏦 Insider Buying on Your Holdings")
    st.caption("CEOs, CFOs, and Directors buying their OWN stock open-market in the last 90 days — one of the strongest buy signals.")

    try:
        from insider_trades import fetch_insider_trades as _fetch_ins_p

        _ins_found = []
        for _r in results:
            _tk = _r["ticker"]
            # Use cached data if already in fund result, else fetch
            _ins_buys = _r.get("insider_buys") or []
            _ins_score = _r.get("insider_buy_score") or 0

            # If not in fund result, fetch directly
            if not _ins_buys and _ins_score == 0:
                _ins_data = _fetch_ins_p(_tk)
                _ins_buys  = _ins_data.get("buys", [])
                _ins_score = _ins_data.get("buy_score", 0)
                _ceo = _ins_data.get("ceo_bought", False)
                _cfo = _ins_data.get("cfo_bought", False)
            else:
                _ceo = _r.get("insider_ceo_bought", False)
                _cfo = _r.get("insider_cfo_bought", False)

            if _ins_buys:
                _ins_found.append({
                    "ticker": _tk,
                    "company": _r.get("company", _tk),
                    "buys": _ins_buys,
                    "score": _ins_score,
                    "ceo": _ceo,
                    "cfo": _cfo,
                })

        if not _ins_found:
            st.info("✅ No insider open-market purchases detected on your holdings in the last 90 days.")
        else:
            # Sort by score descending
            _ins_found.sort(key=lambda x: x["score"], reverse=True)
            for _ih in _ins_found:
                _ibadge = ""
                if _ih["ceo"] and _ih["cfo"]:
                    _ibadge = "🔥 CEO + CFO buying"
                    _iborder = "#f39c12"
                elif _ih["ceo"]:
                    _ibadge = "🔥 CEO buying"
                    _iborder = "#2ecc71"
                elif _ih["cfo"]:
                    _ibadge = "💼 CFO buying"
                    _iborder = "#2ecc71"
                else:
                    _ibadge = f"👔 {len(_ih['buys'])} insider(s) buying"
                    _iborder = "#4a90d9"

                st.markdown(
                    f"<div style='background:#1a2a1a; border-left:4px solid {_iborder}; "
                    f"border-radius:6px; padding:10px 16px; margin-bottom:6px;'>"
                    f"<span style='font-size:1.05rem; font-weight:800; color:#fff;'>{_ih['ticker']}</span>"
                    f"&nbsp;&nbsp;<span style='color:#aaa; font-size:0.82rem;'>{_ih['company'][:40]}</span>"
                    f"&nbsp;&nbsp;<span style='color:{_iborder}; font-size:0.78rem; font-weight:700;'>{_ibadge}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                for _ib in _ih["buys"][:4]:
                    _ival = f"${_ib['value']:,.0f}" if _ib.get("value") else "N/A"
                    st.markdown(
                        f"&nbsp;&nbsp;&nbsp;&nbsp;"
                        f"<span style='color:#2ecc71; font-size:0.82rem;'>📈 <b>{_ib['name']}</b>"
                        f" ({_ib['position']})</span>"
                        f"<span style='color:#888; font-size:0.78rem;'>"
                        f" &nbsp;·&nbsp; {_ib['shares']:,} shares"
                        f" &nbsp;·&nbsp; {_ival}"
                        f" &nbsp;·&nbsp; {_ib['date']}</span>",
                        unsafe_allow_html=True,
                    )

    except Exception as _e_ins:
        st.warning(f"Could not load insider data: {_e_ins}")

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Institutional Activity on Your Holdings
    # ══════════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🏢 Institutional Activity on Your Holdings")
    st.caption("Hedge funds, BlackRock, Vanguard, and other institutions increasing positions (13F filings). Updated quarterly.")

    try:
        from institutional_trades import fetch_institutional_data as _fetch_inst_p

        _inst_found = []
        for _r in results:
            _tk = _r["ticker"]
            # Use cached fund data if already present, else fetch directly
            _inst_score   = _r.get("inst_score") or 0
            _inst_buyers  = _r.get("inst_buyers") or []
            _inst_t1      = _r.get("inst_tier1_buying", False)
            _inst_t1names = _r.get("inst_tier1_buyers") or []
            _inst_pct     = _r.get("inst_pct_held") or 0.0
            _inst_new     = _r.get("inst_new_positions") or []

            if not _inst_buyers and _inst_score == 0:
                _idata       = _fetch_inst_p(_tk)
                _inst_score  = _idata.get("inst_score", 0)
                _inst_buyers = _idata.get("buyers", [])
                _inst_t1     = _idata.get("tier1_buying", False)
                _inst_t1names= _idata.get("tier1_buyers", [])
                _inst_pct    = _idata.get("inst_pct_held", 0.0)
                _inst_new    = _idata.get("new_positions", [])

            if _inst_buyers or _inst_score > 0:
                _inst_found.append({
                    "ticker":    _tk,
                    "company":   _r.get("company", _tk),
                    "score":     _inst_score,
                    "buyers":    _inst_buyers[:5],
                    "tier1":     _inst_t1,
                    "t1names":   _inst_t1names,
                    "pct_held":  _inst_pct,
                    "new_pos":   _inst_new[:3],
                })

        if not _inst_found:
            st.info("No significant institutional buying detected on your holdings this quarter.")
        else:
            _inst_found.sort(key=lambda x: x["score"], reverse=True)
            for _ih2 in _inst_found:
                _t1n = _ih2["t1names"]
                if _ih2["tier1"] and len(_t1n) >= 2:
                    _ibadge2  = f"⭐ {_t1n[0].split()[0]} + {len(_t1n)-1} more tier-1 institutions buying"
                    _iborder2 = "#9b59b6"
                elif _ih2["tier1"] and _t1n:
                    _ibadge2  = f"⭐ {_t1n[0]} adding to position"
                    _iborder2 = "#9b59b6"
                elif len(_ih2["buyers"]) >= 3:
                    _ibadge2  = f"🏢 {len(_ih2['buyers'])} institutions adding"
                    _iborder2 = "#8e44ad"
                elif _ih2["buyers"]:
                    _ibadge2  = f"🏢 {len(_ih2['buyers'])} institution adding"
                    _iborder2 = "#6c3483"
                else:
                    continue

                st.markdown(
                    f"<div style='background:#1a1a2e; border-left:4px solid {_iborder2}; "
                    f"border-radius:6px; padding:10px 16px; margin-bottom:6px;'>"
                    f"<span style='font-size:1.05rem; font-weight:800; color:#fff;'>{_ih2['ticker']}</span>"
                    f"&nbsp;&nbsp;<span style='color:#aaa; font-size:0.82rem;'>{_ih2['company'][:40]}</span>"
                    f"&nbsp;&nbsp;<span style='color:{_iborder2}; font-size:0.78rem; font-weight:700;'>{_ibadge2}</span>"
                    + (f"&nbsp;&nbsp;<span style='color:#888; font-size:0.75rem;'>{_ih2['pct_held']:.1f}% inst. owned</span>" if _ih2["pct_held"] else "")
                    + f"</div>",
                    unsafe_allow_html=True,
                )
                for _ib3 in _ih2["buyers"][:4]:
                    _t1tag3 = " ⭐" if _ib3.get("is_tier1") else ""
                    _pctstr = f" (+{_ib3['pct_change']:.0f}%)" if _ib3.get("pct_change") else ""
                    st.markdown(
                        f"&nbsp;&nbsp;&nbsp;&nbsp;"
                        f"<span style='color:#9b59b6; font-size:0.82rem;'>📈 <b>{_ib3['name']}{_t1tag3}</b></span>"
                        f"<span style='color:#888; font-size:0.78rem;'>"
                        f" &nbsp;·&nbsp; {_ib3['shares']:,} shares{_pctstr}</span>",
                        unsafe_allow_html=True,
                    )

    except Exception as _e_inst:
        st.warning(f"Could not load institutional data: {_e_inst}")

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Earnings Calendar
    # ══════════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📅 Upcoming Earnings on Your Holdings")
    st.caption("Know before earnings hit — plan your risk. Red = within 3 days, Orange = this week, Grey = next 3 weeks.")

    _earn_rows = []
    for _r in results:
        _ed = _r.get("earnings_days_until")
        if _ed is not None:
            _earn_rows.append({
                "ticker":  _r["ticker"],
                "company": _r.get("company", _r["ticker"]),
                "days":    _ed,
                "score":   _r.get("total_score") or 0,
                "signal":  _r.get("signal", ""),
            })

    # Also pick up any holdings not yet scored by fetching directly
    _scored_tickers = {_r["ticker"] for _r in results}
    for _tk in tickers:
        if _tk not in _scored_tickers:
            try:
                import yfinance as _yf2
                _ei  = _yf2.Ticker(_tk).info or {}
                _ets = _ei.get("earningsTimestamp")
                if _ets:
                    from datetime import timezone as _tzp
                    _ed2 = datetime.fromtimestamp(float(_ets), tz=_tzp.utc)
                    _d2  = (_ed2 - datetime.now(tz=_tzp.utc)).days
                    if 0 <= _d2 <= 180:
                        _earn_rows.append({"ticker": _tk, "company": _tk, "days": _d2, "score": 0, "signal": ""})
            except Exception:
                pass

    _earn_rows.sort(key=lambda x: x["days"])

    if not _earn_rows:
        st.info("No earnings dates found for your holdings in the next 6 months.")
    else:
        for _er in _earn_rows:
            _d = _er["days"]
            if _d <= 2:
                _ecol, _etag = "#e74c3c", "🔴 IMMINENT"
            elif _d <= 7:
                _ecol, _etag = "#f39c12", "🟠 THIS WEEK"
            elif _d <= 14:
                _ecol, _etag = "#f1c40f", "🟡 NEXT WEEK"
            else:
                _ecol, _etag = "#888", f"📅 {_d} days"

            _sig_html = ""
            if _er["signal"]:
                _sc = "#2ecc71" if _er["signal"] in ("STRONG BUY", "BUY") else \
                      "#f39c12" if _er["signal"] == "WATCH" else "#888"
                _sig_html = f"<span style='color:{_sc}; font-size:0.75rem;'>{_er['signal']}</span>"

            st.markdown(
                f"<div style='background:{_ecol}15; border-left:3px solid {_ecol}; "
                f"border-radius:5px; padding:8px 14px; margin-bottom:5px; "
                f"display:flex; align-items:center; gap:16px;'>"
                f"<span style='font-weight:800; font-size:1rem; color:#fff; width:60px;'>{_er['ticker']}</span>"
                f"<span style='color:#aaa; font-size:0.82rem; flex:1;'>{_er['company'][:40]}</span>"
                f"<span style='color:{_ecol}; font-weight:700; font-size:0.82rem; width:120px;'>{_etag}</span>"
                f"{_sig_html}"
                f"</div>",
                unsafe_allow_html=True,
            )

with _tab_pol:

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Politicians This Week
    # ══════════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🚨 Politicians This Week")
    st.caption("Politicians who bought or sold your stocks in the last 7 days (transaction or disclosure date).")

    try:
        from scraper import load_cache as _load_trades_w
        from party_lookup import get_lookup as _get_lookup_w, get_party as _get_party_w, PARTY_STYLE as _PSTYLE_W
        from datetime import date as _date_w, timedelta as _td_w

        _trades_w   = _load_trades_w()
        _lookup_w   = _get_lookup_w()
        _my_tickers_w = {r["ticker"].upper() for r in results if r["ticker"]}
        _7d_cutoff  = pd.Timestamp(_date_w.today() - _td_w(days=7))

        if _trades_w:
            _wdf = pd.DataFrame(_trades_w)
            _wdf["ticker"]           = _wdf["ticker"].fillna("").str.upper().str.strip()
            _wdf["transaction_date"] = pd.to_datetime(_wdf["transaction_date"], errors="coerce")
            _wdf["disclosure_date"]  = pd.to_datetime(_wdf.get("disclosure_date"), errors="coerce")
            _wdf["name"]             = _wdf["name"].fillna("").str.strip()
            _wdf["transaction_type"] = _wdf["transaction_type"].fillna("")

            # Match my tickers, either traded OR disclosed in last 7 days
            _wdf_mine = _wdf[_wdf["ticker"].isin(_my_tickers_w)].copy()
            _wdf_week = _wdf_mine[
                (_wdf_mine["transaction_date"] >= _7d_cutoff) |
                (_wdf_mine["disclosure_date"]  >= _7d_cutoff)
            ].copy()
            _wdf_week = _wdf_week[_wdf_week["transaction_date"] <= pd.Timestamp.today()]

            if _wdf_week.empty:
                st.info("✅ No politician trades on your holdings disclosed or transacted in the last 7 days.")
            else:
                # Group by ticker
                _week_tickers = sorted(_wdf_week["ticker"].unique(),
                                       key=lambda t: len(_wdf_week[_wdf_week["ticker"]==t]), reverse=True)

                for _wtk in _week_tickers:
                    _wtrades = _wdf_week[_wdf_week["ticker"] == _wtk].sort_values("transaction_date", ascending=False)
                    _wbuys   = _wtrades["transaction_type"].str.contains("Purchase", case=False, na=False).sum()
                    _wsells  = _wtrades["transaction_type"].str.contains("Sale",     case=False, na=False).sum()
                    _wpols   = _wtrades["name"].nunique()
                    _signal_col = "#2ecc71" if _wbuys > _wsells else "#e74c3c" if _wsells > _wbuys else "#f39c12"

                    st.markdown(
                        f"<div style='background:#1a1a2e; border-left:4px solid {_signal_col}; "
                        f"border-radius:6px; padding:10px 16px; margin-bottom:8px;'>"
                        f"<span style='font-size:1.1rem; font-weight:800; color:#fff;'>{_wtk}</span>"
                        f"&nbsp;&nbsp;"
                        f"<span style='color:#aaa; font-size:0.82rem;'>"
                        f"{'🛒' if _wbuys else ''} {_wbuys} buy{'s' if _wbuys!=1 else ''} &nbsp;·&nbsp; "
                        f"{'📤' if _wsells else ''} {_wsells} sell{'s' if _wsells!=1 else ''} &nbsp;·&nbsp; "
                        f"👥 {_wpols} politician{'s' if _wpols!=1 else ''}</span></div>",
                        unsafe_allow_html=True,
                    )

                    for _, _wr in _wtrades.iterrows():
                        _wp   = _get_party_w(_wr["name"], _lookup_w)
                        _wps  = _PSTYLE_W.get(_wp, _PSTYLE_W["?"])
                        _wbuy = "purchase" in _wr["transaction_type"].lower()
                        _wtc  = "#2ecc71" if _wbuy else "#e74c3c"
                        _wic  = "🛒" if _wbuy else "📤"
                        _wd   = _wr["transaction_date"].strftime("%b %d") if pd.notna(_wr["transaction_date"]) else "—"
                        _wdd  = _wr.get("disclosure_date")
                        _wdd_s = pd.Timestamp(_wdd).strftime("%b %d") if pd.notna(_wdd) else "—"
                        _badge = (
                            f"<span style='background:{_wps['colour']}22; color:{_wps['colour']}; "
                            f"border:1px solid {_wps['colour']}55; border-radius:3px; "
                            f"padding:1px 5px; font-size:0.72rem; font-weight:700;'>"
                            f"{_wps['emoji']} {_wps['short']}</span>"
                        )
                        st.markdown(
                            f"&nbsp;&nbsp;&nbsp;&nbsp;{_badge} &nbsp;"
                            f"<b>{_wr['name']}</b> &nbsp;"
                            f"<span style='color:{_wtc};'>{_wic} {_wr['transaction_type']}</span>"
                            f"&nbsp; <span style='color:#888; font-size:0.8rem;'>"
                            f"Traded: {_wd} &nbsp;·&nbsp; Disclosed: {_wdd_s} &nbsp;·&nbsp; {_wr.get('amount','N/A')}"
                            f"</span>",
                            unsafe_allow_html=True,
                        )
        else:
            st.info("Run a **Disclosure Scan** on the Home page first.")
    except Exception as _ew:
        st.warning(f"Could not load this week's activity: {_ew}")

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Politician Activity on Your Holdings
    # ══════════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🏛️ Politician Activity on Your Holdings")
    st.caption("Which politicians have traded the same stocks you hold — and are they buying or selling?")

    try:
        from scraper import load_cache as _load_trades
        from party_lookup import get_lookup as _get_lookup, get_party as _get_party, PARTY_STYLE as _PSTYLE
        from prices import fetch_pct_changes as _fetch_pct, _get_close_on_or_after
        import json as _json
        from datetime import date as _date, timedelta as _timedelta

        _trades_raw = _load_trades()
        _lookup     = _get_lookup()

        if _trades_raw:
            _tdf = pd.DataFrame(_trades_raw)
            _tdf["ticker"]           = _tdf["ticker"].fillna("").str.upper().str.strip()
            _tdf["name"]             = _tdf["name"].fillna("").str.strip()
            _tdf["transaction_type"] = _tdf["transaction_type"].fillna("")
            _tdf["transaction_date"] = pd.to_datetime(_tdf["transaction_date"], errors="coerce")
            _tdf["amount"]           = _tdf["amount"].fillna("N/A")

            # My portfolio tickers
            my_tickers = {r["ticker"].upper() for r in results if r["ticker"]}

            # Filter pol trades to my holdings
            _pol_on_mine = _tdf[_tdf["ticker"].isin(my_tickers)].copy()
            _pol_on_mine = _pol_on_mine[_pol_on_mine["transaction_date"] <= pd.Timestamp.today()]

            if not _pol_on_mine.empty:
                # Days-back selector
                _days = st.selectbox(
                    "Activity window",
                    [30, 60, 90, 180, 365, 9999],
                    format_func=lambda d: f"Last {d} days" if d < 9999 else "All time",
                    index=2,
                    key="pol_activity_days",
                )
                _cutoff = pd.Timestamp(_date.today() - _timedelta(days=_days))
                _recent = _pol_on_mine[_pol_on_mine["transaction_date"] >= _cutoff]

                if _recent.empty:
                    st.info(f"No politician trades on your holdings in the last {_days} days.")
                else:
                    # Summary per ticker
                    _ticker_summary = []
                    for _tk in sorted(my_tickers):
                        _tk_trades = _recent[_recent["ticker"] == _tk]
                        if _tk_trades.empty:
                            continue
                        _buys  = _tk_trades[_tk_trades["transaction_type"].str.contains("Purchase", case=False, na=False)]
                        _sells = _tk_trades[_tk_trades["transaction_type"].str.contains("Sale",     case=False, na=False)]
                        _unique_pols = _tk_trades["name"].unique()
                        _d_count = sum(1 for n in _unique_pols if _get_party(n, _lookup) == "D")
                        _r_count = sum(1 for n in _unique_pols if _get_party(n, _lookup) == "R")
                        _latest  = _tk_trades["transaction_date"].max()

                        # Consensus signal
                        if len(_buys) > len(_sells) * 1.5:
                            _consensus = "🟢 Mostly Buying"
                        elif len(_sells) > len(_buys) * 1.5:
                            _consensus = "🔴 Mostly Selling"
                        else:
                            _consensus = "🟡 Mixed"

                        _ticker_summary.append({
                            "ticker":    _tk,
                            "buys":      len(_buys),
                            "sells":     len(_sells),
                            "pols":      len(_unique_pols),
                            "d_count":   _d_count,
                            "r_count":   _r_count,
                            "latest":    _latest,
                            "consensus": _consensus,
                            "trades":    _tk_trades,
                        })

                    # Sort by total activity desc
                    _ticker_summary.sort(key=lambda x: x["buys"] + x["sells"], reverse=True)

                    # Summary strip
                    _sum_html = "<div style='display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px;'>"
                    for _ts in _ticker_summary:
                        _latest_str = _ts["latest"].strftime("%b %d") if pd.notna(_ts["latest"]) else "—"
                        _party_str  = ""
                        if _ts["d_count"]: _party_str += f"🔵{_ts['d_count']}D "
                        if _ts["r_count"]: _party_str += f"🔴{_ts['r_count']}R"
                        _sum_html += f"""
    <div style='background:#1a1a2e; border:1px solid #333; border-radius:8px; padding:10px 14px; min-width:160px;'>
        <div style='font-size:1rem; font-weight:800; color:#fff;'>{_ts['ticker']}</div>
        <div style='font-size:0.75rem; color:#aaa; margin-top:2px;'>{_ts['consensus']}</div>
        <div style='font-size:0.72rem; color:#888; margin-top:4px;'>
            🛒 {_ts['buys']} buys · 📤 {_ts['sells']} sells<br>
            👥 {_ts['pols']} politician{'s' if _ts['pols']!=1 else ''} · {_party_str}<br>
            Latest: {_latest_str}
        </div>
    </div>"""
                    _sum_html += "</div>"
                    st.markdown(_sum_html, unsafe_allow_html=True)

                    # Detailed expanders per ticker
                    for _ts in _ticker_summary:
                        with st.expander(
                            f"**{_ts['ticker']}** — {_ts['pols']} politician{'s' if _ts['pols']!=1 else ''} · "
                            f"{_ts['buys']} buys · {_ts['sells']} sells  ·  {_ts['consensus']}",
                            expanded=False,
                        ):
                            _detail = _ts["trades"].sort_values("transaction_date", ascending=False)
                            for _, _tr in _detail.iterrows():
                                _p     = _get_party(_tr["name"], _lookup)
                                _ps    = _PSTYLE.get(_p, _PSTYLE["?"])
                                _is_buy = "purchase" in _tr["transaction_type"].lower()
                                _tx_col = "#2ecc71" if _is_buy else "#e74c3c"
                                _tx_icon= "🛒" if _is_buy else "📤"
                                _badge  = (
                                    f"<span style='background:{_ps['colour']}22; color:{_ps['colour']}; "
                                    f"border:1px solid {_ps['colour']}55; border-radius:3px; "
                                    f"padding:1px 5px; font-size:0.7rem; font-weight:700;'>"
                                    f"{_ps['emoji']} {_ps['short']}</span>"
                                )
                                _date_s = _tr["transaction_date"].strftime("%Y-%m-%d") if pd.notna(_tr["transaction_date"]) else "—"
                                st.markdown(
                                    f"{_badge} &nbsp; **{_tr['name']}** &nbsp; "
                                    f"<span style='color:{_tx_col};'>{_tx_icon} {_tr['transaction_type']}</span>"
                                    f" &nbsp; <span style='color:#888; font-size:0.8rem;'>{_date_s} · {_tr['amount']}</span>",
                                    unsafe_allow_html=True,
                                )
            else:
                st.info("No politician trades found for your current holdings.")
        else:
            st.info("Run a **Disclosure Scan** on the Home page first.")

    except Exception as _e:
        st.warning(f"Could not load politician activity: {_e}")


    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Your Returns vs Politicians
    # ══════════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📊 Your Returns vs. Politicians")
    st.caption("How your performance compares to politicians who traded the same stocks since your statement date.")

    try:
        from prices import _load_price_cache, _get_close_on_or_after

        _price_cache = _load_price_cache()

        _vs_rows = []
        for _r in results:
            _tk = _r["ticker"]
            _cp = _r["current_price"]
            _sp = _r["stmt_price"]
            if not _cp or not _sp or _sp <= 0:
                continue
            _my_ret = (_cp - _sp) / _sp * 100

            # Get politician trades on this ticker
            if not _trades_raw:
                continue
            _ptrades = [t for t in _trades_raw
                        if t.get("ticker","").upper().strip() == _tk
                        and "purchase" in t.get("transaction_type","").lower()]
            if not _ptrades:
                continue

            # Compute pol returns from their trade dates
            _pol_rets = []
            _closes   = _price_cache.get(_tk, {}).get("closes", {})
            if _closes:
                _now_price = _closes[max(_closes.keys())]
                for _pt in _ptrades:
                    _td = str(_pt.get("transaction_date",""))[:10]
                    if not _td:
                        continue
                    _bp = _get_close_on_or_after(_closes, _td)
                    if _bp and _bp > 0:
                        _pol_rets.append((_now_price - _bp) / _bp * 100)

            if not _pol_rets:
                continue

            _pol_avg  = sum(_pol_rets) / len(_pol_rets)
            _pol_best = max(_pol_rets)
            _pol_worst= min(_pol_rets)
            _beat     = _my_ret - _pol_avg

            _vs_rows.append({
                "Ticker":         _tk,
                "Your Return":    f"{_my_ret:+.1f}%",
                "Pol Avg Return": f"{_pol_avg:+.1f}%",
                "Pol Best":       f"{_pol_best:+.1f}%",
                "Pol Worst":      f"{_pol_worst:+.1f}%",
                "You vs Pol Avg": f"{_beat:+.1f}%",
                "# Pol Trades":   len(_pol_rets),
                "_beat":          _beat,
            })

        if _vs_rows:
            _vs_df = pd.DataFrame(_vs_rows).sort_values("_beat", ascending=False).drop(columns=["_beat"])

            # Colour-code the comparison column
            st.dataframe(
                _vs_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "You vs Pol Avg": st.column_config.TextColumn(
                        "You vs Pol Avg",
                        help="Positive = you're beating the average politician return on this stock",
                    ),
                },
            )

            # Quick summary
            _beating = sum(1 for r in _vs_rows if float(r["You vs Pol Avg"].replace("%","").replace("+","")) > 0)
            _total_v = len(_vs_rows)
            if _beating > _total_v / 2:
                st.success(f"🏆 You're beating politician average returns on **{_beating}/{_total_v}** shared stocks.")
            elif _beating == _total_v:
                st.success("🏆 You're beating politicians on every shared stock!")
            else:
                st.info(f"📊 Beating politician average on {_beating}/{_total_v} shared stocks.")

            st.caption(
                "**Note:** Politician returns are calculated from their individual trade dates to today. "
                "Your return is calculated from the statement price. Different entry dates make this a rough comparison."
            )
        else:
            st.info("Load prices via **Analyse Portfolio** and run a **Disclosure Scan** to enable comparison.")

    except Exception as _e:
        st.warning(f"Could not compute comparison: {_e}")


    st.divider()

with _tab_mkt:

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Sector Concentration
    # ══════════════════════════════════════════════════════════════════════════════
    st.subheader("🥧 Portfolio Sector Concentration")
    st.caption("Are you over-concentrated in one sector? Diversification reduces risk.")

    _sector_map: dict[str, list[str]] = {}
    for _r in results:
        _s = _r.get("sector") or "Unknown"
        _sector_map.setdefault(_s, []).append(_r["ticker"])

    if _sector_map:
        _sec_items = sorted(_sector_map.items(), key=lambda x: len(x[1]), reverse=True)
        _total_pos = len(results)
        _cols3 = st.columns(3)
        for _si, (_sec_name, _sec_tks) in enumerate(_sec_items):
            _pct = len(_sec_tks) / _total_pos * 100
            _col3 = _cols3[_si % 3]
            _bar_col = "#e74c3c" if _pct >= 40 else "#f39c12" if _pct >= 25 else "#4a90d9"
            _col3.markdown(
                f"<div style='background:#1a1a2e; border-radius:6px; padding:10px 12px; margin-bottom:8px;'>"
                f"<div style='display:flex; justify-content:space-between;'>"
                f"<span style='font-weight:700; font-size:0.85rem;'>{_sec_name}</span>"
                f"<span style='color:{_bar_col}; font-weight:700;'>{_pct:.0f}%</span>"
                f"</div>"
                f"<div style='background:#333; border-radius:3px; height:6px; margin:6px 0;'>"
                f"<div style='width:{min(_pct,100):.0f}%; height:100%; background:{_bar_col}; border-radius:3px;'></div>"
                f"</div>"
                f"<div style='color:#888; font-size:0.75rem;'>{', '.join(_sec_tks)}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        if any(len(v) / _total_pos >= 0.4 for v in _sector_map.values()):
            st.warning("⚠️ You have 40%+ of your portfolio in one sector — consider diversifying.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Market Sentiment (Fear & Greed)
    # ══════════════════════════════════════════════════════════════════════════════
    st.subheader("😱 Market Sentiment — Fear & Greed")
    st.caption("Composite of VIX, SPY momentum, market breadth, junk bonds, and safe-haven flows.")

    try:
        from market_sentiment import fetch_market_sentiment
        with st.spinner("Loading market sentiment…"):
            _sent = fetch_market_sentiment()

        if not _sent.get("error"):
            _sc   = _sent["score"]
            _lbl  = _sent["label"]
            _col  = _sent["colour"]
            _sigs = _sent.get("signals", [])

            # Big gauge display
            st.markdown(
                f"<div style='background:#1a1a2e; border:2px solid {_col}; border-radius:12px; "
                f"padding:20px 24px; margin:10px 0; display:flex; align-items:center; gap:24px;'>"
                f"<div style='text-align:center; min-width:100px;'>"
                f"<div style='font-size:2.5rem; font-weight:900; color:{_col};'>{_sc:.0f}</div>"
                f"<div style='font-size:0.75rem; color:#888;'>out of 100</div>"
                f"</div>"
                f"<div>"
                f"<div style='font-size:1.4rem; font-weight:800; color:{_col};'>{_lbl}</div>"
                f"<div style='color:#888; font-size:0.82rem; margin-top:4px;'>"
                f"{'Historically a good time to buy — others are fearful' if _sc <= 30 else 'Stay cautious — markets may be overextended' if _sc >= 75 else 'Balanced market conditions'}"
                f"</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Individual signals
            _sig_cols = st.columns(len(_sigs))
            for _sci, _sig in enumerate(_sigs):
                _sscore = _sig["score"]
                _scol2  = "#2ecc71" if _sscore >= 60 else "#e74c3c" if _sscore <= 40 else "#888"
                _sig_cols[_sci].metric(_sig["name"], f"{_sscore:.0f}/100",
                                       delta=_sig.get("detail", ""),
                                       delta_color="off")
        else:
            st.warning(f"Could not load sentiment: {_sent.get('error')}")
    except Exception as _e_sent:
        st.warning(f"Market sentiment unavailable: {_e_sent}")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION: Portfolio Alerts (email)
    # ══════════════════════════════════════════════════════════════════════════════
    st.subheader("🔔 Portfolio Alerts")
    st.caption("Detect signal changes, earnings in < 3 days, and volume surges on your holdings. Email alerts via Gmail.")

    try:
        from portfolio_alerts import check_portfolio_alerts, send_portfolio_alert_email

        # Enrich results with signal field for alert comparison
        _alert_results = []
        for _r in results:
            _sig_label, _ = signal_label(_r.get("total_score") or 0)
            _alert_results.append({**_r, "signal": _sig_label})

        _alerts = check_portfolio_alerts(_alert_results)

        if _alerts:
            for _al in _alerts:
                _al_type = _al["type"]
                _al_col  = {"STRONG_BUY": "#2ecc71", "BUY": "#27ae60",
                            "AVOID": "#e74c3c", "EARNINGS": "#f39c12", "VOLUME": "#3498db"}.get(_al_type, "#888")
                st.markdown(
                    f"<div style='background:{_al_col}18; border-left:4px solid {_al_col}; "
                    f"border-radius:5px; padding:8px 14px; margin-bottom:6px;'>"
                    f"<b style='color:{_al_col};'>{_al['ticker']}</b> &nbsp; {_al['message']}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            _al_col1, _al_col2 = st.columns([1, 4])
            with _al_col1:
                if st.button("📧 Email these alerts", key="send_portfolio_alerts"):
                    if send_portfolio_alert_email(_alerts):
                        st.success(f"Sent {len(_alerts)} alert(s) to your email!")
                    else:
                        st.error("Failed to send — check email config in the main app page.")
        else:
            st.success("✅ No new alerts — all signals stable, no imminent earnings or volume surges.")
    except Exception as _e_alerts:
        st.warning(f"Could not check alerts: {_e_alerts}")

    st.divider()

st.caption("**Disclaimer:** This analysis is for research purposes only and is not financial advice. Always do your own due diligence before making investment decisions.")
