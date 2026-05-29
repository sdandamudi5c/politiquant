"""
Portfolio signal-change alerts — emails you when a stock in your portfolio
flips to STRONG BUY, drops to AVOID, or crosses other thresholds.

Compares the current Growth Score against a stored previous score to detect:
  • New STRONG BUY  (score crossed above 75)
  • New BUY         (score crossed above 58)
  • Dropped to AVOID (score fell below 25)
  • Earnings imminent (≤ 3 days away, not yet alerted today)
  • Volume surge (3× normal volume)

State is stored in portfolio_alert_state.json so we don't re-send the same alert.
"""

import json
import os
from datetime import datetime, date

_DIR        = os.path.dirname(os.path.abspath(__file__))
_STATE_FILE = os.path.join(_DIR, "portfolio_alert_state.json")


def _load_state() -> dict:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_portfolio_alerts(results: list[dict]) -> list[dict]:
    """
    Compare current scores/signals against stored previous state.
    Returns a list of alert dicts for any meaningful changes.

    Each alert dict:
    {
        "ticker":  str,
        "company": str,
        "type":    str,   # "STRONG_BUY" | "BUY" | "AVOID" | "EARNINGS" | "VOLUME"
        "message": str,
        "score":   float,
        "signal":  str,
        "price":   float | None,
    }
    """
    state  = _load_state()
    today  = date.today().isoformat()
    alerts = []

    for r in results:
        tk      = r.get("ticker", "")
        score   = float(r.get("total_score") or 0)
        signal  = r.get("signal", "")
        price   = r.get("current_price")
        company = r.get("company", tk)
        earn_d  = r.get("earnings_days_until")
        vol_r   = float(r.get("volume_ratio") or 0)

        prev = state.get(tk, {})
        prev_score  = float(prev.get("score", 50))
        prev_signal = prev.get("signal", "")
        alerted_earn_date = prev.get("alerted_earn_date", "")

        # ── Signal upgrades / downgrades ──────────────────────────────────────
        if signal == "STRONG BUY" and prev_signal not in ("STRONG BUY",):
            alerts.append({
                "ticker": tk, "company": company, "type": "STRONG_BUY",
                "message": f"🚀 {tk} just hit STRONG BUY (score {score:.0f})",
                "score": score, "signal": signal, "price": price,
            })
        elif signal == "BUY" and prev_signal in ("WATCH", "NEUTRAL", "AVOID", ""):
            alerts.append({
                "ticker": tk, "company": company, "type": "BUY",
                "message": f"📈 {tk} upgraded to BUY (score {score:.0f})",
                "score": score, "signal": signal, "price": price,
            })
        elif signal == "AVOID" and prev_signal not in ("AVOID", ""):
            alerts.append({
                "ticker": tk, "company": company, "type": "AVOID",
                "message": f"🔴 {tk} dropped to AVOID (score {score:.0f}) — consider exiting",
                "score": score, "signal": signal, "price": price,
            })

        # ── Earnings imminent ─────────────────────────────────────────────────
        if earn_d is not None and earn_d <= 3 and alerted_earn_date != today:
            alerts.append({
                "ticker": tk, "company": company, "type": "EARNINGS",
                "message": f"📅 {tk} reports earnings in {earn_d} day(s)!",
                "score": score, "signal": signal, "price": price,
            })
            state.setdefault(tk, {})["alerted_earn_date"] = today

        # ── Volume surge ──────────────────────────────────────────────────────
        if vol_r >= 3.0 and prev.get("alerted_vol_date") != today:
            day_chg = r.get("today_change_pct") or 0
            direction = "📈 UP" if day_chg > 0 else "📉 DOWN"
            alerts.append({
                "ticker": tk, "company": company, "type": "VOLUME",
                "message": f"🔊 {tk} has {vol_r:.1f}× normal volume today ({direction} {day_chg:+.1f}%)",
                "score": score, "signal": signal, "price": price,
            })
            state.setdefault(tk, {})["alerted_vol_date"] = today

        # Update stored state
        state[tk] = {**state.get(tk, {}), "score": score, "signal": signal,
                     "last_checked": today}

    _save_state(state)
    return alerts


def send_portfolio_alert_email(alerts: list[dict]) -> bool:
    """
    Send email with all portfolio alerts. Returns True on success.
    """
    if not alerts:
        return True
    try:
        from emailer import load_email_config, _send
        cfg = load_email_config()
        if not cfg.get("sender_email") or not cfg.get("app_password"):
            return False

        today_str = datetime.now().strftime("%B %d, %Y")

        TYPE_COLOUR = {
            "STRONG_BUY": "#2ecc71",
            "BUY":        "#27ae60",
            "AVOID":      "#e74c3c",
            "EARNINGS":   "#f39c12",
            "VOLUME":     "#3498db",
        }

        rows_html = ""
        for a in alerts:
            col = TYPE_COLOUR.get(a["type"], "#aaa")
            price_str = f"${a['price']:.2f}" if a["price"] else "N/A"
            rows_html += f"""
            <tr>
              <td style="padding:10px 14px; border-bottom:1px solid #333;">
                <span style="font-size:1.05rem; font-weight:800; color:#fff;">{a['ticker']}</span>
                <span style="color:#888; font-size:0.82rem; margin-left:8px;">{a['company'][:35]}</span>
              </td>
              <td style="padding:10px 14px; border-bottom:1px solid #333;
                          color:{col}; font-weight:700;">{a['message']}</td>
              <td style="padding:10px 14px; border-bottom:1px solid #333;
                          color:#aaa; font-size:0.85rem;">{price_str}</td>
            </tr>"""

        html = f"""
        <html><body style="background:#0e1117; color:#fff;
               font-family:Arial,sans-serif; padding:20px;">
          <h2 style="color:#4a90d9;">📊 PolitiQuant Portfolio Alerts — {today_str}</h2>
          <p style="color:#888;">{len(alerts)} alert(s) on your holdings:</p>
          <table style="width:100%; border-collapse:collapse; background:#1a1a2e;
                         border-radius:8px; overflow:hidden;">
            <thead>
              <tr style="background:#222;">
                <th style="padding:10px 14px; text-align:left; color:#aaa;">Stock</th>
                <th style="padding:10px 14px; text-align:left; color:#aaa;">Alert</th>
                <th style="padding:10px 14px; text-align:left; color:#aaa;">Price</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
          <p style="color:#555; font-size:0.75rem; margin-top:20px;">
            PolitiQuant — Not financial advice. For informational purposes only.
          </p>
        </body></html>"""

        _send(
            cfg["sender_email"], cfg["app_password"], cfg["recipient_email"],
            subject=f"[PolitiQuant] {len(alerts)} portfolio alert(s) — {today_str}",
            html=html,
        )
        return True
    except Exception:
        return False
