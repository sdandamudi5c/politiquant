"""
Live job status panel — call render() from any page to show it in the sidebar.
Shows all known background jobs with progress bars and elapsed time.
Also shows a live Finnhub API rate-limit gauge when a job is running.
"""

import os
from datetime import datetime

import streamlit as st

from job_state import JobState

_JOBS = {
    "📈 Growth Report":   "growth_report",
    "💼 Portfolio":        "portfolio",
}


def _elapsed(started_at: str) -> str:
    if not started_at:
        return ""
    try:
        dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        secs = int((datetime.now() - dt).total_seconds())
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"
    except Exception:
        return ""


def _render_macro():
    """Show live macro indicators in the sidebar if FRED key is configured."""
    try:
        from macro_data import fetch_macro
        macro = fetch_macro()
        if macro.get("error") or not macro.get("yield_curve"):
            return
        yc   = macro.get("yield_curve")
        ff   = macro.get("fed_funds_rate")
        cpi  = macro.get("cpi_yoy_pct")
        dir_ = macro.get("rate_direction", "")
        yc_col  = "#2ecc71" if yc and yc > 0.5 else "#e74c3c" if yc and yc < -0.3 else "#f39c12"
        dir_icon = "📉" if "cutting" in dir_ else "📈" if "hiking" in dir_ else "➡️"
        st.sidebar.markdown("---")
        st.sidebar.markdown("**🌍 Macro Environment**")
        st.sidebar.markdown(
            f"<div style='font-size:0.82rem; line-height:1.8;'>"
            f"Yield curve: <b style='color:{yc_col}'>{yc:+.2f}%</b><br>"
            f"Fed funds: <b>{ff:.2f}%</b> {dir_icon} {dir_.replace('_',' ')}<br>"
            f"CPI YoY: <b>{cpi:.1f}%</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def _render_finnhub_gauge():
    """
    Show a live Finnhub API rate-limit gauge in the sidebar.
    Only displayed when a Finnhub key is configured.
    """
    try:
        from finnhub_client import get_api_key, rate_limit_status
        if not get_api_key():
            return
        rl    = rate_limit_status()
        used  = rl["calls_used_last_60s"]
        limit = rl["hard_limit"]          # 55
        pct   = used / limit

        # Colour: green → yellow → red as we approach the cap
        if pct < 0.6:
            colour = "#2ecc71"   # green
            label  = "healthy"
        elif pct < 0.85:
            colour = "#f39c12"   # amber
            label  = "busy"
        else:
            colour = "#e74c3c"   # red
            label  = "near limit"

        st.sidebar.markdown("---")
        st.sidebar.markdown("**📡 Finnhub API**")
        st.sidebar.markdown(
            f"<div style='font-size:0.78rem; color:#aaa; margin-bottom:4px;'>"
            f"{used} / {limit} calls in last 60s "
            f"<span style='color:{colour};'>● {label}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        # Progress bar styled by colour
        st.sidebar.markdown(
            f"<div style='background:#2a2a2a; border-radius:4px; height:8px; "
            f"overflow:hidden; margin-bottom:2px;'>"
            f"<div style='width:{pct*100:.0f}%; background:{colour}; "
            f"height:100%; border-radius:4px; transition:width 0.5s;'></div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        remaining = limit - used
        st.sidebar.caption(f"{remaining} slots free · resets every 60 s · hard cap: {limit}/min")
    except Exception:
        pass


def render():
    """Render the job status panel in the sidebar. Call once per page."""
    _render_macro()
    any_shown = False
    any_running = False

    with st.sidebar:
        for label, job_id in _JOBS.items():
            js = JobState(job_id)

            if js.is_running():
                done, total, current = js.progress()
                _total_display = total if total else "?"
                pct      = min(1.0, done / max(total or 7250, 1))
                elapsed  = _elapsed(js.started_at())
                cancelling = js.is_cancelling()

                if not any_shown:
                    st.markdown("---")
                    st.markdown("**⚙️ Background Jobs**")
                    any_shown = True

                if not cancelling:
                    any_running = True

                # Phase label
                phase = "🔍 Filtering" if "[filtering]" in (current or "") else "📊 Scoring"
                clean = (current or "").replace("[filtering] ", "")

                if cancelling:
                    st.markdown(f"**{label}** — ⏳ Cancelling…")
                    st.progress(pct, text=f"Stopping after current batch…")
                    st.caption("Workers finishing their current stock then stopping")
                else:
                    # Job title + Stop button on same row
                    title_col, stop_col = st.columns([3, 1])
                    title_col.markdown(f"**{label}** — {phase}")
                    if stop_col.button("🛑", key=f"stop_{job_id}",
                                       help="Stop this job (saves results collected so far)"):
                        js.cancel()
                        st.rerun()

                    st.progress(pct, text=f"{done}/{_total_display} · {clean}")
                    st.caption(f"⏱ Running {elapsed}")

            elif js.was_cancelled():
                result    = js.result() or {}
                completed = js.completed_at()
                n = len(result.get("rows", [])) if isinstance(result, dict) else 0
                if not any_shown:
                    st.markdown("---")
                    st.markdown("**⚙️ Background Jobs**")
                    any_shown = True
                st.warning(f"🛑 {label}: stopped early · {n} results saved\n{completed}")

            elif js.is_done():
                completed = js.completed_at()
                result    = js.result()
                n = len(result) if isinstance(result, list) else (
                    len(result.get("rows", [])) if isinstance(result, dict) else "?")
                if not any_shown:
                    st.markdown("---")
                    st.markdown("**⚙️ Background Jobs**")
                    any_shown = True
                st.success(f"✅ {label}: {n} results\n{completed}")

    # Show Finnhub gauge whenever any job is running
    if any_running:
        _render_finnhub_gauge()
