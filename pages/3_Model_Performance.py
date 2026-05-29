import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import streamlit as st

from score_history import get_stats, update_outcomes, backfill_history, get_daily_picks
from sidebar_jobs import render as _render_sidebar
_render_sidebar()

st.set_page_config(page_title="PolitiQuant · Model Performance", page_icon="🧠", layout="wide")

st.title("🧠 Model Performance")
st.caption(
    "Tracks whether the Growth Score actually predicted real 30-day returns. "
    "The model learns from every outcome and improves its weight of each factor over time."
)

stats = get_stats()

# ── Refresh outcomes (manual — avoids slow check on every page load) ──────────
col_r1, col_r2 = st.columns([1, 5])
with col_r1:
    if st.button("🔄 Check outcomes", help="Resolve any stocks that hit their 30-day outcome date"):
        with st.spinner("Checking for new 30-day outcomes…"):
            new = update_outcomes()
        stats = get_stats()   # reload after update
        if new:
            st.success(f"Resolved {new} new outcome(s) — model updated.")
        else:
            st.info("No new outcomes ready yet.")

# ── Top-line status ───────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Stocks Tracked",    stats["n_total"])
c2.metric("Outcomes Resolved", stats["n_completed"])
c3.metric("Pending (< 30d)",   stats["n_pending"])

model = stats["model"]
if model:
    conf = "High" if model["n_training"] >= 500 else "Medium" if model["n_training"] >= 100 else "Early"
    if model.get("directional_acc"):
        c4.metric("Model Confidence", conf, f"Direction accuracy: {model['directional_acc']:.1f}%")
    else:
        c4.metric("Model Confidence", conf, f"R² = {model['r_squared']:.3f}")
else:
    needed = stats["needs_more"]
    c4.metric("Model Status", "Learning", f"{needed} more outcomes needed")

# ── Backfill from history ─────────────────────────────────────────────────────
with st.expander("⚡ Backfill training data from price history", expanded=stats["n_completed"] < stats["min_train"]):
    st.caption(
        "Instead of waiting 30 days per outcome, generate training records from the last "
        "12 months of historical prices — gives hundreds of resolved data points immediately."
    )
    ticker_input = st.text_input(
        "Tickers to backfill (comma-separated)",
        value="NVDA,GOOGL,MSFT,AMZN,TSLA,SOFI,NBIS,HIMS,SMCI,CMBT",
        help="Use your portfolio tickers or any stocks you care about.",
    )
    months_back = st.slider("How many months back", min_value=3, max_value=24, value=12)

    if st.button("Run Backfill", type="primary"):
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
        prog    = st.progress(0.0, text="Starting…")

        def _cb(done, total, ticker):
            pct = min(done / max(total, 1), 1.0)
            prog.progress(pct, text=f"Backfilling {ticker}… ({done}/{total})")

        added = backfill_history(tickers, months_back=months_back, progress_cb=_cb)
        prog.empty()
        st.success(f"Added {added} historical training records. Model retrained automatically.")
        st.rerun()

# ── Progress bar toward active model ─────────────────────────────────────────
if stats["n_completed"] < stats["min_train"]:
    st.info(
        f"Model activates after **{stats['min_train']} resolved outcomes** "
        f"(currently {stats['n_completed']}). "
        f"Use **Backfill** above to generate data from price history instantly."
    )
    pct = stats["n_completed"] / stats["min_train"]
    st.progress(pct, text=f"{stats['n_completed']} / {stats['min_train']} outcomes")

