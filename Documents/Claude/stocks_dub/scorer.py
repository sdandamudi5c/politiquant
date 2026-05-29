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
  News sentiment          5   news_sentiment
  Earnings surprise       3   earnings_surprise
  Short interest          3   short_interest
  Macro environment       4   macro
  ─────────────────────────
  MAX TOTAL            ~115 (clamped to 100)
"""

from fundamentals import fmt_large

FACTOR_NAMES = [
    "analyst_upside", "analyst_rec", "rsi",
    "pos_months_6", "pos_months_12",
    "mom_6m", "mom_1m", "mom_3m", "vs_50ma",
    "rev_cagr", "rev_trend", "ni_cagr", "ni_trend",
    "ret_5yr", "pol_buys", "eps_growth", "fcf", "fwd_pe",
    "news_sentiment", "earnings_surprise",
    "short_interest", "macro", "insider", "inst", "sector",
    "breakout", "volume_surge", "earnings_timing", "analyst_revision",
    "reddit_buzz",        # 30 — retail sentiment from WSB/investing/stocks
    "earnings_beat_rate",   # 31 — fraction of last 8 quarters that beat estimates
    "earnings_beat_streak", # 32 — consecutive quarters beating estimates
    "political_signal",     # 33 — presidential/political news sentiment
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
    # Prefer Finnhub count-based data (continuous weighted score).
    # Falls back to yfinance rating string if Finnhub counts unavailable.
    _fh_sb  = int(fund.get("fh_strong_buy",  0) or 0)
    _fh_b   = int(fund.get("fh_buy",         0) or 0)
    _fh_h   = int(fund.get("fh_hold",        0) or 0)
    _fh_s   = int(fund.get("fh_sell",        0) or 0)
    _fh_ss  = int(fund.get("fh_strong_sell", 0) or 0)
    _fh_tot = _fh_sb + _fh_b + _fh_h + _fh_s + _fh_ss

    if _fh_tot >= 3:
        # Weighted score: strongBuy=+2, buy=+1, hold=0, sell=−1, strongSell=−2
        _weighted  = (_fh_sb * 2 + _fh_b - _fh_s - _fh_ss * 2) / _fh_tot
        _buy_pct   = (_fh_sb + _fh_b) / _fh_tot * 100
        _sell_pct  = (_fh_s  + _fh_ss) / _fh_tot * 100
        _bull_str  = f"{_fh_sb}SB+{_fh_b}B" if _fh_sb else f"{_fh_b}B"
        if _weighted >= 1.5:
            _add("analyst_rec", 12, f"⭐ Strong Buy consensus ({_bull_str} / {_fh_tot} analysts) (+12)")
        elif _weighted >= 0.8 or _buy_pct >= 70:
            _add("analyst_rec",  8, f"⭐ Buy consensus ({_buy_pct:.0f}% bullish, {_fh_tot} analysts) (+8)")
        elif _weighted >= 0.2:
            _add("analyst_rec",  5, f"⭐ Lean-buy consensus ({_buy_pct:.0f}% bullish, {_fh_tot} analysts) (+5)")
        elif _weighted >= -0.2:
            _add("analyst_rec",  2, f"↔️ Hold consensus ({_fh_tot} analysts) (+2)")
        elif _weighted >= -0.8:
            _add("analyst_rec", -8, f"🔻 Lean-sell consensus ({_sell_pct:.0f}% bearish, {_fh_tot} analysts) (−8)")
        else:
            _add("analyst_rec", -15, f"🔻 Sell consensus ({_sell_pct:.0f}% bearish, {_fh_tot} analysts) (−15)")
    else:
        # Fallback: yfinance rating string
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

    # ── Revenue growth — latest YoY % (max +5, min -10) ─────────────────────
    rev_g = _n(fund.get("recent_rev_growth_pct"))
    if rev_g is not None:
        if rev_g >= 30:
            _add("rev_trend",  5, f"📈 Revenue +{rev_g:.0f}% YoY — hyper growth (+5)")
        elif rev_g >= 15:
            _add("rev_trend",  4, f"📈 Revenue +{rev_g:.0f}% YoY — strong growth (+4)")
        elif rev_g >= 8:
            _add("rev_trend",  3, f"📈 Revenue +{rev_g:.0f}% YoY (+3)")
        elif rev_g >= 3:
            _add("rev_trend",  1)
        elif rev_g >= -5:
            _add("rev_trend",  0)
        elif rev_g >= -15:
            _add("rev_trend", -4, f"📉 Revenue shrinking {rev_g:.0f}% YoY (−4)")
        else:
            _add("rev_trend", -10, f"📉 Revenue collapsing {rev_g:.0f}% YoY (−10)")

    # ── NI CAGR (max +2) ─────────────────────────────────────────────────────
    ni_cagr = _n(fund.get("net_income_cagr_5yr_pct"))
    if ni_cagr is not None:
        if ni_cagr >= 20:
            _add("ni_cagr", 2, f"💹 Net income 5yr CAGR {ni_cagr:.1f}% (+2)")
        elif ni_cagr >= 10:
            _add("ni_cagr", 1)

    # ── Net income growth — latest YoY % (max +4, min -9) ───────────────────
    ni_g = _n(fund.get("recent_ni_growth_pct"))
    if ni_g is not None:
        if ni_g >= 30:
            _add("ni_trend",  4, f"💹 Net income +{ni_g:.0f}% YoY (+4)")
        elif ni_g >= 15:
            _add("ni_trend",  3, f"💹 Net income +{ni_g:.0f}% YoY (+3)")
        elif ni_g >= 5:
            _add("ni_trend",  2, f"💹 Net income +{ni_g:.0f}% YoY (+2)")
        elif ni_g >= 0:
            _add("ni_trend",  1)
        elif ni_g >= -20:
            _add("ni_trend", -3, f"📉 Net income down {ni_g:.0f}% YoY (−3)")
        elif ni_g >= -50:
            _add("ni_trend", -6, f"📉 Net income down {ni_g:.0f}% YoY (−6)")
        else:
            _add("ni_trend", -9, f"📉 Net income down {ni_g:.0f}% YoY (−9)")

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

    # ── News sentiment (max +5, min -8) ──────────────────────────────────────
    # Net count of positive minus negative keywords across recent headlines.
    news_net = fund.get("news_sentiment_score")
    if news_net is not None:
        # news_net may be a float (FinBERT confidence-weighted) or int (keyword count)
        _news_display = f"{news_net:+.1f}" if isinstance(news_net, float) else f"{news_net:+d}"
        if news_net >= 4:
            _add("news_sentiment",  5, f"📰 Strong positive news sentiment ({_news_display}) (+5)")
        elif news_net >= 2:
            _add("news_sentiment",  3, f"📰 Positive news sentiment ({_news_display}) (+3)")
        elif news_net == 1:
            _add("news_sentiment",  1, f"📰 Slightly positive news (+1)")
        elif news_net == 0:
            _add("news_sentiment",  0)
        elif news_net >= -2:
            _add("news_sentiment", -3, f"📰 Negative news sentiment ({_news_display}) (−3)")
        elif news_net >= -4:
            _add("news_sentiment", -5, f"📰 Significant negative news ({_news_display}) (−5)")
        else:
            _add("news_sentiment", -8, f"📰 Heavy negative news coverage ({_news_display}) (−8)")

    # ── Earnings surprise (max +3, min -6) ───────────────────────────────────
    surp = _n(fund.get("earnings_surprise_pct"))
    if surp is not None:
        if surp >= 20:
            _add("earnings_surprise",  3, f"🎯 Last quarter EPS beat by {surp:.0f}% (+3)")
        elif surp >= 10:
            _add("earnings_surprise",  2, f"🎯 Last quarter EPS beat by {surp:.0f}% (+2)")
        elif surp >= 3:
            _add("earnings_surprise",  1, f"🎯 Last quarter EPS beat by {surp:.0f}% (+1)")
        elif surp >= -3:
            _add("earnings_surprise",  0)
        elif surp >= -10:
            _add("earnings_surprise", -3, f"⚠️ Last quarter EPS missed by {abs(surp):.0f}% (−3)")
        else:
            _add("earnings_surprise", -6, f"⚠️ Last quarter EPS missed by {abs(surp):.0f}% (−6)")

    # ── Earnings beat rate & streak (max +6, min -4) ─────────────────────────
    # Consistent beaters are attractive pre-earnings setups; chronic missers are risky
    _beat_rate   = _n(fund.get("earnings_beat_rate"))    # 0.0–1.0 fraction of beats
    _beat_streak = int(fund.get("earnings_beat_streak") or 0)
    if _beat_rate is not None:
        if _beat_rate >= 0.875:   # beat 7 of last 8
            _add("earnings_beat_rate",  3,
                 f"🎯 Beat EPS estimates {_beat_rate*100:.0f}% of last 8 quarters — serial outperformer (+3)")
        elif _beat_rate >= 0.75:  # beat 6 of last 8
            _add("earnings_beat_rate",  2,
                 f"🎯 Beat EPS estimates {_beat_rate*100:.0f}% of last 8 quarters (+2)")
        elif _beat_rate >= 0.625: # beat 5 of last 8
            _add("earnings_beat_rate",  1,
                 f"📊 Beat EPS estimates {_beat_rate*100:.0f}% of last 8 quarters (+1)")
        elif _beat_rate <= 0.375: # missed 5+ of last 8
            _add("earnings_beat_rate", -3,
                 f"⚠️ Beat EPS estimates only {_beat_rate*100:.0f}% of last 8 quarters (−3)")
        elif _beat_rate <= 0.25:  # missed 6+ of last 8
            _add("earnings_beat_rate", -4,
                 f"⚠️ Chronic EPS misser — only {_beat_rate*100:.0f}% beat rate (−4)")
    if _beat_streak >= 4:
        _add("earnings_beat_streak",  3,
             f"🔥 Beat EPS estimates {_beat_streak} quarters in a row — momentum (+3)")
    elif _beat_streak >= 2:
        _add("earnings_beat_streak",  1,
             f"📈 Beat EPS estimates {_beat_streak} consecutive quarters (+1)")

    # ── Short interest (max +3, min -6) ──────────────────────────────────────
    spf = _n(fund.get("short_pct_float"))   # % of float sold short
    m1  = _n(fund.get("mom_1m_pct"))
    if spf is not None:
        if spf > 25 and m1 and m1 > 10:
            _add("short_interest",  4, f"🔥 Short squeeze setup — {spf:.0f}% float short, price +{m1:.0f}% (+4)")
        elif spf < 2:
            _add("short_interest",  3, f"📊 Very low short interest ({spf:.1f}% float) — strong confidence (+3)")
        elif spf < 5:
            _add("short_interest",  1)
        elif spf < 15:
            _add("short_interest",  0)
        elif spf < 25:
            _add("short_interest", -3, f"📊 High short interest ({spf:.0f}% float) (−3)")
        else:
            _add("short_interest", -6, f"📊 Very high short interest ({spf:.0f}% float) (−6)")

    # ── Insider buying (SEC Form 4 — open market purchases only) ────────────
    # CEO/CFO buying their OWN stock with real money = strong conviction signal.
    # Merge yfinance + Finnhub data — use whichever source has more activity.
    ins_score  = fund.get("insider_buy_score", 0) or 0
    ins_buys   = max(int(fund.get("insider_buy_count",  0) or 0),
                     int(fund.get("fh_insider_buys",    0) or 0))
    ins_sells  = max(int(fund.get("insider_sell_count", 0) or 0),
                     int(fund.get("fh_insider_sells",   0) or 0))
    ceo_bought = fund.get("insider_ceo_bought", False)
    cfo_bought = fund.get("insider_cfo_bought", False)
    # If Finnhub found executive buyers, record names for the reason string
    _fh_execs  = fund.get("fh_insider_executives") or []

    if ceo_bought and cfo_bought:
        _add("insider", 8, "🏦 CEO + CFO both buying open-market — very strong insider conviction (+8)")
    elif ceo_bought:
        _add("insider", 6, "🏦 CEO buying own stock open-market — strong insider signal (+6)")
    elif cfo_bought:
        _add("insider", 5, "🏦 CFO buying own stock open-market — strong insider signal (+5)")
    elif ins_buys >= 3:
        _name_str = f" ({', '.join(_fh_execs[:2])})" if _fh_execs else ""
        _add("insider", 4, f"🏦 {ins_buys} insiders buying open-market in last 90 days{_name_str} (+4)")
    elif ins_buys >= 1:
        _name_str = f" ({_fh_execs[0]})" if _fh_execs else ""
        _add("insider", 2, f"🏦 Insider open-market purchase detected{_name_str} (+2)")
    elif ins_sells >= 5 and ins_buys == 0:
        _add("insider", -4, f"📉 Heavy insider selling ({ins_sells} sales, 0 buys) (−4)")
    elif ins_sells >= 3 and ins_buys == 0:
        _add("insider", -2, f"📉 Insider selling with no buys ({ins_sells} sales) (−2)")

    # ── Institutional holdings (13F filings via yfinance) ────────────────────
    inst_score    = int(fund.get("inst_score", 0) or 0)
    inst_t1_buy   = bool(fund.get("inst_tier1_buying", False))
    inst_t1_names = fund.get("inst_tier1_buyers", []) or []
    inst_buyers   = int(fund.get("inst_buyer_count", 0) or 0)
    inst_sellers  = int(fund.get("inst_seller_count", 0) or 0)

    if inst_score >= 8:
        names = ", ".join(inst_t1_names[:2]) if inst_t1_names else "Tier-1 institutions"
        _add("inst", inst_score, f"🏢 {names} adding to position — smart-money consensus (+{inst_score})")
    elif inst_score >= 5:
        name = inst_t1_names[0] if inst_t1_names else "Tier-1 institution"
        _add("inst", inst_score, f"🏢 {name} increasing position — institutional conviction (+{inst_score})")
    elif inst_score >= 2:
        _add("inst", inst_score, f"🏢 {inst_buyers} institution(s) adding to positions (+{inst_score})")
    elif inst_score < 0:
        _add("inst", inst_score, f"📉 Institutional net selling ({inst_sellers} sellers, {inst_buyers} buyers) ({inst_score:+d})")

    # ── Sector rotation (vs SPY 30-day momentum) ─────────────────────────────
    _sector_name = fund.get("sector") or ""
    if _sector_name:
        try:
            from sector_rotation import get_sector_signal, MOMENTUM_LABEL
            _sig   = get_sector_signal(_sector_name)
            _smod  = int(_sig.get("score_mod", 0))
            _smom  = _sig.get("momentum", "neutral")
            _vs30  = _sig.get("vs_spy_30d")
            _etf   = _sig.get("etf", "")
            if _smod != 0:
                _label, _ = MOMENTUM_LABEL.get(_smom, ("Sector signal", "#aaa"))
                _vs_str   = f" ({_vs30:+.1f}% vs SPY)" if _vs30 is not None else ""
                _add("sector", _smod,
                     f"{_label} — {_sector_name} ({_etf}){_vs_str} ({_smod:+d})")
        except Exception:
            pass

    # ── Macro environment (FRED data — yield curve + rate direction) ──────────
    try:
        from macro_data import fetch_macro, macro_score_modifier
        macro = fetch_macro()
        fwd_pe = _n(fund.get("forward_pe"))
        m_pts, m_reasons = macro_score_modifier(macro, fwd_pe=fwd_pe)
        if m_pts != 0:
            _add("macro", m_pts)
            for r in m_reasons:
                reasons.append(r)
    except Exception:
        pass

    # ── Analyst upgrades / downgrades (last 30 days) ─────────────────────────
    _ups   = int(fund.get("analyst_upgrades_30d", 0) or 0)
    _downs = int(fund.get("analyst_downgrades_30d", 0) or 0)
    _up_firms = fund.get("analyst_upgrade_firms", []) or []
    _dn_firms = fund.get("analyst_downgrade_firms", []) or []
    if _ups >= 3:
        _firms_str = ", ".join(_up_firms[:2]) if _up_firms else f"{_ups} firms"
        _add("analyst_revision", 6, f"⬆️ {_ups} analyst upgrades in last 30 days ({_firms_str}…) (+6)")
    elif _ups == 2:
        _add("analyst_revision", 4, f"⬆️ 2 analyst upgrades in last 30 days (+4)")
    elif _ups == 1:
        _add("analyst_revision", 2, f"⬆️ Analyst upgrade in last 30 days (+2)")
    if _downs >= 3 and _ups == 0:
        _firms_str = ", ".join(_dn_firms[:2]) if _dn_firms else f"{_downs} firms"
        _add("analyst_revision", -6, f"⬇️ {_downs} analyst downgrades in last 30 days ({_firms_str}…) (−6)")
    elif _downs >= 2 and _ups == 0:
        _add("analyst_revision", -4, f"⬇️ {_downs} analyst downgrades in last 30 days (−4)")
    elif _downs == 1 and _ups == 0:
        _add("analyst_revision", -2, f"⬇️ Analyst downgrade in last 30 days (−2)")

    # ── 52-week breakout / proximity ──────────────────────────────────────────
    _pct_high = _n(fund.get("pct_from_52w_high"))
    _pct_low  = _n(fund.get("pct_from_52w_low"))
    if _pct_high is not None:
        if _pct_high >= -2:
            _add("breakout", 6, f"🚀 At/near 52-week high ({_pct_high:+.1f}%) — breakout territory (+6)")
        elif _pct_high >= -8:
            _add("breakout", 3, f"📈 Within 8% of 52-week high ({_pct_high:+.1f}%) — approaching breakout (+3)")
        elif _pct_high >= -20:
            _add("breakout", 1, f"📊 Within 20% of 52-week high ({_pct_high:+.1f}%) (+1)")
        elif _pct_high <= -50:
            _add("breakout", -4, f"⚠️ {abs(_pct_high):.0f}% below 52-week high — deep decline (−4)")
        elif _pct_high <= -35:
            _add("breakout", -2, f"📉 {abs(_pct_high):.0f}% below 52-week high (−2)")
    if _pct_low is not None and _pct_low <= 10:
        reasons.append(f"⚠️ Near 52-week low ({_pct_low:+.1f}% above) — watch for support")

    # ── Volume surge ──────────────────────────────────────────────────────────
    _vol_ratio  = _n(fund.get("volume_ratio"))
    _day_chg    = _n(fund.get("today_change_pct"))
    if _vol_ratio is not None and _vol_ratio >= 2.0:
        _up_day = _day_chg is not None and _day_chg > 0
        _dn_day = _day_chg is not None and _day_chg < 0
        if _vol_ratio >= 3.0 and _up_day:
            _add("volume_surge", 5, f"🔊 {_vol_ratio:.1f}× normal volume on an up day — strong buying conviction (+5)")
        elif _vol_ratio >= 2.0 and _up_day:
            _add("volume_surge", 3, f"🔊 {_vol_ratio:.1f}× normal volume on an up day — buying interest (+3)")
        elif _vol_ratio >= 3.0 and _dn_day:
            _add("volume_surge", -4, f"📉 {_vol_ratio:.1f}× normal volume on a down day — heavy distribution (−4)")
        elif _vol_ratio >= 2.0 and _dn_day:
            _add("volume_surge", -2, f"📉 {_vol_ratio:.1f}× normal volume on a down day — selling pressure (−2)")
        else:
            _add("volume_surge", 2, f"🔊 {_vol_ratio:.1f}× normal volume — elevated interest (+2)")

    # ── Earnings timing — context-aware (max +4, min -5) ─────────────────────
    # Serial beaters near earnings = opportunity; chronic missers = risk
    _earn_days   = fund.get("earnings_days_until")
    _beat_rate_t = _n(fund.get("earnings_beat_rate"))
    _streak_t    = int(fund.get("earnings_beat_streak") or 0)
    if _earn_days is not None:
        _is_serial_beater  = (_beat_rate_t is not None and _beat_rate_t >= 0.75) or _streak_t >= 3
        _is_chronic_misser = _beat_rate_t is not None and _beat_rate_t <= 0.375
        if _earn_days <= 7:
            if _is_serial_beater:
                _add("earnings_timing",  4,
                     f"📅 Earnings in {_earn_days}d · serial beater ({_beat_rate_t*100:.0f}% beat rate) — pre-earnings setup (+4)")
            elif _is_chronic_misser:
                _add("earnings_timing", -5,
                     f"📅 Earnings in {_earn_days}d · chronic misser ({_beat_rate_t*100:.0f}% beat rate) — event risk (−5)")
            else:
                _add("earnings_timing", -2,
                     f"📅 Earnings in {_earn_days} day(s) — binary event risk (−2)")
        elif _earn_days <= 14:
            if _is_serial_beater:
                _add("earnings_timing",  2,
                     f"📅 Earnings in {_earn_days}d · strong beat history — upcoming catalyst (+2)")
            elif _is_chronic_misser:
                _add("earnings_timing", -2,
                     f"📅 Earnings in {_earn_days}d · weak beat history — note risk (−2)")
            else:
                _add("earnings_timing", -1,
                     f"📅 Earnings in {_earn_days} days — upcoming catalyst (−1)")

    # ── Reddit / WallStreetBets buzz (max +5, min -4) ────────────────────────
    try:
        from reddit_sentiment import fetch_reddit_sentiment
        _reddit = fetch_reddit_sentiment(ticker) if (ticker := fund.get("ticker")) else {}
        _rmod   = _reddit.get("score_mod", 0)
        _rcount = _reddit.get("mention_count", 0)
        _rtrend = _reddit.get("trend", "unknown")
        if _rmod > 0:
            _trend_str = {"viral": "🔥 Viral", "rising": "📈 Trending"}.get(_rtrend, "📊")
            _add("reddit_buzz", _rmod,
                 f"🤳 {_trend_str} on Reddit ({_rcount} mentions, positive) (+{_rmod})")
        elif _rmod < 0:
            _add("reddit_buzz", _rmod,
                 f"🤳 Reddit sentiment negative ({_rcount} mentions) ({_rmod})")
    except Exception:
        pass

    # ── Political / presidential signal (max +6, min -10) ────────────────────
    _pol_mod  = int(fund.get("political_score_mod") or 0)
    _pol_flag = fund.get("political_flag")
    _pol_sent = fund.get("political_sentiment", "neutral")
    if _pol_mod != 0 and _pol_flag:
        if _pol_mod > 0:
            _add("political_signal", _pol_mod,
                 f"🏛️ Presidential/political TAILWIND: {_pol_flag} (+{_pol_mod})")
        else:
            _add("political_signal", _pol_mod,
                 f"🏛️ Presidential/political HEADWIND: {_pol_flag} ({_pol_mod})")

    # Clamp to [0, 100] as a safety net
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
