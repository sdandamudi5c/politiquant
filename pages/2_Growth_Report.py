import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Raise macOS file-descriptor limit early ────────────────────────────────────
# macOS's system-wide launchctl default is 256 fds per process.
# With thousands of stocks each opening HTTP + SQLite connections this gets
# exhausted, causing "Too many open files" even inside Streamlit's page scanner.
# We raise it here (before any imports that open connections) to 65536.
try:
    import resource as _resource
    _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    _target = min(65536, _hard) if _hard > 0 else 65536
    if _soft < _target:
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (_target, _hard))
except Exception:
    pass  # non-fatal — proceed with whatever limit the OS gives us

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

_WORKERS = 4   # parallel fetch workers — kept low to avoid DNS thread exhaustion
               # (each fetch_fundamentals spawns ~3 inner threads, so 4×3 = 12 total)

from fundamentals import fetch_fundamentals, fmt_large, fmt_pct, fmt_ratio
from scraper import load_cache
from scorer import score_stock, score_stock_detailed, signal_label
from score_history import save_scores, predict_return
from job_state import JobState
from sidebar_jobs import render as _render_sidebar

_JOB = JobState("growth_report")
_render_sidebar()

st.set_page_config(page_title="PolitiQuant · Growth Report", page_icon="🚀", layout="wide")

st.title("🚀 Market Scanner")

_scanner_view = st.radio(
    "", ["🚀 Growth Report", "🔄 Sector Rotation"],
    horizontal=True, label_visibility="collapsed", key="scanner_view",
)
st.divider()

