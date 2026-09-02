from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import (
    build_point_in_time_snapshot,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


class PredictionSnapshotStorageTests(unittest.TestCase):
    def test_snapshots_are_write_once_and_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.ensure_schema()
            started = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
            run_id = store.insert_run(
                RunMetadata(
                    started_at=started,
                    finished_at=started,
                    watchlist_size=1,
                    processed_symbols=1,
                    warnings_count=0,
                    errors_count=0,
                )
            )
            snapshot = build_point_in_time_snapshot(
                run_id=run_id,
                ticker="AAPL",
                observed_at=started,
                feature_payload={"current_price": 100.0},
                baseline_output={"action": "NO_TRADE", "final_total_score": 52.0},
                provenance={"price_source": "test"},
            )

            self.assertEqual(1, store.save_prediction_snapshots([snapshot]))
            self.assertEqual(0, store.save_prediction_snapshots([snapshot]))

            stored = store.read_prediction_snapshots(run_id=run_id, ticker="AAPL")
            self.assertEqual(1, len(stored))
            self.assertEqual("PENDING", stored.iloc[0]["label_status"])
            self.assertIsNone(stored.iloc[0]["target_value"])
            self.assertEqual(snapshot["snapshot_hash"], stored.iloc[0]["snapshot_hash"])
            self.assertEqual(
                "legacy_v2.1_heuristic",
                stored.iloc[0]["baseline_model_id"],
            )


if __name__ == "__main__":
    unittest.main()
