from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

import pandas as pd

from market_checker_app.services.ohlc_quality import assess_daily_ohlc
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.us_equity_calendar_service import sessions_between


class OhlcQualityTests(unittest.TestCase):
    def test_unique_friday_history_is_usable_on_monday_before_open(self) -> None:
        result = assess_daily_ohlc(
            full_frame(session_dates_ending(date(2026, 9, 11))),
            as_of=MONDAY_PREOPEN,
        )

        self.assertTrue(result.price_usable)
        self.assertTrue(result.history_usable)
        self.assertEqual(66, result.observation_count)
        self.assertEqual(100.0, result.close)
        self.assertEqual("2026-09-11", result.close_at.date().isoformat())
        self.assertEqual(20, result.close_at.hour)  # EDT close in UTC

    def test_labor_day_gap_uses_friday_as_latest_closed_session(self) -> None:
        tuesday_preopen = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        result = assess_daily_ohlc(
            full_frame(session_dates_ending(date(2026, 9, 4))),
            as_of=tuesday_preopen,
        )

def sessions_ending(end: str, count: int = 60) -> list[pd.Timestamp]:
    end_label = pd.Timestamp(end, tz="UTC")
    start = end_label - pd.Timedelta(days=max(120, count * 3))
    return list(sessions_between(start, end_label)[-count:])


def frame_from_sessions(end: str, count: int = 60, close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({"Close": [close] * count}, index=sessions_ending(end, count))

    def test_non_finite_latest_close_cannot_be_used(self) -> None:
        dates = session_dates_ending(date(2026, 9, 11))
        closes: list[object] = [100.0] * len(dates)
        closes[-1] = float("inf")
        result = assess_daily_ohlc(
            full_frame(dates, closes),
            as_of=MONDAY_PREOPEN,
        )

class OhlcQualityTests(unittest.TestCase):
    def test_weekend_close_is_usable_on_monday(self) -> None:
        result = assess_daily_ohlc(frame_from_sessions("2026-09-11"), as_of=AS_OF)

        self.assertTrue(result.price_usable)
        self.assertTrue(result.history_usable)
        self.assertEqual(66, result.observation_count)
        self.assertEqual(100.0, result.close)
        self.assertEqual(date(2026, 9, 11), result.close_at.date())
        self.assertTrue(any("ignorována" in warning for warning in result.warnings))

    def test_missing_latest_completed_session_is_rejected(self) -> None:
        result = assess_daily_ohlc(frame_from_sessions("2026-09-08"), as_of=AS_OF)

        self.assertFalse(result.price_usable)
        self.assertIsNone(result.close)
        self.assertTrue(any("neodpovídá" in warning for warning in result.warnings))

    def test_all_nan_non_numeric_infinite_and_future_close_are_never_used(self) -> None:
        invalid = pd.DataFrame(
            {"Close": [float("nan"), "none", 0, float("inf")]},
            index=pd.to_datetime(["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"], utc=True),
        )
        future = frame_from_sessions("2026-09-15")

        self.assertFalse(assess_daily_ohlc(invalid, as_of=AS_OF).price_usable)
        self.assertFalse(assess_daily_ohlc(future, as_of=AS_OF).price_usable)

    def test_duplicate_sessions_do_not_create_technical_history(self) -> None:
        duplicate = pd.DataFrame(
            {"Close": [100.0] * 60},
            index=pd.to_datetime(["2026-09-11"] * 60, utc=True),
        )
        result = assess_daily_ohlc(duplicate, as_of=AS_OF)

        self.assertTrue(result.price_usable)
        self.assertFalse(result.history_usable)
        self.assertEqual(1, result.observation_count)
        self.assertTrue(any("duplicit" in warning for warning in result.warnings))

    def test_short_history_has_price_but_not_technical_history(self) -> None:
        result = assess_daily_ohlc(frame_from_sessions("2026-09-11", 10), as_of=AS_OF)

        self.assertTrue(result.price_usable)
        self.assertFalse(result.history_usable)

    def test_unfinished_current_session_is_not_usable(self) -> None:
        as_of_before_close = datetime(2026, 9, 15, 16, tzinfo=timezone.utc)
        result = assess_daily_ohlc(frame_from_sessions("2026-09-15"), as_of=as_of_before_close)

        self.assertFalse(result.price_usable)

    def test_stale_ohlc_is_not_silently_replaced_by_undated_metadata_quote(self) -> None:
        stale = frame_from_sessions("2026-09-08")
        price, source = PipelineService._select_current_price(
            ohlc=stale,
            tech_source="yfinance",
            yahoo_metadata_price=123.0,
            as_of=MONDAY_PREOPEN,
        )
        quote, quote_source = PipelineService._select_current_price(
            ohlc=pd.DataFrame(),
            tech_source="yfinance",
            yahoo_metadata_price=123.0,
            as_of=MONDAY_PREOPEN,
        )

        self.assertIsNone(price)
        self.assertEqual("ohlc_unusable", source)
        self.assertEqual(123.0, quote)
        self.assertEqual("yahoo_metadata_quote_undated", quote_source)


if __name__ == "__main__":
    unittest.main()