# ── SECTOR ROTATION VIEW ──────────────────────────────────────────────────────
if _scanner_view == "🔄 Sector Rotation":
    from sector_rotation import (
        fetch_sector_rotation, MOMENTUM_LABEL, MOMENTUM_COLOUR,
        get_hot_sector_picks, get_hot_subsector_picks,
    )
    from score_history import get_daily_picks as _gdp

    _sr_data = fetch_sector_rotation()
    _sr_spy1  = _sr_data.get("spy_1d");  _sr_spy30 = _sr_data.get("spy_30d")
    _sr_sects = _sr_data.get("sectors", {})
    _sr_subs  = _sr_data.get("sub_sectors", {})

    def _sc(v): return "#2ecc71" if (v or 0) >= 0 else "#e74c3c"
    def _sf(v): return f"{v:+.1f}%" if v is not None else "—"

    _sr_b1, _sr_b2, _sr_b3 = st.columns(3)
    _sr_b1.metric("SPY Today",  _sf(_sr_spy1))
    _sr_b2.metric("SPY 30-day", _sf(_sr_spy30))
    if _sr_data.get("cached_ts"):
        from datetime import datetime as _dt2
        _age_m = int(((_dt2.utcnow()-_dt2.fromisoformat(_sr_data["cached_ts"])).total_seconds())//60)
        _sr_b3.metric("Data age", f"{_age_m}m ago" if _age_m else "< 1m ago")

    SCORE_LK: dict = {}
    try:
        for _p in _gdp().get("stocks", []):
            SCORE_LK[_p["ticker"]] = {"score": _p.get("score"), "signal": _p.get("signal",""), "pred": _p.get("predicted_return")}
    except Exception:
        pass

    SIG_COL = {"STRONG BUY":"#2ecc71","BUY":"#27ae60","WATCH":"#f39c12","NEUTRAL":"#aaa","AVOID":"#e74c3c"}
    MOM_ORD = ["hot","warm","neutral","cool","cold"]

    def _stock_table(stk_list):
        h1,h2,h3,h4,h5,h6 = st.columns([1.2,1,1,1,1,1.8])
        for _h,_l in zip([h1,h2,h3,h4,h5,h6],["TICKER","TODAY","vs SPY","5-DAY","30-DAY","GROWTH SCORE"]):
            _h.markdown(f"<span style='color:#666;font-size:0.72rem;'>{_l}</span>",unsafe_allow_html=True)
        for s in stk_list:
            r1=s.get("ret_1d"); vs1=s.get("vs_spy_1d"); r5=s.get("ret_5d"); r30=s.get("ret_30d")
            inf=SCORE_LK.get(s["ticker"],{}); sig=inf.get("signal",""); scr=inf.get("score"); pred=inf.get("pred")
            _bg="background:#0d2210;" if (vs1 or 0)>=1 else "background:#200d0d;" if (vs1 or 0)<=-1 else ""
            c1,c2,c3,c4,c5,c6=st.columns([1.2,1,1,1,1,1.8])
            c1.markdown(f"<div style='{_bg}padding:3px 5px;border-radius:3px;'><b>{s['ticker']}</b></div>",unsafe_allow_html=True)
            c2.markdown(f"<div style='color:{_sc(r1)};font-weight:700;'>{_sf(r1)}</div>",unsafe_allow_html=True)
            c3.markdown(f"<div style='color:{_sc(vs1)};font-weight:700;'>{_sf(vs1)}</div>",unsafe_allow_html=True)
            c4.markdown(f"<div style='color:{_sc(r5)};'>{_sf(r5)}</div>",unsafe_allow_html=True)
            c5.markdown(f"<div style='color:{_sc(r30)};'>{_sf(r30)}</div>",unsafe_allow_html=True)
            if sig:
                _sig_col = SIG_COL.get(sig, "#888")
                c6.markdown(f"<span style='color:{_sig_col};font-weight:700;font-size:0.82rem;'>{sig}</span>"
                            +(f"<span style='color:#888;font-size:0.75rem;'> {int(scr)}pts" if scr else "")
                            +(f" ML:{pred:+.1f}%" if pred else "")
                            +(f"</span>" if scr else ""),unsafe_allow_html=True)
            else:
                c6.markdown("<span style='color:#555;font-size:0.78rem;'>Not scored yet</span>",unsafe_allow_html=True)

    st.markdown("### 🎯 Stocks in Hot Sectors")
    with st.spinner("Loading movers…"):
        _hsp = get_hot_sector_picks(top_n=10)
    if _hsp:
        for _g in _hsp:
            _mom=_g["momentum"]; _col2=MOMENTUM_COLOUR[_mom]; _ml,_=MOMENTUM_LABEL[_mom]
            _vs=_g.get("vs_spy_30d"); _vs_s=f" · {_vs:+.1f}% vs SPY (30d)" if _vs else ""
            with st.expander(f"{_ml} **{_g['sector']}** ({_g['etf']}){_vs_s}", expanded=(_mom=="hot")):
                _stock_table(_g["stocks"])
    else:
        st.info("No hot/warm sectors currently.")

    st.divider()
    st.markdown("### 🔬 Sub-Sector Picks (Semis, Biotech, Banks…)")
    with st.spinner("Loading sub-sector movers…"):
        _hsub = get_hot_subsector_picks(top_n=8)
    if _hsub:
        for _g in _hsub:
            _mom=_g["momentum"]; _ml,_=MOMENTUM_LABEL[_mom]; _vs=_g.get("vs_spy_30d")
            _vs_s=f" · {_vs:+.1f}% vs SPY" if _vs else ""
            with st.expander(f"{_ml} **{_g['sector']}** ({_g['etf']}){_vs_s}", expanded=(_mom=="hot")):
                if _g.get("desc"): st.caption(_g["desc"])
                _stock_table(_g["stocks"])
    else:
        st.info("No hot/warm sub-sectors currently.")

    st.divider()
    st.markdown("### 🗺️ All Sectors — 30-Day Momentum")
    _sorted_s = sorted(_sr_sects.items(), key=lambda x: MOM_ORD.index(x[1].get("momentum","neutral")))
    _gcols = st.columns(3)
    for _gi, (_sn, _si) in enumerate(_sorted_s):
        _mom=_si.get("momentum","neutral"); _col2=MOMENTUM_COLOUR[_mom]; _ml,_=MOMENTUM_LABEL.get(_mom,("","#aaa"))
        _r1=_si.get("ret_1d"); _r30=_si.get("ret_30d"); _r90=_si.get("ret_90d")
        _vs1=_si.get("vs_spy_1d"); _vs30=_si.get("vs_spy_30d"); _smod=_si.get("score_mod",0)
        _gcols[_gi%3].markdown(
            f"<div style='background:{_col2}15;border:1px solid {_col2}44;border-radius:8px;padding:12px 14px;margin-bottom:10px;'>"
            f"<div style='display:flex;justify-content:space-between;'><span style='font-weight:800;color:#fff;'>{_sn}</span>"
            f"<code style='font-size:0.75rem;color:#888;'>{_si.get('etf','')}</code></div>"
            f"<div style='color:{_col2};font-weight:700;font-size:0.82rem;'>{_ml} &nbsp;<span style='color:#888;font-size:0.75rem;'>Score {_smod:+d}</span></div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;margin-top:8px;'>"
            f"<div style='background:#111;border-radius:4px;padding:5px 8px;'><div style='color:#888;font-size:0.65rem;'>TODAY</div>"
            f"<div style='color:{_sc(_r1)};font-weight:700;'>{_sf(_r1)}</div><div style='color:#666;font-size:0.65rem;'>vs SPY {_sf(_vs1)}</div></div>"
            f"<div style='background:#111;border-radius:4px;padding:5px 8px;'><div style='color:#888;font-size:0.65rem;'>30-DAY</div>"
            f"<div style='color:{_sc(_r30)};font-weight:700;'>{_sf(_r30)}</div><div style='color:#666;font-size:0.65rem;'>vs SPY {_sf(_vs30)}</div></div>"
            f"<div style='background:#111;border-radius:4px;padding:5px 8px;'><div style='color:#888;font-size:0.65rem;'>90-DAY</div>"
            f"<div style='color:{_sc(_r90)};font-weight:700;'>{_sf(_r90)}</div></div>"
            f"</div></div>",unsafe_allow_html=True,)

    st.stop()  # ← don't render Growth Report below

# ── GROWTH REPORT VIEW ────────────────────────────────────────────────────────
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
                "⏱️ **How the scan works:** Every stock gets a quick price check first (~0.3s each). "
                "Only stocks passing the **$1 min price** filter get full analysis including Finnhub news. "
                "With no market cap filter, ~4,000 stocks pass out of 7,000 — "
                "estimated time: **~2–3 hours** on first run. "
                "After that everything is cached for the day — re-runs take under 5 minutes. "
                "Leave this tab open or run overnight."
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
            min_value=0.0, max_value=500.0, value=1.0, step=1.0,
            help="Stocks below this price are skipped.",
            key="min_price_filter",
        )
    with fc2:
        min_market_cap = st.selectbox(
            "Minimum market cap",
            options=[0, 50e6, 300e6, 2e9, 10e9],
            index=0,
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

_btn_col1, _btn_col2 = st.columns([2, 1])
run_report = _btn_col1.button(
    f"Generate Report  ({len(candidate_tickers)} stocks)",
    type="primary",
    disabled=len(candidate_tickers) == 0,
)
_has_cached = bool(st.session_state.get("growth_report_rows") or _JOB.result())
if _btn_col2.button(
    "⚡ Use Last Report",
    disabled=not _has_cached,
    help="Instantly load the last cached report without re-fetching prices.",
    key="use_last_report",
):
    if _JOB.result() and not st.session_state.get("growth_report_rows"):
        st.session_state.growth_report_rows = _JOB.result().get("rows", [])
    st.rerun()

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

# ── Daily email schedule ───────────────────────────────────────────────────────
import subprocess as _sp
from emailer import load_email_config as _lec

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "send_daily_report.py")
_PY     = sys.executable

def _get_crontab() -> str:
    try:
        return _sp.check_output(["crontab", "-l"], stderr=_sp.DEVNULL).decode()
    except Exception:
        return ""

