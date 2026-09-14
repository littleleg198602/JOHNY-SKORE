from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class YahooOhlcCacheStoreTests(unittest.TestCase):
    def _store(self, root: Path, now: datetime = NOW) -> YahooOhlcCacheStore:
        return YahooOhlcCacheStore(
            root / "history.db",
            success_ttl=timedelta(hours=24),
            now_provider=lambda: now,
        )

    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"Open": [99.0, 100.0], "High": [101.0, 102.0], "Low": [98.0, 99.0], "Close": [100.0, 101.0]},
            index=pd.date_range("2026-09-10", periods=2, tz="UTC"),
        )

    def test_persists_and_restores_usable_daily_ohlc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store(root).upsert_success("AAPL", self._frame())
            lookup = self._store(root).get("AAPL")

        self.assertEqual("fresh", lookup.state)
        self.assertTrue(lookup.usable)
        self.assertEqual([100, 101], lookup.frame["Close"].tolist())

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
            with self.assertRaisesRegex(ValueError, "positive numeric Close"):
                store.upsert_success("AAPL", pd.DataFrame({"Close": [float("nan"), 0]}))


if __name__ == "__main__":
    unittest.main()
