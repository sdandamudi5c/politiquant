"""
Single-stock PDF report generator using reportlab.
"""

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from fundamentals import fmt_large, fmt_pct, fmt_ratio, recommend, screen

# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG        = colors.HexColor("#0e1117")
C_CARD      = colors.HexColor("#1e2130")
C_GREEN     = colors.HexColor("#2ecc71")
C_RED       = colors.HexColor("#e74c3c")
C_ORANGE    = colors.HexColor("#f39c12")
C_BLUE      = colors.HexColor("#4a90d9")
C_WHITE     = colors.white
C_GREY      = colors.HexColor("#aaaaaa")
C_DARKGREY  = colors.HexColor("#333333")


def _signal_color(signal: str) -> colors.Color:
    if "BUY" in signal:   return C_GREEN
    if signal == "SELL":  return C_RED
    return C_ORANGE


def _score_color(score: float) -> colors.Color:
    if score >= 65: return C_GREEN
    if score >= 50: return colors.HexColor("#27ae60")
    if score >= 35: return C_ORANGE
    return C_RED


def _val(v, fmt="str"):
    if v is None: return "N/A"
    try:
        if fmt == "pct":   return f"{float(v):+.1f}%"
        if fmt == "price": return f"${float(v):.2f}"
        if fmt == "ratio": return f"{float(v):.2f}x"
        if fmt == "int":   return str(int(v))
        return str(v)
    except Exception:
        return "N/A"


