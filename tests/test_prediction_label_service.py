from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import build_point_in_time_snapshot
from market_checker_app.services.prediction_label_service import (
    PredictionLabelService,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _history(values: list[float], *, start: str = "2026-01-02") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(values), freq="B", tz="UTC")
    return pd.DataFrame({"Close": values}, index=index)


class PredictionLabelServiceTests(unittest.TestCase):
    def _snapshot(
        self,
        store: SQLiteStore,
        *,
        as_of: datetime,
        ticker: str = "AAPL",
        benchmark: str = "SPY",
    ) -> int:
        store.ensure_schema()
        run_id = store.insert_run(
            RunMetadata(
                started_at=as_of,
                finished_at=as_of,
                watchlist_size=1,
                processed_symbols=1,
                warnings_count=0,
                errors_count=0,
            )
        )
        snapshot = build_point_in_time_snapshot(
            run_id=run_id,
            ticker=ticker,
            observed_at=as_of,
            feature_payload={"current_price": 100.0},
            baseline_output={"action": "BUY"},
            provenance={"price_source": "test"},
            benchmark_ticker=benchmark,
            benchmark_selection="default_fallback",
        )
        self.assertEqual(1, store.save_prediction_snapshots([snapshot]))
        return run_id

    def test_mature_window_is_resolved_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            as_of = datetime(2026, 1, 2, 16, tzinfo=timezone.utc)
            self._snapshot(store, as_of=as_of)
            histories = {
                "AAPL": _history([100, 101, 102, 103, 104, 110]),
                "SPY": _history([100, 100, 101, 102, 103, 105]),
            }

            def loader(ticker: str) -> pd.DataFrame:
                return histories[ticker]

            service = PredictionLabelService()
            first = service.resolve_pending_snapshots(
                store=store,
                price_loader=loader,
                as_of=datetime(2026, 1, 12, tzinfo=timezone.utc),
            )

            self.assertEqual(
                {
                    "pending_before": 1,
                    "resolved": 1,
                    "unavailable": 0,
                    "deferred": 0,
                    "source_failures": 0,
                },
                first,
            )
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("RESOLVED", stored.iloc[0]["label_status"])
            self.assertAlmostEqual(0.05, float(stored.iloc[0]["target_value"]))

            second = service.resolve_pending_snapshots(
                store=store,
                price_loader=loader,
                as_of=datetime(2026, 1, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(0, second["pending_before"])
            self.assertEqual(0, second["resolved"])

    def test_incomplete_horizon_remains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(
                store,
                as_of=datetime(2026, 1, 2, 16, tzinfo=timezone.utc),
            )
            histories = {
                "AAPL": _history([100, 101, 102]),
                "SPY": _history([100, 100, 101]),
            }
            result = PredictionLabelService().resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 6, tzinfo=timezone.utc),
            )

            self.assertEqual(1, result["pending_before"])
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["deferred"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("PENDING", stored.iloc[0]["label_status"])

    def test_loaded_but_mature_unusable_window_becomes_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(
                store,
                as_of=datetime(2026, 1, 2, 16, tzinfo=timezone.utc),
            )
            histories = {
                "AAPL": _history([100, 101]),
                "SPY": _history([100, 100]),
            }
            result = PredictionLabelService(maturity_grace_days=7).resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 20, tzinfo=timezone.utc),
            )

            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["unavailable"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("UNAVAILABLE", stored.iloc[0]["label_status"])
            self.assertIsNone(stored.iloc[0]["target_value"])


if __name__ == "__main__":
    unittest.main()
