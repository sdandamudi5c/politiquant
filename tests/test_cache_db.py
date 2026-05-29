"""
Tests for cache_db.py (SQLite cache)

Regression tests for bugs we actually hit:
  - BUG: fundamentals_cache.json corrupted at 14.7 MB after mid-write crash
    → atomic SQLite writes should prevent this
  - BUG: concurrent writes from parallel yfinance threads caused race conditions
    → WAL mode + per-thread connections should handle this
  - BUG: _MEM_CACHE loaded stale data from dead process
    → SQLite always reads from disk so cross-process staleness is avoided
"""

import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCacheDB(unittest.TestCase):

    def setUp(self):
        """Each test uses a temp DB so they don't interfere with production cache."""
        self.tmpdir = tempfile.mkdtemp()
        import cache_db
        self._orig_db = cache_db._DB_FILE
        cache_db._DB_FILE  = os.path.join(self.tmpdir, "test_cache.db")
        cache_db._INITIALISED = False
        # Reset thread-local connections
        if hasattr(cache_db._local, "conn"):
            try:
                cache_db._local.conn.close()
            except Exception:
                pass
            cache_db._local.conn = None

    def tearDown(self):
        import cache_db
        cache_db._DB_FILE = self._orig_db
        cache_db._INITIALISED = False
        if hasattr(cache_db._local, "conn"):
            try:
                cache_db._local.conn.close()
            except Exception:
                pass
            cache_db._local.conn = None

    # ── Basic operations ──────────────────────────────────────────────────────

    def test_get_missing_returns_none(self):
        import cache_db
        self.assertIsNone(cache_db.get("fundamentals", "AAPL"))

    def test_set_and_get_roundtrip(self):
        import cache_db
        data = {"ticker": "AAPL", "price": 150.0, "cached_ts": "2026-01-01T00:00:00"}
        cache_db.set("fundamentals", "AAPL", data)
        result = cache_db.get("fundamentals", "AAPL")
        self.assertEqual(result, data)

    def test_set_overwrites_existing(self):
        import cache_db
        cache_db.set("fundamentals", "AAPL", {"price": 100.0})
        cache_db.set("fundamentals", "AAPL", {"price": 200.0})
        result = cache_db.get("fundamentals", "AAPL")
        self.assertEqual(result["price"], 200.0)

    def test_different_namespaces_dont_collide(self):
        import cache_db
        cache_db.set("fundamentals", "AAPL", {"source": "fundamentals"})
        cache_db.set("insider",      "AAPL", {"source": "insider"})
        self.assertEqual(cache_db.get("fundamentals", "AAPL")["source"], "fundamentals")
        self.assertEqual(cache_db.get("insider",      "AAPL")["source"], "insider")

    def test_clear_removes_namespace_entries(self):
        import cache_db
        cache_db.set("fundamentals", "AAPL", {"price": 100})
        cache_db.set("fundamentals", "MSFT", {"price": 200})
        cache_db.set("insider",      "AAPL", {"score": 5})
        cache_db.clear("fundamentals")
        self.assertIsNone(cache_db.get("fundamentals", "AAPL"))
        self.assertIsNone(cache_db.get("fundamentals", "MSFT"))
        # Other namespaces unaffected
        self.assertIsNotNone(cache_db.get("insider", "AAPL"))

    def test_delete_old_removes_stale_entries(self):
        import cache_db
        cache_db.set("fundamentals", "OLD", {"price": 1})
        time.sleep(0.1)
        cache_db.delete_old("fundamentals", older_than_seconds=0.05)
        self.assertIsNone(cache_db.get("fundamentals", "OLD"))

    def test_delete_old_keeps_fresh_entries(self):
        import cache_db
        cache_db.set("fundamentals", "FRESH", {"price": 1})
        deleted = cache_db.delete_old("fundamentals", older_than_seconds=3600)
        self.assertEqual(deleted, 0)
        self.assertIsNotNone(cache_db.get("fundamentals", "FRESH"))

    # ── REGRESSION: corruption under concurrent writes ────────────────────────

    def test_concurrent_writes_no_corruption(self):
        """
        REGRESSION: JSON files corrupted under parallel writes (14.7MB crash).
        SQLite with WAL mode must handle 20 concurrent writers without data loss.
        """
        import cache_db
        errors   = []
        n_writes = 50

        def writer(ticker):
            try:
                cache_db.set("fundamentals", ticker, {
                    "ticker": ticker,
                    "price":  float(hash(ticker) % 500 + 10),
                    "cached_ts": "2026-01-01T00:00:00",
                })
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"T{i:03d}",))
                   for i in range(n_writes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent writes caused errors: {errors}")
        # Spot-check a few entries
        for i in [0, 10, 25, 49]:
            result = cache_db.get("fundamentals", f"T{i:03d}")
            self.assertIsNotNone(result, f"T{i:03d} missing after concurrent writes")
            self.assertEqual(result["ticker"], f"T{i:03d}")

    def test_concurrent_reads_no_errors(self):
        """Multiple threads reading simultaneously should all succeed."""
        import cache_db
        cache_db.set("fundamentals", "SHARED", {"price": 42.0})
        errors  = []
        results = []

        def reader():
            try:
                r = cache_db.get("fundamentals", "SHARED")
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], "Concurrent reads caused errors")
        self.assertEqual(len(results), 20)
        self.assertTrue(all(r and r["price"] == 42.0 for r in results))

    # ── Data integrity ────────────────────────────────────────────────────────

    def test_stores_and_retrieves_complex_nested_data(self):
        """Cache must handle the complex nested dicts fundamentals.py produces."""
        import cache_db
        complex_data = {
            "ticker": "AAPL",
            "news": [{"headline": "Apple beats", "score": 0.8}],
            "insider_data": {"buyers": [{"name": "Tim Cook", "shares": 10000}]},
            "factor_pts": {"analyst_upside": 9.0, "rsi": -3.0},
            "cached_ts": "2026-01-01T12:00:00",
            "none_field": None,
            "zero_field": 0,
            "false_field": False,
        }
        cache_db.set("fundamentals", "AAPL", complex_data)
        result = cache_db.get("fundamentals", "AAPL")
        self.assertEqual(result["none_field"], None)
        self.assertEqual(result["zero_field"], 0)
        self.assertEqual(result["false_field"], False)
        self.assertEqual(result["news"][0]["score"], 0.8)

    def test_db_file_created_on_first_write(self):
        import cache_db
        self.assertFalse(os.path.exists(cache_db._DB_FILE))
        cache_db.set("test", "key", {"val": 1})
        self.assertTrue(os.path.exists(cache_db._DB_FILE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
