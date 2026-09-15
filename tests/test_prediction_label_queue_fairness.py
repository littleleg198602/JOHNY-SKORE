from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import build_point_in_time_snapshot
from market_checker_app.prediction_label_runner import resolve_prediction_labels
from market_checker_app.storage.prediction_label_queue_store import PredictionLabelQueueStore
from market_checker_app.storage.sqlite_store import SQLiteStore


DATES = [
    "2026-01-02",
    "2026-01-05",
    "2026-01-06",
    "2026-01-07",
    "2026-01-08",
    "2026-01-09",
]


def _history(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime(DATES, utc=True),
    )


class PredictionLabelQueueFairnessTests(unittest.TestCase):
    def _seed(self, store: SQLiteStore, count: int = 700) -> list[str]:
        observed_at = datetime(2026, 1, 2, 22, tzinfo=timezone.utc)
        store.ensure_schema()
        run_id = store.insert_run(
            RunMetadata(
                started_at=observed_at,
                finished_at=observed_at,
                watchlist_size=count,
                processed_symbols=count,
                warnings_count=0,
                errors_count=0,
            )
        )
        tickers = [f"T{index:04d}" for index in range(count)]
        snapshots = [
            build_point_in_time_snapshot(
                run_id=run_id,
                ticker=ticker,
                observed_at=observed_at,
                feature_payload={"current_price": 100.0},
                baseline_output={"action": "BUY"},
                provenance={"price_source": "queue-test"},
                benchmark_ticker="SPY",
                benchmark_selection="default_fallback",
            )
            for ticker in tickers
        ]
        self.assertEqual(count, store.save_prediction_snapshots(snapshots))
        return tickers

    def test_first_120_unavailable_do_not_starve_later_due_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            tickers = self._seed(store, 700)
            blocked = set(tickers[:120])
            asset_history = _history([100, 101, 102, 103, 104, 110])
            benchmark_history = _history([100, 100, 101, 102, 103, 105])
            calls: list[str] = []

            def loader(ticker: str):
                calls.append(ticker)
                if ticker == "SPY":
                    return benchmark_history
                if ticker in blocked:
                    return None
                return asset_history

            clock = datetime(2026, 1, 30, 22, tzinfo=timezone.utc)
            report = resolve_prediction_labels(
                store=store,
                limit=1000,
                page_size=120,
                time_budget_seconds=60,
                as_of=clock,
                price_loader=loader,
            )

            self.assertEqual("PARTIAL", report["status"])
            self.assertEqual(700, report["pending_before"])
            self.assertEqual(700, report["processed_candidates"])
            self.assertEqual(580, report["resolved"])
            self.assertEqual(120, report["pending_after"])
            self.assertEqual(120, report["backlog"])
            self.assertGreaterEqual(report["page_count"], 6)
            self.assertIn(tickers[-1], calls)

            snapshots = store.read_prediction_snapshots()
            later = snapshots[snapshots["ticker"] == tickers[-1]].iloc[0]
            first = snapshots[snapshots["ticker"] == tickers[0]].iloc[0]
            self.assertEqual("RESOLVED", later["label_status"])
            self.assertEqual("PENDING", first["label_status"])

            queue = PredictionLabelQueueStore(store.db_path)
            state = queue.metrics(as_of=clock)
            self.assertEqual(120, state.backlog)
            self.assertEqual(120, state.deferred_retry)
            self.assertEqual(0, state.actionable)

            # Immediate restart is idempotent and respects persisted retry_after.
            calls_before_restart = len(calls)
            second = resolve_prediction_labels(
                store=store,
                limit=1000,
                page_size=120,
                time_budget_seconds=60,
                as_of=clock,
                price_loader=loader,
            )
            self.assertEqual(120, second["pending_before"])
            self.assertEqual(120, second["pending_after"])
            self.assertEqual(0, second["processed_candidates"])
            self.assertEqual(calls_before_restart, len(calls))
            self.assertEqual(0, second["resolved"])

    def test_immature_snapshots_are_not_selected_as_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            self._seed(store, 3)
            report = resolve_prediction_labels(
                store=store,
                limit=1000,
                page_size=120,
                as_of=datetime(2026, 1, 5, 12, tzinfo=timezone.utc),
                price_loader=lambda _: (_ for _ in ()).throw(
                    AssertionError("immature snapshot must not load prices")
                ),
            )

            self.assertEqual("SUCCESS", report["status"])
            self.assertEqual(3, report["pending_before"])
            self.assertEqual(0, report["processed_candidates"])
            self.assertEqual(3, report["after_immature"])
            self.assertEqual(3, report["pending_after"])


if __name__ == "__main__":
    unittest.main()
