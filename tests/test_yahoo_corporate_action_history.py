from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.services.yahoo_corporate_action_history import (
    YahooCorporateActionHistoryClient,
)


class YahooCorporateActionHistoryClientTests(unittest.TestCase):
    def test_bulk_download_explicitly_requests_actions_without_auto_adjust(self) -> None:
        history = pd.DataFrame(
            {
                "Open": [99.0, 100.0],
                "High": [101.0, 102.0],
                "Low": [98.0, 99.0],
                "Close": [100.0, 101.0],
                "Volume": [1000.0, 1100.0],
                "Dividends": [0.0, 0.0],
                "Stock Splits": [0.0, 0.0],
            },
            index=pd.to_datetime(["2026-09-10", "2026-09-11"], utc=True),
        )
        client = YahooCorporateActionHistoryClient(retry_attempts=1)

        with patch(
            "market_checker_app.services.yahoo_corporate_action_history.yf.download",
            return_value=history,
        ) as mocked:
            frames, warnings = client.fetch_batch(["AAPL"], period="1y")

        self.assertEqual({}, warnings)
        self.assertIn("AAPL", frames)
        self.assertIn("Stock Splits", frames["AAPL"].columns)
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs["actions"])
        self.assertFalse(kwargs["auto_adjust"])
        self.assertEqual("ticker", kwargs["group_by"])

    def test_frame_without_split_actions_is_not_accepted(self) -> None:
        history = pd.DataFrame(
            {"Close": [100.0]},
            index=pd.to_datetime(["2026-09-11"], utc=True),
        )
        client = YahooCorporateActionHistoryClient(retry_attempts=1)

        with patch(
            "market_checker_app.services.yahoo_corporate_action_history.yf.download",
            return_value=history,
        ), patch.object(
            YahooCorporateActionHistoryClient,
            "fetch_one",
            return_value=(None, "missing split actions"),
        ):
            frames, warnings = client.fetch_batch(["AAPL"], period="1y")

        self.assertEqual({}, frames)
        self.assertIn("AAPL", warnings)
        self.assertIn("split", warnings["AAPL"])


if __name__ == "__main__":
    unittest.main()
