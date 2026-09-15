from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.services.price_methodology import PRICE_METHOD_VERSION
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class YahooOhlcCacheStoreTests(unittest.TestCase):
    def _store(self, root: Path, now: datetime = NOW, **kwargs) -> YahooOhlcCacheStore:
        return YahooOhlcCacheStore(
            root / "history.db",
            success_ttl=timedelta(hours=24),
            now_provider=lambda: now,
            **kwargs,
        )

    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [99.0, 100.0],
                "High": [101.0, 102.0],
                "Low": [98.0, 99.0],
                "Close": [100.0, 101.0],
            },
            index=pd.to_datetime(["2026-09-10", "2026-09-11"], utc=True),
        )

    def test_persists_and_restores_versioned_daily_ohlc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store(root).upsert_success("AAPL", self._frame())
            lookup = self._store(root).get("AAPL")

        self.assertEqual("fresh", lookup.state)
        self.assertTrue(lookup.usable)
        self.assertEqual([100, 101], lookup.frame["Close"].tolist())
        self.assertEqual("yfinance", lookup.provider)
        self.assertEqual("1d", lookup.interval)
        self.assertEqual("split_only", lookup.adjustment)
        self.assertEqual(PRICE_METHOD_VERSION, lookup.methodology_version)
        self.assertEqual(date(2026, 9, 10), lookup.first_session)
        self.assertEqual(date(2026, 9, 11), lookup.last_session)

    def test_failure_can_use_explicit_stale_cache_but_never_invents_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            store.upsert_success("AAPL", self._frame())
            store.note_failure("AAPL", "HTTP 429")
            stale = store.get("AAPL")
            missing = store.get("MSFT")

        self.assertEqual("stale", stale.state)
        self.assertTrue(stale.usable)
        self.assertEqual("HTTP 429", stale.error)
        self.assertEqual("missing", missing.state)
        self.assertIsNone(missing.frame)

    def test_rejects_empty_or_non_numeric_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            with self.assertRaisesRegex(ValueError, "finite positive Close"):
                store.upsert_success(
                    "AAPL",
                    pd.DataFrame({"Close": [float("nan"), 0]}),
                )

    def test_failed_missing_symbol_is_persisted_with_retry_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.note_failure("MSFT", "HTTP 429")
            lookup = store.get("MSFT")

        self.assertEqual("failed", lookup.state)
        self.assertFalse(lookup.usable)
        self.assertEqual("HTTP 429", lookup.error)
        self.assertEqual(1, lookup.attempt_count)
        self.assertFalse(lookup.can_retry(NOW))

    def test_failure_preserves_stale_frame_and_success_resets_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.upsert_success("AAPL", self._frame())
            store.note_failure("AAPL", "temporary timeout")
            stale = store.get("AAPL")
            store.upsert_success("AAPL", self._frame())
            refreshed = store.get("AAPL")

        self.assertEqual("stale", stale.state)
        self.assertTrue(stale.usable)
        self.assertEqual(1, stale.attempt_count)
        self.assertFalse(stale.can_retry(NOW))
        self.assertEqual("fresh", refreshed.state)
        self.assertEqual(0, refreshed.attempt_count)
        self.assertTrue(refreshed.can_retry(NOW))

    def test_required_sessions_detect_missing_interior_session(self) -> None:
        frame = pd.DataFrame(
            {"Close": [100.0, 102.0]},
            index=pd.to_datetime(["2026-09-09", "2026-09-11"], utc=True),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.upsert_success("AAPL", frame)
            lookup = store.get(
                "AAPL",
                required_sessions=(
                    date(2026, 9, 9),
                    date(2026, 9, 10),
                    date(2026, 9, 11),
                ),
            )

        self.assertEqual("incomplete", lookup.state)
        self.assertFalse(lookup.usable)
        self.assertEqual((date(2026, 9, 10),), lookup.missing_sessions)
        self.assertIsNotNone(lookup.frame)

    def test_successive_refresh_merges_history_instead_of_replacing_it(self) -> None:
        first = pd.DataFrame(
            {"Close": [100.0, 101.0]},
            index=pd.to_datetime(["2026-09-08", "2026-09-09"], utc=True),
        )
        second = pd.DataFrame(
            {"Close": [102.0, 103.0]},
            index=pd.to_datetime(["2026-09-10", "2026-09-11"], utc=True),
        )
        required = tuple(
            date(2026, 9, day) for day in (8, 9, 10, 11)
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.upsert_success("AAPL", first)
            before = store.get("AAPL", required_sessions=required)
            store.upsert_success("AAPL", second)
            after = store.get("AAPL", required_sessions=required)

        self.assertEqual("incomplete", before.state)
        self.assertEqual("fresh", after.state)
        self.assertTrue(after.usable)
        self.assertEqual([100.0, 101.0, 102.0, 103.0], after.frame["Close"].tolist())

    def test_same_ticker_can_store_incompatible_methodologies_side_by_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self._store(root)
            alternate = self._store(
                root,
                adjustment="vendor_adjusted",
                methodology_version="vendor_total_return_v1",
            )
            current.upsert_success(
                "AAPL",
                pd.DataFrame(
                    {"Close": [101.0]},
                    index=pd.to_datetime(["2026-09-11"], utc=True),
                ),
            )
            alternate.upsert_success(
                "AAPL",
                pd.DataFrame(
                    {"Close": [201.0]},
                    index=pd.to_datetime(["2026-09-11"], utc=True),
                ),
            )

            current_lookup = current.get("AAPL")
            alternate_lookup = alternate.get("AAPL")

        self.assertEqual(101.0, current_lookup.frame["Close"].iloc[-1])
        self.assertEqual(201.0, alternate_lookup.frame["Close"].iloc[-1])
        self.assertNotEqual(
            current_lookup.methodology_version,
            alternate_lookup.methodology_version,
        )

    def test_cache_normalizes_split_but_does_not_apply_dividend(self) -> None:
        frame = pd.DataFrame(
            {
                "Close": [400.0, 100.0, 102.0],
                "Stock Splits": [0.0, 4.0, 0.0],
                "Dividends": [0.0, 0.0, 1.0],
            },
            index=pd.to_datetime(
                ["2026-01-05", "2026-01-06", "2026-01-07"],
                utc=True,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.upsert_success("AAPL", frame)
            lookup = store.get("AAPL")

        self.assertEqual([100.0, 100.0, 102.0], lookup.frame["Close"].tolist())
        self.assertEqual([0.0, 0.0, 1.0], lookup.frame["Dividends"].tolist())


if __name__ == "__main__":
    unittest.main()
