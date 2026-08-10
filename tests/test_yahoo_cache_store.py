from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_checker_app.storage.yahoo_cache_store import YahooCacheStore


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class YahooCacheStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "nested" / "yahoo.db"
        self.start = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        self.clock = MutableClock(self.start)
        self.store = YahooCacheStore(
            self.db_path,
            success_ttl=timedelta(hours=24),
            failure_retry_ttl=timedelta(minutes=30),
            now_provider=self.clock,
        )

    def test_schema_has_expected_columns_and_is_independent(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(yahoo_metadata_cache)")}
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(
            columns,
            {"ticker", "yahoo_ticker", "status", "data_json", "fetched_at", "expires_at", "error", "updated_at"},
        )
        self.assertNotIn("runs", tables)
        self.assertNotIn("signal_history", tables)

    def test_success_is_fresh_then_stale_and_remains_explicitly_usable(self) -> None:
        self.store.upsert_success(" aapl ", {"marketCap": 3_000, "quoteType": "EQUITY"}, yahoo_ticker="AAPL")

        fresh = self.store.get("AAPL")
        self.assertEqual(fresh.state, "fresh")
        self.assertTrue(fresh.usable)
        self.assertEqual(fresh.record.data["marketCap"], 3_000)  # type: ignore[union-attr]
        self.assertIsNotNone(self.store.get_fresh("aapl"))
        self.assertIsNone(self.store.get_stale("aapl"))

        self.clock.value = self.start + timedelta(hours=24)
        stale = self.store.get("AAPL")
        self.assertEqual(stale.state, "stale")
        self.assertTrue(stale.usable)
        self.assertIsNone(self.store.get_usable("AAPL"))
        self.assertIsNotNone(self.store.get_usable("AAPL", allow_stale=True))

    def test_atomic_upsert_replaces_one_ticker_and_invalid_json_leaves_old_data(self) -> None:
        self.store.upsert_success("MSFT", {"version": 1})
        self.clock.value += timedelta(minutes=1)
        self.store.upsert_success("MSFT", {"version": 2})
        self.assertEqual(self.store.get("MSFT").record.data, {"version": 2})  # type: ignore[union-attr]

        with self.assertRaises(TypeError):
            self.store.upsert_success("MSFT", {"bad": object()})
        self.assertEqual(self.store.get("MSFT").record.data, {"version": 2})  # type: ignore[union-attr]
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM yahoo_metadata_cache WHERE ticker='MSFT'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_json_serialization_handles_dates_nonfinite_numbers_and_unicode(self) -> None:
        self.store.upsert_success(
            "NVDA",
            {
                "when": self.start,
                "nan": float("nan"),
                "infinity": float("inf"),
                "label": "Čipová firma",
                "nested": {"values": (1, 2)},
            },
        )
        data = self.store.get("NVDA").record.data  # type: ignore[union-attr]
        self.assertEqual(data["when"], "2026-08-10T08:00:00Z")
        self.assertIsNone(data["nan"])
        self.assertIsNone(data["infinity"])
        self.assertEqual(data["label"], "Čipová firma")
        self.assertEqual(data["nested"]["values"], [1, 2])

    def test_failure_waits_for_retry_ttl_and_success_ttl_controls_refresh(self) -> None:
        self.store.upsert_failure("FAIL", "HTTP 429")
        self.store.upsert_success("OK", {"price": 10})

        self.assertEqual(self.store.list_tickers_needing_refresh(["MISSING", "FAIL", "OK"]), ["MISSING"])
        self.clock.value = self.start + timedelta(minutes=30)
        self.assertEqual(self.store.list_tickers_needing_refresh(["MISSING", "FAIL", "OK"]), ["MISSING", "FAIL"])
        self.clock.value = self.start + timedelta(hours=24)
        self.assertEqual(
            self.store.list_tickers_needing_refresh(["MISSING", "FAIL", "OK"]),
            ["MISSING", "FAIL", "OK"],
        )

    def test_failed_refresh_preserves_stale_success_until_retry_deadline(self) -> None:
        self.store.upsert_success("AAPL", {"marketCap": 3_000})
        self.clock.value = self.start + timedelta(days=1)
        self.store.upsert_failure("AAPL", "HTTP 429")

        lookup = self.store.get("AAPL")
        self.assertEqual(lookup.state, "stale")
        self.assertEqual(lookup.record.status, "failed")  # type: ignore[union-attr]
        self.assertEqual(lookup.record.data, {"marketCap": 3_000})  # type: ignore[union-attr]
        self.assertEqual(lookup.record.error, "HTTP 429")  # type: ignore[union-attr]
        self.assertIsNotNone(self.store.get_usable("AAPL", allow_stale=True))
        self.assertEqual(self.store.list_tickers_needing_refresh(["AAPL"]), [])

        self.clock.value += timedelta(minutes=30)
        self.assertEqual(self.store.list_tickers_needing_refresh(["AAPL"]), ["AAPL"])

    def test_unsupported_is_terminal_and_never_scheduled(self) -> None:
        self.store.upsert_unsupported("EURUSD", yahoo_ticker="EURUSD=X")
        self.clock.value = self.start + timedelta(days=365)
        lookup = self.store.get("EURUSD")
        self.assertEqual(lookup.state, "unsupported")
        self.assertEqual(lookup.record.yahoo_ticker, "EURUSD=X")  # type: ignore[union-attr]
        self.assertEqual(self.store.list_tickers_needing_refresh(["EURUSD"]), [])

    def test_coverage_counts_unique_watchlist_states(self) -> None:
        self.store.upsert_success("FRESH", {"a": 1})
        self.store.upsert_success("STALE", {"b": 2}, ttl=timedelta(minutes=1))
        self.store.upsert_failure("FAILED", "timeout")
        self.store.upsert_unsupported("UNSUPPORTED")
        self.clock.value = self.start + timedelta(minutes=2)

        coverage = self.store.coverage(
            ["fresh", "FRESH", "STALE", "FAILED", "UNSUPPORTED", "MISSING"]
        )
        self.assertEqual(coverage.total, 5)
        self.assertEqual(coverage.fresh, 1)
        self.assertEqual(coverage.stale, 1)
        self.assertEqual(coverage.failed, 1)
        self.assertEqual(coverage.unsupported, 1)
        self.assertEqual(coverage.missing, 1)
        self.assertEqual(coverage.usable, 2)
        self.assertEqual(coverage.fresh_ratio, 0.2)

    def test_corrupt_json_does_not_crash_and_is_scheduled_for_refresh(self) -> None:
        self.store.upsert_success("BROKEN", {"valid": True})
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE yahoo_metadata_cache SET data_json = ? WHERE ticker = ?",
                (json.dumps(["not", "an", "object"]), "BROKEN"),
            )

        lookup = self.store.get("BROKEN")
        self.assertEqual(lookup.state, "corrupt")
        self.assertIsNone(lookup.record)
        self.assertEqual(self.store.coverage(["BROKEN"]).corrupt, 1)
        self.assertEqual(self.store.list_tickers_needing_refresh(["BROKEN"]), ["BROKEN"])

    def test_explicit_now_overrides_clock_deterministically(self) -> None:
        self.store.upsert_success("AAPL", {"ok": True})
        before_expiry = self.start + timedelta(hours=23, minutes=59)
        at_expiry = self.start + timedelta(hours=24)
        self.assertEqual(self.store.get("AAPL", now=before_expiry).state, "fresh")
        self.assertEqual(self.store.get("AAPL", now=at_expiry).state, "stale")


if __name__ == "__main__":
    unittest.main()
