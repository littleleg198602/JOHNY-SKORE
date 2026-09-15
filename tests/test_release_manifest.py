from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.prediction_contract import (
    BASELINE_MODEL_ID,
    BASELINE_MODEL_VERSION,
    FEATURE_SET_VERSION,
    LEGACY_BASELINE_MODEL_ID,
    LEGACY_BASELINE_MODEL_VERSION,
    PRIMARY_TARGET_VERSION,
    build_point_in_time_snapshot,
)
from market_checker_app.release_manifest import (
    ACTIVE_MODEL_ID,
    ACTIVE_MODEL_VERSION,
    ACTIVE_SCORING_VERSION,
    build_release_manifest,
)
from market_checker_app.services.ranking_service import RankingService


class ReleaseManifestTests(unittest.TestCase):
    def test_effective_config_change_changes_hash_without_exporting_raw_config(self) -> None:
        first = AppConfig()
        second = replace(first, large_universe_threshold=first.large_universe_threshold + 1)

        a = build_release_manifest(config=first, code_sha="abc123")
        b = build_release_manifest(config=second, code_sha="abc123")

        self.assertEqual("abc123", a["code_sha"])
        self.assertEqual("effective_runtime", a["config_hash_kind"])
        self.assertNotEqual(a["config_hash"], b["config_hash"])
        self.assertNotIn("large_universe_threshold", a)
        self.assertNotIn("user_agent", a)

    def test_manifest_without_runtime_object_uses_committed_config_hash(self) -> None:
        manifest = build_release_manifest(code_sha="release-sha")

        self.assertEqual("committed_runtime_config", manifest["config_hash_kind"])
        self.assertTrue(str(manifest["config_hash"]))

    def test_active_versions_are_distinct_from_frozen_v21_baseline(self) -> None:
        manifest = build_release_manifest(code_sha="release-sha")

        self.assertEqual(ACTIVE_SCORING_VERSION, manifest["scoring_version"])
        self.assertEqual(ACTIVE_MODEL_ID, manifest["model_id"])
        self.assertEqual(ACTIVE_MODEL_VERSION, manifest["model_version"])
        self.assertEqual(FEATURE_SET_VERSION, manifest["feature_set_version"])
        self.assertEqual(PRIMARY_TARGET_VERSION, manifest["target_version"])
        self.assertNotEqual(ACTIVE_MODEL_ID, LEGACY_BASELINE_MODEL_ID)
        self.assertNotEqual(ACTIVE_MODEL_VERSION, LEGACY_BASELINE_MODEL_VERSION)
        self.assertNotEqual("UNKNOWN", manifest["manifest_hash"])

    def test_final_ranked_rows_are_stamped_with_active_version(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "final_total_score": 70.0,
                    "scoring_version": LEGACY_BASELINE_MODEL_VERSION,
                },
                {
                    "ticker": "MSFT",
                    "final_total_score": 60.0,
                    "scoring_version": LEGACY_BASELINE_MODEL_VERSION,
                },
            ]
        )

        ranked = RankingService.apply_ranking(frame)

        self.assertTrue((ranked["scoring_version"] == ACTIVE_SCORING_VERSION).all())
        self.assertTrue((ranked["model_version"] == ACTIVE_MODEL_VERSION).all())
        self.assertTrue((ranked["feature_set_version"] == FEATURE_SET_VERSION).all())
        self.assertTrue((ranked["target_version"] == PRIMARY_TARGET_VERSION).all())
        self.assertTrue(ranked["code_sha"].notna().all())
        self.assertTrue(ranked["config_hash"].notna().all())
        self.assertTrue(ranked["release_manifest_hash"].notna().all())

    def test_new_snapshot_carries_release_identity_and_active_model(self) -> None:
        manifest = build_release_manifest(
            config={"fixture": "A"},
            code_sha="deadbeef",
        )
        snapshot = build_point_in_time_snapshot(
            run_id=1,
            ticker="AAPL",
            observed_at=datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc),
            feature_payload={"x": 1},
            baseline_output={
                "action": "NO_TRADE",
                "scoring_version": LEGACY_BASELINE_MODEL_VERSION,
            },
            provenance={"source": "test"},
            release_manifest=manifest,
        )

        self.assertEqual(BASELINE_MODEL_ID, snapshot["baseline_model_id"])
        self.assertEqual(BASELINE_MODEL_VERSION, snapshot["baseline_model_version"])
        self.assertEqual(
            ACTIVE_SCORING_VERSION,
            snapshot["baseline_output"]["scoring_version"],
        )
        self.assertEqual(
            "deadbeef",
            snapshot["baseline_output"]["code_sha"],
        )
        self.assertEqual(
            "deadbeef",
            snapshot["provenance"]["release_manifest"]["code_sha"],
        )
        self.assertEqual(
            manifest["config_hash"],
            snapshot["provenance"]["release_manifest"]["config_hash"],
        )
        self.assertEqual(
            FEATURE_SET_VERSION,
            snapshot["provenance"]["feature_set_version"],
        )


if __name__ == "__main__":
    unittest.main()
