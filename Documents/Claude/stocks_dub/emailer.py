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
    new_purchases: list[dict],
) -> None:
    """
    Send a purchase-alert email via Gmail SMTP.
    Raises on failure so the caller can surface the error.
    """
    if not new_purchases:
        return

    subject = f"[Stock Alert] {len(new_purchases)} new political stock purchase(s) disclosed"

    # ── HTML body ──────────────────────────────────────────────────────────────
    rows_html = ""
    for t in new_purchases:
        tx_color = "#2ecc71" if "purchase" in t.get("transaction_type", "").lower() else "#e74c3c"
        rows_html += f"""
        <tr>
          <td style="padding:8px 12px; border-bottom:1px solid #333;">{t.get('name', '')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #333; font-weight:bold;">{t.get('ticker', '')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #333;">{t.get('asset_name', '')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #333; color:{tx_color};">{t.get('transaction_type', '')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #333;">{t.get('transaction_date', '')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #333;">{t.get('amount', '')}</td>
          <td style="padding:8px 12px; border-bottom:1px solid #333; color:#aaa;">{t.get('chamber', '')}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif; background:#0e1117; color:#e0e0e0; padding:24px;">
      <h2 style="color:#4a90d9; margin-bottom:4px;">Political Stock Alert</h2>
      <p style="color:#aaa; margin-top:0;">{len(new_purchases)} new disclosure(s) found in the latest scan.</p>
      <table style="border-collapse:collapse; width:100%; background:#1e2130; border-radius:8px; overflow:hidden;">
        <thead>
          <tr style="background:#2a3150;">
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Politician</th>
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Ticker</th>
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Asset</th>
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Type</th>
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Trade Date</th>
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Amount</th>
            <th style="padding:10px 12px; text-align:left; color:#4a90d9;">Chamber</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="color:#666; font-size:0.82rem; margin-top:20px;">
        Sent by your Political Stock Disclosure Tracker — data from U.S. House &amp; Senate public filings.
      </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender_email
    msg["To"]      = recipient_email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, app_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
