from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import build_point_in_time_snapshot
from market_checker_app.prediction_label_runner import resolve_prediction_labels
from market_checker_app.storage.sqlite_store import SQLiteStore


def _history(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.date_range("2026-01-02", periods=len(values), freq="B", tz="UTC"),
    )


class PredictionLabelRunnerTests(unittest.TestCase):
    def test_resolves_bounded_batch_without_running_market_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            observed_at = datetime(2026, 1, 2, 22, tzinfo=timezone.utc)
            store.ensure_schema()
            run_id = store.insert_run(
                RunMetadata(
                    started_at=observed_at,
                    finished_at=observed_at,
                    watchlist_size=1,
                    processed_symbols=1,
                    warnings_count=0,
                    errors_count=0,
                )
            )
            store.save_prediction_snapshots(
                [
                    build_point_in_time_snapshot(
                        run_id=run_id,
                        ticker="AAPL",
                        observed_at=observed_at,
                        feature_payload={"current_price": 100.0},
                        baseline_output={"action": "BUY"},
                        provenance={"price_source": "test"},
                        benchmark_ticker="SPY",
                        benchmark_selection="default_fallback",
                    )
                ]
            )
            histories = {
                "AAPL": _history([100, 101, 102, 103, 104, 110]),
                "SPY": _history([100, 100, 101, 102, 103, 105]),
            }

            report = resolve_prediction_labels(
                store=store,
                limit=1,
                as_of=datetime(2026, 1, 9, 22, tzinfo=timezone.utc),
                price_loader=lambda ticker: histories[ticker],
            )

            self.assertEqual("SUCCESS", report["status"])
            self.assertEqual(2, report["candidate_symbols"])
            self.assertEqual(1, report["pending_before"])
            self.assertEqual(1, report["resolved"])
            self.assertEqual(0, report["downloaded_symbols"])
            self.assertEqual(
                "RESOLVED",
                store.read_prediction_snapshots(ticker="AAPL").iloc[0]["label_status"],
            )

    def test_rejects_non_positive_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "kladné"):
                resolve_prediction_labels(
                    store=SQLiteStore(Path(tmp) / "history.db"),
                    limit=0,
                )


if __name__ == "__main__":
    unittest.main()