def _cron_is_set() -> bool:
    return "send_daily_report.py" in _get_crontab()

def _set_cron(hour: int, minute: int) -> bool:
    existing = _get_crontab()
    lines = [l for l in existing.splitlines()
             if "send_daily_report.py" not in l and l.strip()]
    lines.append(f"{minute} {hour} * * 1-5 {_PY} {_SCRIPT} >> /tmp/politiquant_daily.log 2>&1")
    proc = _sp.run(["crontab", "-"], input=("\n".join(lines) + "\n").encode(), capture_output=True)
    return proc.returncode == 0

def _remove_cron():
    existing = _get_crontab()
    lines = [l for l in existing.splitlines()
             if "send_daily_report.py" not in l and l.strip()]
    _sp.run(["crontab", "-"], input=("\n".join(lines) + "\n").encode(), capture_output=True)

_active = _cron_is_set()
_ec     = _lec()
_recip  = _ec.get("recipient_email", "(not set)")

with st.expander(
    f"⏰ Schedule Daily Email Report  —  {'🟢 Active' if _active else '⚪ Not scheduled'}",
    expanded=True,
):
    sc1, sc2, sc3 = st.columns([1, 1, 2])
    _hour   = sc1.number_input("Hour (24h)", min_value=0, max_value=23, value=8,  key="sched_hour")
    _minute = sc2.number_input("Minute",     min_value=0, max_value=59, value=0,  key="sched_min")
    sc3.caption(
        f"Sends PDF to **{_recip}** every day at {int(_hour):02d}:{int(_minute):02d}. "
        "Uses the last cached Growth Report — regenerate it in the app occasionally for fresh scores."
    )

    btn1, btn2, btn3 = st.columns(3)

    if btn3.button("📤 Send Now", key="sched_send_now"):
        _cached_rows = st.session_state.get("growth_report_rows", [])
        if not _ec.get("sender_email") or not _ec.get("app_password"):
            st.error("⚠️ Configure email on the main page first.")
        elif not _cached_rows:
            st.error("⚠️ No report data yet — generate a report first.")
        else:
            with st.spinner("Building PDF and sending…"):
                try:
                    import smtplib
                    from email import encoders as _enc
                    from email.mime.base import MIMEBase as _MIMEBase
                    from email.mime.multipart import MIMEMultipart as _MIMEMulti
                    from email.mime.text import MIMEText as _MIMEText
                    from send_daily_report import _build_pdf

                    _pdf  = _build_pdf(_cached_rows)
                    _sb2  = sum(1 for r in _cached_rows if r.get("signal") == "STRONG BUY")
                    _b2   = sum(1 for r in _cached_rows if r.get("signal") == "BUY")
                    _msg  = _MIMEMulti()
                    _msg["Subject"] = (
                        f"🚀 PolitiQuant Growth Report — "
                        f"{date.today().strftime('%b %d, %Y')} · "
                        f"{_sb2} Strong Buys · {_b2} Buys"
                    )
                    _msg["From"] = _ec["sender_email"]
                    _msg["To"]   = _ec["recipient_email"]
                    _msg.attach(_MIMEText(
                        f"Your PolitiQuant Growth Report for "
                        f"{date.today().strftime('%B %d, %Y')} is attached.\n\n"
                        f"  • {len(_cached_rows)} stocks  ·  {_sb2} Strong Buy  ·  {_b2} Buy\n\n"
                        f"Not financial advice.\n\n— PolitiQuant", "plain"
                    ))
                    _part = _MIMEBase("application", "octet-stream")
                    _part.set_payload(_pdf)
                    _enc.encode_base64(_part)
                    _part.add_header("Content-Disposition",
                        f'attachment; filename="PolitiQuant_Report_{date.today()}.pdf"')
                    _msg.attach(_part)
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as _srv:
                        _srv.login(_ec["sender_email"], _ec["app_password"])
                        _srv.sendmail(_ec["sender_email"], _ec["recipient_email"], _msg.as_string())
                    st.success(f"✅ Sent to **{_ec['recipient_email']}** ({len(_pdf)//1024} KB)")
                except Exception as _e:
                    st.error(f"❌ Failed: {_e}")

    if btn1.button("✅ Enable daily schedule", key="sched_enable"):
        if not _ec.get("sender_email") or not _ec.get("app_password"):
            st.error("Configure email on the main page first.")
        else:
            if _set_cron(int(_hour), int(_minute)):
                st.success(f"🟢 Scheduled every day at {int(_hour):02d}:{int(_minute):02d}!")
                st.rerun()
            else:
                st.error("Failed to set schedule.")

    if btn2.button("🛑 Disable schedule", key="sched_disable", disabled=not _active):
        _remove_cron()
        st.success("Schedule removed.")
        st.rerun()

    if _active:
        for _line in _get_crontab().splitlines():
            if "send_daily_report.py" in _line:
                st.code(_line, language="bash")
                break

# ── Quick pre-filter (no Finnhub, no full history) ─────────────────────────────
def _passes_filter(ticker: str, min_price: float, min_market_cap: float) -> tuple:
    """
    Lightweight check using yfinance fast_info (~0.3s vs ~5s for full fetch).
    Returns (passes: bool, price: float, market_cap: float).
    If fast_info fails, returns (True, 0, 0) so we don't incorrectly skip.
    """
    try:
        fi = yf.Ticker(ticker).fast_info
        price  = fi.get("lastPrice")  or fi.get("last_price")  or 0
        mktcap = fi.get("marketCap")  or fi.get("market_cap")  or 0
        passes = (price >= min_price) and (not min_market_cap or mktcap >= min_market_cap)
        return passes, float(price), float(mktcap)
    except Exception:
        return True, 0, 0   # can't check → don't skip, let full fetch decide


