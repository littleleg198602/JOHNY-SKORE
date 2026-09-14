from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.services.ohlc_quality import assess_daily_ohlc


AS_OF = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def frame(closes: list[object], dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes}, index=pd.to_datetime(dates, utc=True))


class OhlcQualityTests(unittest.TestCase):
    def test_weekend_close_is_usable_on_monday(self) -> None:
        result = assess_daily_ohlc(
            frame([100.0] * 60, ["2026-06-22"] * 59 + ["2026-09-11"]),
            as_of=AS_OF,
        )

        self.assertTrue(result.price_usable)
        self.assertTrue(result.history_usable)
        self.assertEqual(100.0, result.close)
        self.assertEqual("2026-09-11", result.close_at.date().isoformat())

    def test_holiday_gap_is_usable_but_old_close_is_rejected(self) -> None:
        recent = assess_daily_ohlc(
            frame([100.0] * 60, ["2026-06-22"] * 59 + ["2026-09-08"]),
            as_of=AS_OF,
        )
        stale = assess_daily_ohlc(
            frame([100.0] * 60, ["2026-06-22"] * 59 + ["2026-09-06"]),
            as_of=AS_OF,
        )

        self.assertTrue(recent.price_usable)
        self.assertFalse(stale.price_usable)
        self.assertIsNone(stale.close)
        self.assertTrue(any("zastaralý" in warning for warning in stale.warnings))

    def test_all_nan_non_numeric_and_future_close_are_never_used(self) -> None:
        invalid = assess_daily_ohlc(
            frame([float("nan"), "none", 0], ["2026-09-10", "2026-09-11", "2026-09-12"]),
            as_of=AS_OF,
        )
        future = assess_daily_ohlc(
            frame([100.0] * 60, ["2026-06-22"] * 59 + ["2026-09-15"]),
            as_of=AS_OF,
        )

        self.assertFalse(invalid.price_usable)
        self.assertFalse(future.price_usable)
        self.assertIsNone(future.close)

    def test_short_history_has_price_but_not_technical_history(self) -> None:
        result = assess_daily_ohlc(
            frame([100.0] * 10, ["2026-09-11"] * 10),
            as_of=AS_OF,
        )

        self.assertTrue(result.price_usable)
        self.assertFalse(result.history_usable)
        self.assertTrue(any("historie" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
