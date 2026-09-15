from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.us_equity_calendar import (
    latest_closed_us_equity_session,
    previous_us_equity_sessions,
)


def _closed_index(count: int) -> pd.DatetimeIndex:
    latest = latest_closed_us_equity_session(datetime.now(timezone.utc))
    sessions = previous_us_equity_sessions(latest.session_date, count)
    return pd.to_datetime(
        [session.session_date.isoformat() for session in sessions],
        utc=True,
    )


class CurrentPriceSelectionTests(unittest.TestCase):
    def test_fresh_yahoo_ohlc_close_beats_stale_metadata_quote(self) -> None:
        index = _closed_index(2)
        price, source = PipelineService._select_current_price(
            ohlc=pd.DataFrame({"Close": [100.0, 101.25]}, index=index),
            tech_source="yfinance_ohlc_cache",
            yahoo_metadata_price=95.0,
        )

        self.assertEqual(101.25, price)
        self.assertEqual("yahoo_ohlc_close", source)

    def test_mt5_close_beats_metadata_quote(self) -> None:
        index = _closed_index(1)
        price, source = PipelineService._select_current_price(
            ohlc=pd.DataFrame({"Close": [100.0]}, index=index),
            tech_source="mt5",
            yahoo_metadata_price=99.0,
        )

        self.assertEqual(100.0, price)
        self.assertEqual("mt5_close", source)

    def test_metadata_is_only_fallback_when_no_valid_close_exists(self) -> None:
        price, source = PipelineService._select_current_price(
            ohlc=pd.DataFrame({"Close": [float("nan")]}),
            tech_source="yfinance_bulk",
            yahoo_metadata_price="42.5",
        )

        self.assertEqual(42.5, price)
        self.assertEqual("yahoo_metadata_quote_undated", source)


if __name__ == "__main__":
    unittest.main()
