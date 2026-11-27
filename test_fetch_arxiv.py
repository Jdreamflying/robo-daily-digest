import contextlib
import io
import unittest
from datetime import date
from unittest.mock import Mock, patch

import requests

from daily_arxiv.fetch_arxiv import query_day


class QueryDayRetryTests(unittest.TestCase):
    def test_retries_after_429_and_recovers_on_200(self):
        rate_limited = requests.Response()
        rate_limited.status_code = 429
        rate_limited.headers["Retry-After"] = "0"
        rate_limited.url = "https://export.arxiv.org/api/query"
        rate_limited.raw = io.BytesIO()

        success = requests.Response()
        success.status_code = 200
        success.url = "https://export.arxiv.org/api/query"
        success._content = (
            b'<feed xmlns="http://www.w3.org/2005/Atom" '
            b'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            b"<opensearch:totalResults>0</opensearch:totalResults></feed>"
        )

        session = Mock()
        session.get.side_effect = [rate_limited, success]
        stderr = io.StringIO()

        with patch("daily_arxiv.fetch_arxiv.time.sleep") as sleep, contextlib.redirect_stderr(stderr):
            entries = query_day(
                {"category": "cs.RO", "url": "https://export.arxiv.org/api/query"},
                date(2026, 11, 24), session,
            )

        self.assertEqual(entries, [])
        self.assertEqual(session.get.call_count, 2)
        sleep.assert_called_once_with(0.0)
        self.assertIn("arXiv HTTP 429", stderr.getvalue())
        self.assertIn("retry 1/5", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
