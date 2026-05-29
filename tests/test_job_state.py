"""
Tests for job_state.py

Regression tests for bugs we actually hit:
  - BUG: progress() returned total=1 (default) when total was missing
    from state file, causing sidebar to show "4079/1" and full red bar
  - BUG: daily_job.py didn't call js.start(), leaving no total/started_at
  - BUG: stale "running" state surviving app restarts
"""

import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestJobState(unittest.TestCase):

    def setUp(self):
        """Each test gets its own temp directory so state files don't collide."""
        self.tmpdir = tempfile.mkdtemp()
        # Patch _DIR in job_state so files go to tmpdir
        import job_state as js_mod
        self._orig_dir = js_mod._DIR
        js_mod._DIR = self.tmpdir

    def tearDown(self):
        import job_state as js_mod
        js_mod._DIR = self._orig_dir

    def _make(self, job_id="test_job"):
        from job_state import JobState
        return JobState(job_id)

    # ── REGRESSION: 4079/1 bug ────────────────────────────────────────────────

    def test_progress_missing_total_returns_zero_not_one(self):
        """
        REGRESSION: total used to default to 1, causing '4079/1' in sidebar.
        Now must default to 0 so sidebar can detect it as unknown.
        """
        js = self._make()
        # Manually write a partial state (like daily_job.py used to produce)
        state_path = os.path.join(self.tmpdir, ".job_test_job.json")
        with open(state_path, "w") as f:
            json.dump({"status": "running", "done": 4079, "current": "GOSS"}, f)

        done, total, current = js.progress()
        self.assertEqual(done, 4079)
        self.assertEqual(total, 0,
            "total should be 0 (unknown) when missing from state — not 1. "
            "A total of 1 causes '4079/1' display in sidebar.")
        self.assertEqual(current, "GOSS")

    def test_progress_bar_pct_with_missing_total(self):
        """
        REGRESSION: when total=0, sidebar should use fallback (not divide by 1).
        Simulates the sidebar calculation to verify it won't hit 100%.
        """
        done  = 4079
        total = 0   # unknown

        # Replicate the sidebar calculation
        _known_total = total if total > 1 else None
        _bar_total   = _known_total or max(done + 1, 7250)
        pct          = min(0.99, done / _bar_total)

        self.assertIsNone(_known_total)
        self.assertEqual(_bar_total, 7250)
        self.assertLess(pct, 1.0, "Progress bar must not show 100% when total is unknown")
        self.assertAlmostEqual(pct, 4079 / 7250, places=4)

    # ── Normal lifecycle ──────────────────────────────────────────────────────

    def test_initial_state_is_idle(self):
        js = self._make()
        self.assertTrue(js.is_idle())
        self.assertFalse(js.is_running())
        self.assertFalse(js.is_done())

    def test_start_sets_total_and_running(self):
        js = self._make()
        js.start(total=895)
        self.assertTrue(js.is_running())
        done, total, current = js.progress()
        self.assertEqual(total, 895)
        self.assertEqual(done, 0)
        self.assertIsNotNone(js.started_at())

    def test_update_increments_progress(self):
        js = self._make()
        js.start(total=100)
        js.update(done=42, current="AAPL")
        done, total, current = js.progress()
        self.assertEqual(done, 42)
        self.assertEqual(total, 100)
        self.assertEqual(current, "AAPL")

    def test_finish_marks_done(self):
        js = self._make()
        js.start(total=10)
        js.update(done=10, current="LAST")
        js.finish(result=[{"ticker": "AAPL"}])
        self.assertTrue(js.is_done())
        self.assertFalse(js.is_running())
        result = js.result()
        self.assertEqual(len(result), 1)

    def test_cancel_flow(self):
        js = self._make()
        js.start(total=100)
        js.update(done=50, current="MID")
        js.cancel()
        self.assertTrue(js.is_cancelling())
        # finish() after cancel should mark as "cancelled" not "done"
        js.finish(result=[])
        self.assertTrue(js.was_cancelled())
        # is_done() returns True for both "done" and "cancelled" (job finished)
        # so check the actual status string instead
        self.assertNotEqual(js.state().get("status"), "done",
            "After cancel+finish, status should be 'cancelled', not 'done'")

    def test_reset_clears_state(self):
        js = self._make()
        js.start(total=50)
        js.reset()
        self.assertTrue(js.is_idle())

    # ── Concurrent writes ─────────────────────────────────────────────────────

    def test_concurrent_updates_dont_corrupt_state(self):
        """Multiple threads updating progress shouldn't corrupt the state file."""
        js = self._make()
        js.start(total=1000)
        errors = []

        def worker(start, end):
            try:
                for i in range(start, end):
                    js.update(done=i, current=f"T{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i*100, (i+1)*100))
                   for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent updates caused errors: {errors}")
        # State should still be valid JSON
        done, total, _ = js.progress()
        self.assertEqual(total, 1000)
        self.assertIsInstance(done, int)

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_progress_on_nonexistent_file_returns_zeros(self):
        js = self._make("nonexistent_job")
        done, total, current = js.progress()
        self.assertEqual(done, 0)
        self.assertEqual(total, 0)
        self.assertEqual(current, "")

    def test_started_at_present_after_start(self):
        js = self._make()
        js.start(total=10)
        started = js.started_at()
        self.assertIsNotNone(started)
        self.assertNotEqual(started, "")
        # Should be parseable as datetime
        from datetime import datetime
        dt = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
        self.assertIsNotNone(dt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
