from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

import pandas as pd

from market_checker_app.services.ohlc_quality import assess_daily_ohlc
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.us_equity_calendar import (
    previous_us_equity_sessions,
)


MONDAY_PREOPEN = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def full_frame(session_dates: list[date], closes: list[object] | None = None) -> pd.DataFrame:
    values = closes or [100.0] * len(session_dates)
    numeric_for_shape = [
        float(value)
        if isinstance(value, (int, float))
        and value not in {float("inf"), float("-inf")}
        else 100.0
        for value in values
    ]
    return pd.DataFrame(
        {
            "Open": numeric_for_shape,
            "High": [value + 1.0 for value in numeric_for_shape],
            "Low": [max(0.01, value - 1.0) for value in numeric_for_shape],
            "Close": values,
            "Volume": [1000.0] * len(session_dates),
        },
        index=pd.to_datetime([day.isoformat() for day in session_dates], utc=True),
    )


def session_dates_ending(day: date, count: int = 66) -> list[date]:
    return [
        session.session_date
        for session in previous_us_equity_sessions(day, count)
    ]


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

        self.assertTrue(result.price_usable)
        self.assertTrue(result.history_usable)
        self.assertEqual("2026-09-04", result.close_at.date().isoformat())

    def test_non_finite_latest_close_cannot_be_used(self) -> None:
        dates = session_dates_ending(date(2026, 9, 11))
        closes: list[object] = [100.0] * len(dates)
        closes[-1] = float("inf")
        result = assess_daily_ohlc(
            full_frame(dates, closes),
            as_of=MONDAY_PREOPEN,
        )

        self.assertFalse(result.price_usable)
        self.assertIsNone(result.close)
        self.assertTrue(any("nekonečnou" in warning for warning in result.warnings))
        self.assertTrue(
            any("poslední očekávaná" in warning.lower() for warning in result.warnings)
        )

    def test_duplicate_rows_count_as_one_session_not_sixty_six(self) -> None:
        repeated = [date(2026, 9, 11)] * 66
        result = assess_daily_ohlc(
            full_frame(repeated),
            as_of=MONDAY_PREOPEN,
        )

        self.assertTrue(result.price_usable)
        self.assertFalse(result.history_usable)
        self.assertEqual(1, result.observation_count)
        self.assertTrue(any("duplicit" in warning.lower() for warning in result.warnings))

    def test_open_current_day_bar_is_ignored_and_prior_closed_price_survives(self) -> None:
        dates = session_dates_ending(date(2026, 9, 11), 66) + [date(2026, 9, 14)]
        closes: list[object] = [100.0] * 66 + [999.0]
        result = assess_daily_ohlc(
            full_frame(dates, closes),
            as_of=MONDAY_PREOPEN,
        )

        self.assertTrue(result.price_usable)
        self.assertTrue(result.history_usable)
        self.assertEqual(66, result.observation_count)
        self.assertEqual(100.0, result.close)
        self.assertEqual(date(2026, 9, 11), result.close_at.date())
        self.assertTrue(any("ignorována" in warning for warning in result.warnings))

    def test_only_open_current_day_bar_cannot_supply_price(self) -> None:
        result = assess_daily_ohlc(
            full_frame([date(2026, 9, 14)], [999.0]),
            as_of=MONDAY_PREOPEN,
        )

        self.assertFalse(result.price_usable)
        self.assertFalse(result.history_usable)
        self.assertIsNone(result.close)
        self.assertEqual(0, result.observation_count)

    def test_missing_latest_closed_session_rejects_stale_frame(self) -> None:
        result = assess_daily_ohlc(
            full_frame(session_dates_ending(date(2026, 9, 10))),
            as_of=MONDAY_PREOPEN,
        )

        self.assertFalse(result.price_usable)
        self.assertIsNone(result.close)
        self.assertTrue(any("2026-09-11" in warning for warning in result.warnings))

    def test_black_friday_half_day_is_used_only_after_early_close(self) -> None:
        dates = session_dates_ending(date(2026, 11, 27))
        closes: list[object] = [100.0] * (len(dates) - 1) + [125.0]
        before = assess_daily_ohlc(
            full_frame(dates, closes),
            as_of=datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc),
        )
        after = assess_daily_ohlc(
            full_frame(dates, closes),
            as_of=datetime(2026, 11, 27, 18, 5, tzinfo=timezone.utc),
        )

        self.assertTrue(before.price_usable)
        self.assertFalse(before.history_usable)
        self.assertEqual(date(2026, 11, 25), before.close_at.date())
        self.assertEqual(100.0, before.close)
        self.assertTrue(after.price_usable)
        self.assertTrue(after.history_usable)
        self.assertEqual(125.0, after.close)
        self.assertEqual(
            datetime(2026, 11, 27, 18, tzinfo=timezone.utc),
            after.close_at,
        )

    def test_close_only_frame_can_supply_price_but_not_technical_history(self) -> None:
        dates = session_dates_ending(date(2026, 9, 11))
        close_only = pd.DataFrame(
            {"Close": [100.0] * len(dates)},
            index=pd.to_datetime([day.isoformat() for day in dates], utc=True),
        )
        result = assess_daily_ohlc(close_only, as_of=MONDAY_PREOPEN)

        self.assertTrue(result.price_usable)
        self.assertFalse(result.history_usable)
        self.assertTrue(any("Open" in warning for warning in result.warnings))

    def test_stale_ohlc_is_not_silently_replaced_by_undated_metadata_quote(self) -> None:
        stale = full_frame(session_dates_ending(date(2026, 9, 10)))
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
