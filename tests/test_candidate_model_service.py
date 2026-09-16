from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.services.candidate_model_service import (
    LOGISTIC_CANDIDATE_MODEL_ID,
    MOMENTUM_BASELINE_MODEL_ID,
    build_candidate_model_report,
)
from market_checker_app.models import RunMetadata
from market_checker_app.storage.sqlite_store import SQLiteStore


def _at(day: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)


def _payload(seed: float) -> dict[str, object]:
    return {
        "market_factors": {
            "asset_returns": {"5d": seed / 100, "20d": seed / 80, "60d": seed / 60},
            "relative_returns": {"5d": seed / 120, "20d": seed / 100, "60d": seed / 90},
            "realized_volatility": {"20d_annualized": 0.2 + abs(seed) / 1000},
            "drawdown": {"252d": -0.15 + seed / 1000},
        }
    }


def _row(
    index: int,
    *,
    label: float | None,
    as_of_day: int,
    observed_day: int | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "snapshot_id": f"snapshot-{index}",
        "ticker": f"T{index:03d}",
        "as_of": _at(as_of_day).isoformat(),
        "target_version": "excess_return_5d_nyse_split_price_v3",
        "label_status": "RESOLVED" if label is not None else "PENDING",
        "target_value": label,
        "target_observed_at": (
            _at(observed_day if observed_day is not None else as_of_day + 7).isoformat()
            if label is not None
            else None
        ),
        "feature_payload_json": json.dumps(payload if payload is not None else _payload(float(index))),
    }


class CandidateModelServiceTests(unittest.TestCase):
    def _snapshots(self) -> pd.DataFrame:
        rows = [
            _row(index, label=(0.03 if index % 2 else -0.02), as_of_day=index)
            for index in range(1, 9)
        ]
        rows.extend(
            [
                _row(90, label=None, as_of_day=30, payload=_payload(9.0)),
                _row(91, label=None, as_of_day=30, payload=_payload(-9.0)),
            ]
        )
        return pd.DataFrame(rows)

    def test_trains_only_on_resolved_observations_known_before_asof(self) -> None:
        snapshots = self._snapshots()
        snapshots = pd.concat(
            [
                snapshots,
                pd.DataFrame(
                    [_row(99, label=0.9, as_of_day=9, observed_day=38)]
                ),
            ],
            ignore_index=True,
        )

        report = build_candidate_model_report(
            snapshots,
            prediction_snapshot_ids=["snapshot-90", "snapshot-91"],
            as_of=_at(30),
            minimum_training_samples=6,
            iterations=120,
        )

        self.assertEqual("TRAINED", report["status"])
        self.assertEqual(8, report["training_sample_count"])
        artifact = report["artifact"]
        assert isinstance(artifact, dict)
        self.assertEqual(LOGISTIC_CANDIDATE_MODEL_ID, artifact["model_id"])
        self.assertNotIn("snapshot-99", artifact["training_snapshot_ids"])
        predictions = report["predictions"]
        self.assertEqual(2, len(predictions))
        self.assertTrue(
            all(item["candidate_probability_up"] is not None for item in predictions)
        )
        self.assertEqual(
            {MOMENTUM_BASELINE_MODEL_ID},
            {item["momentum_baseline_model_id"] for item in predictions},
        )
        self.assertEqual(
            [1, 2], sorted(item["shadow_rank"] for item in predictions)
        )

    def test_falls_back_to_momentum_when_training_history_is_insufficient(self) -> None:
        rows = self._snapshots().iloc[:5].to_dict(orient="records")
        rows.append(_row(90, label=None, as_of_day=30))
        snapshots = pd.DataFrame(rows)

        report = build_candidate_model_report(
            snapshots,
            prediction_snapshot_ids=["snapshot-90"],
            as_of=_at(30),
            minimum_training_samples=6,
        )

        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertEqual("MINIMUM_TRAINING_SAMPLES_NOT_MET", report["reason"])
        prediction = report["predictions"][0]
        self.assertEqual("MOMENTUM_BASELINE", prediction["selected_for_analysis"])
        self.assertIsNotNone(prediction["momentum_probability_up"])
        self.assertIsNone(prediction["candidate_probability_up"])

    def test_missing_current_features_do_not_create_a_candidate_prediction(self) -> None:
        snapshots = self._snapshots()
        snapshots.loc[snapshots["snapshot_id"] == "snapshot-90", "feature_payload_json"] = "{}"

        report = build_candidate_model_report(
            snapshots,
            prediction_snapshot_ids=["snapshot-90"],
            as_of=_at(30),
            minimum_training_samples=6,
            iterations=120,
        )

        prediction = report["predictions"][0]
        self.assertEqual("MOMENTUM_BASELINE", prediction["selected_for_analysis"])
        self.assertEqual("CANDIDATE_FEATURES_MISSING", prediction["selection_reason"])
        self.assertIsNone(prediction["candidate_probability_up"])

    def test_persists_only_a_trained_shadow_artifact(self) -> None:
        report = build_candidate_model_report(
            self._snapshots(),
            prediction_snapshot_ids=["snapshot-90", "snapshot-91"],
            as_of=_at(30),
            minimum_training_samples=6,
            iterations=120,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "models.db")
            store.ensure_schema()
            # Prediction rows are normally written by the weekly runner first.
            snapshot_rows = self._snapshots().to_dict(orient="records")
            run_id = store.insert_run(
                RunMetadata(
                    started_at=_at(30),
                    finished_at=_at(30),
                    watchlist_size=len(snapshot_rows),
                    processed_symbols=len(snapshot_rows),
                    warnings_count=0,
                    errors_count=0,
                )
            )
            for row in snapshot_rows:
                row.update(
                    {
                        "run_id": run_id,
                        "snapshot_schema_version": "feature_snapshot_v1",
                        "observed_at": row["as_of"],
                        "target_name": "5d_excess_return_vs_benchmark",
                        "horizon_trading_days": 5,
                        "benchmark_ticker": "SPY",
                        "benchmark_selection": "default_fallback",
                        "baseline_model_id": "heuristic_consensus",
                        "baseline_model_version": "v2.3",
                        "feature_payload": json.loads(row["feature_payload_json"]),
                        "baseline_output": {},
                        "provenance": {},
                        "snapshot_hash": row["snapshot_id"],
                    }
                )
            store.save_prediction_snapshots(snapshot_rows)
            self.assertEqual(2, store.save_candidate_model_report(report))
            artifact = report["artifact"]
            assert isinstance(artifact, dict)
            self.assertEqual(1, len(store.read_candidate_model_artifacts()))
            self.assertEqual(
                2,
                len(store.read_candidate_model_predictions(artifact["artifact_id"])),
            )


if __name__ == "__main__":
    unittest.main()
