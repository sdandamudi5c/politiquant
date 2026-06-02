"""
PolitiQuant · Politicians
Cross-Reference + Politician Profile in one page.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from scraper import load_cache
from scorer import score_stock, signal_label
from job_state import JobState
from party_lookup import enrich_trades as _enrich_trades, PARTY_STYLE as _PARTY_STYLE, get_party, get_lookup, party_badge
from prices import fetch_pct_changes, add_pct_change_to_df
from sidebar_jobs import render as _render_sidebar

_render_sidebar()

st.set_page_config(
    page_title="PolitiQuant · Politicians",
    page_icon="🏛️",
    layout="wide",
)

st.title("🏛️ Politicians")

# Auto-switch to Profile view when arriving via a Profile → link
_auto_profile = bool(st.query_params.get("politician", ""))
_default_view_idx = 1 if _auto_profile else 0

_pol_view = st.radio(
    "", ["🔍 Cross Reference", "👤 Politician Profile"],
    horizontal=True, label_visibility="collapsed", key="pol_view",
    index=_default_view_idx,
)
st.divider()

_SHOWING_PROFILE = (_pol_view == "👤 Politician Profile")

if not _SHOWING_PROFILE:
    # ── CROSS REFERENCE ──────────────────────────────────────────────────────
    st.markdown("""
    <div style='display:flex; align-items:center; gap:10px; margin-bottom:4px;'>
        <span style='font-size:2rem;'>🔗</span>
        <div>
            <div style='font-size:1.6rem; font-weight:800;
                        background: linear-gradient(90deg, #4a90d9, #7b68ee);
                        -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
                Political Intelligence × Growth Scores
            </div>
            <div style='font-size:0.82rem; color:#888; margin-top:-2px;'>
                Stocks politicians are actively buying — ranked by growth potential
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.caption(
        "Cross-references House PTR disclosures with the 22-factor Growth Report. "
        "A stock bought by multiple politicians **and** scoring high = the strongest signal in PolitiQuant."
    )


    # ── Load political trades ──────────────────────────────────────────────────────
    trades_raw = load_cache()
    if not trades_raw:
        st.warning("No trade data. Go to the **Home** page and click **Run Disclosure Scan Now**.")
        st.stop()

    df_trades = pd.DataFrame(trades_raw)
    df_trades["name"]             = df_trades["name"].fillna("").str.strip()
    df_trades["ticker"]           = df_trades["ticker"].fillna("").str.upper().str.strip()
    df_trades["transaction_type"] = df_trades["transaction_type"].fillna("")
    df_trades["transaction_date"] = pd.to_datetime(df_trades["transaction_date"], errors="coerce")
    df_trades["amount"]           = df_trades["amount"].fillna("N/A")

    # Remove non-ticker rows
    df_trades = df_trades[~df_trades["ticker"].isin(["N/A", "--", ""])]
    df_trades = df_trades[df_trades["ticker"].str.match(r"^[A-Z]{1,5}$", na=False)]

    # Enrich with party data (7-day cache)
    if "party" not in df_trades.columns:
        with st.spinner("Loading party affiliations…"):
            df_trades = pd.DataFrame(_enrich_trades(df_trades.to_dict("records")))
    if "party" not in df_trades.columns:
        df_trades["party"] = "?"
    df_trades["party"] = df_trades["party"].fillna("?")


    # ── Load Growth Report scores ──────────────────────────────────────────────────
    @st.cache_data(ttl=300)
    def _load_scores() -> dict:
        """Returns {ticker: row_dict} from the latest Growth Report result."""
        result = JobState("growth_report").result()
        if result and result.get("rows"):
            return {r["ticker"]: r for r in result["rows"]}
        return {}

    @st.cache_data(ttl=3600)
    def _score_from_cache(ticker: str) -> dict | None:
        """Score a ticker from fundamentals cache when Growth Report hasn't run yet."""
        _DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_path = os.path.join(_DIR, "fundamentals_cache.json")
        try:
            with open(cache_path) as f:
                fc = json.load(f)
            fund = fc.get(ticker)
            if not fund or fund.get("error") or not fund.get("current_price"):
                return None
            score, reasons = score_stock(fund, pol_buys_30d=0)
            label, colour  = signal_label(score)
            return {
                "ticker":          ticker,
                "company":         fund.get("company_name", ticker),
                "sector":          fund.get("sector") or "—",
                "score":           round(score, 1),
                "signal":          label,
                "colour":          colour,
                "price":           fund.get("current_price"),
                "analyst_target":  fund.get("analyst_target"),
                "analyst_rating":  fund.get("analyst_rating") or "N/A",
                "market_cap":      fund.get("market_cap"),
                "reasons":         reasons,
            }
        except Exception:
            return None

    gr_scores = _load_scores()
    has_gr    = bool(gr_scores)


    # ── Controls ───────────────────────────────────────────────────────────────────
    st.divider()
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        days_back = st.selectbox(
            "Look back",
            [30, 60, 90, 180, 365],
            format_func=lambda d: f"Last {d} days",
            index=1,
        )
    with c2:
        min_pols = st.selectbox(
            "Min. politicians buying",
            [1, 2, 3, 5],
            index=0,
            format_func=lambda n: f"{n}+ politician{'s' if n > 1 else ''}",
        )
    with c3:
        min_score = st.selectbox(
            "Min. Growth Score",
            [0, 40, 58, 75],
            index=0,
            format_func=lambda s: {0: "Any score", 40: "40+ (WATCH)", 58: "58+ (BUY)", 75: "75+ (STRONG BUY)"}.get(s, str(s)),
        )
    with c4:
        party_filter = st.selectbox(
            "Party",
            ["All", "🔵 Democrat", "🔴 Republican"],
        )

    if not has_gr:
        st.info(
            "💡 **Tip:** Run the **Growth Report** first for full 22-factor scores. "
            "Showing scores from fundamentals cache where available."
        )


    # ── Filter to purchases in window ─────────────────────────────────────────────
    cutoff   = pd.Timestamp(date.today() - timedelta(days=days_back))
    purchases = df_trades[
        df_trades["transaction_type"].str.contains("Purchase", case=False, na=False) &
        (df_trades["transaction_date"] >= cutoff)
    ].copy()

    if party_filter != "All":
        _code = {"🔵 Democrat": "D", "🔴 Republican": "R"}.get(party_filter, "?")
        purchases = purchases[purchases["party"] == _code]

    if purchases.empty:
        st.warning(f"No purchases found in the last {days_back} days with current filters.")
        st.stop()


    # ── Aggregate by ticker ────────────────────────────────────────────────────────
    def _agg_ticker(group: pd.DataFrame) -> dict:
        unique_pols = group.drop_duplicates("name")
        d_count = (unique_pols["party"] == "D").sum()
        r_count = (unique_pols["party"] == "R").sum()
        buyers  = []
        for _, row in unique_pols.sort_values("transaction_date", ascending=False).iterrows():
            pstyle = _PARTY_STYLE.get(row["party"], _PARTY_STYLE["?"])
            buyers.append({
                "name":   row["name"],
                "party":  row["party"],
                "emoji":  pstyle["emoji"],
                "date":   row["transaction_date"].date() if pd.notna(row["transaction_date"]) else None,
                "amount": row.get("amount", "N/A"),
            })
        return {
            "ticker":       group.name,
            "pol_count":    unique_pols["name"].nunique(),
            "d_count":      int(d_count),
            "r_count":      int(r_count),
            "latest_buy":   group["transaction_date"].max(),
            "total_trades": len(group),
            "buyers":       buyers,
        }

    ticker_agg = (
        purchases.groupby("ticker")
        .apply(_agg_ticker, include_groups=False)
        .reset_index(drop=True)
    )
    ticker_agg = pd.DataFrame(ticker_agg.tolist())
    ticker_agg = ticker_agg[ticker_agg["pol_count"] >= min_pols]

    if ticker_agg.empty:
        st.warning(f"No tickers with {min_pols}+ politician buyer(s) in the last {days_back} days.")
        st.stop()


    # ── Attach Growth Report / fundamentals scores ─────────────────────────────────
    def _get_score_row(ticker: str) -> dict:
        if ticker in gr_scores:
            return gr_scores[ticker]
        return _score_from_cache(ticker) or {}

    score_rows = []
    for _, row in ticker_agg.iterrows():
        sr = _get_score_row(row["ticker"])
        score_rows.append({
            **row.to_dict(),
            "score":          sr.get("score"),
            "signal":         sr.get("signal", "N/A"),
            "colour":         sr.get("colour", "#888888"),
            "company":        sr.get("company", row["ticker"]),
            "sector":         sr.get("sector", "—"),
            "price":          sr.get("price"),
            "analyst_target": sr.get("analyst_target"),
            "analyst_rating": sr.get("analyst_rating", "N/A"),
            "market_cap":     sr.get("market_cap"),
            "reasons":        sr.get("reasons", []),
        })

    result_df = pd.DataFrame(score_rows)

    # Apply min score filter
    if min_score > 0:
        result_df = result_df[result_df["score"].notna() & (result_df["score"] >= min_score)]

    if result_df.empty:
        st.warning(f"No results match the current filters (score ≥ {min_score}).")
        st.stop()

    # Sort: by score desc, then by pol_count desc
    result_df = result_df.sort_values(
        ["score", "pol_count"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True)


    # ── Summary metrics ────────────────────────────────────────────────────────────
    strong_buys = result_df[result_df["signal"] == "STRONG BUY"].shape[0]
    buys        = result_df[result_df["signal"] == "BUY"].shape[0]
    total_pols  = purchases["name"].nunique()
    top_score   = result_df["score"].max() if result_df["score"].notna().any() else 0

    mm1, mm2, mm3, mm4 = st.columns(4)
    mm1.metric("Stocks w/ Political Activity", len(result_df))
    mm2.metric("Unique Politicians Buying",    total_pols)
    mm3.metric("STRONG BUY + BUY",            f"{strong_buys + buys}")
    mm4.metric("Highest Score",               f"{top_score:.0f}/100" if top_score else "—")

    st.divider()


    # ── Hot Picks section ──────────────────────────────────────────────────────────
    st.subheader("🔥 Hot Picks")
    st.caption("Highest-conviction signals — multiple politicians buying + high Growth Score.")

    hot = result_df[
        result_df["score"].notna() &
        (result_df["pol_count"] >= 2) &
        (result_df["score"] >= 50)
    ].head(8)

    if hot.empty:
        hot = result_df[result_df["score"].notna()].head(5)

    if not hot.empty:
        cols = st.columns(min(len(hot), 4))
        for i, (_, row) in enumerate(hot.iterrows()):
            col = cols[i % 4]
            score_val  = row["score"]
            colour     = row["colour"] or "#888"
            signal     = row["signal"] or "N/A"

            # Party pill row
            party_pills = ""
            if row["d_count"]:
                party_pills += f"<span style='background:#3b82f622; color:#3b82f6; border:1px solid #3b82f655; border-radius:3px; padding:1px 5px; font-size:0.7rem; font-weight:700; margin-right:4px;'>🔵 {row['d_count']}D</span>"
            if row["r_count"]:
                party_pills += f"<span style='background:#ef444422; color:#ef4444; border:1px solid #ef444455; border-radius:3px; padding:1px 5px; font-size:0.7rem; font-weight:700; margin-right:4px;'>🔴 {row['r_count']}R</span>"

            # Buyers list (max 3 names)
            buyer_names = ", ".join(
                f"{b['emoji']} {b['name'].split()[-1]}" for b in row["buyers"][:3]
            )
            if len(row["buyers"]) > 3:
                buyer_names += f" +{len(row['buyers'])-3} more"

            latest = row["latest_buy"].strftime("%b %d") if pd.notna(row["latest_buy"]) else "—"

            col.markdown(f"""
    <div style='background:#1a1a2e; border:1px solid {colour}55; border-radius:10px;
                padding:14px; margin-bottom:8px;'>
        <div style='display:flex; justify-content:space-between; align-items:flex-start;'>
            <div>
                <div style='font-size:1.3rem; font-weight:800; color:#fff;'>{row['ticker']}</div>
                <div style='font-size:0.72rem; color:#aaa; margin-top:1px; white-space:nowrap;
                            overflow:hidden; text-overflow:ellipsis; max-width:130px;'>{row['company']}</div>
            </div>
            <div style='text-align:right;'>
                <div style='font-size:1.4rem; font-weight:800; color:{colour};'>{score_val:.0f}</div>
                <div style='font-size:0.65rem; color:{colour}; font-weight:700;'>{signal}</div>
            </div>
        </div>
        <div style='margin-top:8px; font-size:0.75rem; color:#ccc;'>
            🏛️ <b>{row['pol_count']} politician{'s' if row['pol_count'] != 1 else ''}</b> buying
        </div>
        <div style='margin-top:3px;'>{party_pills}</div>
        <div style='margin-top:5px; font-size:0.72rem; color:#aaa;'>{buyer_names}</div>
        <div style='margin-top:4px; font-size:0.7rem; color:#666;'>Latest: {latest}</div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()


    # ── Full ranked table ──────────────────────────────────────────────────────────
    st.subheader("📊 All Politically-Active Stocks — Ranked by Score")

    _sector_opts = ["All"] + sorted(result_df["sector"].dropna().unique().tolist())
    fc1, fc2 = st.columns([2, 1])
    with fc1:
        _search = st.text_input("Search ticker or company", placeholder="e.g. NVDA, Apple", key="xref_search")
    with fc2:
        _sector = st.selectbox("Sector", _sector_opts, key="xref_sector")

    display_df = result_df.copy()
    if _search.strip():
        _s = _search.strip().upper()
        display_df = display_df[
            display_df["ticker"].str.contains(_s, case=False, na=False) |
            display_df["company"].str.contains(_search.strip(), case=False, na=False)
        ]
    if _sector != "All":
        display_df = display_df[display_df["sector"] == _sector]

    # Build HTML table
    def _score_badge(score, colour, signal):
        if score is None:
            return "<span style='color:#666;'>N/A</span>"
        return (
            f"<span style='background:{colour}22; color:{colour}; "
            f"border:1px solid {colour}55; border-radius:4px; "
            f"padding:2px 7px; font-weight:700; font-size:0.8rem;'>"
            f"{score:.0f} {signal}</span>"
        )

    def _party_bar(d, r):
        out = ""
        if d:
            out += f"<span style='color:#3b82f6; font-weight:700;'>🔵{d}D</span> "
        if r:
            out += f"<span style='color:#ef4444; font-weight:700;'>🔴{r}R</span>"
        return out or "⚪"

    table_rows = []
    for _, row in display_df.iterrows():
        latest = row["latest_buy"].strftime("%Y-%m-%d") if pd.notna(row["latest_buy"]) else "—"
        price_str  = f"${row['price']:.2f}"  if row.get("price")          else "—"
        target_str = f"${row['analyst_target']:.2f}" if row.get("analyst_target") else "—"
        upside = None
        if row.get("price") and row.get("analyst_target") and row["price"] > 0:
            upside = (row["analyst_target"] - row["price"]) / row["price"] * 100

        table_rows.append({
            "Ticker":       row["ticker"],
            "Company":      row["company"],
            "Sector":       row["sector"],
            "Score":        row["score"] if pd.notna(row.get("score")) else None,
            "Signal":       row["signal"] or "N/A",
            "Politicians":  row["pol_count"],
            "🔵D / 🔴R":   f"{row['d_count']}D / {row['r_count']}R",
            "Latest Buy":   latest,
            "Price":        price_str,
            "Target":       target_str,
            "Analyst Upside": f"{upside:+.0f}%" if upside is not None else "—",
            "Rating":       row["analyst_rating"],
        })

    st.dataframe(
        pd.DataFrame(table_rows),
        hide_index=True,
        use_container_width=True,
        height=min(600, len(table_rows) * 38 + 40),
        column_config={
            "Score": st.column_config.NumberColumn("Score", format="%.1f", min_value=0, max_value=100),
            "Politicians": st.column_config.NumberColumn("👥 Politicians", format="%.0f"),
        }
    )

    st.divider()


    # ── Buyer detail expander per ticker ──────────────────────────────────────────
    st.subheader("🏛️ Politician Activity by Stock")
    st.caption("Expand any ticker to see exactly who bought, when, and for how much.")

    for _, row in display_df.head(30).iterrows():
        score_str = f"{row['score']:.0f}/100" if pd.notna(row.get("score")) else "no score"
        with st.expander(
            f"**{row['ticker']}** — {row['company']}  ·  "
            f"{row['pol_count']} buyer{'s' if row['pol_count'] != 1 else ''}  ·  {score_str}",
            expanded=False,
        ):
            exp_c1, exp_c2 = st.columns([3, 2])

            with exp_c1:
                st.markdown("**Politician buyers:**")
                for b in row["buyers"]:
                    pstyle = _PARTY_STYLE.get(b["party"], _PARTY_STYLE["?"])
                    date_str = b["date"].strftime("%Y-%m-%d") if b["date"] else "—"
                    badge = (
                        f"<span style='background:{pstyle['colour']}22; color:{pstyle['colour']}; "
                        f"border:1px solid {pstyle['colour']}55; border-radius:3px; "
                        f"padding:1px 5px; font-size:0.72rem; font-weight:700;'>"
                        f"{pstyle['emoji']} {pstyle['short']}</span>"
                    )
                    b_col1, b_col2 = st.columns([4, 1])
                    b_col1.markdown(
                        f"{badge} &nbsp; **{b['name']}** &nbsp;&nbsp; "
                        f"<span style='color:#aaa; font-size:0.8rem;'>{date_str} · {b['amount']}</span>",
                        unsafe_allow_html=True,
                    )
                    b_col2.page_link(
                        "pages/5_Politicians.py",
                        label="Profile →",
                        help=f"View {b['name']}'s full profile",
                        query_params={"politician": b["name"]},
                    )

            with exp_c2:
                if row.get("reasons"):
                    st.markdown("**Growth Score reasons:**")
                    for reason in row["reasons"][:6]:
                        st.markdown(f"<div style='font-size:0.78rem; color:#ccc;'>{reason}</div>",
                                    unsafe_allow_html=True)
                else:
                    st.caption("Run Growth Report for detailed score breakdown.")


    # ── Download ──────────────────────────────────────────────────────────────────
    st.divider()
    csv_df = pd.DataFrame([{
        "Ticker":        r["ticker"],
        "Company":       r["company"],
        "Sector":        r["sector"],
        "Score":         r["score"],
        "Signal":        r["signal"],
        "Politicians":   r["pol_count"],
        "Democrats":     r["d_count"],
        "Republicans":   r["r_count"],
        "Latest_Buy":    r["latest_buy"].strftime("%Y-%m-%d") if pd.notna(r["latest_buy"]) else "",
        "Price":         r["price"],
        "Analyst_Target":r["analyst_target"],
        "Rating":        r["analyst_rating"],
    } for _, r in result_df.iterrows()])

    st.download_button(
        "⬇️ Download Cross-Reference as CSV",
        data=csv_df.to_csv(index=False).encode("utf-8"),
        file_name=f"politiquant_xref_{date.today()}.csv",
        mime="text/csv",
    )

    st.stop()

# ── POLITICIAN PROFILE ───────────────────────────────────────────────────────
# ── Load trades ────────────────────────────────────────────────────────────────
trades_raw = load_cache()
if not trades_raw:
    st.warning("No trade data. Go to the **Home** page and click **Run Disclosure Scan Now**.")
    st.stop()

df_all = pd.DataFrame(trades_raw)
df_all["name"]             = df_all["name"].fillna("").str.strip()
df_all["ticker"]           = df_all["ticker"].fillna("N/A").str.upper().str.strip()
df_all["transaction_type"] = df_all["transaction_type"].fillna("")
df_all["transaction_date"] = pd.to_datetime(df_all["transaction_date"], errors="coerce")
df_all["disclosure_date"]  = pd.to_datetime(df_all["disclosure_date"],  errors="coerce")
df_all["amount"]           = df_all["amount"].fillna("N/A")

valid_tx = {"Purchase", "Sale", "Sale (Partial)", "Exchange"}
df_all = df_all[df_all["transaction_type"].isin(valid_tx)]
df_all = df_all[df_all["transaction_date"] <= pd.Timestamp.today()]

# Load party lookup once
_lookup = get_lookup()


# ── Politician selector ────────────────────────────────────────────────────────
st.markdown("""
<div style='display:flex; align-items:center; gap:10px; margin-bottom:8px;'>
    <span style='font-size:2rem;'>👤</span>
    <div style='font-size:1.6rem; font-weight:800;
                background: linear-gradient(90deg, #4a90d9, #7b68ee);
                -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
        Politician Profile
    </div>
</div>
""", unsafe_allow_html=True)

all_names = sorted(df_all["name"].dropna().unique().tolist())
all_names = [n for n in all_names if n]

# Support pre-selection from URL query param (e.g. ?politician=Nancy+Pelosi)
_qp = st.query_params.get("politician", "")
_default_idx = all_names.index(_qp) if _qp in all_names else 0

selected = st.selectbox(
    "Select a politician",
    all_names,
    index=_default_idx,
    placeholder="Search by name…",
)

if not selected:
    st.info("Select a politician above to view their profile.")
    st.stop()


# ── Filter to selected politician ─────────────────────────────────────────────
df = df_all[df_all["name"] == selected].copy().sort_values("transaction_date", ascending=False)

if df.empty:
    st.warning(f"No trades found for {selected}.")
    st.stop()

party   = get_party(selected, _lookup)
pstyle  = _PARTY_STYLE.get(party, _PARTY_STYLE["?"])
chamber = df["chamber"].iloc[0] if "chamber" in df.columns else "N/A"
state   = df["state"].iloc[0]   if "state"   in df.columns else "N/A"


# ── Profile header ─────────────────────────────────────────────────────────────
col_head, col_stats = st.columns([2, 3])

with col_head:
    st.markdown(f"""
<div style='background:#1a1a2e; border:1px solid {pstyle["colour"]}55;
            border-radius:12px; padding:20px 24px; margin-top:4px;'>
    <div style='font-size:2rem;'>{pstyle["emoji"]}</div>
    <div style='font-size:1.5rem; font-weight:800; color:#fff; margin-top:4px;'>{selected}</div>
    <div style='margin-top:6px;'>
        <span style='background:{pstyle["colour"]}22; color:{pstyle["colour"]};
                     border:1px solid {pstyle["colour"]}55; border-radius:5px;
                     padding:3px 10px; font-size:0.85rem; font-weight:700;'>
            {pstyle["emoji"]} {pstyle["label"]}
        </span>
    </div>
    <div style='margin-top:10px; font-size:0.85rem; color:#aaa; line-height:1.7;'>
        🏛️ {chamber}&nbsp;&nbsp;|&nbsp;&nbsp;📍 {state}
    </div>
</div>
""", unsafe_allow_html=True)

with col_stats:
    purchases = df[df["transaction_type"].str.contains("Purchase", case=False, na=False)]
    sales     = df[df["transaction_type"].str.contains("Sale",     case=False, na=False)]
    tickers   = df[~df["ticker"].isin(["N/A", "--", ""])]["ticker"].nunique()
    date_from = df["transaction_date"].min()
    date_to   = df["transaction_date"].max()
    span_days = (date_to - date_from).days if pd.notna(date_from) and pd.notna(date_to) else 0

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Total Trades",      len(df))
    s2.metric("Purchases",         len(purchases))
    s3.metric("Sales",             len(sales))
    s4.metric("Unique Tickers",    tickers)

    s5, s6, s7, s8 = st.columns(4)
    s5.metric("Active Since",      date_from.strftime("%b %Y") if pd.notna(date_from) else "—")
    s6.metric("Most Recent Trade", date_to.strftime("%b %d, %Y") if pd.notna(date_to)   else "—")
    s7.metric("Data Span",         f"{span_days} days")
    buy_pct = f"{100*len(purchases)/len(df):.0f}%" if len(df) else "—"
    s8.metric("Buy Ratio",         buy_pct)

st.divider()


# ── Price performance ──────────────────────────────────────────────────────────
st.subheader("📈 Trade Performance")
st.caption("Estimated return from transaction date to today for each purchase. Based on closing prices.")

_pc_key  = f"price_data_{selected}"
if _pc_key not in st.session_state:
    st.session_state[_pc_key] = {}

# enriched is populated below inside 'if price_data:' — initialise here so tab2 never hits NameError
enriched = pd.DataFrame()

load_col, _ = st.columns([1, 3])
if load_col.button("Load / Refresh Prices", type="primary", key="load_prices_btn"):
    with st.spinner("Fetching price history…"):
        try:
            st.session_state[_pc_key] = fetch_pct_changes(
                purchases.to_dict("records"),
                progress_callback=lambda m: None,
            )
        except Exception as e:
            st.error(f"Price fetch error: {e}")

price_data = st.session_state[_pc_key]

if price_data:
    enriched = add_pct_change_to_df(purchases, price_data).dropna(subset=["pct_change"])

    if not enriched.empty:
        def _midpoint(amt_str):
            if not amt_str: return 0
            s = str(amt_str).replace("$","").replace(",","").strip()
            if "over" in s.lower() or ">" in s: return 5_000_000
            parts = [p.strip() for p in s.replace("–","-").split("-") if p.strip()]
            try:
                nums = [float(p) for p in parts if p.replace(".","").isdigit()]
                if len(nums) == 2: return (nums[0]+nums[1])/2
                if len(nums) == 1: return nums[0]
            except: pass
            return 8_000

        enriched["amt_mid"]    = enriched["amount"].apply(_midpoint)
        enriched["est_profit"] = enriched["amt_mid"] * enriched["pct_change"] / 100

        avg_ret    = enriched["pct_change"].mean()
        total_pnl  = enriched["est_profit"].sum()
        best_idx   = enriched["pct_change"].idxmax()
        worst_idx  = enriched["pct_change"].idxmin()
        best_row   = enriched.loc[best_idx]
        worst_row  = enriched.loc[worst_idx]

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Avg Return (Purchases)", f"{avg_ret:+.1f}%",
                  delta=f"{'▲' if avg_ret >= 0 else '▼'} vs 0%")
        p2.metric("Est. Total P&L",
                  f"${total_pnl:+,.0f}",
                  delta="Across all priced purchases")
        p3.metric("Best Trade",
                  f"{best_row['ticker']} ({best_row['pct_change']:+.1f}%)",
                  delta=f"{best_row['transaction_date'].strftime('%b %d, %Y')}")
        p4.metric("Worst Trade",
                  f"{worst_row['ticker']} ({worst_row['pct_change']:+.1f}%)",
                  delta=f"{worst_row['transaction_date'].strftime('%b %d, %Y')}")

        st.divider()

        # ── Return distribution bar chart ─────────────────────────────────────
        st.markdown("**Return per purchase — since trade date:**")
        chart_df = enriched[["ticker","transaction_date","pct_change","est_profit"]].copy()
        chart_df = chart_df.sort_values("pct_change", ascending=False)
        max_abs  = chart_df["pct_change"].abs().max() or 1

        bars = "<div style='display:flex; flex-direction:column; gap:4px; margin-bottom:12px;'>"
        for _, r in chart_df.iterrows():
            val   = r["pct_change"]
            col   = "#2ecc71" if val >= 0 else "#e74c3c"
            width = int(abs(val) / max_abs * 60)
            label = f"{val:+.1f}%"
            date_ = r["transaction_date"].strftime("%b %d '%y") if pd.notna(r["transaction_date"]) else ""
            bars += (
                f"<div style='display:flex; align-items:center; gap:8px;'>"
                f"<div style='width:60px; text-align:right; font-size:0.77rem; "
                f"color:#ccc; font-weight:700;'>{r['ticker']}</div>"
                f"<div style='width:70px; font-size:0.7rem; color:#777;'>{date_}</div>"
                f"<div style='flex:1; background:#222; border-radius:3px; height:14px;'>"
                f"<div style='width:{width}%; height:100%; background:{col}; border-radius:3px;'></div></div>"
                f"<div style='width:70px; color:{col}; font-weight:700; font-size:0.8rem;'>{label}</div>"
                f"</div>"
            )
        bars += "</div>"
        st.markdown(bars, unsafe_allow_html=True)

    else:
        st.info("No priced purchases found — the tickers may be delisted or unavailable.")

else:
    st.info("Click **Load / Refresh Prices** to see return performance.")

st.divider()


# ── Trade activity timeline ────────────────────────────────────────────────────
st.subheader("📅 Trade History")

tab1, tab2, tab3 = st.tabs(["All Trades", "Top Picks", "Portfolio Breakdown"])

with tab1:
    col_map = {
        "ticker":           "Ticker",
        "asset_name":       "Asset",
        "transaction_type": "Type",
        "transaction_date": "Trade Date",
        "disclosure_date":  "Disclosed",
        "amount":           "Amount Range",
    }
    existing = [c for c in col_map if c in df.columns]
    show = df[existing].rename(columns=col_map).copy()
    show["Trade Date"]   = show["Trade Date"].dt.strftime("%Y-%m-%d").fillna("")
    show["Disclosed"]    = show["Disclosed"].dt.strftime("%Y-%m-%d").fillna("") if "Disclosed" in show.columns else ""

    # Color-code type column
    _type_search = st.text_input("Filter by ticker", placeholder="e.g. NVDA", key="hist_search")
    if _type_search.strip():
        show = show[show["Ticker"].str.contains(_type_search.strip().upper(), case=False, na=False)]

    st.dataframe(show, hide_index=True, use_container_width=True, height=420)

with tab2:
    if price_data and "enriched" in dir() and not enriched.empty:
        st.markdown("**🏆 Best Performing Purchases** (by % return since trade date)")
        top5 = enriched.nlargest(10, "pct_change")[
            ["ticker","transaction_date","pct_change","est_profit","amount"]
        ].copy()
        top5["transaction_date"] = top5["transaction_date"].dt.strftime("%Y-%m-%d")
        top5.columns = ["Ticker","Trade Date","Return %","Est. Profit","Amount"]
        top5["Return %"]    = top5["Return %"].map(lambda x: f"{x:+.1f}%")
        top5["Est. Profit"] = top5["Est. Profit"].map(lambda x: f"${x:+,.0f}")
        st.dataframe(top5, hide_index=True, use_container_width=True)

        st.markdown("**📉 Worst Performing Purchases**")
        bot5 = enriched.nsmallest(10, "pct_change")[
            ["ticker","transaction_date","pct_change","est_profit","amount"]
        ].copy()
        bot5["transaction_date"] = bot5["transaction_date"].dt.strftime("%Y-%m-%d")
        bot5.columns = ["Ticker","Trade Date","Return %","Est. Loss","Amount"]
        bot5["Return %"]   = bot5["Return %"].map(lambda x: f"{x:+.1f}%")
        bot5["Est. Loss"]  = bot5["Est. Loss"].map(lambda x: f"${x:+,.0f}")
        st.dataframe(bot5, hide_index=True, use_container_width=True)

        # ── Ticker frequency ──────────────────────────────────────────────────
        st.markdown("**🔁 Most Traded Tickers (Purchases)**")
        freq = (
            purchases.groupby("ticker")
            .agg(times=("ticker","count"), latest=("transaction_date","max"))
            .sort_values("times", ascending=False)
            .head(15)
            .reset_index()
        )
        freq["latest"] = freq["latest"].dt.strftime("%Y-%m-%d")
        freq.columns   = ["Ticker","# Purchases","Latest Buy"]
        st.dataframe(freq, hide_index=True, use_container_width=True)
    else:
        st.info("Load prices (on the performance section above) to see best/worst picks.")

with tab3:
    # ── Sector breakdown ──────────────────────────────────────────────────────
    st.markdown("**Sector distribution of purchases:**")

    # Get sectors from fundamentals cache
    _fund_cache_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fundamentals_cache.json")
    _sectors = {}
    try:
        import json
        with open(_fund_cache_path) as _f:
            _fc = json.load(_f)
        for t in purchases["ticker"].unique():
            _fd = _fc.get(t, {})
            if _fd.get("sector"):
                _sectors[t] = _fd["sector"]
    except Exception:
        pass

    if _sectors:
        purch_tickers = purchases["ticker"].map(lambda t: _sectors.get(t, "Unknown"))
        sector_counts = purch_tickers.value_counts().reset_index()
        sector_counts.columns = ["Sector", "# Purchases"]

        max_s = sector_counts["# Purchases"].max() or 1
        s_html = "<div style='display:flex; flex-direction:column; gap:5px; margin-bottom:16px;'>"
        for _, r in sector_counts.iterrows():
            w = int(r["# Purchases"] / max_s * 70)
            s_html += (
                f"<div style='display:flex; align-items:center; gap:8px;'>"
                f"<div style='width:160px; text-align:right; font-size:0.8rem; color:#ccc;'>{r['Sector']}</div>"
                f"<div style='flex:1; background:#222; border-radius:3px; height:14px;'>"
                f"<div style='width:{w}%; height:100%; background:#7b68ee; border-radius:3px;'></div></div>"
                f"<div style='width:30px; color:#aaa; font-size:0.8rem;'>{r['# Purchases']}</div>"
                f"</div>"
            )
        s_html += "</div>"
        st.markdown(s_html, unsafe_allow_html=True)
    else:
        st.caption("Run Growth Report to populate sector data.")

    # ── Buy vs sell by year ───────────────────────────────────────────────────
    st.markdown("**Buy vs Sell activity by year:**")
    df_yr = df.copy()
    df_yr["year"] = df_yr["transaction_date"].dt.year
    df_yr["is_buy"]  = df_yr["transaction_type"].str.contains("Purchase", case=False, na=False)
    df_yr["is_sell"] = df_yr["transaction_type"].str.contains("Sale",     case=False, na=False)
    yr_agg = df_yr.groupby("year").agg(
        buys=("is_buy","sum"), sells=("is_sell","sum")
    ).reset_index().sort_values("year")
    yr_agg.columns = ["Year","Purchases","Sales"]
    st.dataframe(yr_agg, hide_index=True, use_container_width=True)

st.divider()


# ── Growth score cross-reference ───────────────────────────────────────────────
st.subheader("🔗 Cross-Reference with Growth Scores")
st.caption("How do this politician's purchase picks score on the 22-factor Growth Report?")

@st.cache_data(ttl=300)
def _gr_scores() -> dict:
    result = JobState("growth_report").result()
    if result and result.get("rows"):
        return {r["ticker"]: r for r in result["rows"]}
    return {}

gr = _gr_scores()
purch_tickers_list = [
    t for t in purchases["ticker"].unique() if t not in ("N/A","--","")
]

if gr:
    scored = [(t, gr[t]) for t in purch_tickers_list if t in gr]
    scored.sort(key=lambda x: x[1]["score"], reverse=True)

    if scored:
        xref_rows = []
        for ticker, sr in scored:
            last_buy = purchases[purchases["ticker"] == ticker]["transaction_date"].max()
            xref_rows.append({
                "Ticker":     ticker,
                "Company":    sr.get("company", ticker),
                "Score":      sr.get("score"),
                "Signal":     sr.get("signal","N/A"),
                "Price":      f"${sr['price']:.2f}" if sr.get("price") else "—",
                "Rating":     sr.get("analyst_rating","N/A"),
                "Last Bought":last_buy.strftime("%Y-%m-%d") if pd.notna(last_buy) else "—",
            })
        st.dataframe(
            pd.DataFrame(xref_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Score": st.column_config.NumberColumn("Score", format="%.1f"),
            },
        )
        scored_pct = len(scored) / len(purch_tickers_list) * 100 if purch_tickers_list else 0
        st.caption(f"{len(scored)}/{len(purch_tickers_list)} of {selected}'s purchased tickers have Growth Report scores ({scored_pct:.0f}% coverage)")
    else:
        st.info("None of this politician's purchased tickers appeared in the Growth Report. Run Growth Report with a broader universe.")
else:
    st.info("💡 Run the **Growth Report** to see how this politician's picks score.")


# ── Compare with other politicians ────────────────────────────────────────────
st.divider()
st.subheader("⚖️ Compare with Other Politicians")
st.caption(f"Stocks {selected} bought that other politicians also bought (last 90 days).")

cutoff_90 = pd.Timestamp(date.today() - timedelta(days=90))
recent_purch = purchases[purchases["transaction_date"] >= cutoff_90]["ticker"].unique()

if len(recent_purch) > 0:
    others = df_all[
        df_all["transaction_type"].str.contains("Purchase", case=False, na=False) &
        (df_all["transaction_date"] >= cutoff_90) &
        df_all["ticker"].isin(recent_purch) &
        (df_all["name"] != selected)
    ]
    if not others.empty:
        overlap = (
            others.groupby("ticker")["name"]
            .apply(lambda x: ", ".join(sorted(x.unique())))
            .reset_index()
        )
        overlap.columns = ["Ticker", "Also Bought By"]
        overlap["# Others"] = overlap["Also Bought By"].str.count(",") + 1
        overlap = overlap.sort_values("# Others", ascending=False)
        st.dataframe(overlap, hide_index=True, use_container_width=True)
    else:
        st.info(f"No other politicians bought the same stocks as {selected} in the last 90 days.")
else:
    st.info(f"No recent purchases found for {selected} in the last 90 days.")


# ── Download ──────────────────────────────────────────────────────────────────
st.divider()
col_map2 = {
    "ticker":           "Ticker",
    "asset_name":       "Asset",
    "transaction_type": "Type",
    "transaction_date": "Trade Date",
    "disclosure_date":  "Disclosed",
    "amount":           "Amount Range",
    "state":            "State",
    "chamber":          "Chamber",
}
dl_cols  = [c for c in col_map2 if c in df.columns]
dl_df    = df[dl_cols].rename(columns=col_map2).copy()
dl_df["Trade Date"]  = dl_df["Trade Date"].dt.strftime("%Y-%m-%d").fillna("")
if "Disclosed" in dl_df.columns:
    dl_df["Disclosed"] = dl_df["Disclosed"].dt.strftime("%Y-%m-%d").fillna("")

st.download_button(
    f"⬇️ Download {selected}'s trades as CSV",
    data=dl_df.to_csv(index=False).encode("utf-8"),
    file_name=f"politiquant_{selected.replace(' ','_')}.csv",
    mime="text/csv",
)
