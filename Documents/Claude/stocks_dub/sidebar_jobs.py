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


_STALE_MINUTES = 10   # job is considered stalled if no update in this many minutes


def _minutes_since(ts: str) -> "float | None":
    """Return minutes elapsed since a '%Y-%m-%d %H:%M:%S' timestamp, or None."""
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - dt).total_seconds() / 60
    except Exception:
        return None


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
                cancelling = js.is_cancelling()

                # ── Stale detection ────────────────────────────────────────────
                # A job is stale if:
                #   • We have a timestamp and it's >10 min old, OR
                #   • We have NO timestamps at all (old-style state written by
                #     code before the started_at/last_updated fields were added).
                #     Those jobs have no way to self-resolve — always stale.
                # Intentional cancelling is NOT stale (user asked for it to stop).
                _last_upd  = js.last_updated_at() or js.started_at()
                _idle_mins = _minutes_since(_last_upd)
                _no_ts     = not _last_upd          # truly unknown — old-style state
                _stale     = (_no_ts or (_idle_mins is not None
                              and _idle_mins > _stale_minutes))

                if not any_shown:
                    st.markdown("---")
                    st.markdown("**⚙️ Background Jobs**")
                    any_shown = True

                if _stale:
                    # Show an orange warning + Reset button instead of running state
                    st.warning(
                        f"⚠️ **{label}** — stalled?\n"
                        f"No activity for {_idle_mins:.0f} min (last: {current or '—'})"
                    )
                    if st.button("🔄 Reset job", key=f"reset_{job_id}",
                                 help="Clear the stale state so the job can be restarted"):
                        js.reset()
                        st.rerun()
                    continue   # don't fall through to the progress bar

                if not cancelling:
                    any_running = True

                # ── Progress bar ───────────────────────────────────────────────
                # total=0 → unknown (job started without calling js.start() — old code)
                _known_total   = total if total > 1 else None
                _total_display = str(_known_total) if _known_total else "?"

                # When total is unknown keep bar at ~60% — avoids the misleading
                # "100% red" look that happens when done > 7250 fallback.
                if _known_total:
                    pct = min(0.99, done / _known_total)
                else:
                    pct = 0.60   # indeterminate — we genuinely don't know progress

                elapsed = _elapsed(js.started_at())
                phase   = "🔍 Filtering" if "[filtering]" in (current or "") else "📊 Scoring"
                clean   = (current or "").replace("[filtering] ", "")

                if cancelling:
                    st.markdown(f"**{label}** — ⏳ Cancelling…")
                    st.progress(pct, text="Stopping after current batch…")
                    st.caption("Workers finishing their current stock then stopping")
                else:
                    title_col, stop_col = st.columns([3, 1])
                    title_col.markdown(f"**{label}** — {phase}")
                    if stop_col.button("🛑", key=f"stop_{job_id}",
                                       help="Stop this job (saves results collected so far)"):
                        js.cancel()
                        st.rerun()

                    st.progress(pct, text=f"{done}/{_total_display} · {clean}")
                    _elapsed_str = f" · {elapsed}" if elapsed else ""
                    st.caption(f"⏱ Running{_elapsed_str}")

            elif js.was_cancelled():
                result    = js.result() or {}
                completed = js.completed_at()
                n = len(result.get("rows", [])) if isinstance(result, dict) else 0
                if not any_shown:
                    st.markdown("---")
                    st.markdown("**⚙️ Background Jobs**")
                    any_shown = True
                col_msg, col_btn = st.columns([4, 1])
                col_msg.warning(f"🛑 **{label}**: stopped early · {n} results\n{completed}")
                if col_btn.button("✕", key=f"dismiss_{job_id}", help="Dismiss"):
                    js.reset()
                    st.rerun()

            elif js.is_done():
                completed = js.completed_at()
                result    = js.result()
                n = len(result) if isinstance(result, list) else (
                    len(result.get("rows", [])) if isinstance(result, dict) else "?")
                if not any_shown:
                    st.markdown("---")
                    st.markdown("**⚙️ Background Jobs**")
                    any_shown = True
                col_msg, col_btn = st.columns([4, 1])
                col_msg.success(f"✅ **{label}**: {n} results\n{completed}")
                if col_btn.button("✕", key=f"dismiss_{job_id}", help="Dismiss"):
                    js.reset()
                    st.rerun()

    # Show Finnhub gauge whenever any job is running
    if any_running:
        _render_finnhub_gauge()
