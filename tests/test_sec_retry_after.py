import unittest
from urllib.error import HTTPError

from market_checker_app.collectors.sec_edgar_client import SecEdgarClient


class SecRetryAfterTest(unittest.TestCase):
    def test_retry_after_header_is_honored_and_bounded(self):
        url = "https://data.sec.gov/submissions/CIK0000320193.json"
        calls = 0
        sleeps = []

        def transport(request_url, headers, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPError(url, 429, "limit", {"Retry-After": "5"}, None)
            return {"ok": True}

        client = SecEdgarClient(
            user_agent="Test App test@example.com", transport=transport,
            sleep=sleeps.append, min_request_interval_seconds=0.11,
        )
        self.assertEqual(client._request_json(url), {"ok": True})
        self.assertIn(5.0, sleeps)
        error = HTTPError(url, 429, "limit", {"Retry-After": "3600"}, None)
        self.assertEqual(client._retry_delay(1, error), 120.0)


if __name__ == "__main__":
    unittest.main()
