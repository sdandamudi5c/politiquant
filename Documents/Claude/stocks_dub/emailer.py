"""
Email alerts via Gmail SMTP — free, no API key.
Credentials stored in email_config.json (gitignored).
"""

import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

CONFIG_FILE = "email_config.json"


def load_email_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_email_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def send_alert(
    sender_email: str,
    app_password: str,
    recipient_email: str,
    new_purchases: list,
) -> None:
    """Legacy basic alert — kept for backward compatibility."""
    if not new_purchases:
        return
    _send(sender_email, app_password, recipient_email,
          subject=f"[PolitiQuant] {len(new_purchases)} new political purchase(s) disclosed",
          html=_basic_html(new_purchases))


def send_smart_alert(
    sender_email: str,
    app_password: str,
    recipient_email: str,
    events: list,
) -> None:
    """
    Send a rich smart-alert email for high-signal events.
    Each event is a dict from smart_alerts.check_smart_alerts().
    """
    if not events:
        return

    type_labels = {
        "CONSENSUS":  "🏛️ Consensus Buy",
        "BIPARTISAN": "🤝 Bipartisan",
        "HIGH_SCORE": "📈 High Score",
        "PORTFOLIO":  "💼 Your Stock",
    }

    cards_html = ""
    for ev in events:
        ticker  = ev["ticker"]
        company = ev["company"] or ticker
        score   = ev.get("score")
        signal  = ev.get("signal", "N/A")
        colour  = ev.get("colour", "#4a90d9")
        t_label = type_labels.get(ev["type"], ev["type"])

        # Score badge
        score_badge = (
            f"<span style='background:{colour}22; color:{colour}; border:1px solid {colour}55; "
            f"border-radius:4px; padding:2px 8px; font-weight:700; font-size:0.85rem;'>"
            f"{score:.0f}/100 {signal}</span>"
        ) if score is not None else "<span style='color:#888;'>Score N/A</span>"

        # Party pills
        party_pills = ""
        if ev.get("d_count"):
            party_pills += (
                f"<span style='background:#3b82f622; color:#3b82f6; border:1px solid #3b82f655; "
                f"border-radius:3px; padding:1px 6px; font-size:0.75rem; font-weight:700; margin-right:4px;'>"
                f"🔵 {ev['d_count']}D</span>"
            )
        if ev.get("r_count"):
            party_pills += (
                f"<span style='background:#ef444422; color:#ef4444; border:1px solid #ef444455; "
                f"border-radius:3px; padding:1px 6px; font-size:0.75rem; font-weight:700;'>"
                f"🔴 {ev['r_count']}R</span>"
            )

        # All triggers for this ticker
        trigger_list = "".join(
            f"<li style='margin:2px 0; color:#ccc;'>{t['headline']}</li>"
            for t in ev.get("triggers", [])
        )

        # Buyer rows (max 6)
        buyer_rows = ""
        for b in ev.get("buyers", [])[:6]:
            buyer_rows += (
                f"<tr>"
                f"<td style='padding:5px 10px; color:{b['colour']}; font-weight:700;'>{b['emoji']} {b['label']}</td>"
                f"<td style='padding:5px 10px;'>{b['name']}</td>"
                f"<td style='padding:5px 10px; color:#aaa;'>{b['date']}</td>"
                f"<td style='padding:5px 10px; color:#aaa;'>{b['amount']}</td>"
                f"<td style='padding:5px 10px; color:#888; font-size:0.8rem;'>{b['chamber']}</td>"
                f"</tr>"
            )

        # Portfolio flag
        portfolio_flag = (
            "<div style='background:#7b68ee22; border:1px solid #7b68ee55; border-radius:4px; "
            "padding:4px 10px; font-size:0.8rem; color:#7b68ee; margin-top:6px; display:inline-block;'>"
            "💼 You hold this stock</div>"
        ) if ev.get("in_portfolio") else ""

        cards_html += f"""
<div style='background:#1a1a2e; border:1px solid {colour}44; border-radius:10px;
            padding:18px 20px; margin-bottom:16px;'>
  <div style='display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;'>
    <div>
      <span style='font-size:1.3rem; font-weight:800; color:#fff;'>{ticker}</span>
      &nbsp;
      <span style='color:#aaa; font-size:0.9rem;'>{company}</span>
    </div>
    <div style='display:flex; align-items:center; gap:8px; flex-wrap:wrap;'>
      <span style='background:#ffffff11; color:#ddd; border-radius:4px;
                   padding:2px 8px; font-size:0.78rem; font-weight:700;'>{t_label}</span>
      {score_badge}
    </div>
  </div>
  <div style='margin:8px 0;'>{party_pills}</div>
  <ul style='margin:8px 0; padding-left:18px; font-size:0.82rem;'>{trigger_list}</ul>
  {portfolio_flag}
  <table style='border-collapse:collapse; width:100%; margin-top:10px;
                background:#0e0e1a; border-radius:6px; overflow:hidden; font-size:0.82rem;'>
    <thead>
      <tr style='background:#2a3150;'>
        <th style='padding:6px 10px; text-align:left; color:#4a90d9;'>Party</th>
        <th style='padding:6px 10px; text-align:left; color:#4a90d9;'>Politician</th>
        <th style='padding:6px 10px; text-align:left; color:#4a90d9;'>Date</th>
        <th style='padding:6px 10px; text-align:left; color:#4a90d9;'>Amount</th>
        <th style='padding:6px 10px; text-align:left; color:#4a90d9;'>Chamber</th>
      </tr>
    </thead>
    <tbody style='color:#ddd;'>{buyer_rows}</tbody>
  </table>
</div>"""

    n_consensus  = sum(1 for e in events if e["type"] == "CONSENSUS")
    n_high_score = sum(1 for e in events if e["type"] == "HIGH_SCORE")
    n_portfolio  = sum(1 for e in events if e["in_portfolio"])
    summary_parts = []
    if n_consensus:  summary_parts.append(f"{n_consensus} consensus buy{'s' if n_consensus>1 else ''}")
    if n_high_score: summary_parts.append(f"{n_high_score} high-score pick{'s' if n_high_score>1 else ''}")
    if n_portfolio:  summary_parts.append(f"{n_portfolio} in your portfolio")
    summary = " · ".join(summary_parts) or f"{len(events)} alert(s)"

    subject = f"🏛️ PolitiQuant Alert — {len(events)} signal{'s' if len(events)>1 else ''}: {summary}"

    html = f"""
<html><body style="font-family:Arial,sans-serif; background:#0e1117; color:#e0e0e0;
                   padding:24px; max-width:700px; margin:0 auto;">
  <div style='display:flex; align-items:center; gap:10px; margin-bottom:4px;'>
    <span style='font-size:1.8rem;'>🏛️</span>
    <div>
      <div style='font-size:1.4rem; font-weight:800; color:#4a90d9;'>PolitiQuant Alert</div>
      <div style='font-size:0.82rem; color:#888;'>{summary}</div>
    </div>
  </div>
  <hr style='border:none; border-top:1px solid #333; margin:16px 0;'>
  {cards_html}
  <p style='color:#555; font-size:0.78rem; margin-top:24px;'>
    PolitiQuant — Political intelligence · Growth scoring · Running locally on your Mac.<br>
    Data from U.S. House Clerk &amp; Senate STOCK Act public filings.
  </p>
</body></html>"""

    _send(sender_email, app_password, recipient_email, subject=subject, html=html)