# ── Background scoring function (8 parallel workers) ──────────────────────────
def _run_in_background(tickers, df_all_copy, min_price_, min_market_cap_, universe_key):
    """
    Runs in a daemon thread.
      Phase 1 — fast_info pre-filter  (no Finnhub, no history)
      Phase 2 — full fundamentals     (Finnhub + all enrichment enabled)
    Workers capped low (_WORKERS=4) to avoid fd exhaustion; the fd limit is also
    raised here so the daemon thread starts with the correct ceiling.
    """
    # Ensure the daemon thread has a high enough fd limit (macOS default is 256).
    try:
        import resource as _res
        _s, _h = _res.getrlimit(_res.RLIMIT_NOFILE)
        _t = min(65536, _h) if _h > 0 else 65536
        if _s < _t:
            _res.setrlimit(_res.RLIMIT_NOFILE, (_t, _h))
    except Exception:
        pass

    _workers = _WORKERS
    import pandas as _pd
    cutoff_30d = _pd.Timestamp(date.today() - timedelta(days=30))

    rows    = []
    skipped = {"penny": 0, "mktcap": 0, "error": 0}
    _lock   = threading.Lock()
    _done   = [0]   # mutable counter (list trick — works in Python 3.8)

    def _safe(v):
        try: return round(float(v), 1)
        except: return None

    # ── Phase 1: parallel pre-filter (fast_info only) ─────────────────────────
    # 8 workers hit Yahoo fast_info simultaneously — ~0.3s each, no Finnhub calls
    passing_tickers = []

    def _filter_one(ticker):
        if _JOB.is_cancelling():
            with _lock:
                _done[0] += 1
            return ticker, False, 0, 0   # treat as filtered-out, no API call
        passes, price, mktcap = _passes_filter(ticker, min_price_, min_market_cap_)
        with _lock:
            _done[0] += 1
            _JOB.update(done=_done[0], current=f"[filtering] {ticker}")
        return ticker, passes, price, mktcap

    with ThreadPoolExecutor(max_workers=_workers) as ex:
        futs = {ex.submit(_filter_one, t): t for t in tickers}
        for fut in as_completed(futs):
            try:
                ticker, passes, price, _ = fut.result()
                if passes:
                    passing_tickers.append(ticker)
                else:
                    with _lock:
                        if price and price < min_price_:
                            skipped["penny"] += 1
                        else:
                            skipped["mktcap"] += 1
            except Exception:
                with _lock:
                    skipped["error"] += 1

    # Reset progress counter for phase 2
    with _lock:
        _done[0] = 0
    _JOB.start(total=len(passing_tickers), meta={"universe_key": universe_key})

    # ── Phase 2: parallel full fetch (Finnhub shared 55/min pool) ─────────────
    # 8 workers fetch fundamentals simultaneously.
    # Finnhub calls from all workers share the same sliding-window rate limiter —
    # whichever thread would exceed 55/min is blocked, others keep going.

    def _fetch_one(ticker):
        if _JOB.is_cancelling():
            with _lock:
                _done[0] += 1
                _JOB.update(done=_done[0], current=ticker)
            return None   # stop immediately — no yfinance, no Finnhub
        try:
            fund = fetch_fundamentals(ticker)
            if fund.get("error") and not fund.get("current_price"):
                with _lock:
                    skipped["error"] += 1
                    _done[0] += 1
                    _JOB.update(done=_done[0], current=ticker)
                return None

            price   = fund.get("current_price") or 0
            mkt_cap = fund.get("market_cap")    or 0
            if price < min_price_:
                with _lock:
                    skipped["penny"] += 1
                    _done[0] += 1
                    _JOB.update(done=_done[0], current=ticker)
                return None
            if min_market_cap_ and mkt_cap < min_market_cap_:
                with _lock:
                    skipped["mktcap"] += 1
                    _done[0] += 1
                    _JOB.update(done=_done[0], current=ticker)
                return None

            mom = _momentum_from_fund(fund)

            if not df_all_copy.empty:
                pol_mask = (
                    (df_all_copy["ticker"] == ticker) &
                    (df_all_copy["transaction_type"].str.contains(
                        "Purchase", case=False, na=False)) &
                    (df_all_copy["transaction_date"] >= cutoff_30d)
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
            except: upside = None

            row = {
                "ticker": ticker, "company": fund.get("company_name", ticker),
                "sector": fund.get("sector") or "—", "score": round(score, 1),
                "signal": signal, "colour": colour, "factor_pts": factor_pts,
                "price": cp, "analyst_target": target, "upside_pct": upside,
                "analyst_rating": fund.get("analyst_rating") or "N/A",
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
                # Institutional (13F)
                "inst_score":         fund.get("inst_score", 0),
                "inst_buyer_count":   fund.get("inst_buyer_count", 0),
                "inst_tier1_buying":  fund.get("inst_tier1_buying", False),
                "inst_tier1_buyers":  fund.get("inst_tier1_buyers", []),
                "inst_buyers":        fund.get("inst_buyers", []),
                "inst_new_positions": fund.get("inst_new_positions", []),
                "inst_pct_held":      fund.get("inst_pct_held", 0.0),
                # Sector rotation
                "sector":             fund.get("sector", ""),
                # Earnings / volume / 52w
                "earnings_days_until": fund.get("earnings_days_until"),
                "volume_ratio":        fund.get("volume_ratio"),
                "today_change_pct":    fund.get("today_change_pct"),
                "pct_from_52w_high":   fund.get("pct_from_52w_high"),
                "pct_from_52w_low":    fund.get("pct_from_52w_low"),
                "week_52_high":        fund.get("week_52_high"),
                "week_52_low":         fund.get("week_52_low"),
                # Analyst revisions
                "analyst_upgrades_30d":   fund.get("analyst_upgrades_30d", 0),
                "analyst_downgrades_30d": fund.get("analyst_downgrades_30d", 0),
                "analyst_upgrade_firms":  fund.get("analyst_upgrade_firms", []),
                "analyst_downgrade_firms":fund.get("analyst_downgrade_firms", []),
            }

            with _lock:
                rows.append(row)
                _done[0] += 1
                _JOB.update(done=_done[0], current=ticker)

            return row

        except Exception:
            with _lock:
                skipped["error"] += 1
                _done[0] += 1
                _JOB.update(done=_done[0], current=ticker)
            return None

    try:
        with ThreadPoolExecutor(max_workers=_workers) as ex:
            futs = {ex.submit(_fetch_one, t): t for t in passing_tickers}
            for fut in as_completed(futs):
                fut.result()   # exceptions are handled inside _fetch_one

        rows.sort(key=lambda r: r["score"], reverse=True)
        try:
            history_batch = [
                {"ticker": r["ticker"], "price": r["price"], "score": r["score"],
                 "factor_pts": r.get("factor_pts", {})} for r in rows if r.get("price")
            ]
            save_scores(history_batch)
        except Exception:
            pass
    finally:
        # Always mark job done — even if an exception occurs above.
        # Without this, the sidebar shows "Running" forever after a crash.
        _JOB.finish(result={"rows": rows, "skipped": skipped, "universe_key": universe_key})


# ── Run / status ───────────────────────────────────────────────────────────────
if run_report:
    _JOB.reset()
    _JOB.start(total=len(candidate_tickers),
               meta={"universe_key": _universe_key})
    t = threading.Thread(
        target=_run_in_background,
        args=(candidate_tickers, df_all.copy() if not df_all.empty else pd.DataFrame(),
              min_price, min_market_cap, _universe_key),
        daemon=True,
    )
    t.start()
    st.rerun()

# ── Show progress if running ───────────────────────────────────────────────────
if _JOB.is_running():
    done, total, current = _JOB.progress()
    _tdisplay  = str(total) if total > 1 else "?"
    # Cap at 0.99 — hitting 1.0 looks "done" but the job is still running.
    # When total is unknown (0), show 0.60 indeterminate rather than letting
    # done/fallback overflow past 100% (happens when min_price=0 and all stocks
    # pass phase 1 so done == fallback_total at the phase 1→2 boundary).
    if total > 1:
        pct = min(0.99, done / total)
    else:
        pct = 0.60   # indeterminate — total not yet known
    cancelling = _JOB.is_cancelling()
    phase      = "🔍 Filtering" if "[filtering]" in (current or "") else "📊 Scoring"
    clean      = (current or "").replace("[filtering] ", "")

    if cancelling:
        st.warning("⏳ Stopping… workers are finishing their current stock.")
        st.progress(pct, text=f"Stopping — {done}/{_tdisplay} processed")
    else:
        c1, c2 = st.columns([5, 1])
        c1.progress(pct, text=f"{phase} {clean}… {done}/{_tdisplay} stocks")
        if c2.button("🛑 Stop", key="main_stop_btn",
                     help="Stop job and keep results collected so far"):
            _JOB.cancel()
            st.rerun()
        _remaining = f"~{total - done} remaining" if total > 1 else "calculating…"
        st.caption(f"⏱ Started {_JOB.started_at()}  ·  {done} done  ·  {_remaining}")

    import time; time.sleep(1.5); st.rerun()
    st.stop()

# ── Load results ───────────────────────────────────────────────────────────────
if not _JOB.is_done() and "growth_report_rows" not in st.session_state:
    st.stop()

if _JOB.was_cancelled():
    st.warning("🛑 Job was stopped early — showing partial results collected before cancellation.")

if _JOB.is_done():
    result = _JOB.result()
    if result:
        st.session_state.growth_report_rows = result["rows"]
        st.session_state["_last_universe"]  = result.get("universe_key", _universe_key)
        skipped = result.get("skipped", {})
        skip_parts = []
        if skipped.get("penny"):  skip_parts.append(f"{skipped['penny']} penny stocks")
        if skipped.get("mktcap"): skip_parts.append(f"{skipped['mktcap']} below market cap")
        if skipped.get("error"):  skip_parts.append(f"{skipped['error']} with no data")
        if skip_parts:
            st.info(f"Skipped: {', '.join(skip_parts)}. **{len(result['rows'])} stocks** in report.  "
                    f"Completed {_JOB.completed_at()}")

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
        "Upgrades (30d)": r.get("analyst_upgrades_30d") or 0,
        "Downgrades(30d)":r.get("analyst_downgrades_30d") or 0,
        "Volume Ratio":   r.get("volume_ratio"),
        "52w High %":     r.get("pct_from_52w_high"),
        "Earnings In":    f"{r['earnings_days_until']}d" if r.get("earnings_days_until") is not None else "",
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

# ── Download + Email ─────────────────────────────────────────────────────
dl_col, email_col = st.columns([1, 1])

csv = tdf.to_csv(index=False).encode("utf-8")
dl_col.download_button(
    "⬇️ Download report as CSV",
    data=csv,
    file_name=f"growth_report_{date.today()}.csv",
    mime="text/csv",
)

if email_col.button("📧 Email report as PDF", key="email_growth_report"):
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
    from emailer import load_email_config

    ecfg = load_email_config()
    if not ecfg.get("sender_email") or not ecfg.get("app_password") or not ecfg.get("recipient_email"):
        st.error("⚠️ Email not configured — fill in the Email Alerts section on the main page first.")
    else:
        with st.spinner("Building PDF and sending…"):
            try:
                import io, smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText
                from email.mime.base import MIMEBase
                from email import encoders
                from reportlab.lib.pagesizes import landscape, A4
                from reportlab.lib import colors
                from reportlab.lib.units import cm
                from reportlab.platypus import (
                    SimpleDocTemplate, Table, TableStyle, Paragraph,
                    Spacer, HRFlowable,
                )
                from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
                from reportlab.lib.enums import TA_CENTER, TA_LEFT

                # ── Signal counts ──────────────────────────────────────────────
                _sb = sum(1 for r in table_rows if r["signal"] == "STRONG BUY")
                _b  = sum(1 for r in table_rows if r["signal"] == "BUY")
                _w  = sum(1 for r in table_rows if r["signal"] == "WATCH")

                # ── Colour helpers ─────────────────────────────────────────────
                def _sig_color(sig):
                    return {
                        "STRONG BUY": colors.HexColor("#2ecc71"),
                        "BUY":        colors.HexColor("#27ae60"),
                        "WATCH":      colors.HexColor("#f39c12"),
                        "NEUTRAL":    colors.HexColor("#888888"),
                        "AVOID":      colors.HexColor("#e74c3c"),
                    }.get(sig, colors.HexColor("#888888"))

                def _upside_color(v):
                    if v is None: return colors.HexColor("#888888")
                    return colors.HexColor("#2ecc71") if v >= 0 else colors.HexColor("#e74c3c")

                # ── Build PDF in memory ────────────────────────────────────────
                buf = io.BytesIO()
                doc = SimpleDocTemplate(
                    buf,
                    pagesize=landscape(A4),
                    leftMargin=1.5*cm, rightMargin=1.5*cm,
                    topMargin=1.5*cm,  bottomMargin=1.5*cm,
                )

                styles = getSampleStyleSheet()
                title_style = ParagraphStyle(
                    "title", fontSize=18, fontName="Helvetica-Bold",
                    textColor=colors.HexColor("#4a90d9"), spaceAfter=4,
                )
                sub_style = ParagraphStyle(
                    "sub", fontSize=9, fontName="Helvetica",
                    textColor=colors.HexColor("#888888"), spaceAfter=8,
                )
                cell_style = ParagraphStyle(
                    "cell", fontSize=7.5, fontName="Helvetica",
                    textColor=colors.HexColor("#222222"),
                )
                cell_bold = ParagraphStyle(
                    "cellb", fontSize=7.5, fontName="Helvetica-Bold",
                    textColor=colors.HexColor("#1a5276"),
                )

                story = []

                # Title
                story.append(Paragraph("🚀 PolitiQuant — Growth Report", title_style))
                story.append(Paragraph(
                    f"{date.today().strftime('%B %d, %Y')}  ·  {len(table_rows)} stocks  ·  "
                    f"{_sb} Strong Buy  ·  {_b} Buy  ·  {_w} Watch",
                    sub_style,
                ))
                story.append(HRFlowable(width="100%", thickness=0.5,
                                        color=colors.HexColor("#cccccc"), spaceAfter=10))

                # Table header
                col_headers = [
                    "Ticker", "Company", "Sector", "Score", "Signal",
                    "Price", "Upside", "1M %", "3M %", "RSI",
                    "Wall St", "Pol Buys",
                ]
                col_widths = [1.5*cm, 4.5*cm, 3.2*cm, 1.4*cm, 2.2*cm,
                              1.5*cm, 1.5*cm, 1.4*cm, 1.4*cm, 1.2*cm,
                              2.2*cm, 1.5*cm]

                def _fmt(v, suffix="", decimals=1):
                    if v is None: return "—"
                    try: return f"{float(v):+.{decimals}f}{suffix}"
                    except: return str(v)

                data = [col_headers]
                row_colors = []  # (row_index, bg_color)

                for i, r in enumerate(table_rows, start=1):
                    sig = r["signal"]
                    _sc = _sig_color(sig)
                    # Alternating row bg
                    row_colors.append((i, colors.HexColor("#f5f8ff") if i % 2 == 0 else colors.white))

                    data.append([
                        Paragraph(r["ticker"],             cell_bold),
                        Paragraph(r["company"][:32],       cell_style),
                        Paragraph((r["sector"] or "—")[:20], cell_style),
                        Paragraph(f"{r['score']:.1f}",     cell_bold),
                        Paragraph(sig,                     ParagraphStyle(
                            "sig", fontSize=7, fontName="Helvetica-Bold",
                            textColor=_sc)),
                        Paragraph(f"${r['price']:.2f}" if r["price"] else "—", cell_style),
                        Paragraph(_fmt(r["upside_pct"], "%"), ParagraphStyle(
                            "up", fontSize=7.5, fontName="Helvetica",
                            textColor=_upside_color(r["upside_pct"]))),
                        Paragraph(_fmt(r["mom_1m_pct"], "%"), cell_style),
                        Paragraph(_fmt(r["mom_3m_pct"], "%"), cell_style),
                        Paragraph(f"{r['rsi']:.0f}" if r["rsi"] else "—", cell_style),
                        Paragraph(r["analyst_rating"] or "N/A", cell_style),
                        Paragraph(str(r["pol_buys_30d"]), cell_style),
                    ])

                tbl = Table(data, colWidths=col_widths, repeatRows=1)

                # Base style
                ts = TableStyle([
                    # Header
                    ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#2a3150")),
                    ("TEXTCOLOR",   (0, 0), (-1, 0), colors.HexColor("#4a90d9")),
                    ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE",    (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                    ("TOPPADDING",    (0, 0), (-1, 0), 6),
                    # All cells
                    ("FONTSIZE",    (0, 1), (-1, -1), 7.5),
                    ("TOPPADDING",  (0, 1), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING",(0, 0), (-1, -1), 5),
                    ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.white, colors.HexColor("#f5f8ff")]),
                    ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
                ])
                tbl.setStyle(ts)
                story.append(tbl)

                story.append(Spacer(1, 12))
                story.append(Paragraph(
                    "PolitiQuant · Political intelligence · Growth scoring · "
                    "Not financial advice — use as a research starting point.",
                    ParagraphStyle("footer", fontSize=7, textColor=colors.HexColor("#aaaaaa")),
                ))

                doc.build(story)
                pdf_bytes = buf.getvalue()

                # ── Attach PDF and send ────────────────────────────────────────
                msg = MIMEMultipart()
                msg["Subject"] = (
                    f"🚀 PolitiQuant Growth Report — "
                    f"{date.today().strftime('%b %d, %Y')} · "
                    f"{_sb} Strong Buys · {_b} Buys"
                )
                msg["From"] = ecfg["sender_email"]
                msg["To"]   = ecfg["recipient_email"]

                body = MIMEText(
                    f"Hi,\n\nPlease find attached your PolitiQuant Growth Report "
                    f"for {date.today().strftime('%B %d, %Y')}.\n\n"
                    f"  • {len(table_rows)} stocks analysed\n"
                    f"  • {_sb} Strong Buy  |  {_b} Buy  |  {_w} Watch\n\n"
                    f"Not financial advice.\n\n— PolitiQuant",
                    "plain",
                )
                msg.attach(body)

                part = MIMEBase("application", "octet-stream")
                part.set_payload(pdf_bytes)
                encoders.encode_base64(part)
                fname = f"PolitiQuant_GrowthReport_{date.today()}.pdf"
                part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
                msg.attach(part)

                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
                    srv.login(ecfg["sender_email"], ecfg["app_password"])
                    srv.sendmail(ecfg["sender_email"], ecfg["recipient_email"], msg.as_string())

                st.success(
                    f"✅ PDF report emailed to **{ecfg['recipient_email']}** — check your inbox! "
                    f"({len(table_rows)} stocks, {len(pdf_bytes)//1024} KB)"
                )
            except Exception as _e:
                st.error(f"❌ Failed to send: {_e}")

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

        # Insider badge
        _ins_badge = ""
        if r.get("insider_ceo_bought") and r.get("insider_cfo_bought"):
            _ins_badge = " &nbsp;·&nbsp; <span style='color:#f39c12; font-weight:700;'>🏦 CEO+CFO buying</span>"
        elif r.get("insider_ceo_bought"):
            _ins_badge = " &nbsp;·&nbsp; <span style='color:#2ecc71; font-weight:700;'>🏦 CEO buying</span>"
        elif r.get("insider_cfo_bought"):
            _ins_badge = " &nbsp;·&nbsp; <span style='color:#2ecc71; font-weight:700;'>🏦 CFO buying</span>"
        elif (r.get("insider_buy_count") or 0) >= 2:
            _ins_badge = f" &nbsp;·&nbsp; <span style='color:#4a90d9; font-weight:700;'>🏦 {r['insider_buy_count']} insiders buying</span>"
        elif (r.get("insider_buy_count") or 0) == 1:
            _ins_badge = " &nbsp;·&nbsp; <span style='color:#4a90d9;'>🏦 Insider buying</span>"

        # Institutional badge
        _inst_badge = ""
        _inst_t1 = r.get("inst_tier1_buyers") or []
        _inst_bc  = r.get("inst_buyer_count") or 0
        if r.get("inst_tier1_buying") and len(_inst_t1) >= 2:
            _inst_badge = f" &nbsp;·&nbsp; <span style='color:#9b59b6; font-weight:700;'>🏢 {_inst_t1[0].split()[0]}+{len(_inst_t1)-1} more buying</span>"
        elif r.get("inst_tier1_buying") and _inst_t1:
            _inst_badge = f" &nbsp;·&nbsp; <span style='color:#9b59b6; font-weight:700;'>🏢 {_inst_t1[0].split()[0]} buying</span>"
        elif _inst_bc >= 3:
            _inst_badge = f" &nbsp;·&nbsp; <span style='color:#8e44ad;'>🏢 {_inst_bc} institutions adding</span>"

        # Sector badge
        _sector_badge = ""
        _sector_name = r.get("sector") or ""
        if _sector_name:
            try:
                from sector_rotation import get_sector_signal, MOMENTUM_LABEL, MOMENTUM_COLOUR
                _ssig  = get_sector_signal(_sector_name)
                _smom  = _ssig.get("momentum", "neutral")
                _svs30 = _ssig.get("vs_spy_30d")
                if _smom in ("hot", "warm"):
                    _scol  = MOMENTUM_COLOUR[_smom]
                    _slbl  = "🔥" if _smom == "hot" else "📈"
                    _svstr = f" {_svs30:+.1f}% vs SPY" if _svs30 is not None else ""
                    _sector_badge = (f" &nbsp;·&nbsp; <span style='color:{_scol}; font-size:0.78rem;'>"
                                     f"{_slbl} {_sector_name}{_svstr}</span>")
                elif _smom in ("cold", "cool"):
                    _scol  = MOMENTUM_COLOUR[_smom]
                    _slbl  = "❄️" if _smom == "cold" else "📉"
                    _svstr = f" {_svs30:+.1f}% vs SPY" if _svs30 is not None else ""
                    _sector_badge = (f" &nbsp;·&nbsp; <span style='color:{_scol}; font-size:0.78rem;'>"
                                     f"{_slbl} {_sector_name}{_svstr}</span>")
            except Exception:
                pass

        # Analyst revision badge
        _rev_badge = ""
        _ups   = r.get("analyst_upgrades_30d") or 0
        _downs = r.get("analyst_downgrades_30d") or 0
        _up_firms = r.get("analyst_upgrade_firms") or []
        if _ups >= 2:
            _firm_str = f" ({_up_firms[0]}…)" if _up_firms else ""
            _rev_badge = (f" &nbsp;·&nbsp; <span style='color:#2ecc71; font-weight:700;'>"
                          f"⬆️ {_ups} upgrades{_firm_str}</span>")
        elif _ups == 1:
            _firm_str = f" ({_up_firms[0]})" if _up_firms else ""
            _rev_badge = (f" &nbsp;·&nbsp; <span style='color:#2ecc71;'>"
                          f"⬆️ Upgraded{_firm_str}</span>")
        elif _downs >= 2:
            _rev_badge = (f" &nbsp;·&nbsp; <span style='color:#e74c3c;'>"
                          f"⬇️ {_downs} downgrades</span>")
        elif _downs == 1:
            _rev_badge = " &nbsp;·&nbsp; <span style='color:#e74c3c;'>⬇️ Downgraded</span>"

        # Earnings countdown badge
        _earn_badge = ""
        _earn_days  = r.get("earnings_days_until")
        if _earn_days is not None:
            if _earn_days <= 2:
                _earn_badge = (f" &nbsp;·&nbsp; <span style='color:#e74c3c; font-weight:700;'>"
                               f"📅 Earnings in {_earn_days}d ⚠️</span>")
            elif _earn_days <= 7:
                _earn_badge = (f" &nbsp;·&nbsp; <span style='color:#f39c12; font-weight:700;'>"
                               f"📅 Earnings in {_earn_days}d</span>")
            elif _earn_days <= 21:
                _earn_badge = (f" &nbsp;·&nbsp; <span style='color:#888;'>"
                               f"📅 Earnings in {_earn_days}d</span>")

        # Volume surge badge
        _vol_badge  = ""
        _vol_ratio  = r.get("volume_ratio")
        _day_chg    = r.get("today_change_pct")
        if _vol_ratio and _vol_ratio >= 2.0:
            _up = _day_chg is not None and _day_chg > 0
            _dn = _day_chg is not None and _day_chg < 0
            _vcol = "#2ecc71" if _up else "#e74c3c" if _dn else "#f39c12"
            _vol_badge = (f" &nbsp;·&nbsp; <span style='color:{_vcol}; font-weight:700;'>"
                          f"🔊 {_vol_ratio:.1f}× vol</span>")

        # 52-week proximity badge
        _52w_badge = ""
        _p52h = r.get("pct_from_52w_high")
        _p52l = r.get("pct_from_52w_low")
        if _p52h is not None:
            if _p52h >= -2:
                _52w_badge = (" &nbsp;·&nbsp; <span style='color:#2ecc71; font-weight:700;'>"
                              "🚀 Near 52w high</span>")
            elif _p52h >= -8:
                _52w_badge = (f" &nbsp;·&nbsp; <span style='color:#f39c12;'>"
                              f"📈 {abs(_p52h):.0f}% from 52w high</span>")
        if _p52l is not None and _p52l <= 10 and not _52w_badge:
            _52w_badge = (f" &nbsp;·&nbsp; <span style='color:#e74c3c;'>"
                          f"⚠️ Near 52w low ({_p52l:+.0f}%)</span>")

        st.markdown(
            f"<div style='background:{colour}22; border-left:4px solid {colour}; "
            f"padding:10px 14px; border-radius:6px; margin:8px 0;'>"
            f"<b style='color:{colour};'>{signal}</b> — {r['analyst_rating']} (Wall St) · "
            f"Target: {target_str} · Pol buys 30d: {r['pol_buys_30d']}"
            f"{_rev_badge}"
            f"{_ins_badge}"
            f"{_inst_badge}"
            f"{_sector_badge}"
            f"{_earn_badge}"
            f"{_vol_badge}"
            f"{_52w_badge}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Insider buys detail
        if r.get("insider_buys"):
            with st.expander("🏦 Insider purchases (last 90 days)", expanded=False):
                for _ib in r["insider_buys"][:5]:
                    _ival = f"${_ib['value']:,.0f}" if _ib.get("value") else ""
                    st.caption(
                        f"📈 **{_ib['name']}** ({_ib['position']}) — "
                        f"{_ib['shares']:,} shares {_ival} on {_ib['date']}"
                    )

        # Institutional buyers detail
        if r.get("inst_buyers") or r.get("inst_new_positions"):
            _inst_score = r.get("inst_score") or 0
            _inst_pct   = r.get("inst_pct_held") or 0
            _exp_label  = f"🏢 Institutional activity (13F) — score {_inst_score:+d}"
            with st.expander(_exp_label, expanded=False):
                if _inst_pct:
                    st.caption(f"📊 **{_inst_pct:.1f}%** of float held by institutions")
                if r.get("inst_new_positions"):
                    st.caption("**🆕 New positions opened:**")
                    for _ip in r["inst_new_positions"][:3]:
                        _t1tag = " ⭐" if _ip.get("is_tier1") else ""
                        st.caption(f"  • **{_ip['name']}{_t1tag}** — {_ip['shares']:,} shares (brand new)")
                if r.get("inst_buyers"):
                    st.caption("**📈 Institutions adding to positions:**")
                    for _ib2 in r["inst_buyers"][:5]:
                        _t1tag = " ⭐" if _ib2.get("is_tier1") else ""
                        _pct_s = f" (+{_ib2['pct_change']:.0f}%)" if _ib2.get("pct_change") else ""
                        st.caption(f"  • **{_ib2['name']}{_t1tag}**{_pct_s}")

        # ML prediction (only shown once model is trained)
        try:
            pred = predict_return(r.get("factor_pts", {}))
            if pred:
                p_ret, p_ci, n_train, dir_acc = pred
                p_col = "#2ecc71" if p_ret > 0 else "#e74c3c"
                conf  = "High" if n_train >= 500 else "Medium" if n_train >= 100 else "Early"
                st.markdown(
                    f"<div style='background:#1a2a1a; border:1px solid {p_col}55; border-radius:8px; "
                    f"padding:10px 14px; margin:6px 0; display:flex; align-items:center; gap:16px;'>"
                    f"<div><div style='font-size:0.75rem; color:#aaa;'>🧠 ML Predicted 30-day return</div>"
                    f"<div style='font-size:1.4rem; font-weight:bold; color:{p_col};'>"
                    f"{p_ret:+.1f}% <span style='font-size:0.85rem; color:#aaa;'>± {p_ci:.1f}%</span></div></div>"
                    f"<div style='font-size:0.75rem; color:#888;'>"
                    f"Based on {n_train} outcomes · {conf} confidence · "
                    f"Model direction accuracy: {dir_acc:.1f}%</div>"
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
