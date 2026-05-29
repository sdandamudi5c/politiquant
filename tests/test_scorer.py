"""
Tests for scorer.py

Covers:
  - FACTOR_NAMES count (adding Reddit as 30th factor)
  - Score always stays in [0, 100]
  - Empty/None fund data doesn't crash
  - Individual factor scoring logic
  - signal_label thresholds
  - score_stock_detailed returns 3-tuple
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _minimal_fund(**kwargs):
    """Return a minimal fund dict with sane defaults."""
    base = {
        "ticker": "TEST", "current_price": 100.0, "analyst_target": 120.0,
        "analyst_rating": "buy", "num_analyst_opinions": 10,
        "rsi": 50.0, "positive_months_6": 4, "positive_months_12": 8,
        "mom_6m_pct": 10.0, "mom_1m_pct": 3.0, "mom_3m_pct": 7.0,
        "vs_50ma_pct": 5.0, "revenue_cagr_5yr_pct": 12.0,
        "net_income_cagr_5yr_pct": 8.0, "eps_yoy_pct": 10.0,
        "free_cash_flow": 1e9, "forward_pe": 20.0,
        "news_sentiment_score": 0.2, "earnings_surprise_pct": 5.0,
        "short_pct_float": 3.0, "total_return_5yr_pct": 50.0,
        "pct_from_52w_high": -5.0, "pct_from_52w_low": 30.0,
        "volume_ratio": 1.0, "today_change_pct": 0.5,
        "analyst_upgrades_30d": 1, "analyst_downgrades_30d": 0,
        "sector": "Technology",
    }
    base.update(kwargs)
    return base


class TestFactorNames(unittest.TestCase):

    def test_factor_names_count(self):
        """REGRESSION: factor count grows as new signals are added; must be ≥30."""
        from scorer import FACTOR_NAMES
        self.assertGreaterEqual(len(FACTOR_NAMES), 30,
            f"Expected at least 30 factors, got {len(FACTOR_NAMES)}: {FACTOR_NAMES}")
        # Spot-check the 3 newest additions
        for name in ("earnings_beat_rate", "earnings_beat_streak", "political_signal"):
            self.assertIn(name, FACTOR_NAMES, f"Missing new factor: {name}")

    def test_reddit_buzz_in_factor_names(self):
        from scorer import FACTOR_NAMES
        self.assertIn("reddit_buzz", FACTOR_NAMES,
            "reddit_buzz should be in FACTOR_NAMES as the 30th factor")

    def test_no_duplicate_factor_names(self):
        from scorer import FACTOR_NAMES
        self.assertEqual(len(FACTOR_NAMES), len(set(FACTOR_NAMES)),
            "FACTOR_NAMES contains duplicates")

    def test_factor_names_are_strings(self):
        from scorer import FACTOR_NAMES
        for name in FACTOR_NAMES:
            self.assertIsInstance(name, str)
            self.assertGreater(len(name), 0)


class TestScoreRange(unittest.TestCase):

    def test_score_always_between_0_and_100(self):
        from scorer import score_stock
        # Strong stock
        s1, _ = score_stock(_minimal_fund(
            analyst_target=200, rsi=30, mom_6m_pct=40, eps_yoy_pct=50
        ))
        self.assertGreaterEqual(s1, 0)
        self.assertLessEqual(s1, 100)

        # Terrible stock
        s2, _ = score_stock(_minimal_fund(
            current_price=100, analyst_target=50, rsi=85,
            mom_6m_pct=-40, eps_yoy_pct=-50, short_pct_float=40
        ))
        self.assertGreaterEqual(s2, 0)
        self.assertLessEqual(s2, 100)

    def test_empty_fund_does_not_crash(self):
        """REGRESSION: None/missing fields should degrade gracefully, not raise."""
        from scorer import score_stock
        score, reasons = score_stock({})
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_none_values_do_not_crash(self):
        from scorer import score_stock
        fund = {k: None for k in _minimal_fund()}
        score, reasons = score_stock(fund)
        self.assertIsInstance(score, float)

    def test_score_stock_detailed_returns_3_tuple(self):
        from scorer import score_stock_detailed
        result = score_stock_detailed(_minimal_fund())
        self.assertEqual(len(result), 3,
            "score_stock_detailed must return (score, reasons, factor_pts)")
        score, reasons, factor_pts = result
        self.assertIsInstance(score, float)
        self.assertIsInstance(reasons, list)
        self.assertIsInstance(factor_pts, dict)

    def test_factor_pts_covers_all_factor_names(self):
        """factor_pts dict must have an entry for every factor in FACTOR_NAMES."""
        from scorer import score_stock_detailed, FACTOR_NAMES
        _, _, factor_pts = score_stock_detailed(_minimal_fund())
        for name in FACTOR_NAMES:
            self.assertIn(name, factor_pts,
                f"factor_pts missing key '{name}' — score_stock_detailed is broken")


class TestIndividualFactors(unittest.TestCase):

    def _pts(self, fund, factor):
        from scorer import score_stock_detailed
        _, _, fp = score_stock_detailed(fund)
        return fp.get(factor, 0)

    def test_analyst_upside_high(self):
        pts = self._pts(_minimal_fund(current_price=100, analyst_target=160), "analyst_upside")
        self.assertGreater(pts, 0, "60% upside should give positive analyst_upside points")

    def test_analyst_upside_stock_above_target(self):
        pts = self._pts(_minimal_fund(current_price=120, analyst_target=100), "analyst_upside")
        self.assertLess(pts, 0, "Stock above analyst target should give negative points")

    def test_rsi_overbought_penalty(self):
        pts = self._pts(_minimal_fund(rsi=82), "rsi")
        self.assertLess(pts, 0, "RSI 82 (overbought) should give negative points")

    def test_rsi_oversold_bonus(self):
        pts = self._pts(_minimal_fund(rsi=22), "rsi")
        self.assertGreater(pts, 0, "RSI 22 (oversold) should give positive points")

    def test_strong_momentum_positive(self):
        pts = self._pts(_minimal_fund(mom_6m_pct=35), "mom_6m")
        self.assertGreater(pts, 0)

    def test_sharp_decline_negative(self):
        pts = self._pts(_minimal_fund(mom_6m_pct=-30), "mom_6m")
        self.assertLess(pts, 0)

    def test_pol_buys_adds_points(self):
        from scorer import score_stock_detailed
        _, _, fp_no_pol  = score_stock_detailed(_minimal_fund(), pol_buys_30d=0)
        _, _, fp_with_pol = score_stock_detailed(_minimal_fund(), pol_buys_30d=5)
        self.assertGreater(fp_with_pol["pol_buys"], fp_no_pol["pol_buys"],
            "Political buys should increase pol_buys score")


class TestSignalLabel(unittest.TestCase):

    def test_strong_buy_threshold(self):
        from scorer import signal_label
        label, colour = signal_label(75)
        self.assertEqual(label, "STRONG BUY")
        label, _ = signal_label(100)
        self.assertEqual(label, "STRONG BUY")

    def test_buy_threshold(self):
        from scorer import signal_label
        label, _ = signal_label(58)
        self.assertEqual(label, "BUY")
        label, _ = signal_label(74)
        self.assertEqual(label, "BUY")

    def test_watch_threshold(self):
        from scorer import signal_label
        label, _ = signal_label(40)
        self.assertEqual(label, "WATCH")

    def test_avoid_threshold(self):
        from scorer import signal_label
        label, _ = signal_label(0)
        self.assertEqual(label, "AVOID")
        label, _ = signal_label(24)
        self.assertEqual(label, "AVOID")

    def test_signal_label_returns_hex_colour(self):
        from scorer import signal_label
        for score in [0, 25, 42, 58, 75, 100]:
            _, colour = signal_label(score)
            self.assertTrue(colour.startswith("#"),
                f"signal_label({score}) returned non-hex colour: {colour}")
            self.assertEqual(len(colour), 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
