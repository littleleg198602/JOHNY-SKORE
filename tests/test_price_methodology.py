from __future__ import annotations

import unittest

import pandas as pd

from market_checker_app.services.price_methodology import (
    PRICE_BASIS,
    PRICE_METHOD_VERSION,
    normalize_split_adjusted_price_frame,
)


class PriceMethodologyTests(unittest.TestCase):
    def test_split_is_neutralized_but_dividend_is_not_added_to_price_return(self) -> None:
        frame = pd.DataFrame(
            {
                "Open": [396.0, 99.0, 101.0],
                "High": [404.0, 102.0, 103.0],
                "Low": [392.0, 98.0, 100.0],
                "Close": [400.0, 100.0, 102.0],
                "Volume": [1_000.0, 4_000.0, 4_100.0],
                "Stock Splits": [0.0, 4.0, 0.0],
                "Dividends": [0.0, 0.0, 1.0],
            },
            index=pd.to_datetime(
                ["2026-01-05", "2026-01-06", "2026-01-07"],
                utc=True,
            ),
        )

        normalized = normalize_split_adjusted_price_frame(frame)

        self.assertEqual([100.0, 100.0, 102.0], normalized["Close"].tolist())
        self.assertEqual([4_000.0, 4_000.0, 4_100.0], normalized["Volume"].tolist())
        self.assertEqual([0.0, 0.0, 1.0], normalized["Dividends"].tolist())
        self.assertAlmostEqual(0.02, normalized["Close"].iloc[-1] / normalized["Close"].iloc[0] - 1.0)
        self.assertEqual(PRICE_BASIS, normalized.attrs["price_basis"])
        self.assertEqual(PRICE_METHOD_VERSION, normalized.attrs["price_method_version"])
        self.assertFalse(normalized.attrs["dividends_included"])

    def test_frame_without_actions_is_treated_as_already_split_comparable(self) -> None:
        frame = pd.DataFrame(
            {"Close": [100.0, 101.0]},
            index=pd.to_datetime(["2026-01-05", "2026-01-06"], utc=True),
        )

        normalized = normalize_split_adjusted_price_frame(frame)

        self.assertEqual([100.0, 101.0], normalized["Close"].tolist())


if __name__ == "__main__":
    unittest.main()
