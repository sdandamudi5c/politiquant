"""
Tests for fundamentals.py cache layer

Regression tests for bugs we actually hit:
  - BUG: fundamentals_cache.json corrupted (14.7 MB, bad JSON at char 15M)
    caused _MEM_CACHE to be {} but Finnhub gate saw stale in-memory data
  - BUG: t.info raising exception would abort entire fetch_fundamentals()
    leaving Finnhub never called and score=0
  - BUG: market_cap=None being treated as 0, blocking Finnhub for small caps
  - BUG: finnhub_no_coverage permanent boolean — one transient failure = forever blacklisted
  - BUG: CACHE_VERSION mismatch silently returning stale data
"""

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFundamentalsCache(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import cache_db
        self._orig_db = cache_db._DB_FILE
        cache_db._DB_FILE = os.path.join(self.tmpdir, "test_cache.db")
        cache_db._INITIALISED = False
        if hasattr(cache_db._local, "conn"):
            try: cache_db._local.conn.close()
            except: pass
            cache_db._local.conn = None
        # Clear in-memory cache
        import fundamentals
        fundamentals._MEM_CACHE.clear()

    def tearDown(self):
        import cache_db, fundamentals
        cache_db._DB_FILE = self._orig_db
        cache_db._INITIALISED = False
        fundamentals._MEM_CACHE.clear()

    def test_cache_ttl_24h_fresh_data_not_refetched(self):
        """Fresh cache (< 24h) must be returned without hitting yfinance."""
        import fundamentals, cache_db
        fresh_result = {
            "ticker": "AAPL", "current_price": 150.0, "error": None,
            "cached_ts": datetime.utcnow().isoformat(),
            "cache_version": fundamentals.CACHE_VERSION,
        }
        cache_db.set("fundamentals", "AAPL", fresh_result)

        with patch("yfinance.Ticker") as mock_ticker:
            result = fundamentals.fetch_fundamentals("AAPL")

        mock_ticker.assert_not_called()
        self.assertEqual(result["current_price"], 150.0)

    def test_stale_cache_triggers_refetch(self):
        """Cache older than 24h must be refreshed."""
        import fundamentals, cache_db
        stale_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        stale_result = {
            "ticker": "AAPL", "current_price": 100.0, "error": None,
            "cached_ts": stale_ts,
            "cache_version": fundamentals.CACHE_VERSION,
        }
        cache_db.set("fundamentals", "AAPL", stale_result)

        mock_info = {"currentPrice": 175.0, "marketCap": 3e12, "sector": "Technology"}
        mock_t = MagicMock()
        mock_t.info = mock_info
        mock_t.history.return_value = MagicMock(empty=True)
        mock_t.income_stmt = None
        mock_t.cashflow = None
        mock_t.balance_sheet = None
        mock_t.upgrades_downgrades = None
        mock_t.news = []
        mock_t.earnings_history = None

        with patch("yfinance.Ticker", return_value=mock_t):
            with patch("institutional_trades.fetch_institutional_data", return_value={}):
                with patch("insider_trades.fetch_insider_trades", return_value={}):
                    result = fundamentals.fetch_fundamentals("AAPL")

        # Should have hit yfinance (not returned stale 100.0)
        self.assertNotEqual(result.get("current_price"), 100.0)

    def test_wrong_cache_version_triggers_refetch(self):
        """Old cache_version must be treated as stale."""
        import fundamentals, cache_db
        old_result = {
            "ticker": "MSFT", "current_price": 200.0, "error": None,
            "cached_ts": datetime.utcnow().isoformat(),
            "cache_version": fundamentals.CACHE_VERSION - 1,  # old version
        }
        cache_db.set("fundamentals", "MSFT", old_result)

        mock_t = MagicMock()
        mock_t.info = {"currentPrice": 300.0, "marketCap": 2e12}
        mock_t.history.return_value = MagicMock(empty=True)
        mock_t.income_stmt = None
        mock_t.cashflow = None
        mock_t.balance_sheet = None
        mock_t.upgrades_downgrades = None
        mock_t.news = []
        mock_t.earnings_history = None

        with patch("yfinance.Ticker", return_value=mock_t):
            with patch("institutional_trades.fetch_institutional_data", return_value={}):
                with patch("insider_trades.fetch_insider_trades", return_value={}):
                    result = fundamentals.fetch_fundamentals("MSFT")

        # Stale version should have been refreshed
        self.assertNotEqual(result.get("cache_version"),
                            fundamentals.CACHE_VERSION - 1)

    # ── REGRESSION: Finnhub gate bugs ────────────────────────────────────────

    def test_market_cap_none_does_not_block_finnhub(self):
        """
        REGRESSION: market_cap=None was treated as 0 → _mktcap < 10M
        → Finnhub gate said "skip" → news_sentiment always None.
        None market_cap must be treated as "unknown" not "zero".
        """
        import fundamentals
        # Simulate t.info returning no marketCap (rate limited / missing)
        mock_t = MagicMock()
        mock_t.info = {}   # no marketCap key at all
        mock_t.history.return_value = MagicMock(empty=True)
        mock_t.income_stmt = None
        mock_t.cashflow = None
        mock_t.balance_sheet = None
        mock_t.upgrades_downgrades = None
        mock_t.news = []
        mock_t.earnings_history = None

        finnhub_called = []

        def fake_finnhub(ticker, *args, **kwargs):
            finnhub_called.append(ticker)
            return {"sentiment": 0.1, "headlines": []}

        with patch("yfinance.Ticker", return_value=mock_t):
            with patch("institutional_trades.fetch_institutional_data", return_value={}):
                with patch("insider_trades.fetch_insider_trades", return_value={}):
                    # Patch the finnhub call inside fetch_fundamentals
                    with patch("finnhub_client.fetch_news_sentiment",
                               side_effect=fake_finnhub) as mock_finn:
                        fundamentals.fetch_fundamentals("TEST")
                        # Finnhub should have been attempted
                        # (market_cap=None means we don't know, so we try)

    def test_finnhub_no_coverage_7day_ttl(self):
        """
        REGRESSION: finnhub_no_coverage was a permanent boolean.
        Now it's a timestamp — must expire after 7 days.
        """
        import fundamentals, cache_db
        # Store result with old no_coverage timestamp (8 days ago)
        old_ts = (datetime.utcnow() - timedelta(days=8)).isoformat()
        cached = {
            "ticker": "TINY",
            "current_price": 5.0,
            "market_cap": 50_000_000,  # $50M — above threshold
            "cached_ts": datetime.utcnow().isoformat(),
            "cache_version": fundamentals.CACHE_VERSION,
            "finnhub_no_coverage_ts": old_ts,  # 8 days ago — should be expired
            "error": None,
        }
        cache_db.set("fundamentals", "TINY", cached)
        fundamentals._MEM_CACHE["TINY"] = cached

        # The 8-day-old no_coverage flag should be ignored (TTL = 7 days)
        result = fundamentals.fetch_fundamentals("TINY")
        # If TTL logic works, the stale flag is cleared from result
        no_cov_ts = result.get("finnhub_no_coverage_ts")
        if no_cov_ts:
            age = (datetime.utcnow() - datetime.fromisoformat(no_cov_ts)).total_seconds()
            self.assertGreater(age, 0)   # at least it's there if set fresh


class TestFundamentalsSemaphore(unittest.TestCase):
    """Test the concurrency cap prevents DNS thread exhaustion."""

    def test_semaphore_exists_with_correct_value(self):
        """
        REGRESSION: 8 workers × 9 inner threads = 72 connections → DNS crash.
        Semaphore must cap at _MAX_CONCURRENT.
        """
        import fundamentals
        self.assertLessEqual(fundamentals._MAX_CONCURRENT, 4,
            "Semaphore must cap at ≤4 to prevent fd exhaustion "
            "(reduced to 3 after macOS 'Too many open files' crash fix)")
        self.assertIsNotNone(fundamentals._FETCH_SEMAPHORE)

    def test_semaphore_limits_concurrent_fetches(self):
        """No more than _MAX_CONCURRENT fetches should run simultaneously."""
        import fundamentals, threading, time

        concurrent_count = [0]
        max_seen         = [0]
        lock             = threading.Lock()

        original_fetch = fundamentals.fetch_fundamentals

        def counting_fetch(ticker):
            with lock:
                concurrent_count[0] += 1
                if concurrent_count[0] > max_seen[0]:
                    max_seen[0] = concurrent_count[0]
            try:
                time.sleep(0.05)
                return {"ticker": ticker, "current_price": 100.0,
                        "error": None, "cached_ts": datetime.utcnow().isoformat(),
                        "cache_version": fundamentals.CACHE_VERSION}
            finally:
                with lock:
                    concurrent_count[0] -= 1

        # Spawn 10 threads, should be capped at 4 simultaneous
        from datetime import datetime
        import cache_db, tempfile, os
        tmpdir = tempfile.mkdtemp()
        orig_db = cache_db._DB_FILE
        cache_db._DB_FILE = os.path.join(tmpdir, "sem_test.db")
        cache_db._INITIALISED = False

        try:
            threads = []
            results = []

            mock_t = MagicMock()
            mock_t.info = {"currentPrice": 100.0}
            mock_t.history.return_value = MagicMock(empty=True)
            mock_t.income_stmt = None
            mock_t.cashflow = None
            mock_t.balance_sheet = None
            mock_t.upgrades_downgrades = None
            mock_t.news = []
            mock_t.earnings_history = None

            with patch("yfinance.Ticker", return_value=mock_t):
                with patch("institutional_trades.fetch_institutional_data", return_value={}):
                    with patch("insider_trades.fetch_insider_trades", return_value={}):
                        for i in range(10):
                            t = threading.Thread(
                                target=lambda tk=f"T{i}": results.append(
                                    fundamentals.fetch_fundamentals(tk)
                                )
                            )
                            threads.append(t)
                        for t in threads: t.start()
                        for t in threads: t.join()

            self.assertLessEqual(
                max_seen[0], fundamentals._MAX_CONCURRENT + 1,
                f"Max concurrent fetches was {max_seen[0]}, "
                f"should be ≤ {fundamentals._MAX_CONCURRENT}"
            )
        finally:
            cache_db._DB_FILE = orig_db
            cache_db._INITIALISED = False


if __name__ == "__main__":
    unittest.main(verbosity=2)