def generate_stock_pdf(fund: dict, score: float | None = None, reasons: list[str] | None = None) -> bytes:
    """
    Generate a comprehensive single-stock PDF report.
    Returns raw PDF bytes suitable for st.download_button.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    W = A4[0] - 30 * mm  # usable width

    def style(name="Normal", **kw):
        base = styles[name]
        s = ParagraphStyle(name + "_custom", parent=base, **kw)
        return s

    H1  = style("Heading1", fontSize=22, textColor=C_WHITE,    spaceAfter=2)
    H2  = style("Heading2", fontSize=13, textColor=C_BLUE,     spaceAfter=3, spaceBefore=8)
    H3  = style("Heading3", fontSize=10, textColor=C_GREY,     spaceAfter=2)
    NRM = style("Normal",   fontSize=9,  textColor=C_WHITE,    leading=14)
    SML = style("Normal",   fontSize=8,  textColor=C_GREY,     leading=12)
    BIG = style("Normal",   fontSize=32, textColor=C_WHITE,    fontName="Helvetica-Bold")
    CAP = style("Normal",   fontSize=7,  textColor=C_GREY,     leading=10)

    signal, sig_colour, auto_reasons = recommend(fund)
    criteria = screen(fund)
    if reasons is None:
        reasons = auto_reasons
    if score is None:
        passes = sum(1 for v in criteria.values() if v is True)
        score = passes / len(criteria) * 100

    sig_color = _signal_color(signal)

    ticker  = fund.get("ticker", "")
    company = fund.get("company_name", ticker)
    sector  = fund.get("sector") or "N/A"
    price   = fund.get("current_price")
    target  = fund.get("analyst_target")
    upside  = ((target - price) / price * 100) if target and price else None

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(f"<b>{ticker}</b>", H1),
        Paragraph(signal, style("Normal", fontSize=28, textColor=sig_color, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
    ]]
    header_tbl = Table(header_data, colWidths=[W * 0.65, W * 0.35])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), C_CARD),
        ("ROUNDEDCORNERS", (0, 0), (-1, -1), [6]),
        ("TOPPADDING",  (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, -1), 12),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 12),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph(f"{company}  ·  {sector}  ·  Generated {date.today()}", SML))
    story.append(Spacer(1, 4 * mm))

    # ── Key metrics row ───────────────────────────────────────────────────────
    def metric_cell(label, value, color=C_WHITE):
        return [
            Paragraph(f'<font color="#{color.hexval()[2:] if hasattr(color,"hexval") else "ffffff"}"><b>{value}</b></font>', style("Normal", fontSize=13, textColor=color, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(label, style("Normal", fontSize=7, textColor=C_GREY, alignment=TA_CENTER)),
        ]

    w52h = fund.get("week_52_high")
    w52l = fund.get("week_52_low")
    pct_from_high = ((price - w52h) / w52h * 100) if price and w52h else None

    metrics = [
        ("Price",          _val(price, "price"),          C_WHITE),
        ("Analyst Target", _val(target, "price"),         C_GREEN if upside and upside > 0 else C_RED),
        ("Upside",         _val(upside, "pct") if upside else "N/A", C_GREEN if upside and upside > 0 else C_RED),
        ("Market Cap",     fmt_large(fund.get("market_cap")), C_WHITE),
        ("52w High",       _val(w52h, "price"),           C_WHITE),
        ("52w Low",        _val(w52l, "price"),           C_WHITE),
    ]
    met_vals  = [[Paragraph(f"<b>{v}</b>", style("Normal", fontSize=12, textColor=c, fontName="Helvetica-Bold", alignment=TA_CENTER)) for _, v, c in metrics]]
    met_lbls  = [[Paragraph(l, style("Normal", fontSize=7, textColor=C_GREY, alignment=TA_CENTER)) for l, _, _ in metrics]]
    met_tbl = Table(met_vals + met_lbls, colWidths=[W / 6] * 6)
    met_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_CARD),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEAFTER",     (0, 0), (-2, -1), 0.5, C_DARKGREY),
    ]))
    story.append(met_tbl)
    story.append(Spacer(1, 5 * mm))

    # ── Growth score ─────────────────────────────────────────────────────────
    story.append(Paragraph("Growth Score", H2))
    score_color = _score_color(score)
    score_data = [[
        Paragraph(f"<b>{score:.1f} / 100</b>", style("Normal", fontSize=20, textColor=score_color, fontName="Helvetica-Bold")),
        Paragraph(
            f"Wall St: <b>{fund.get('analyst_rating') or 'N/A'}</b>  ·  "
            f"{fund.get('num_analyst_opinions') or '?'} analysts  ·  "
            f"Pol buys (30d): <b>{fund.get('pol_buys_30d', 0)}</b>",
            style("Normal", fontSize=9, textColor=C_WHITE, leading=14),
        ),
    ]]
    score_tbl = Table(score_data, colWidths=[W * 0.25, W * 0.75])
    score_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_CARD),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LINEAFTER",     (0, 0), (0, -1), 1, score_color),
    ]))
    story.append(score_tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Scoring breakdown ────────────────────────────────────────────────────
    story.append(Paragraph("Scoring Breakdown", H2))
    positives = [r for r in reasons if r.startswith("✅") or "+" in r.split("(")[-1]]
    negatives = [r for r in reasons if r.startswith("❌") or "−" in r.split("(")[-1]]

    reason_rows = []
    max_r = max(len(positives), len(negatives))
    for i in range(max_r):
        p = Paragraph(positives[i].replace("✅ ", "▲ ") if i < len(positives) else "", style("Normal", fontSize=8, textColor=C_GREEN, leading=12))
        n = Paragraph(negatives[i].replace("❌ ", "▼ ") if i < len(negatives) else "", style("Normal", fontSize=8, textColor=C_RED, leading=12))
        reason_rows.append([p, n])

    if reason_rows:
        reason_tbl = Table(reason_rows, colWidths=[W * 0.5, W * 0.5])
        reason_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), C_CARD),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("LINEAFTER",     (0, 0), (0, -1), 0.5, C_DARKGREY),
        ]))
        story.append(reason_tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Screening ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Growth Screening (threshold > 5%)", H2))
    scr_data = [["Criterion", "Value", "Result"]]
    scr_vals = [
        ("5yr Total Return",    fmt_pct(fund.get("total_return_5yr_pct")),    criteria.get("5yr Total Return > 5%")),
        ("5yr Revenue CAGR",    fmt_pct(fund.get("revenue_cagr_5yr_pct")),    criteria.get("5yr Revenue CAGR > 5%")),
        ("EPS YoY Growth",      fmt_pct(fund.get("eps_yoy_pct")),             criteria.get("EPS YoY Growth > 5%")),
        ("5yr Net Income CAGR", fmt_pct(fund.get("net_income_cagr_5yr_pct")), criteria.get("5yr Net Income CAGR > 5%")),
    ]
    for label, val, passed in scr_vals:
        badge = "✓ Pass" if passed else ("✗ Fail" if passed is False else "? N/A")
        clr   = C_GREEN   if passed else (C_RED if passed is False else C_GREY)
        scr_data.append([
            Paragraph(label, NRM),
            Paragraph(val,   NRM),
            Paragraph(f"<b>{badge}</b>", style("Normal", fontSize=9, textColor=clr, fontName="Helvetica-Bold")),
        ])
    scr_tbl = Table(scr_data, colWidths=[W * 0.45, W * 0.3, W * 0.25])
    scr_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE),
        ("BACKGROUND",    (0, 1), (-1, -1), C_CARD),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_CARD, colors.HexColor("#252840")]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.3, C_DARKGREY),
    ]))
    story.append(scr_tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Momentum ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Price Momentum & Technical", H2))
    mom_data = [["Period", "Return", "Signal"]]
    def mom_row(label, val):
        if val is None: return [Paragraph(label, NRM), Paragraph("N/A", NRM), Paragraph("—", NRM)]
        sig = "▲ Bullish" if val > 0 else "▼ Bearish"
        clr = C_GREEN if val > 0 else C_RED
        return [
            Paragraph(label, NRM),
            Paragraph(f"{val:+.1f}%", style("Normal", fontSize=9, textColor=clr)),
            Paragraph(f"<b>{sig}</b>", style("Normal", fontSize=9, textColor=clr, fontName="Helvetica-Bold")),
        ]
    mom_data += [
        mom_row("1-Month Return",   fund.get("mom_1m_pct")),
        mom_row("3-Month Return",   fund.get("mom_3m_pct")),
        mom_row("6-Month Return",   fund.get("mom_6m_pct")),
        mom_row("5yr Total Return", fund.get("total_return_5yr_pct")),
        mom_row("vs 50-day MA",     fund.get("vs_50ma_pct")),
        mom_row("vs 200-day MA",    fund.get("vs_200ma_pct")),
    ]
    rsi = fund.get("rsi")
    rsi_sig = "Oversold ▲" if rsi and rsi < 40 else ("Overbought ▼" if rsi and rsi > 70 else "Neutral")
    rsi_clr = C_GREEN if rsi and rsi < 40 else (C_RED if rsi and rsi > 70 else C_GREY)
    mom_data.append([
        Paragraph("RSI (14)", NRM),
        Paragraph(_val(rsi), NRM),
        Paragraph(f"<b>{rsi_sig}</b>", style("Normal", fontSize=9, textColor=rsi_clr, fontName="Helvetica-Bold")),
    ])

    p6  = fund.get("positive_months_6")
    p12 = fund.get("positive_months_12")
    if p6 is not None:
        p6_clr = C_GREEN if p6 >= 4 else (C_RED if p6 <= 2 else C_ORANGE)
        mom_data.append([
            Paragraph("Positive months / 6", NRM),
            Paragraph(f"{p6} / 6", style("Normal", fontSize=9, textColor=p6_clr)),
            Paragraph("▲ Consistent" if p6 >= 4 else ("▼ Weak" if p6 <= 2 else "Mixed"), style("Normal", fontSize=9, textColor=p6_clr)),
        ])
    if p12 is not None:
        p12_clr = C_GREEN if p12 >= 8 else (C_RED if p12 <= 4 else C_ORANGE)
        mom_data.append([
            Paragraph("Positive months / 12", NRM),
            Paragraph(f"{p12} / 12", style("Normal", fontSize=9, textColor=p12_clr)),
            Paragraph("▲ Consistent" if p12 >= 8 else ("▼ Weak" if p12 <= 4 else "Mixed"), style("Normal", fontSize=9, textColor=p12_clr)),
        ])

    mom_tbl = Table(mom_data, colWidths=[W * 0.4, W * 0.25, W * 0.35])
    mom_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE),
        ("BACKGROUND",    (0, 1), (-1, -1), C_CARD),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_CARD, colors.HexColor("#252840")]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.3, C_DARKGREY),
    ]))
    story.append(mom_tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Monthly returns table ─────────────────────────────────────────────────
    monthly = fund.get("monthly_returns", [])
    if monthly:
        story.append(Paragraph("Month-by-Month Returns (last 24 months)", H2))
        # Split into two columns of 12
        left  = monthly[-24:-12] if len(monthly) >= 12 else []
        right = monthly[-12:]

        def month_rows(months):
            rows = []
            for m in months:
                v = m.get("return_pct")
                clr = C_GREEN if v and v > 0 else C_RED
                rows.append([
                    Paragraph(m["month"], SML),
                    Paragraph(f"{v:+.1f}%" if v is not None else "N/A",
                              style("Normal", fontSize=8, textColor=clr, alignment=TA_RIGHT)),
                ])
            return rows

        left_rows  = month_rows(left)
        right_rows = month_rows(right)
        max_r = max(len(left_rows), len(right_rows))
        empty = [Paragraph("", SML), Paragraph("", SML)]
        combined = []
        for i in range(max_r):
            l = left_rows[i]  if i < len(left_rows)  else empty
            r = right_rows[i] if i < len(right_rows) else empty
            combined.append(l + [Paragraph("  ", SML)] + r)

        if combined:
            cw = W / 5
            mo_tbl = Table(combined, colWidths=[cw * 1.5, cw, cw * 0.3, cw * 1.5, cw])
            mo_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), C_CARD),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING",   (0, 0), (-1, -1), 6),
                ("ALIGN",         (1, 0), (1, -1), "RIGHT"),
                ("ALIGN",         (4, 0), (4, -1), "RIGHT"),
                ("LINEBELOW",     (0, 0), (-1, -1), 0.2, C_DARKGREY),
            ]))
            story.append(mo_tbl)
        story.append(Spacer(1, 4 * mm))

    # ── Multi-year financials ─────────────────────────────────────────────────
    annual_fin = fund.get("annual_financials", [])
    annual_ret = fund.get("annual_returns", [])
    ret_by_yr  = {r["year"]: r.get("price_return_pct") for r in annual_ret}

    if annual_fin:
        story.append(Paragraph("Multi-Year Financial History", H2))
        fin_data = [["Year", "Revenue", "Net Income", "EPS", "Price Return"]]
        for row in sorted(annual_fin, key=lambda x: x["year"]):
            yr   = row["year"]
            ret  = ret_by_yr.get(yr)
            ret_clr = C_GREEN if ret and ret > 0 else (C_RED if ret and ret < 0 else C_GREY)
            fin_data.append([
                Paragraph(str(yr), NRM),
                Paragraph(fmt_large(row.get("revenue")),    NRM),
                Paragraph(fmt_large(row.get("net_income")), NRM),
                Paragraph(f"${row['eps']:.2f}" if row.get("eps") else "N/A", NRM),
                Paragraph(f"{ret:+.1f}%" if ret else "N/A",
                          style("Normal", fontSize=9, textColor=ret_clr)),
            ])
        fin_tbl = Table(fin_data, colWidths=[W * 0.12, W * 0.22, W * 0.22, W * 0.16, W * 0.18])
        fin_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE),
            ("BACKGROUND",    (0, 1), (-1, -1), C_CARD),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 9),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_CARD, colors.HexColor("#252840")]),
            ("LINEBELOW",     (0, 0), (-1, -1), 0.3, C_DARKGREY),
        ]))
        story.append(fin_tbl)
        story.append(Spacer(1, 4 * mm))

    # ── Valuation ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Valuation & Financial Health", H2))
    val_items = [
        ("P/E Ratio (Trailing)",  fmt_ratio(fund.get("pe_ratio"))),
        ("P/E Ratio (Forward)",   fmt_ratio(fund.get("forward_pe"))),
        ("Price / Sales",         fmt_ratio(fund.get("price_to_sales"))),
        ("PEG Ratio",             fmt_ratio(fund.get("peg_ratio"))),
        ("Return on Equity",      fmt_pct(fund.get("return_on_equity"))),
        ("Profit Margin",         fmt_pct(fund.get("profit_margin"))),
        ("Free Cash Flow",        fmt_large(fund.get("free_cash_flow"))),
        ("LT Debt / Capital",     fmt_pct(fund.get("lt_debt_to_capital_pct"))),
        ("Dividend Yield",        f"{fund['dividend_yield']*100:.2f}%" if fund.get("dividend_yield") else "None"),
        ("Beta",                  f"{fund['beta']:.2f}" if fund.get("beta") else "N/A"),
    ]
    # Two-column layout
    half = len(val_items) // 2 + len(val_items) % 2
    val_rows = []
    for i in range(half):
        l = val_items[i]
        r = val_items[i + half] if i + half < len(val_items) else ("", "")
        val_rows.append([
            Paragraph(l[0], SML), Paragraph(f"<b>{l[1]}</b>", style("Normal", fontSize=9, textColor=C_WHITE, fontName="Helvetica-Bold")),
            Paragraph("  ", SML),
            Paragraph(r[0], SML), Paragraph(f"<b>{r[1]}</b>", style("Normal", fontSize=9, textColor=C_WHITE, fontName="Helvetica-Bold")),
        ])
    val_tbl = Table(val_rows, colWidths=[W * 0.26, W * 0.2, W * 0.08, W * 0.26, W * 0.2])
    val_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_CARD),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C_CARD, colors.HexColor("#252840")]),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.2, C_DARKGREY),
    ]))
    story.append(val_tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width=W, color=C_DARKGREY))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "Generated by Political Stock Disclosure Tracker · Data via Yahoo Finance (free) · "
        "Not financial advice — use as a research starting point.",
        style("Normal", fontSize=7, textColor=C_GREY, alignment=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()
