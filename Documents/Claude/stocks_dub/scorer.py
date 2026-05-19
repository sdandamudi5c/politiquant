"""
Shared 0-100 growth scoring.

Design: theoretical max = 100 (no clamping cheating).
Thresholds are strict — only genuinely exceptional stocks reach 80+.
Penalties are strong enough to push weak stocks below 20.

Factor allocation (max pts):
  Analyst upside         14   analyst_upside
  Analyst recommendation 12   analyst_rec
  RSI mean-reversion      7   rsi
  Monthly consistency 6m  7   pos_months_6
  Monthly consistency 12m  3   pos_months_12
  6-month momentum        6   mom_6m
  1-month momentum        5   mom_1m
  3-month momentum        5   mom_3m
  Price vs 50-day MA      6   vs_50ma
  Revenue CAGR            3   rev_cagr
  Revenue trend           5   rev_trend
  NI CAGR                 2   ni_cagr
  NI trend                4   ni_trend
  5yr total return        4   ret_5yr
  Politician buys         6   pol_buys
  EPS growth              4   eps_growth
  Free cash flow          4   fcf
  Forward P/E             3   fwd_pe
  ─────────────────────────
  MAX TOTAL             100
"""

from fundamentals import fmt_large

FACTOR_NAMES = [
    "analyst_upside", "analyst_rec", "rsi",
    "pos_months_6", "pos_months_12",
    "mom_6m", "mom_1m", "mom_3m", "vs_50ma",
    "rev_cagr", "rev_trend", "ni_cagr", "ni_trend",
    "ret_5yr", "pol_buys", "eps_growth", "fcf", "fwd_pe",
]


