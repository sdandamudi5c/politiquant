"""
Tests for reddit_sentiment.py

Regression tests for bugs we actually hit:
  - BUG: Reddit app creation form was broken → switched to public JSON API
    requiring zero authentication. Must work with NO keys configured.
  - BUG: old version used praw which required OAuth setup
  - Tests that the no-auth public endpoint approach is the default
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRedditSentimentNoAuth(unittest.TestCase):
    """
    REGRESSION: Reddit sentiment must work with NO API keys.
    The old PRAW approach required OAuth which was broken on Reddit's side.
    """

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

    def tearDown(self):
        import cache_db
        cache_db._DB_FILE = self._orig_db
        cache_db._INITIALISED = False

    def test_no_praw_import_required(self):
        """
        REGRESSION: module must not import praw at module level.
        praw requires OAuth setup which was broken on Reddit.
        """
        import importlib
        import reddit_sentiment
        source = open(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "reddit_sentiment.py")
        ).read()
        # praw should not be imported at module level
        self.assertNotIn("import praw", source.split("def ")[0],
            "praw must not be imported at module level — use public JSON API instead")

    def test_no_api_keys_needed(self):
        """
        REGRESSION: fetch_reddit_sentiment must work without any API keys.
        Should NOT return an error just because keys are missing.
        """
        import reddit_sentiment
        # Mock the HTTP call to avoid real network
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "children": [
                    {"data": {
                        "title": "AAPL bullish buy calls",
                        "score": 100,
                        "permalink": "/r/wallstreetbets/comments/abc123",
                        "created_utc": 1748000000.0,
                        "selftext": "",
                    }}
                ]
            }
        }

        with patch("reddit_sentiment.requests.get", return_value=mock_response):
            result = reddit_sentiment.fetch_reddit_sentiment("AAPL")

        self.assertIsNone(result["error"],
            f"Should not error without API keys. Got: {result['error']}")
        self.assertIn("mention_count", result)
        self.assertIn("score_mod", result)
        self.assertIn("sentiment_avg", result)

    def test_result_structure(self):
        """fetch_reddit_sentiment must return all required keys."""
        import reddit_sentiment
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"children": []}}

        with patch("reddit_sentiment.requests.get", return_value=mock_response):
            result = reddit_sentiment.fetch_reddit_sentiment("NVDA")

        required_keys = [
            "ticker", "mention_count", "sentiment_avg", "subreddit_counts",
            "top_posts", "trend", "score_mod", "cached_ts", "error"
        ]
        for key in required_keys:
            self.assertIn(key, result, f"Missing required key: {key}")

    def test_score_mod_range(self):
        """score_mod must stay within -4 to +5."""
        import reddit_sentiment
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Simulate 25 highly bullish posts
        mock_response.json.return_value = {
            "data": {"children": [
                {"data": {
                    "title": f"BUY BUY BUY bullish calls moon rocket {i}",
                    "score": 1000 + i,
                    "permalink": f"/r/wallstreetbets/comments/{i}",
                    "created_utc": 1748000000.0,
                    "selftext": "",
                }} for i in range(25)
            ]}
        }

        with patch("reddit_sentiment.requests.get", return_value=mock_response):
            result = reddit_sentiment.fetch_reddit_sentiment("GME")

        self.assertGreaterEqual(result["score_mod"], -4)
        self.assertLessEqual(result["score_mod"], 5)

    def test_ticker_word_boundary_matching(self):
        """
        Ticker must match as whole word, not substring.
        e.g. 'APPS' should not match post mentioning 'AAPL'
        """
        import reddit_sentiment
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"children": [
                {"data": {
                    "title": "AAPL is going to moon",
                    "score": 50,
                    "permalink": "/r/stocks/comments/xyz",
                    "created_utc": 1748000000.0,
                    "selftext": "",
                }}
            ]}
        }

        # "AA" should NOT match in "AAPL"
        with patch("reddit_sentiment.requests.get", return_value=mock_response):
            result = reddit_sentiment.fetch_reddit_sentiment("AA")

        self.assertEqual(result["mention_count"], 0,
            "Ticker 'AA' should not match inside 'AAPL' — requires word boundary")

    def test_http_error_returns_graceful_empty(self):
        """
        Network errors must return empty result, not raise.
        The exception is caught inside _search_subreddit which returns [],
        so the outer result has mention_count=0 and no crash.
        """
        import reddit_sentiment
        with patch("reddit_sentiment.requests.get", side_effect=Exception("timeout")):
            result = reddit_sentiment.fetch_reddit_sentiment("TSLA")
        self.assertIsNotNone(result)
        self.assertEqual(result["mention_count"], 0,
            "Network error should produce mention_count=0, not raise")

    def test_caching_works(self):
        """Second call with same ticker returns cached result."""
        import reddit_sentiment
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"children": []}}

        with patch("reddit_sentiment.requests.get", return_value=mock_response) as mock_get:
            reddit_sentiment.fetch_reddit_sentiment("MSFT")
            reddit_sentiment.fetch_reddit_sentiment("MSFT")  # second call

        # requests.get should only be called for the first fetch (3 subreddits)
        # The second call should hit cache
        self.assertEqual(mock_get.call_count, 3,
            "Second call should use cache, not make new HTTP requests")


class TestSentimentScoring(unittest.TestCase):

    def test_bull_words_positive_sentiment(self):
        import reddit_sentiment
        score = reddit_sentiment._score_text("buy bullish calls moon rocket rally")
        self.assertGreater(score, 0)

    def test_bear_words_negative_sentiment(self):
        import reddit_sentiment
        score = reddit_sentiment._score_text("sell bearish puts crash dump bankrupt")
        self.assertLess(score, 0)

    def test_neutral_text_zero_sentiment(self):
        import reddit_sentiment
        score = reddit_sentiment._score_text("quarterly earnings report today")
        self.assertEqual(score, 0.0)

    def test_sentiment_range(self):
        import reddit_sentiment
        for text in [
            "buy buy buy buy bull bull calls rocket",
            "sell sell sell crash dump bankrupt",
            "",
            "neutral informational post",
        ]:
            score = reddit_sentiment._score_text(text)
            self.assertGreaterEqual(score, -1.0)
            self.assertLessEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
