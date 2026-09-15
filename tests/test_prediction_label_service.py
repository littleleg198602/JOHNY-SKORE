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


def _history(values: list[float], dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime(dates, utc=True),
    )


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

    def test_mature_exact_session_window_is_resolved_and_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            as_of = datetime(2026, 1, 2, 22, tzinfo=timezone.utc)
            self._snapshot(store, as_of=as_of)
            dates = [
                "2026-01-02",
                "2026-01-05",
                "2026-01-06",
                "2026-01-07",
                "2026-01-08",
                "2026-01-09",
            ]
            histories = {
                "AAPL": _history([100, 101, 102, 103, 104, 110], dates),
                "SPY": _history([100, 100, 101, 102, 103, 105], dates),
            }

            service = PredictionLabelService()
            first = service.resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 12, 22, tzinfo=timezone.utc),
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
            self.assertEqual(
                "2026-01-09T21:00:00+00:00",
                stored.iloc[0]["target_observed_at"],
            )

            second = service.resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 12, 22, tzinfo=timezone.utc),
            )
            self.assertEqual(0, second["pending_before"])
            self.assertEqual(0, second["resolved"])

    def test_incomplete_horizon_remains_pending_before_target_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(
                store,
                as_of=datetime(2026, 1, 2, 22, tzinfo=timezone.utc),
            )
            dates = ["2026-01-02", "2026-01-05", "2026-01-06"]
            histories = {
                "AAPL": _history([100, 101, 102], dates),
                "SPY": _history([100, 100, 101], dates),
            }
            result = PredictionLabelService().resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 6, 22, tzinfo=timezone.utc),
            )

            self.assertEqual(1, result["pending_before"])
            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["deferred"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("PENDING", stored.iloc[0]["label_status"])

    def test_shared_missing_expected_session_does_not_shift_horizon_forward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(
                store,
                as_of=datetime(2026, 9, 1, 21, tzinfo=timezone.utc),
            )
            # Exact future NYSE sessions are Sep 2, 3, 4, 8, 9. Both series
            # omit Sep 4 and contain Sep 10 instead. Row-count logic used to
            # shift the target and incorrectly accept the later endpoint.
            dates = [
                "2026-09-01",
                "2026-09-02",
                "2026-09-03",
                "2026-09-08",
                "2026-09-09",
                "2026-09-10",
            ]
            histories = {
                "AAPL": _history([100, 101, 102, 103, 104, 105], dates),
                "SPY": _history([100, 100, 101, 102, 103, 104], dates),
            }
            result = PredictionLabelService(
                maturity_grace_days=7
            ).resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 9, 30, 22, tzinfo=timezone.utc),
            )

            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["unavailable"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("UNAVAILABLE", stored.iloc[0]["label_status"])

    def test_future_loader_rows_cannot_resolve_before_evaluation_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(
                store,
                as_of=datetime(2026, 9, 2, 21, tzinfo=timezone.utc),
            )
            dates = [
                "2026-09-02",
                "2026-09-03",
                "2026-09-04",
                "2026-09-08",
                "2026-09-09",
                "2026-09-10",
            ]
            histories = {
                "AAPL": _history([100, 101, 102, 103, 104, 110], dates),
                "SPY": _history([100, 100, 101, 102, 103, 105], dates),
            }
            result = PredictionLabelService().resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
            )

            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["deferred"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("PENDING", stored.iloc[0]["label_status"])
            self.assertIsNone(stored.iloc[0]["target_observed_at"])

    def test_half_day_endpoint_is_available_only_after_early_close(self) -> None:
        dates = ["2026-11-25", "2026-11-27"]
        asset = _history([100, 110], dates)
        benchmark = _history([100, 105], dates)
        as_of = datetime(2026, 11, 25, 22, tzinfo=timezone.utc)

        before = PredictionLabelService._common_price_windows(
            asset,
            benchmark,
            as_of=as_of,
            horizon=1,
            evaluation_as_of=datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc),
        )
        after = PredictionLabelService._common_price_windows(
            asset,
            benchmark,
            as_of=as_of,
            horizon=1,
            evaluation_as_of=datetime(2026, 11, 27, 18, 5, tzinfo=timezone.utc),
        )

        self.assertIsNone(before)
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(datetime(2026, 11, 27, 18, tzinfo=timezone.utc), after[2])

    def test_loaded_but_mature_unusable_window_becomes_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._snapshot(
                store,
                as_of=datetime(2026, 1, 2, 22, tzinfo=timezone.utc),
            )
            dates = ["2026-01-02", "2026-01-05"]
            histories = {
                "AAPL": _history([100, 101], dates),
                "SPY": _history([100, 100], dates),
            }
            result = PredictionLabelService(
                maturity_grace_days=7
            ).resolve_pending_snapshots(
                store=store,
                price_loader=lambda ticker: histories[ticker],
                as_of=datetime(2026, 1, 20, 22, tzinfo=timezone.utc),
            )

            self.assertEqual(0, result["resolved"])
            self.assertEqual(1, result["unavailable"])
            stored = store.read_prediction_snapshots(ticker="AAPL")
            self.assertEqual("UNAVAILABLE", stored.iloc[0]["label_status"])
            self.assertIsNone(stored.iloc[0]["target_value"])


if __name__ == "__main__":
    unittest.main()
