import pandas as pd
import streamlit as st

from emailer import load_email_config, save_email_config, send_alert
from prices import add_pct_change_to_df, fetch_pct_changes
from scraper import get_cache_age, load_cache, run_scan

st.set_page_config(
    page_title="Political Stock Disclosures",
    page_icon="📊",
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

st.title("Political Stock Disclosure Tracker")
st.caption(
    "Tracks U.S. House Periodic Transaction Reports (PTRs) from the official "
    "House Clerk public data — no API key required."
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
with st.expander("Email Alerts (optional)", expanded=False):
    ecfg = load_email_config()
    ea1, ea2, ea3 = st.columns(3)
    sender_email = ea1.text_input(
        "Your Gmail address (sender)",
        value=ecfg.get("sender_email", ""),
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
        value=ecfg.get("recipient_email", "call2sarath@gmail.com"),
        placeholder="recipient@gmail.com",
        key="cfg_recipient",
    )
    ea_col1, ea_col2 = st.columns([1, 4])
    if ea_col1.button("Save settings", key="save_email_cfg"):
        save_email_config({
            "sender_email":    sender_email.strip(),
            "app_password":    app_password.strip(),
            "recipient_email": recipient_email.strip(),
        })
        st.success("Email settings saved.")
    ea_col2.caption(
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

            # ── Email alert for new purchases ──────────────────────────────────
            ecfg = load_email_config()
            if ecfg.get("sender_email") and ecfg.get("app_password") and ecfg.get("recipient_email"):
                new_purchases = [
                    t for t in trades
                    if t.get("doc_id") not in prev_doc_ids
                    and "purchase" in t.get("transaction_type", "").lower()
                ]
                if new_purchases:
                    try:
                        send_alert(
                            sender_email=ecfg["sender_email"],
                            app_password=ecfg["app_password"],
                            recipient_email=ecfg["recipient_email"],
                            new_purchases=new_purchases,
                        )
                        st.session_state.warnings.insert(
                            0, f"📧 Email alert sent — {len(new_purchases)} new purchase(s) to {ecfg['recipient_email']}"
                        )
                    except Exception as email_err:
                        st.session_state.warnings.append(f"Email alert failed: {email_err}")

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

        fc4, fc5 = st.columns(2)
        with fc4:
            min_date = df["transaction_date"].min()
            max_date = df["transaction_date"].max()
            if pd.notna(min_date) and pd.notna(max_date):
                date_range = st.date_input(
                    "Transaction date range",
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
    if date_range and len(date_range) == 2:
        start_d, end_d = date_range
        mask &= df["transaction_date"].dt.date >= start_d
        mask &= df["transaction_date"].dt.date <= end_d

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

    st.divider()

    # ── Price % change chart ───────────────────────────────────────────────────
    st.subheader("Stock % Change Since Trade (by Politician)")

    chart_col1, chart_col2 = st.columns([1, 4])
    with chart_col1:
        top_n = st.slider("Show top N", min_value=5, max_value=100, value=15, step=5)
        chart_tx = st.radio("Transaction type", ["Purchases", "Sales", "Both"], index=0)
        chart_name_filter = st.text_input("Filter by politician name", placeholder="e.g. Pelosi", key="chart_name_filter")
        load_prices = st.button("Load / Refresh Prices")

    with chart_col2:
        if "price_data" not in st.session_state:
            st.session_state.price_data = {}

        if load_prices:
            with st.spinner("Fetching stock prices from Yahoo Finance (free)…"):
                try:
                    st.session_state.price_data = fetch_pct_changes(
                        filtered.to_dict("records"),
                        progress_callback=lambda m: None,
                    )
                except Exception as e:
                    st.error(f"Price fetch failed: {e}")

        price_data = st.session_state.price_data

        if price_data:
            enriched = add_pct_change_to_df(filtered, price_data)
            enriched = enriched.dropna(subset=["pct_change"])

            if chart_name_filter.strip():
                enriched = enriched[enriched["name"].str.contains(chart_name_filter.strip(), case=False, na=False)]

            if chart_tx == "Purchases":
                enriched = enriched[enriched["transaction_type"].str.contains("Purchase", case=False, na=False)]
            elif chart_tx == "Sales":
                enriched = enriched[enriched["transaction_type"].str.contains("Sale", case=False, na=False)]

            if enriched.empty:
                st.info("No priced trades match the current filters.")
            else:
                chart_df = (
                    enriched.groupby("name")["pct_change"]
                    .mean()
                    .reset_index(name="avg_pct_change")
                    .nlargest(top_n, "avg_pct_change")
                    .sort_values("avg_pct_change", ascending=True)
                )

                # Colour: green if gain, red if loss
                chart_df["color"] = chart_df["avg_pct_change"].apply(
                    lambda x: "#2ecc71" if x >= 0 else "#e74c3c"
                )

                st.caption(
                    "Shows the average % change in each stock from the politician's trade date to today. "
                    "Green = stock went up after the trade. Red = stock went down."
                )
                st.bar_chart(
                    chart_df.set_index("name")["avg_pct_change"],
                    horizontal=True,
                    height=max(300, top_n * 28),
                    color="#2ecc71",
                )

                # Summary table below chart — keep numeric so column sort works
                summary = chart_df[["name", "avg_pct_change"]].rename(
                    columns={"name": "Politician", "avg_pct_change": "Avg % Change Since Trade"}
                ).sort_values("Avg % Change Since Trade", ascending=False)
                st.dataframe(
                    summary,
                    hide_index=True,
                    use_container_width=True,
                    height=min(400, top_n * 38 + 40),
                    column_config={
                        "Avg % Change Since Trade": st.column_config.NumberColumn(
                            format="%.2f%%"
                        )
                    },
                )
        else:
            st.info("Click **Load / Refresh Prices** to fetch current stock prices from Yahoo Finance.")

    st.divider()

    # ── Table ──────────────────────────────────────────────────────────────────
    table_search = st.text_input(
        "Search trades by politician name",
        placeholder='e.g. "Virginia Foxx", "Pelosi"',
        key="table_search",
    )

    col_map = {
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