def _n(val) -> "float | None":
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _score_internal(fund: dict, pol_buys_30d: int = 0) -> "tuple[float, list[str], dict[str, float]]":
    score   = 0.0
    reasons = []
    fp      = {name: 0.0 for name in FACTOR_NAMES}

    def _add(factor: str, pts: float, reason: str = ""):
        nonlocal score
        score      += pts
        fp[factor] += pts
        if reason:
            reasons.append(reason)

    # ── Analyst upside (max +14, min -15) ────────────────────────────────────
    # Strict: needs 50%+ for max. Most stocks have 10-30% → only middle tiers.
    price  = _n(fund.get("current_price"))
    target = _n(fund.get("analyst_target"))
    if price and target and price > 0:
        upside = (target - price) / price * 100
        if upside >= 50:
            _add("analyst_upside", 14,  f"🎯 Analyst target +{upside:.0f}% upside (+14)")
        elif upside >= 30:
            _add("analyst_upside",  9,  f"🎯 Analyst target +{upside:.0f}% upside (+9)")
        elif upside >= 15:
            _add("analyst_upside",  5,  f"🎯 Analyst target +{upside:.0f}% upside (+5)")
        elif upside >= 0:
            _add("analyst_upside",  2,  f"🎯 Analyst target +{upside:.0f}% upside (+2)")
        elif upside >= -10:
            _add("analyst_upside", -8,  f"⚠️ Stock above analyst target ({upside:.0f}%) (−8)")
        else:
            _add("analyst_upside", -15, f"⚠️ Stock well above analyst target ({upside:.0f}%) (−15)")

    # ── Analyst recommendation (max +12, min -15) ────────────────────────────
    rating = (fund.get("analyst_rating") or "").lower()
    n_ana  = fund.get("num_analyst_opinions") or 0
    if "strong buy" in rating:
        _add("analyst_rec", 12,  f"⭐ Strong Buy ({n_ana} analysts) (+12)")
    elif rating == "buy":
        _add("analyst_rec",  7,  f"⭐ Buy consensus ({n_ana} analysts) (+7)")
    elif "hold" in rating or "neutral" in rating:
        _add("analyst_rec",  2,  f"↔️ Hold consensus ({n_ana} analysts) (+2)")
    elif "underperform" in rating:
        _add("analyst_rec", -8,  f"🔻 Underperform consensus ({n_ana} analysts) (−8)")
    elif "sell" in rating:
        _add("analyst_rec", -15, f"🔻 Sell consensus ({n_ana} analysts) (−15)")

    # ── RSI mean-reversion (max +7, min -12) ─────────────────────────────────
    # Strict: only extreme oversold gets full points. Neutral = 0, not +4.
    rsi = _n(fund.get("rsi"))
    if rsi is not None:
        if rsi < 25:
            _add("rsi",  7, f"📉 RSI {rsi:.0f} — extreme oversold, strong bounce signal (+7)")
        elif rsi < 35:
            _add("rsi",  4, f"📉 RSI {rsi:.0f} — oversold territory (+4)")
        elif rsi < 45:
            _add("rsi",  1, f"📉 RSI {rsi:.0f} — mildly oversold (+1)")
        elif rsi < 60:
            _add("rsi",  0)  # neutral — no points
        elif rsi < 70:
            _add("rsi", -3,  f"📊 RSI {rsi:.0f} — elevated, momentum slowing (−3)")
        else:
            _add("rsi", -12, f"🔴 RSI {rsi:.0f} — overbought, pullback risk (−12)")

    # ── Monthly consistency 6m (max +7, min -12) ─────────────────────────────
    # Strict: needs 6/6 for max (all months positive). 4/6 = just +2.
    pos6 = fund.get("positive_months_6")
    if pos6 is not None:
        if pos6 == 6:
            _add("pos_months_6",  7, f"📅 6/6 months positive — perfect consistency (+7)")
        elif pos6 == 5:
            _add("pos_months_6",  4, f"📅 5/6 months positive (+4)")
        elif pos6 == 4:
            _add("pos_months_6",  2, f"📅 4/6 months positive (+2)")
        elif pos6 == 3:
            _add("pos_months_6",  0)  # mediocre — no reward
        elif pos6 == 2:
            _add("pos_months_6", -4, f"📅 Only 2/6 months positive — persistent weakness (−4)")
        elif pos6 <= 1:
            _add("pos_months_6", -12, f"📅 {pos6}/6 months positive — severe downtrend (−12)")

    # ── Monthly consistency 12m (max +3, min -6) ─────────────────────────────
    pos12 = fund.get("positive_months_12")
    if pos12 is not None:
        if pos12 >= 10:
            _add("pos_months_12",  3, f"📅 {pos12}/12 months positive (+3)")
        elif pos12 >= 8:
            _add("pos_months_12",  1)
        elif pos12 <= 4:
            _add("pos_months_12", -6, f"📅 Only {pos12}/12 months positive (−6)")
        elif pos12 <= 6:
            _add("pos_months_12", -2)

    # ── 6-month momentum (max +6, min -8) ────────────────────────────────────
    # Strict: needs 30%+ for max. Flat/slightly up = 0.
    m6 = _n(fund.get("mom_6m_pct"))
    if m6 is not None:
        if m6 >= 30:
            _add("mom_6m",  6, f"🚀 Strong 6-month momentum (+{m6:.1f}%) (+6)")
        elif m6 >= 15:
            _add("mom_6m",  4, f"📈 Solid 6-month momentum (+{m6:.1f}%) (+4)")
        elif m6 >= 5:
            _add("mom_6m",  2, f"📈 Positive 6-month trend (+{m6:.1f}%) (+2)")
        elif m6 >= 0:
            _add("mom_6m",  0)  # flat — no reward
        elif m6 >= -15:
            _add("mom_6m", -4, f"📉 6-month trend negative ({m6:.1f}%) (−4)")
        else:
            _add("mom_6m", -8, f"📉 Sharp 6-month decline ({m6:.1f}%) (−8)")

    # ── 1-month momentum (max +5, min -6) ────────────────────────────────────
    m1 = _n(fund.get("mom_1m_pct"))
    if m1 is not None:
        if m1 >= 12:
            _add("mom_1m",  5, f"🚀 Strong 1-month move (+{m1:.1f}%) (+5)")
        elif m1 >= 6:
            _add("mom_1m",  3, f"📈 Positive 1-month move (+{m1:.1f}%) (+3)")
        elif m1 >= 2:
            _add("mom_1m",  1)
        elif m1 >= -3:
            _add("mom_1m",  0)  # small move — neutral
        elif m1 >= -8:
            _add("mom_1m", -3, f"📉 1-month price down {m1:.1f}% (−3)")
        else:
            _add("mom_1m", -6, f"📉 Sharp 1-month drop {m1:.1f}% (−6)")

    # ── 3-month momentum (max +5, min -6) ────────────────────────────────────
    m3 = _n(fund.get("mom_3m_pct"))
    if m3 is not None:
        if m3 >= 20:
            _add("mom_3m",  5, f"🚀 Strong 3-month trend (+{m3:.1f}%) (+5)")
        elif m3 >= 10:
            _add("mom_3m",  3, f"📈 Positive 3-month trend (+{m3:.1f}%) (+3)")
        elif m3 >= 3:
            _add("mom_3m",  1)
        elif m3 >= -5:
            _add("mom_3m",  0)
        elif m3 >= -15:
            _add("mom_3m", -3, f"📉 3-month trend negative ({m3:.1f}%) (−3)")
        else:
            _add("mom_3m", -6, f"📉 Sharp 3-month decline ({m3:.1f}%) (−6)")

    # ── Price vs 50-day MA (max +6, min -5) ──────────────────────────────────
    vs50 = _n(fund.get("vs_50ma_pct"))
    if vs50 is not None:
        if -5 <= vs50 <= 0:
            _add("vs_50ma",  6, f"📍 Just below 50-day MA ({vs50:+.1f}%) — recovery setup (+6)")
        elif 0 < vs50 <= 3:
            _add("vs_50ma",  4, f"📍 Just above 50-day MA ({vs50:+.1f}%) (+4)")
        elif -12 <= vs50 < -5:
            _add("vs_50ma",  2, f"📍 Below 50-day MA ({vs50:+.1f}%) — potential bounce (+2)")
        elif vs50 < -12:
            _add("vs_50ma", -5, f"📍 Far below 50-day MA ({vs50:+.1f}%) — momentum breakdown (−5)")
        else:
            _add("vs_50ma",  0)  # well above MA — no extra reward

    # ── Revenue CAGR (max +3) ─────────────────────────────────────────────────
    rev_cagr = _n(fund.get("revenue_cagr_5yr_pct"))
    if rev_cagr is not None:
        if rev_cagr >= 15:
            _add("rev_cagr", 3, f"📊 Revenue 5yr CAGR {rev_cagr:.1f}% (+3)")
        elif rev_cagr >= 8:
            _add("rev_cagr", 1, f"📊 Revenue 5yr CAGR {rev_cagr:.1f}% (+1)")

    # ── Revenue growth trend (max +5, min -10) ───────────────────────────────
    rev_trend = _n(fund.get("revenue_trend"))
    if rev_trend is not None:
        if rev_trend > 10:
            _add("rev_trend",  5, f"📈 Revenue growth accelerating sharply (+{rev_trend:.1f}pp) (+5)")
        elif rev_trend > 4:
            _add("rev_trend",  3, f"📈 Revenue growth accelerating (+{rev_trend:.1f}pp) (+3)")
        elif rev_trend > 0:
            _add("rev_trend",  1)
        elif rev_trend >= -5:
            _add("rev_trend", -2, f"📉 Revenue growth slowing ({rev_trend:.1f}pp) (−2)")
        elif rev_trend >= -15:
            _add("rev_trend", -5, f"📉 Revenue growth decelerating ({rev_trend:.1f}pp) (−5)")
        else:
            _add("rev_trend", -10, f"📉 Revenue growth collapsing ({rev_trend:.1f}pp) (−10)")

    # ── NI CAGR (max +2) ─────────────────────────────────────────────────────
    ni_cagr = _n(fund.get("net_income_cagr_5yr_pct"))
    if ni_cagr is not None:
        if ni_cagr >= 20:
            _add("ni_cagr", 2, f"💹 Net income 5yr CAGR {ni_cagr:.1f}% (+2)")
        elif ni_cagr >= 10:
            _add("ni_cagr", 1)

    # ── NI growth trend (max +4, min -9) ─────────────────────────────────────
    ni_trend = _n(fund.get("ni_trend"))
    if ni_trend is not None:
        if ni_trend > 10:
            _add("ni_trend",  4, f"📈 Net income growth accelerating sharply (+{ni_trend:.1f}pp) (+4)")
        elif ni_trend > 4:
            _add("ni_trend",  2, f"📈 Net income growth improving (+{ni_trend:.1f}pp) (+2)")
        elif ni_trend > 0:
            _add("ni_trend",  1)
        elif ni_trend >= -5:
            _add("ni_trend", -2, f"📉 Net income growth slowing ({ni_trend:.1f}pp) (−2)")
        elif ni_trend >= -15:
            _add("ni_trend", -5, f"📉 Net income declining ({ni_trend:.1f}pp) (−5)")
        else:
            _add("ni_trend", -9, f"📉 Net income collapsing ({ni_trend:.1f}pp) (−9)")

    # ── 5-year total return (max +4, min -5) ─────────────────────────────────
    ret5 = _n(fund.get("total_return_5yr_pct"))
    if ret5 is not None:
        if ret5 >= 200:
            _add("ret_5yr",  4, f"🏆 5yr total return +{ret5:.0f}% (+4)")
        elif ret5 >= 80:
            _add("ret_5yr",  2, f"📈 5yr total return +{ret5:.0f}% (+2)")
        elif ret5 >= 20:
            _add("ret_5yr",  1)
        elif ret5 < -20:
            _add("ret_5yr", -5, f"📉 5yr total return {ret5:.0f}% (−5)")
        elif ret5 < 0:
            _add("ret_5yr", -2, f"📉 5yr total return {ret5:.0f}% (−2)")

    # ── Politician buys (max +6) ──────────────────────────────────────────────
    if pol_buys_30d >= 5:
        _add("pol_buys", 6, f"🏛️ {pol_buys_30d} politicians bought in last 30 days (+6)")
    elif pol_buys_30d >= 3:
        _add("pol_buys", 4, f"🏛️ {pol_buys_30d} politicians bought in last 30 days (+4)")
    elif pol_buys_30d == 2:
        _add("pol_buys", 2, f"🏛️ 2 politicians bought in last 30 days (+2)")
    elif pol_buys_30d == 1:
        _add("pol_buys", 1, f"🏛️ 1 politician bought in last 30 days (+1)")

    # ── EPS growth (max +4) ───────────────────────────────────────────────────
    # Strict: needs 20%+ for max. ≥5% gets only +1.
    eps = _n(fund.get("eps_yoy_pct"))
    if eps is not None:
        if eps >= 20:
            _add("eps_growth", 4, f"💹 EPS growing {eps:+.1f}% YoY (+4)")
        elif eps >= 10:
            _add("eps_growth", 2, f"💹 EPS growing {eps:+.1f}% YoY (+2)")
        elif eps >= 5:
            _add("eps_growth", 1)
        elif eps < -10:
            _add("eps_growth", -4, f"📉 EPS declining {eps:.1f}% YoY (−4)")

    # ── Free cash flow (max +4, min -4) ──────────────────────────────────────
    fcf = _n(fund.get("free_cash_flow"))
    if fcf is not None:
        if fcf > 0:
            _add("fcf",  4, f"💰 Positive FCF ({fmt_large(fcf)}) (+4)")
        else:
            _add("fcf", -4, f"🔴 Negative FCF ({fmt_large(fcf)}) (−4)")

    # ── Forward P/E vs trailing (max +3, min -4) ──────────────────────────────
    pe  = _n(fund.get("pe_ratio"))
    fpe = _n(fund.get("forward_pe"))
    if pe is not None and fpe is not None:
        if fpe < pe * 0.85:
            _add("fwd_pe",  3, f"📊 Forward P/E ({fpe:.1f}) well below Trailing ({pe:.1f}) — earnings growing (+3)")
        elif fpe < pe:
            _add("fwd_pe",  1, f"📊 Forward P/E ({fpe:.1f}) < Trailing ({pe:.1f}) (+1)")
        elif fpe > pe * 1.2:
            _add("fwd_pe", -4, f"📊 Forward P/E ({fpe:.1f}) > Trailing ({pe:.1f}) — earnings shrinking (−4)")

    # Clamp to [0, 100] as a safety net only — well-designed stocks shouldn't hit 100
    return max(0.0, min(100.0, score)), reasons, fp


def score_stock(fund: dict, pol_buys_30d: int = 0) -> "tuple[float, list[str]]":
    s, r, _ = _score_internal(fund, pol_buys_30d)
    return s, r


def score_stock_detailed(fund: dict, pol_buys_30d: int = 0) -> "tuple[float, list[str], dict[str, float]]":
    return _score_internal(fund, pol_buys_30d)


def signal_label(score: float) -> "tuple[str, str]":
    """Returns (label, hex_colour)."""
    if score >= 75:
        return "STRONG BUY", "#2ecc71"
    elif score >= 58:
        return "BUY", "#27ae60"
    elif score >= 40:
        return "WATCH", "#f39c12"
    elif score >= 25:
        return "NEUTRAL", "#aaaaaa"
    else:
        return "AVOID", "#e74c3c"
