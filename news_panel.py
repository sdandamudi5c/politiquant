"""
Reusable Streamlit news panel backed by google_news.fetch_google_news.

Renders an opt-in "Load latest news" button and, once loaded, a sentiment badge
plus a list of escaped, clickable headlines.  ON-DEMAND only — it never fetches
until the user clicks, so it is safe to drop into pages that render many tickers
(portfolio, watchlist) without firing dozens of requests on load.

Pages own their own container (an expander, a card, …).  This renders the inner
content and exposes is_loaded() so the caller can keep its container OPEN across
the load rerun:

    import news_panel
    with st.expander("📰 Latest News (Google)",
                     expanded=news_panel.is_loaded(ticker, "fa")):
        news_panel.render(ticker, company, key_prefix="fa")

Why the on_click callback matters
---------------------------------
The "loaded" flag is set in the button's on_click callback, which Streamlit runs
*before* the script reruns.  That way is_loaded() is already True when the caller
creates its expander/card on the same rerun — otherwise the container would
collapse on the very click that loads the news (the flag would be set only after
the container was already drawn).
"""

import html

import streamlit as st


def _loaded_key(ticker: str, key_prefix: str) -> str:
    return f"{key_prefix}_gnews_loaded_{ticker}"


def _mark_loaded(lk: str):
    st.session_state[lk] = True


def is_loaded(ticker: str, key_prefix: str = "gnews") -> bool:
    """True once the user has loaded Google News for this ticker in this session."""
    return bool(st.session_state.get(_loaded_key(ticker, key_prefix)))


def render(ticker: str, company: str = None, key_prefix: str = "gnews",
           max_items: int = 20, intro: str = None):
    """
    Render the opt-in Google News panel for one ticker (inline — no container).

    key_prefix namespaces the button/flag so the same ticker can have an
    independent panel on different pages (e.g. "fa" vs "pf").
    """
    lk = _loaded_key(ticker, key_prefix)
    st.button(f"Load latest news for {ticker}",
              key=f"{key_prefix}_gnews_btn_{ticker}",
              on_click=_mark_loaded, args=(lk,))

    if not st.session_state.get(lk):
        st.caption(intro or "Click to load broader Google News coverage for this stock.")
        return

    with st.spinner(f"Fetching Google News for {ticker}…"):
        try:
            from google_news import fetch_google_news
            gn = fetch_google_news(ticker, company)
        except Exception as e:
            gn = {"items": [], "count": 0, "error": str(e), "sentiment": {}}

    if gn.get("error"):
        st.caption(f"News unavailable: {gn['error']}")
        return
    if not gn.get("items"):
        st.caption("No recent news found for this ticker.")
        return

    s    = gn.get("sentiment", {}) or {}
    sc   = s.get("sentiment_score", 0) or 0
    scol = "#2ecc71" if sc > 0.5 else "#e74c3c" if sc < -0.5 else "#888"
    slbl = "Bullish" if sc > 0.5 else "Bearish" if sc < -0.5 else "Neutral"
    st.markdown(
        f"<div style='font-size:0.9rem; margin-bottom:6px;'>"
        f"<b>{gn['count']}</b> headlines · sentiment "
        f"<b style='color:{scol}'>{slbl}</b> "
        f"<span style='color:#888'>({s.get('positive_count', 0)}▲ / "
        f"{s.get('negative_count', 0)}▼ · {s.get('method', '—')})</span></div>",
        unsafe_allow_html=True,
    )
    for it in gn["items"][:max_items]:
        # Escape HTML, then neutralise '$' so Streamlit doesn't render it as LaTeX.
        t   = html.escape(it["title"]).replace("$", "&#36;")
        src = html.escape(it["source"]).replace("$", "&#36;")
        u   = html.escape(it["url"], quote=True)
        st.markdown(
            f"<div style='margin:6px 0; font-size:0.88rem; line-height:1.35;'>"
            f"<a href='{u}' target='_blank' style='color:#4a90d9; "
            f"text-decoration:none;'>{t}</a><br>"
            f"<span style='color:#888; font-size:0.78rem;'>{src} · "
            f"{it['published']}</span></div>",
            unsafe_allow_html=True,
        )