# ── Tier accuracy table ───────────────────────────────────────────────────────
if model and model.get("tier_stats"):
    st.divider()
    st.subheader("Signal Tier Accuracy")
    st.caption("Does a higher score actually lead to better returns?")

    TIER_ORDER  = ["STRONG BUY", "BUY", "WATCH", "NEUTRAL", "AVOID"]
    TIER_COLOUR = {
        "STRONG BUY": "#2ecc71", "BUY": "#27ae60",
        "WATCH": "#f39c12", "NEUTRAL": "#aaaaaa", "AVOID": "#e74c3c",
    }
    tier_stats = model["tier_stats"]

    # ── Summary bar chart (custom HTML so order + colour are correct) ─────────
    max_abs_ret = max(
        (abs(ts["avg_return"]) for ts in tier_stats.values()), default=1
    ) or 1

    bar_html = "<div style='display:flex; flex-direction:column; gap:6px; margin-bottom:16px;'>"
    for tier in TIER_ORDER:
        ts = tier_stats.get(tier)
        if not ts:
            continue
        col   = TIER_COLOUR[tier]
        ret   = ts["avg_return"]
        wr    = ts["win_rate"]
        n     = ts["count"]
        width = int(abs(ret) / max_abs_ret * 60)   # max 60% of container
        sign_col = "#2ecc71" if ret >= 0 else "#e74c3c"
        bar_html += (
            f"<div style='display:flex; align-items:center; gap:10px;'>"
            f"<div style='width:110px; text-align:right; color:{col}; font-weight:bold; font-size:0.85rem;'>{tier}</div>"
            f"<div style='flex:1; background:#222; border-radius:3px; height:18px; position:relative;'>"
            f"<div style='width:{width}%; height:100%; background:{sign_col}; border-radius:3px; opacity:0.85;'></div>"
            f"</div>"
            f"<div style='width:60px; color:{sign_col}; font-weight:bold; font-size:0.85rem;'>{ret:+.1f}%</div>"
            f"<div style='width:80px; color:#aaa; font-size:0.82rem;'>{wr:.0f}% win · {n}pts</div>"
            f"</div>"
        )
    bar_html += "</div>"
    st.markdown(bar_html, unsafe_allow_html=True)

    # ── Per-tier expandable stock list ────────────────────────────────────────
    for tier in TIER_ORDER:
        ts = tier_stats.get(tier)
        if not ts:
            continue
        col   = TIER_COLOUR[tier]
        label = (f"{tier} — avg {ts['avg_return']:+.1f}%  |  "
                 f"{ts['win_rate']:.0f}% win rate  |  "
                 f"{ts['count']} data points")
        with st.expander(label, expanded=False):
            stocks = ts.get("stocks", [])
            if not stocks:
                st.caption("No stocks in this tier.")
                continue
            rows = []
            for s in stocks:
                ret = s["return"]
                rows.append({
                    "Ticker": s["ticker"],
                    "Score":  f"{s['score']:.0f}",
                    "Scored": s["date"],
                    "30d Return": f"{ret:+.1f}%",
                    "Result": "✅" if ret > 0 else "❌",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

# ── Daily picks ───────────────────────────────────────────────────────────────
st.divider()
st.subheader("📅 Daily Signals")
st.caption("All stocks scored on the selected date, ranked by ML-predicted 30-day return.")

picks_data = get_daily_picks()
_all_dates = sorted(
    {r["scored_date"] for r in (stats.get("recent_outcomes") or [])}
    | ({picks_data["date"]} if picks_data["date"] else set()),
    reverse=True,
)
# Also include the most recent pending date if not already present
if picks_data["date"] and picks_data["date"] not in _all_dates:
    _all_dates = sorted([picks_data["date"]] + _all_dates, reverse=True)
available_dates = _all_dates or ([picks_data["date"]] if picks_data["date"] else [])

# Date selector — default to most recent
if available_dates:
    selected_date = st.selectbox("Scoring date", available_dates, index=0)
    if selected_date != picks_data["date"]:
        picks_data = get_daily_picks(selected_date)
else:
    selected_date = picks_data["date"]

picks = picks_data.get("stocks", [])

if not picks:
    st.info("No scored stocks found for this date. Run the Growth Report to generate signals.")
else:
    # Filter controls
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        search = st.text_input("Filter ticker", placeholder="e.g. NVDA", label_visibility="collapsed")
    with col_f2:
        sig_filter = st.multiselect(
            "Signal", ["STRONG BUY", "BUY", "WATCH", "NEUTRAL", "AVOID"],
            default=["STRONG BUY", "BUY", "WATCH"],
            label_visibility="collapsed",
        )
    with col_f3:
        ml_only = st.checkbox("ML prediction only", value=bool(model))

    filtered = picks
    if search:
        filtered = [p for p in filtered if search.upper() in p["ticker"]]
    if sig_filter:
        filtered = [p for p in filtered if p["signal"] in sig_filter]
    if ml_only:
        filtered = [p for p in filtered if p["predicted_return"] is not None]

    MAX_ROWS = 500
    if len(filtered) > MAX_ROWS:
        st.caption(f"Showing top {MAX_ROWS} of {len(filtered)} stocks (filtered from {len(picks)} total) scored on {selected_date}")
        filtered = filtered[:MAX_ROWS]
    else:
        st.caption(f"Showing {len(filtered)} of {len(picks)} stocks scored on {selected_date}")

    # Summary stat row
    if filtered:
        buys  = [p for p in filtered if p["signal"] in ("STRONG BUY", "BUY")]
        preds = [p["predicted_return"] for p in filtered if p["predicted_return"] is not None]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("BUY / STRONG BUY", len(buys))
        m2.metric("Total shown", len(filtered))
        if preds:
            m3.metric("Best ML prediction", f"{max(preds):+.1f}%")
            m4.metric("Avg ML prediction",  f"{sum(preds)/len(preds):+.1f}%")

    # Table
    rows = []
    for p in filtered:
        pred = p["predicted_return"]
        ci   = p["ci_95"]
        rows.append({
            "Ticker":        p["ticker"],
            "Score":         f"{p['score']:.0f}",
            "Signal":        p["signal"],
            "Price":         f"${p['price']:.2f}" if p["price"] else "N/A",
            "ML 30d Return": f"{pred:+.1f}% ± {ci:.1f}%" if pred is not None else "—",
            "Action":        (
                "🟢 BUY"     if p["signal"] in ("STRONG BUY", "BUY") else
                "🟡 WATCH"   if p["signal"] == "WATCH" else
                "⚪ HOLD"    if p["signal"] == "NEUTRAL" else
                "🔴 AVOID"
            ),
        })

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Signal":        st.column_config.TextColumn(width="medium"),
            "ML 30d Return": st.column_config.TextColumn(width="medium"),
            "Action":        st.column_config.TextColumn(width="small"),
        },
    )

