"""Validate every production record independently of fixed semantic fixtures."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import unittest

from market_checker_app.services.healthcare_coverage_service import build_healthcare_coverage_report
from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry,
)


SEED_IDENTITIES = Path(__file__).parent / "fixtures" / "healthcare" / "reviewed_seed_identities_20261011.json"
SEED_SHA256 = "fbd27ff5030218e43a73ceb2de5eab444587c2fd787be436717681a5655cbd69"


class HealthcareProductionManifestTests(unittest.TestCase):
    def test_semantic_seed_is_the_exact_reviewed_baseline_snapshot(self):
        # This is the complete production manifest from a7aa1fec, not a subset
        # recomputed from whatever records happen to exist at test time.
        self.assertEqual(SEED_SHA256, hashlib.sha256(SEED_IDENTITIES.read_bytes()).hexdigest())

    def test_production_loader_validates_every_actual_manifest_record(self):
        raw = json.loads(DEFAULT_HEALTHCARE_IDENTITIES.read_text(encoding="utf-8"))
        registry = HealthcareIdentityRegistry.load(DEFAULT_HEALTHCARE_IDENTITIES)
        records = registry.records()
        self.assertEqual(raw["records"], list(records))
        self.assertEqual(raw["record_count"], len(records))
        self.assertEqual(len(records), len({row["record_id"] for row in records}))

    def test_production_coverage_diagnoses_the_actual_size_without_runtime_acceptance(self):
        raw = json.loads(DEFAULT_HEALTHCARE_IDENTITIES.read_text(encoding="utf-8"))
        as_of = max(datetime.fromisoformat(row["known_at"]) for row in raw["records"]) + timedelta(seconds=1)
        report = build_healthcare_coverage_report(as_of=as_of, identities_path=DEFAULT_HEALTHCARE_IDENTITIES)
        manifest = report["identity_manifest"]
        self.assertEqual("VALID", manifest["status"])
        self.assertEqual([], manifest["errors"])
        self.assertEqual((len(raw["records"]), len(raw["records"]), 0),
                         (manifest["record_count"], manifest["valid_record_count"], manifest["malformed_record_count"]))
        self.assertEqual("NOT_PROVIDED", report["runtime"]["status"])
        self.assertFalse(any(report["acceptance"].values()))


if __name__ == "__main__":
    unittest.main()
