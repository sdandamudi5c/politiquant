"""
PolitiQuant — Daily Growth Report emailer.
Run by cron every day. Loads the last cached Growth Report result
and emails it as a PDF. No Streamlit required.

Usage (cron does this automatically):
    python3 /Users/meghnasarath/Documents/Claude/stocks_dub/send_daily_report.py
"""

import io
import json
import os
import smtplib
import sys
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Make sure project root is on path
_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)


def _load_email_config() -> dict:
    path = os.path.join(_DIR, "email_config.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_report_rows() -> list:
    """Load last Growth Report result from job_state cache."""
    try:
        from job_state import JobState
        result = JobState("growth_report").result()
        if result and result.get("rows"):
            return result["rows"]
    except Exception:
        pass
    return []


def _build_pdf(rows: list) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    def _sig_color(sig):
        return {
            "STRONG BUY": colors.HexColor("#2ecc71"),
            "BUY":        colors.HexColor("#27ae60"),
            "WATCH":      colors.HexColor("#f39c12"),
            "NEUTRAL":    colors.HexColor("#888888"),
            "AVOID":      colors.HexColor("#e74c3c"),
        }.get(sig, colors.HexColor("#888888"))

    def _upside_color(v):
        if v is None:
            return colors.HexColor("#888888")
        return colors.HexColor("#2ecc71") if v >= 0 else colors.HexColor("#e74c3c")

    def _fmt(v, suffix="", decimals=1):
        if v is None:
            return "—"
        try:
            return f"{float(v):+.{decimals}f}{suffix}"
        except Exception:
            return str(v)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm,  bottomMargin=1.5*cm,
    )

    title_s = ParagraphStyle("t",  fontSize=18, fontName="Helvetica-Bold",
                              textColor=colors.HexColor("#4a90d9"), spaceAfter=4)
    sub_s   = ParagraphStyle("s",  fontSize=9,  fontName="Helvetica",
                              textColor=colors.HexColor("#888888"), spaceAfter=8)
    cell_s  = ParagraphStyle("c",  fontSize=7.5, fontName="Helvetica",
                              textColor=colors.HexColor("#222222"))
    bold_s  = ParagraphStyle("cb", fontSize=7.5, fontName="Helvetica-Bold",
                              textColor=colors.HexColor("#1a5276"))
    foot_s  = ParagraphStyle("f",  fontSize=7,   fontName="Helvetica",
                              textColor=colors.HexColor("#aaaaaa"))

    sb = sum(1 for r in rows if r.get("signal") == "STRONG BUY")
    b  = sum(1 for r in rows if r.get("signal") == "BUY")
    w  = sum(1 for r in rows if r.get("signal") == "WATCH")

    story = [
        Paragraph("🚀 PolitiQuant — Daily Growth Report", title_s),
        Paragraph(
            f"{date.today().strftime('%B %d, %Y')}  ·  {len(rows)} stocks  ·  "
            f"{sb} Strong Buy  ·  {b} Buy  ·  {w} Watch",
            sub_s,
        ),
        HRFlowable(width="100%", thickness=0.5,
                   color=colors.HexColor("#cccccc"), spaceAfter=10),
    ]

    col_headers = [
        "Ticker", "Company", "Sector", "Score", "Signal",
        "Price", "Upside", "1M %", "3M %", "RSI", "Wall St", "Pol Buys",
    ]
    col_widths = [
        1.5*cm, 4.5*cm, 3.2*cm, 1.4*cm, 2.2*cm,
        1.5*cm, 1.5*cm, 1.4*cm, 1.4*cm, 1.2*cm, 2.2*cm, 1.5*cm,
    ]

    data = [col_headers]
    for r in rows:
        sig = r.get("signal", "—")
        data.append([
            Paragraph(r.get("ticker", ""),               bold_s),
            Paragraph((r.get("company") or "")[:32],     cell_s),
            Paragraph((r.get("sector")  or "—")[:20],    cell_s),
            Paragraph(f"{r.get('score', 0):.1f}",        bold_s),
            Paragraph(sig, ParagraphStyle(
                "sig", fontSize=7, fontName="Helvetica-Bold",
                textColor=_sig_color(sig))),
            Paragraph(f"${r['price']:.2f}" if r.get("price") else "—", cell_s),
            Paragraph(_fmt(r.get("upside_pct"), "%"),    ParagraphStyle(
                "up", fontSize=7.5, fontName="Helvetica",
                textColor=_upside_color(r.get("upside_pct")))),
            Paragraph(_fmt(r.get("mom_1m_pct"), "%"),    cell_s),
            Paragraph(_fmt(r.get("mom_3m_pct"), "%"),    cell_s),
            Paragraph(f"{r['rsi']:.0f}" if r.get("rsi") else "—", cell_s),
            Paragraph(r.get("analyst_rating") or "N/A",  cell_s),
            Paragraph(str(r.get("pol_buys_30d", 0)),     cell_s),
        ])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#2a3150")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.HexColor("#4a90d9")),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING",    (0, 0), (-1, 0), 6),
        ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
        ("TOPPADDING",    (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8ff")]),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story.append(tbl)
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "PolitiQuant · Political intelligence · Growth scoring · "
        "Not financial advice — use as a research starting point.",
        foot_s,
    ))

    doc.build(story)
    return buf.getvalue()


def send_daily_report():
    ecfg = _load_email_config()
    if not ecfg.get("sender_email") or not ecfg.get("app_password") or not ecfg.get("recipient_email"):
        print("ERROR: Email not configured in email_config.json")
        return False

    rows = _load_report_rows()
    if not rows:
        print("No Growth Report data available — run a report in the app first.")
        return False

    print(f"Building PDF for {len(rows)} stocks…")
    pdf_bytes = _build_pdf(rows)

    sb = sum(1 for r in rows if r.get("signal") == "STRONG BUY")
    b  = sum(1 for r in rows if r.get("signal") == "BUY")

    msg = MIMEMultipart()
    msg["Subject"] = (
        f"🚀 PolitiQuant Daily Report — "
        f"{date.today().strftime('%b %d, %Y')} · "
        f"{sb} Strong Buys · {b} Buys"
    )
    msg["From"] = ecfg["sender_email"]
    msg["To"]   = ecfg["recipient_email"]

    msg.attach(MIMEText(
        f"Hi,\n\nYour daily PolitiQuant Growth Report for "
        f"{date.today().strftime('%B %d, %Y')} is attached.\n\n"
        f"  • {len(rows)} stocks analysed\n"
        f"  • {sb} Strong Buy  |  {b} Buy\n\n"
        f"Not financial advice.\n\n— PolitiQuant",
        "plain",
    ))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    fname = f"PolitiQuant_DailyReport_{date.today()}.pdf"
    part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(ecfg["sender_email"], ecfg["app_password"])
        srv.sendmail(ecfg["sender_email"], ecfg["recipient_email"], msg.as_string())

    print(f"✅ Report sent to {ecfg['recipient_email']} ({len(pdf_bytes)//1024} KB)")
    return True


if __name__ == "__main__":
    success = send_daily_report()
    sys.exit(0 if success else 1)
