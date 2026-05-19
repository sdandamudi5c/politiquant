import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import streamlit as st

from score_history import get_stats, update_outcomes

st.set_page_config(page_title="Model Performance", page_icon="🧠", layout="wide")

st.title("🧠 Model Performance")
st.caption(
    "Tracks whether the Growth Score actually predicted real 30-day returns. "
    "The model learns from every outcome and improves its weight of each factor over time."
)

# ── Refresh outcomes ──────────────────────────────────────────────────────────
with st.spinner("Checking for new 30-day outcomes…"):
    new = update_outcomes()
if new:
    st.success(f"Resolved {new} new outcome(s) — model updated.")

stats = get_stats()

# ── Top-line status ───────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Stocks Tracked",    stats["n_total"])
c2.metric("Outcomes Resolved", stats["n_completed"])
c3.metric("Pending (< 30d)",   stats["n_pending"])

model = stats["model"]
if model:
    conf = "High" if model["n_training"] >= 100 else "Medium" if model["n_training"] >= 50 else "Early"
    c4.metric("Model Confidence", conf, f"R² = {model['r_squared']:.3f}")
else:
    needed = stats["needs_more"]
    c4.metric("Model Status", "Learning", f"{needed} more outcomes needed")

# ── Progress bar toward active model ─────────────────────────────────────────
if stats["n_completed"] < stats["min_train"]:
    st.info(
        f"Model activates after **{stats['min_train']} resolved outcomes** "
        f"(currently {stats['n_completed']}). "
        f"Run the Growth Report daily — each stock scored today becomes a data point in 30 days."
    )
    pct = stats["n_completed"] / stats["min_train"]
    st.progress(pct, text=f"{stats['n_completed']} / {stats['min_train']} outcomes")

# ── Tier accuracy table ───────────────────────────────────────────────────────
if model and model.get("tier_stats"):
    st.divider()
    st.subheader("Signal Tier Accuracy")
    st.caption("Does a higher score actually lead to better returns? This table tells you.")

    tier_order  = ["STRONG BUY", "BUY", "WATCH", "NEUTRAL", "AVOID"]
    tier_colour = {
        "STRONG BUY": "#2ecc71", "BUY": "#27ae60",
        "WATCH": "#f39c12", "NEUTRAL": "#aaaaaa", "AVOID": "#e74c3c",
    }
    rows = []
    for tier in tier_order:
        ts = model["tier_stats"].get(tier)
        if ts:
            rows.append({
                "Signal":       tier,
                "# Stocks":     ts["count"],
                "Avg Return":   f"{ts['avg_return']:+.1f}%",
                "Win Rate":     f"{ts['win_rate']:.0f}%",
                "Best":         f"{ts['best']:+.1f}%",
                "Worst":        f"{ts['worst']:+.1f}%",
            })

    if rows:
        tier_df = pd.DataFrame(rows)

        # Colour-coded signal column via markdown
        def colour_signal(row):
            c = tier_colour.get(row["Signal"], "#aaa")
            return [f"color: {c}; font-weight:bold"] + [""] * (len(row) - 1)

        st.dataframe(
            tier_df,
            hide_index=True,
            use_container_width=True,
        )

        # Bar chart of avg return by tier
        chart_data = pd.DataFrame(rows)[["Signal", "Avg Return"]]
        chart_data["Avg Return"] = chart_data["Avg Return"].str.replace("%", "").str.replace("+", "").astype(float)
        chart_data = chart_data.set_index("Signal")
        st.bar_chart(chart_data, color="#4a90d9", height=220)

# ── Factor importance ─────────────────────────────────────────────────────────
if model and stats["factor_importance"]:
    st.divider()
    st.subheader("What the Model Learned: Factor Importance")
    st.caption(
        "Which factors actually predicted 30-day returns in your data. "
        "Positive weight = factor predicts gains. Negative = factor predicts decline."
    )

    fi = stats["factor_importance"]
    fi_df = pd.DataFrame(fi).rename(columns={"factor": "Factor", "weight": "Learned Weight", "abs": "Importance"})

    max_abs = fi_df["Importance"].max() or 1.0
    cols = st.columns(2)
    for i, row in fi_df.iterrows():
        col = cols[i % 2]
        w   = row["Learned Weight"]
        bar_pct = int(row["Importance"] / max_abs * 100)
        bar_col = "#2ecc71" if w >= 0 else "#e74c3c"
        col.markdown(
            f"<div style='margin-bottom:6px;'>"
            f"<div style='display:flex; justify-content:space-between; font-size:0.85rem;'>"
            f"<span>{row['Factor']}</span>"
            f"<span style='color:{bar_col}; font-weight:bold;'>{w:+.3f}</span>"
            f"</div>"
            f"<div style='background:#222; border-radius:3px; height:6px;'>"
            f"<div style='width:{bar_pct}%; height:100%; background:{bar_col};'></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )

    st.caption(
        f"Model trained on {model['n_training']} outcomes · "
        f"R² = {model['r_squared']:.3f} · "
        f"Typical prediction error ±{model['residual_std']:.1f}%"
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
            "Ticker":       r["ticker"],
            "Scored":       r["scored_date"],
            "Score":        f"{score:.0f}",
            "Signal":       tier,
            "Price In":     f"${r['price_at_score']:.2f}" if r["price_at_score"] else "N/A",
            "Price Out":    f"${r['price_at_outcome']:.2f}" if r["price_at_outcome"] else "N/A",
            "Actual Return": f"{actual:+.1f}%" if actual is not None else "N/A",
            "Result":       "✅" if actual and actual > 0 else "❌",
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

elif stats["n_total"] == 0:
    st.info("No history yet. Run the **Growth Report** to start tracking outcomes.")
