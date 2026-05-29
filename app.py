import pandas as pd
import streamlit as st

from emailer import load_email_config, save_email_config, send_alert, send_smart_alert
from prices import add_pct_change_to_df, fetch_pct_changes
from scraper import get_cache_age, load_cache, run_scan
from party_lookup import enrich_trades as _enrich_trades, party_badge as _party_badge, PARTY_STYLE as _PARTY_STYLE

# Kick off FinBERT model load in the background the moment the app starts.
# By the time the user clicks Generate Report, it's ready.
try:
    from finbert_sentiment import preload_async
    preload_async()
except Exception:
    pass

st.set_page_config(
    page_title="PolitiQuant",
    page_icon="🏛️",
    layout="wide",
)

st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    .stButton > button {
        background-color: #1f4e79;
        color: white;
        font-size: 1.05rem;
        font-weight: bold;
        padding: 0.55rem 1.5rem;
        border-radius: 8px;
        border: none;
        width: 100%;
    }
    .stButton > button:hover { background-color: #2d6aa0; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div style='display:flex; align-items:center; gap:12px; margin-bottom:0.2rem;'>
    <span style='font-size:2.4rem;'>🏛️</span>
    <div>
        <div style='font-size:2rem; font-weight:800; letter-spacing:-0.5px;
                    background: linear-gradient(90deg, #4a90d9, #7b68ee);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
            PolitiQuant
        </div>
        <div style='font-size:0.85rem; color:#888; margin-top:-4px;'>
            Political intelligence · Growth scoring · ML predictions · Portfolio tracking
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

st.caption(
    "Tracks U.S. House PTRs · Scores stocks across 22 factors · "
    "FinBERT news sentiment · Macro environment (FRED) · Free, runs locally."
)

# ── Session state ──────────────────────────────────────────────────────────────
for key, default in [
    ("trades", load_cache()),
    ("warnings", []),
    ("last_error", None),
    ("scan_running", False),
    ("scan_done", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── Email alert settings ───────────────────────────────────────────────────────
with st.expander("🔑 API Keys & AI Model — Finnhub, FRED, FinBERT", expanded=False):
    from macro_data import get_api_key as _get_fred_key, save_api_key as _save_api_key
    from finnhub_client import get_api_key as _get_fh_key, rate_limit_status as _fh_rate

    # FinBERT status row
    try:
        from finbert_sentiment import load_status as _fb_status
        fbs = _fb_status()
        if fbs["status"] == "ready":
            st.success("🧠 **FinBERT** loaded — full-sentence NLP sentiment active (running locally, free)")
        elif fbs["status"] == "error":
            st.error(f"🧠 FinBERT failed to load: {fbs['message']}")
        else:
            st.info("🧠 **FinBERT** loading in background… (~400 MB, first time only). "
                    "Falls back to keyword scoring until ready.")
    except Exception:
        pass

    st.divider()
    ak1, ak2 = st.columns(2)
    with ak1:
        st.markdown("**Finnhub** — more news articles + earnings surprise")
        st.caption(
            "Free key at [finnhub.io](https://finnhub.io) — replaces keyword matching "
            "with real NLP sentiment scores. Hard-capped at **55 calls/min** (free tier = 60)."
        )
        fh_key = st.text_input("Finnhub API Key", value=_get_fh_key(),
                                type="password", key="cfg_fh_key")
        if st.button("Save Finnhub key", key="save_fh"):
            _save_api_key(fh_key.strip(), "finnhub")
            st.success("✅ Finnhub key saved. Clear fundamentals cache to apply immediately.")
            # Show rate-limit status for reassurance
            rl = _fh_rate()
            st.caption(f"Rate limit window: {rl['calls_used_last_60s']}/{rl['hard_limit']} calls used in last 60 s")
    with ak2:
        st.markdown("**FRED** — macro environment scoring")
        st.caption("Free key at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) — adds yield curve, rate direction, inflation to scoring")
        fred_key = st.text_input("FRED API Key", value=_get_fred_key(),
                                  type="password", key="cfg_fred_key")
        if st.button("Save FRED key", key="save_fred"):
            _save_api_key(fred_key.strip(), "fred")
            from macro_data import fetch_macro
            with st.spinner("Fetching macro data…"):
                macro = fetch_macro(api_key=fred_key.strip())
            if macro.get("error"):
                st.error(f"FRED error: {macro['error']}")
            else:
                yc   = macro.get("yield_curve")
                ff   = macro.get("fed_funds_rate")
                cpi  = macro.get("cpi_yoy_pct")
                dir_ = macro.get("rate_direction", "")
                st.success(f"FRED connected — Yield curve: {yc:+.2f}%  |  Fed funds: {ff:.2f}%  |  CPI: {cpi:.1f}% YoY  |  Rates: {dir_}")

with st.expander("Email Alerts (optional)", expanded=False):
    ecfg = load_email_config()
    ea1, ea2, ea3 = st.columns(3)
    sender_email = ea1.text_input(
        "Your Gmail address (sender)",
        value=ecfg.get("sender_email", "sarathclaudemail2210@gmail.com"),
        placeholder="you@gmail.com",
        key="cfg_sender",
    )
    app_password = ea2.text_input(
        "Gmail App Password",
        value=ecfg.get("app_password", ""),
        type="password",
        placeholder="16-char app password",
        key="cfg_apppass",
    )
    recipient_email = ea3.text_input(
        "Alert recipient email",
        value=ecfg.get("recipient_email", "sarathai2210@gmail.com"),
        placeholder="recipient@gmail.com",
        key="cfg_recipient",
    )

    st.markdown("**🎯 Smart Alert Filters** — only send email when signals are truly high-value")
    sa1, sa2 = st.columns(2)
    with sa1:
        alert_min_score = st.slider(
            "Min Growth Score to trigger alert",
            min_value=40, max_value=90,
            value=int(ecfg.get("alert_min_score", 58)),
            step=1,
            help="Only send HIGH_SCORE alerts when a stock scores at least this value (0–100).",
            key="cfg_min_score",
        )
        alert_min_pols = st.selectbox(
            "Min politicians for consensus alert",
            options=[2, 3, 4, 5, 6],
            index=[2, 3, 4, 5, 6].index(int(ecfg.get("alert_min_pols", 3))),
            help="Trigger a CONSENSUS alert when this many politicians bought the same stock in 30 days.",
            key="cfg_min_pols",
        )
    with sa2:
        st.markdown("**Alert types to send:**")
        alert_consensus  = st.checkbox("🏛️ Consensus buys (3+ politicians)",
                                       value=ecfg.get("alert_consensus",  True), key="cfg_alert_consensus")
        alert_bipartisan = st.checkbox("🤝 Bipartisan buys (D + R on same stock)",
                                       value=ecfg.get("alert_bipartisan", True), key="cfg_alert_bipartisan")
        alert_high_score = st.checkbox("📈 High growth score",
                                       value=ecfg.get("alert_high_score", True), key="cfg_alert_high_score")
        alert_portfolio  = st.checkbox("💼 Your portfolio stocks",
                                       value=ecfg.get("alert_portfolio",  True), key="cfg_alert_portfolio")

    ea_col1, ea_col2, ea_col3 = st.columns([1, 1, 3])
    if ea_col1.button("Save settings", key="save_email_cfg"):
        save_email_config({
            "sender_email":    sender_email.strip(),
            "app_password":    app_password.strip(),
            "recipient_email": recipient_email.strip(),
            "alert_min_score": alert_min_score,
            "alert_min_pols":  alert_min_pols,
            "alert_consensus":  alert_consensus,
            "alert_bipartisan": alert_bipartisan,
            "alert_high_score": alert_high_score,
            "alert_portfolio":  alert_portfolio,
        })
        st.success("✅ Email settings saved.")

    if ea_col2.button("📧 Send test email", key="send_test_email"):
        if not sender_email.strip() or not app_password.strip() or not recipient_email.strip():
            st.error("Fill in sender email, app password, and recipient email first.")
        else:
            with st.spinner("Sending test email…"):
                try:
                    from emailer import _send
                    _send(
                        sender    = sender_email.strip(),
                        password  = app_password.strip(),
                        recipient = recipient_email.strip(),
                        subject   = "✅ PolitiQuant — Email alerts are working!",
                        html      = """
<html><body style="font-family:Arial,sans-serif; background:#0e1117; color:#e0e0e0;
                   padding:24px; max-width:600px; margin:0 auto;">
  <div style='display:flex; align-items:center; gap:12px; margin-bottom:16px;'>
    <span style='font-size:2rem;'>🏛️</span>
    <div>
      <div style='font-size:1.4rem; font-weight:800; color:#4a90d9;'>PolitiQuant</div>
      <div style='font-size:0.82rem; color:#888;'>Email alert test</div>
    </div>
  </div>
  <div style='background:#1a2a1a; border:1px solid #2ecc7155; border-radius:10px; padding:20px;'>
    <div style='font-size:1.2rem; font-weight:700; color:#2ecc71; margin-bottom:8px;'>
      ✅ Your email alerts are working!
    </div>
    <p style='color:#ccc; margin:0;'>
      When PolitiQuant detects high-signal political trades — consensus buys,
      bipartisan activity, high growth scores, or stocks you hold — it will
      send a rich alert email like this to <strong style='color:#4a90d9;'>{recipient}</strong>.
    </p>
  </div>
  <p style='color:#555; font-size:0.78rem; margin-top:20px;'>
    PolitiQuant · Running locally on your Mac · Data from U.S. House Clerk &amp; Senate STOCK Act filings.
  </p>
</body></html>""".format(recipient=recipient_email.strip()),
                    )
                    st.success(f"✅ Test email sent to **{recipient_email.strip()}** — check your inbox!")
                except Exception as e:
                    st.error(f"❌ Failed to send: {e}")
                    st.caption(
                        "Common fixes: make sure you're using a **Gmail App Password** "
                        "(not your real Gmail password). "
                        "Get one at: Google Account → Security → 2-Step Verification → App Passwords"
                    )

    ea_col3.caption(
        "ℹ️ Uses Gmail SMTP — free, no paid service. "
        "App Password ≠ your Gmail login password. "
        "Get one at: **Google Account → Security → 2-Step Verification → App Passwords**"
    )

# ── Controls row ───────────────────────────────────────────────────────────────
ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 1, 3])

with ctrl_col1:
    scan_clicked = st.button("Run Disclosure Scan Now", disabled=st.session_state.scan_running)

with ctrl_col2:
    days_back = st.selectbox(
        "Look back",
        options=[30, 60, 90, 180, 365, 500],
        index=0,
        format_func=lambda d: f"{d} days",
        label_visibility="collapsed",
    )

with ctrl_col3:
    cache_age = get_cache_age()
    if st.session_state.scan_running:
        st.info("Scan in progress — parsing PTR PDFs from the House Clerk…")
    elif st.session_state.scan_done and not st.session_state.last_error:
        st.success(f"Scan complete — {len(st.session_state.trades):,} total trades in cache.")
    elif cache_age and st.session_state.trades:
        st.info(f"Cached data (last fetched {cache_age}). Click the button to refresh.")
    elif not st.session_state.trades:
        st.warning("No data yet. Click **Run Disclosure Scan Now** to begin.")

# ── Trigger scan ───────────────────────────────────────────────────────────────
if scan_clicked and not st.session_state.scan_running:
    st.session_state.scan_running = True
    st.session_state.last_error = None
    st.session_state.warnings = []
    prev_doc_ids = {t.get("doc_id") for t in (st.session_state.trades or [])}

    with st.spinner("Fetching filing index and parsing PTR PDFs — this may take a few minutes on first run…"):
        try:
            trades, warnings = run_scan(years=[2024, 2025, 2026], days_back=days_back)
            st.session_state.trades = trades
            st.session_state.warnings = warnings
            st.session_state.scan_done = True

            # ── Smart email alerts for new trades ─────────────────────────────
            ecfg = load_email_config()
            if ecfg.get("sender_email") and ecfg.get("app_password") and ecfg.get("recipient_email"):
                new_trades_list = [t for t in trades if t.get("doc_id") not in prev_doc_ids]
                if new_trades_list:
                    try:
                        from smart_alerts import check_smart_alerts
                        # Load portfolio tickers for PORTFOLIO alert type
                        try:
                            import importlib.util as _ilu, os as _os
                            _pspec = _ilu.spec_from_file_location(
                                "_portfolio_page",
                                _os.path.join(_os.path.dirname(__file__), "pages", "4_My_Portfolio.py"),
                            )
                            _pmod = _ilu.module_from_spec(_pspec)
                            _pspec.loader.exec_module(_pmod)
                            _my_tickers = {r["Ticker"] for r in _pmod._DEFAULT if r.get("Ticker")}
                        except Exception:
                            _my_tickers = set()

                        events = check_smart_alerts(
                            new_trades      = new_trades_list,
                            all_trades      = trades,
                            my_tickers      = _my_tickers,
                            min_score       = float(ecfg.get("alert_min_score", 58)),
                            min_pols        = int(ecfg.get("alert_min_pols", 3)),
                            alert_on_score       = ecfg.get("alert_high_score",  True),
                            alert_on_consensus   = ecfg.get("alert_consensus",   True),
                            alert_on_portfolio   = ecfg.get("alert_portfolio",   True),
                            alert_on_bipartisan  = ecfg.get("alert_bipartisan",  True),
                        )

                        if events:
                            send_smart_alert(
                                sender_email    = ecfg["sender_email"],
                                app_password    = ecfg["app_password"],
                                recipient_email = ecfg["recipient_email"],
                                events          = events,
                            )
                            # Friendly summary of what was sent
                            _type_counts = {}
                            for _ev in events:
                                _type_counts[_ev["type"]] = _type_counts.get(_ev["type"], 0) + 1
                            _summary_parts = []
                            if _type_counts.get("CONSENSUS"):
                                _summary_parts.append(f"{_type_counts['CONSENSUS']} consensus")
                            if _type_counts.get("BIPARTISAN"):
                                _summary_parts.append(f"{_type_counts['BIPARTISAN']} bipartisan")
                            if _type_counts.get("HIGH_SCORE"):
                                _summary_parts.append(f"{_type_counts['HIGH_SCORE']} high-score")
                            if _type_counts.get("PORTFOLIO"):
                                _summary_parts.append(f"{_type_counts['PORTFOLIO']} portfolio")
                            _detail = " · ".join(_summary_parts) or f"{len(events)} signal(s)"
                            st.session_state.warnings.insert(
                                0,
                                f"📧 Smart alert sent — {len(events)} signal(s): {_detail} → {ecfg['recipient_email']}"
                            )
                        else:
                            # New trades arrived but nothing passed the smart filter threshold
                            n_new = len(new_trades_list)
                            st.session_state.warnings.append(
                                f"ℹ️ {n_new} new trade(s) found — no alerts triggered "
                                "(below consensus/score thresholds)."
                            )
                    except Exception as email_err:
                        st.session_state.warnings.append(f"Smart alert failed: {email_err}")

        except RuntimeError as e:
            st.session_state.last_error = str(e)
        finally:
            st.session_state.scan_running = False
    st.rerun()

if st.session_state.last_error:
    st.error(f"Scan failed — {st.session_state.last_error}")

for w in st.session_state.warnings:
    if w.startswith("📧"):
        st.success(w)
    else:
        st.warning(w)

# ── Filters ────────────────────────────────────────────────────────────────────
trades = st.session_state.trades

if trades:
    df = pd.DataFrame(trades)
    df["name"] = df["name"].fillna("").str.strip()
    df["ticker"] = df["ticker"].fillna("N/A").str.upper().str.strip()
    df["transaction_type"] = df["transaction_type"].fillna("").str.strip()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    df["disclosure_date"] = pd.to_datetime(df["disclosure_date"], errors="coerce")

    # ── Party enrichment (cached 7 days via party_lookup) ──────────────────────
    if "party" not in df.columns:
        with st.spinner("Looking up party affiliations…"):
            enriched_list = _enrich_trades(df.to_dict("records"))
            df = pd.DataFrame(enriched_list)
    if "party" not in df.columns:
        df["party"] = "?"
    else:
        df["party"] = df["party"].fillna("?")

    # Clean up garbage transaction types from PDF parse artifacts
    valid_tx = {"Purchase", "Sale", "Sale (Partial)", "Exchange"}
    df = df[df["transaction_type"].isin(valid_tx)]

    st.divider()
    with st.expander("Filters", expanded=True):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            name_filter = st.text_input(
                "Search politician name",
                placeholder='e.g. "Pelosi", "Trump"',
                key="filter_name",
            )
        with fc2:
            ticker_filter = st.text_input(
                "Search ticker symbol",
                placeholder='e.g. "NVDA", "AAPL"',
                key="filter_ticker",
            )
        with fc3:
            tx_types = ["All"] + sorted(df["transaction_type"].dropna().unique().tolist())
            type_filter = st.selectbox("Transaction Type", tx_types, key="filter_tx_type")

        fc4, fc5, fc6 = st.columns(3)
        with fc4:
            date_col_choice = st.radio(
                "Filter date by",
                ["Transaction Date", "Disclosure Date"],
                horizontal=True,
                key="filter_date_col",
            )
            _date_col = "transaction_date" if date_col_choice == "Transaction Date" else "disclosure_date"
            min_date = df[_date_col].min()
            max_date = df[_date_col].max()
            if pd.notna(min_date) and pd.notna(max_date):
                date_range = st.date_input(
                    f"{date_col_choice} range",
                    value=(min_date.date(), max_date.date()),
                    min_value=min_date.date(),
                    max_value=max_date.date(),
                    key="filter_date_range",
                )
            else:
                date_range = None
        with fc5:
            chambers = ["All", "House", "Senate"]
            chamber_filter = st.selectbox("Chamber", chambers, key="filter_chamber")
        with fc6:
            party_filter = st.selectbox(
                "Party",
                ["All", "🔵 Democrat", "🔴 Republican", "🟣 Independent"],
                key="filter_party",
            )

    # Apply filters
    mask = pd.Series([True] * len(df), index=df.index)
    if name_filter.strip():
        mask &= df["name"].str.contains(name_filter.strip(), case=False, na=False)
    if ticker_filter.strip():
        mask &= df["ticker"].str.contains(ticker_filter.strip().upper(), case=False, na=False)
    if type_filter != "All":
        mask &= df["transaction_type"] == type_filter
    if chamber_filter != "All":
        mask &= df["chamber"] == chamber_filter
    if party_filter != "All":
        _party_code = {"🔵 Democrat": "D", "🔴 Republican": "R", "🟣 Independent": "I"}.get(party_filter, "?")
        mask &= df["party"] == _party_code
    if date_range and len(date_range) == 2:
        start_d, end_d = date_range
        mask &= df[_date_col].dt.date >= start_d
        mask &= df[_date_col].dt.date <= end_d

    filtered = df[mask].copy()

    # Drop rows where transaction_date is in the future (filing data-entry errors)
    today = pd.Timestamp.today().normalize()
    bad = filtered["transaction_date"] > today
    if bad.any():
        st.warning(
            f"{bad.sum()} row(s) removed — transaction date is in the future, "
            "which means the filer entered the wrong year on the government form. "
            f"({', '.join(filtered.loc[bad, 'name'].unique())})"
        )
        filtered = filtered[~bad]

    # ── Metrics ────────────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Matching Trades", f"{len(filtered):,}")
    m2.metric("Unique Politicians", filtered["name"].nunique())
    m3.metric("Unique Tickers", filtered["ticker"].nunique())
    buys = filtered["transaction_type"].str.contains("Purchase", case=False, na=False).sum()
    sells = filtered["transaction_type"].str.contains("Sale", case=False, na=False).sum()
    m4.metric("Purchases / Sales", f"{buys:,} / {sells:,}")

    # ── Party comparison strip ─────────────────────────────────────────────────
    _d_mask = filtered["party"] == "D"
    _r_mask = filtered["party"] == "R"
    _d_trades = _d_mask.sum()
    _r_trades = _r_mask.sum()
    _d_pols   = filtered.loc[_d_mask, "name"].nunique()
    _r_pols   = filtered.loc[_r_mask, "name"].nunique()
    _d_purch  = filtered.loc[_d_mask & filtered["transaction_type"].str.contains("Purchase", case=False, na=False)].shape[0]
    _r_purch  = filtered.loc[_r_mask & filtered["transaction_type"].str.contains("Purchase", case=False, na=False)].shape[0]
    _d_pct    = f"{100*_d_purch/_d_trades:.0f}% buys" if _d_trades else "—"
    _r_pct    = f"{100*_r_purch/_r_trades:.0f}% buys" if _r_trades else "—"

    st.markdown(
        f"""<div style='display:flex; gap:16px; margin:8px 0 4px 0; flex-wrap:wrap;'>
        <div style='background:#3b82f622; border:1px solid #3b82f655; border-radius:8px;
                    padding:8px 18px; display:flex; align-items:center; gap:10px;'>
            <span style='font-size:1.5rem;'>🔵</span>
            <div>
                <div style='font-weight:700; color:#3b82f6; font-size:0.95rem;'>Democrats</div>
                <div style='font-size:0.8rem; color:#aaa;'>{_d_trades:,} trades · {_d_pols} politicians · {_d_pct}</div>
            </div>
        </div>
        <div style='background:#ef444422; border:1px solid #ef444455; border-radius:8px;
                    padding:8px 18px; display:flex; align-items:center; gap:10px;'>
            <span style='font-size:1.5rem;'>🔴</span>
            <div>
                <div style='font-weight:700; color:#ef4444; font-size:0.95rem;'>Republicans</div>
                <div style='font-size:0.8rem; color:#aaa;'>{_r_trades:,} trades · {_r_pols} politicians · {_r_pct}</div>
            </div>
        </div>
        </div>""",
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Politician profit leaderboard ─────────────────────────────────────────
    st.subheader("💰 Politician Profit Leaderboard")
    st.caption("Estimated gains/losses since each trade was disclosed. Based on trade amount ranges × price change.")

    def _amount_midpoint(amt_str: str) -> float:
        """Parse Robinhood-style amount range to midpoint in dollars."""
        if not amt_str:
            return 0
        s = str(amt_str).replace("$", "").replace(",", "").strip()
        if "over" in s.lower() or ">" in s:
            return 5_000_000
        parts = [p.strip() for p in s.replace("–", "-").split("-") if p.strip()]
        try:
            nums = [float(p) for p in parts if p.replace(".", "").isdigit()]
            if len(nums) == 2:
                return (nums[0] + nums[1]) / 2
            if len(nums) == 1:
                return nums[0]
        except Exception:
            pass
        return 8_000  # default midpoint for unknown ranges

    chart_col1, chart_col2 = st.columns([1, 4])
    with chart_col1:
        n_unique    = filtered["name"].nunique() if not filtered.empty else 50
        top_n       = st.slider("Show top N politicians", min_value=5, max_value=max(n_unique, 5), value=min(25, n_unique), step=5)
        show_all    = st.checkbox(f"Show all ({n_unique} politicians)", value=False)
        chart_tx    = st.radio("Transaction type", ["Purchases", "Sales", "Both"], index=0)
        sort_by     = st.radio("Sort by", ["Est. Profit $", "Avg % Return"], index=0)
        name_filter = st.text_input("Filter by name", placeholder="e.g. Pelosi", key="chart_name_filter")
        load_prices = st.button("Load / Refresh Prices", type="primary")

    with chart_col2:
        if "price_data" not in st.session_state:
            st.session_state.price_data = {}

        if load_prices:
            with st.spinner("Fetching current stock prices…"):
                try:
                    st.session_state.price_data = fetch_pct_changes(
                        filtered.to_dict("records"),
                        progress_callback=lambda m: None,
                    )
                except Exception as e:
                    st.error(f"Price fetch failed: {e}")

        price_data = st.session_state.price_data

        if not price_data:
            st.info("Click **Load / Refresh Prices** to see who profited most.")
        else:
            enriched = add_pct_change_to_df(filtered, price_data)
            enriched = enriched.dropna(subset=["pct_change"])

            if chart_tx == "Purchases":
                enriched = enriched[enriched["transaction_type"].str.contains("Purchase", case=False, na=False)]
            elif chart_tx == "Sales":
                enriched = enriched[enriched["transaction_type"].str.contains("Sale", case=False, na=False)]

            if name_filter.strip():
                enriched = enriched[enriched["name"].str.contains(name_filter.strip(), case=False, na=False)]

            if enriched.empty:
                st.info("No priced trades match the current filters.")
            else:
                # Compute estimated profit per trade
                enriched["amt_mid"]      = enriched["amount"].apply(_amount_midpoint)
                enriched["est_profit"]   = enriched["amt_mid"] * enriched["pct_change"] / 100

                # Aggregate per politician (include party — all rows for one politician share the same party)
                agg = enriched.groupby("name").agg(
                    party        =("party",       "first"),
                    trades       =("ticker",      "count"),
                    avg_pct      =("pct_change",  "mean"),
                    est_profit   =("est_profit",  "sum"),
                    best_ticker  =("ticker",      lambda x: x.iloc[enriched.loc[x.index, "pct_change"].values.argmax()]),
                    best_pct     =("pct_change",  "max"),
                    worst_pct    =("pct_change",  "min"),
                ).reset_index()

                sort_col = "est_profit" if sort_by == "Est. Profit $" else "avg_pct"
                if not show_all:
                    agg = agg.nlargest(top_n, sort_col)

                # ── Custom HTML bar chart — party emoji + green/red per bar ──
                max_val = agg[sort_col].abs().max() or 1
                bars_html = "<div style='display:flex; flex-direction:column; gap:5px; margin-bottom:16px;'>"
                for _, row in agg.sort_values(sort_col).iterrows():
                    val          = row[sort_col]
                    col          = "#2ecc71" if val >= 0 else "#e74c3c"
                    width        = int(abs(val) / max_val * 65)
                    label        = f"${val:+,.0f}" if sort_by == "Est. Profit $" else f"{val:+.1f}%"
                    party_emoji  = _PARTY_STYLE.get(row.get("party", "?"), _PARTY_STYLE["?"]).get("emoji", "⚪")
                    bars_html += (
                        f"<div style='display:flex; align-items:center; gap:8px;'>"
                        f"<div style='width:175px; text-align:right; font-size:0.8rem; color:#ccc; "
                        f"white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'>"
                        f"{party_emoji} {row['name']}</div>"
                        f"<div style='flex:1; background:#222; border-radius:3px; height:16px;'>"
                        f"<div style='width:{width}%; height:100%; background:{col}; border-radius:3px;'></div></div>"
                        f"<div style='width:90px; color:{col}; font-weight:bold; font-size:0.82rem;'>{label}</div>"
                        f"</div>"
                    )
                bars_html += "</div>"
                st.markdown(bars_html, unsafe_allow_html=True)

                # ── Summary table ─────────────────────────────────────────────
                table_rows = []
                for _, row in agg.sort_values(sort_col, ascending=False).iterrows():
                    _pstyle = _PARTY_STYLE.get(row.get("party", "?"), _PARTY_STYLE["?"])
                    table_rows.append({
                        "Party":           f"{_pstyle['emoji']} {_pstyle['label']}",
                        "Politician":      row["name"],
                        "Trades":          int(row["trades"]),
                        "Avg % Return":    f"{row['avg_pct']:+.1f}%",
                        "Est. Profit":     f"${row['est_profit']:+,.0f}",
                        "Best Trade":      f"{row['best_ticker']} ({row['best_pct']:+.1f}%)",
                        "Worst Trade %":   f"{row['worst_pct']:+.1f}%",
                    })
                st.dataframe(
                    pd.DataFrame(table_rows),
                    hide_index=True,
                    use_container_width=True,
                    height=min(500, top_n * 38 + 40),
                )

    st.divider()

    # ── Table ──────────────────────────────────────────────────────────────────
    table_search = st.text_input(
        "Search trades by politician name",
        placeholder='e.g. "Virginia Foxx", "Pelosi"',
        key="table_search",
    )

    col_map = {
        "party":            "Party",
        "name":             "Politician",
        "state":            "State/District",
        "ticker":           "Ticker",
        "asset_name":       "Asset Name",
        "transaction_type": "Transaction Type",
        "transaction_date": "Transaction Date",
        "disclosure_date":  "Disclosure Date",
        "amount":           "Amount Range",
    }
    existing_cols = [c for c in col_map if c in filtered.columns]
    show = filtered[existing_cols].rename(columns=col_map).copy()
    # Map raw party codes to readable labels in the table
    if "Party" in show.columns:
        show["Party"] = show["Party"].map(
            lambda p: f"{_PARTY_STYLE.get(p, _PARTY_STYLE['?'])['emoji']} {_PARTY_STYLE.get(p, _PARTY_STYLE['?'])['short']}"
        )
    show["Transaction Date"] = show["Transaction Date"].dt.strftime("%Y-%m-%d").fillna("")
    show["Disclosure Date"] = show["Disclosure Date"].dt.strftime("%Y-%m-%d").fillna("")
    show = show.sort_values("Transaction Date", ascending=False)

    if table_search.strip():
        show = show[show["Politician"].str.contains(table_search.strip(), case=False, na=False)]

    st.dataframe(show, use_container_width=True, height=580, hide_index=True)

    csv = show.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download filtered results as CSV",
        data=csv,
        file_name="political_disclosures.csv",
        mime="text/csv",
    )

else:
    st.divider()
    st.info("No trades loaded. Click **Run Disclosure Scan Now** to fetch disclosures from the House Clerk.")