def _basic_html(trades: list) -> str:
    rows = ""
    for t in trades:
        col = "#2ecc71" if "purchase" in t.get("transaction_type", "").lower() else "#e74c3c"
        rows += (
            f"<tr>"
            f"<td style='padding:8px 12px; border-bottom:1px solid #333;'>{t.get('name','')}</td>"
            f"<td style='padding:8px 12px; border-bottom:1px solid #333; font-weight:bold;'>{t.get('ticker','')}</td>"
            f"<td style='padding:8px 12px; border-bottom:1px solid #333;'>{t.get('asset_name','')}</td>"
            f"<td style='padding:8px 12px; border-bottom:1px solid #333; color:{col};'>{t.get('transaction_type','')}</td>"
            f"<td style='padding:8px 12px; border-bottom:1px solid #333;'>{t.get('transaction_date','')}</td>"
            f"<td style='padding:8px 12px; border-bottom:1px solid #333;'>{t.get('amount','')}</td>"
            f"</tr>"
        )
    return f"""
<html><body style="font-family:Arial,sans-serif; background:#0e1117; color:#e0e0e0; padding:24px;">
  <h2 style="color:#4a90d9;">PolitiQuant — New Disclosures</h2>
  <table style="border-collapse:collapse; width:100%; background:#1e2130; border-radius:8px;">
    <thead><tr style="background:#2a3150;">
      <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Politician</th>
      <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Ticker</th>
      <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Asset</th>
      <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Type</th>
      <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Date</th>
      <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Amount</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body></html>"""


def _send(sender: str, password: str, recipient: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
