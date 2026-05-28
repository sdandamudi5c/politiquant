"""
Tests for .gitignore correctness

Regression tests for bugs we actually hit:
  - BUG: .gitignore had inline comments: `api_keys.json # Finnhub keys`
    Git doesn't support inline comments — the whole line became the pattern,
    so api_keys.json was NEVER actually ignored and would have been committed.
  - BUG: portfolio.json (personal holdings) was not in .gitignore at all
  - BUG: cache.db (SQLite) not added to .gitignore after migration

Tests parse the actual .gitignore and verify critical files are covered
by clean patterns (no inline comments).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GITIGNORE    = os.path.join(_PROJECT_ROOT, ".gitignore")


def _get_patterns() -> list[str]:
    """Return all non-comment, non-empty lines from .gitignore."""
    patterns = []
    with open(_GITIGNORE) as f:
        for line in f:
            line = line.rstrip("\n")
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                patterns.append(stripped)
    return patterns


def _is_ignored(filename: str, patterns: list[str]) -> bool:
    """Simple gitignore pattern matching for exact filenames and globs."""
    import fnmatch
    for pattern in patterns:
        # Strip trailing comments — but this should not exist after our fix
        clean = pattern.split(" #")[0].strip() if " #" in pattern else pattern
        if fnmatch.fnmatch(filename, clean):
            return True
        if fnmatch.fnmatch(filename, os.path.basename(clean)):
            return True
    return False


class TestGitignorePatterns(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.exists(_GITIGNORE), ".gitignore not found")
        self.patterns = _get_patterns()

    # ── REGRESSION: inline comments break gitignore ───────────────────────────

    def test_no_inline_comments_in_patterns(self):
        """
        REGRESSION: api_keys.json had inline comment → pattern never matched.
        Every non-comment line must be a pure pattern with no ' #' in it.
        """
        with open(_GITIGNORE) as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    self.assertNotIn(" #", stripped,
                        f".gitignore line {lineno} has inline comment: {stripped!r}\n"
                        f"Git doesn't support inline comments — move comment to its own line.")

    # ── Critical secrets must be ignored ─────────────────────────────────────

    def test_api_keys_json_is_ignored(self):
        """
        REGRESSION: api_keys.json was not being ignored due to inline comment.
        This file contains Finnhub + FRED API keys.
        """
        self.assertTrue(
            any("api_keys.json" in p for p in self.patterns),
            "api_keys.json must be in .gitignore — contains API secrets"
        )

    def test_email_config_json_is_ignored(self):
        """email_config.json contains Gmail app password."""
        self.assertTrue(
            any("email_config.json" in p for p in self.patterns),
            "email_config.json must be in .gitignore — contains Gmail credentials"
        )

    def test_portfolio_json_is_ignored(self):
        """
        REGRESSION: portfolio.json was missing from .gitignore entirely.
        Contains personal holdings (tickers, quantities, cost basis).
        """
        self.assertTrue(
            any("portfolio.json" in p for p in self.patterns),
            "portfolio.json must be in .gitignore — contains personal holdings data"
        )

    # ── Cache files must be ignored ───────────────────────────────────────────

    def test_sqlite_cache_db_is_ignored(self):
        """
        REGRESSION: cache.db was not in .gitignore after SQLite migration.
        Cache databases should not be committed.
        """
        self.assertTrue(
            any("cache.db" in p for p in self.patterns),
            "cache.db must be in .gitignore — SQLite cache database"
        )

    def test_wal_files_are_ignored(self):
        """WAL journal files from SQLite should also be ignored."""
        wal_covered = any("cache.db-wal" in p or "*.wal" in p for p in self.patterns)
        shm_covered = any("cache.db-shm" in p or "*.shm" in p for p in self.patterns)
        self.assertTrue(wal_covered, "cache.db-wal must be in .gitignore")
        self.assertTrue(shm_covered, "cache.db-shm must be in .gitignore")

    def test_json_cache_files_ignored(self):
        """Legacy JSON cache files should be ignored."""
        cache_files = [
            "fundamentals_cache.json", "insider_cache.json",
            "institutional_cache.json", "sector_cache.json",
            "trends_cache.json", "sentiment_cache.json",
            "fred_cache.json", "party_cache.json",
        ]
        for fname in cache_files:
            covered = any(fname in p for p in self.patterns)
            self.assertTrue(covered,
                f"{fname} should be in .gitignore (cache file, auto-rebuilt)")

    def test_score_history_is_ignored(self):
        """score_history.json is ML training data — not source code."""
        self.assertTrue(
            any("score_history.json" in p for p in self.patterns),
            "score_history.json must be in .gitignore"
        )

    def test_tmp_files_ignored(self):
        """Temp files from atomic cache writes must be ignored."""
        self.assertTrue(
            any("*.tmp" in p for p in self.patterns),
            "*.tmp must be in .gitignore"
        )

    def test_job_state_files_ignored(self):
        """Runtime job state files must be ignored."""
        self.assertTrue(
            any(".job_" in p for p in self.patterns),
            ".job_*.json must be in .gitignore"
        )

    # ── Safe files should NOT be ignored ─────────────────────────────────────

    def test_example_files_not_ignored(self):
        """
        api_keys.json.example and email_config.json.example are templates
        that SHOULD be committed — they must not be caught by any pattern.
        """
        patterns_str = "\n".join(self.patterns)
        # Exact match check (patterns shouldn't catch .example files)
        self.assertNotIn("api_keys.json.example", patterns_str,
            "api_keys.json.example should NOT be ignored — it's a safe template")
        self.assertNotIn("email_config.json.example", patterns_str,
            "email_config.json.example should NOT be ignored — it's a safe template")

    def test_python_files_not_ignored(self):
        """Source .py files must never be ignored."""
        for pattern in self.patterns:
            self.assertFalse(
                pattern.endswith(".py") and not pattern.startswith("!"),
                f"Pattern '{pattern}' would ignore Python source files!"
            )


class TestGitignoreFileExists(unittest.TestCase):

    def test_gitignore_file_exists(self):
        self.assertTrue(os.path.exists(_GITIGNORE),
            ".gitignore file is missing from project root")

    def test_gitignore_not_empty(self):
        with open(_GITIGNORE) as f:
            content = f.read().strip()
        self.assertGreater(len(content), 0, ".gitignore is empty")

    def test_venv_is_ignored(self):
        patterns = _get_patterns()
        self.assertTrue(
            any("venv/" in p or "venv" == p for p in patterns),
            "venv/ must be in .gitignore"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