# ── Factor importance ─────────────────────────────────────────────────────────
if model and stats["factor_importance"]:
    st.divider()
    st.subheader("What the Model Learned: Factor Importance")
    # New RF model uses feature importance (0–1); old Ridge used signed weights
    is_rf = model.get("engine") in ("random_forest", "gradient_boosting")
    if is_rf:
        st.caption(
            "Higher importance = this factor more strongly predicts 30-day returns. "
            "Based on Random Forest feature importance across all training outcomes."
        )
    else:
        st.caption(
            "Positive weight = factor predicts gains. Negative = factor predicts decline."
        )

    fi = stats["factor_importance"]
    fi_df = pd.DataFrame(fi).rename(columns={"factor": "Factor", "weight": "Learned Weight", "abs": "Importance"})

    max_abs = fi_df["Importance"].max() or 1.0
    cols = st.columns(2)
    for i, row in fi_df.iterrows():
        col   = cols[i % 2]
        w     = row["Learned Weight"]
        bar_pct = int(row["Importance"] / max_abs * 100)
        # RF importance is always positive; Ridge weights can be negative
        bar_col = "#4a90d9" if is_rf else ("#2ecc71" if w >= 0 else "#e74c3c")
        w_str   = f"{w:.3f}" if is_rf else f"{w:+.3f}"
        col.markdown(
            f"<div style='margin-bottom:6px;'>"
            f"<div style='display:flex; justify-content:space-between; font-size:0.85rem;'>"
            f"<span>{row['Factor']}</span>"
            f"<span style='color:{bar_col}; font-weight:bold;'>{w_str}</span>"
            f"</div>"
            f"<div style='background:#222; border-radius:3px; height:6px;'>"
            f"<div style='width:{bar_pct}%; height:100%; background:{bar_col};'></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )

    _dir = f" · Direction accuracy: {model['directional_acc']:.1f}%" if model.get("directional_acc") else f" · R² = {model['r_squared']:.3f}"
    _engine = model.get("engine", "ridge").replace("_", " ").title()
    st.caption(
        f"{_engine} · Trained on {model['n_training']} outcomes"
        f"{_dir} · Typical error ±{model['residual_std']:.1f}%"
    )

# ── Recent outcomes ───────────────────────────────────────────────────────────
if stats["recent_outcomes"]:
    st.divider()
    st.subheader("Recent Outcomes")

    rows = []
    for r in stats["recent_outcomes"]:
        actual = r["actual_return_pct"]
        score  = r.get("total_score") or 0
        if   score >= 65: tier = "STRONG BUY"
        elif score >= 50: tier = "BUY"
        elif score >= 35: tier = "WATCH"
        elif score >= 20: tier = "NEUTRAL"
        else:             tier = "AVOID"
        rows.append({
            "Ticker":        r["ticker"],
            "Scored":        r["scored_date"],
            "Score":         f"{score:.0f}",
            "Signal":        tier,
            "Price In":      f"${r['price_at_score']:.2f}" if r["price_at_score"] else "N/A",
            "Price Out":     f"${r['price_at_outcome']:.2f}" if r["price_at_outcome"] else "N/A",
            "Actual Return": f"{actual:+.1f}%" if actual is not None else "N/A",
            "Result":        "✅" if actual and actual > 0 else "❌",
            "Source":        "backfill" if r.get("backfilled") else "live",
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

elif stats["n_total"] == 0:
    st.info("No history yet. Run the **Growth Report** to start tracking outcomes.")
