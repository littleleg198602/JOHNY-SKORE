"""Archived CMS projections never establish a current verified issuer snapshot.

These are offline lookup regressions, with SQL and network entry points blocked.
The retained two HCA rows are real projected fields, not complete CMS HTTP
responses. Modified intervals and claimed provenance below are synthetic
adversarial controls; none of these cases is a LIVE CMS acceptance.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sqlite3
import unittest
from unittest.mock import patch

from market_checker_app.services.healthcare_coverage_service import _object_states
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, healthcare_resolver_version,
)
from market_checker_app.services.healthcare_scout_service import (
    CMS_URL, _candidate, _identity_details,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/sourcefront_healthcare_20261020"
LOOKUP_AT = datetime(2026, 10, 20, 14, 29, 57, 893258, tzinfo=timezone.utc)
ISSUER = {"subject_id": "HCA", "cik": "0000860730"}


class CmsArchivedProjectionAttributionTests(unittest.TestCase):
    def setUp(self):
        for target in ("sqlite3.connect", "socket.create_connection", "socket.socket.connect"):
            self.enterContext(patch(target, side_effect=AssertionError("No SQL or network in offline lookup")))
        self.registry = HealthcareIdentityRegistry.load()
        self.rows = json.loads((ROOT / "tests/fixtures/healthcare/hca_cms_captured_20261011.json").read_bytes())
        self.records = [row for row in self.registry.records() if row["source"] == "cms"]

    def lookup(self, record, **changes):
        args = dict(source="cms", kind="cms_provider_owner", ticker="HCA", cik="0000860730",
                    entity_name=record["entity_name"], identifiers=deepcopy(record["identifiers"]),
                    event_date=LOOKUP_AT.date(), as_of=LOOKUP_AT)
        args.update(changes)
        return self.registry.resolve(**args)

    def test_actual_archived_projected_rows_are_source_only_on_current_day(self):
        for row in self.rows:
            with self.subTest(enrollment=row["ENROLLMENT ID"]):
                candidate = _candidate("cms", row, "HCA HEALTHCARE INC", LOOKUP_AT)
                self.assertIsNotNone(candidate)
                self.assertEqual(row["ORGANIZATION NAME"], candidate[1]["ORGANIZATION NAME"])
                self.assertEqual(row["ASSOCIATION DATE - OWNER"], candidate[1]["ASSOCIATION DATE - OWNER"])
                result = _identity_details(self.registry, "cms", candidate[1], ISSUER, LOOKUP_AT)
                self.assertFalse(result["issuer_attribution_allowed"])
                self.assertIsNone(result["identity_evidence"])
                self.assertEqual("CMS_SNAPSHOT_DATE_MISMATCH", result["identity_resolution"])
                self.assertEqual("OBSERVED_SNAPSHOT", result["ownership_attribution_time_basis"])
                self.assertFalse(result["historical_association_attribution_allowed"])

    def test_original_record_day_does_not_replace_missing_original_body_provider_binding(self):
        for record in self.records:
            with self.subTest(record=record["record_id"]):
                result = self.lookup(record, event_date=date.fromisoformat(record["effective_from"]))
                self.assertFalse(result["issuer_attribution_allowed"])
                self.assertIsNone(result["identity_evidence"])
                self.assertEqual("CMS_SNAPSHOT_ORIGINAL_BINDING_MISSING", result["identity_resolution"])
                self.assertEqual(record["record_id"], result["retained_relationship_record_id"])

    def test_synthetic_finite_one_day_interval_is_not_original_ccn_pac_or_body_proof(self):
        record = deepcopy(self.records[0])
        start = date.fromisoformat(record["effective_from"])
        record["effective_to"] = (start + timedelta(days=1)).isoformat()
        self.registry = HealthcareIdentityRegistry([record])
        result = self.lookup(record, event_date=start)
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertEqual("CMS_SNAPSHOT_ORIGINAL_BINDING_MISSING", result["identity_resolution"])

    def test_synthetic_current_metadata_and_same_day_observed_argument_are_not_original_proof(self):
        record = deepcopy(self.records[0])
        record.update(effective_from=LOOKUP_AT.date().isoformat(), known_at=LOOKUP_AT.isoformat())
        for item in record["evidence"]:
            item["observed_at"] = LOOKUP_AT.isoformat()
        self.registry = HealthcareIdentityRegistry([record])
        result = self.lookup(record, observed_at=LOOKUP_AT)
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertEqual("CMS_SNAPSHOT_ORIGINAL_BINDING_MISSING", result["identity_resolution"])

    def test_synthetic_caller_supplied_hash_ccn_and_pac_cannot_upgrade_legacy_record(self):
        detail = _candidate("cms", self.rows[0], "HCA HEALTHCARE INC", LOOKUP_AT)[1]
        detail.update({"CCN": "123456", "ASSOCIATE ID": "1234567890",
                       "source_observation": {"source_url": CMS_URL,
                                              "body_sha256": "a" * 64,
                                              "observed_at": LOOKUP_AT.isoformat(),
                                              "clock_basis": "ACTUAL_HTTP_RETRIEVAL"}})
        result = _identity_details(self.registry, "cms", detail, ISSUER, LOOKUP_AT)
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertIsNone(result["identity_evidence"])

    def test_wrong_object_issuer_and_old_association_remain_denied(self):
        record = self.records[0]
        for changes in ({"cik": "0000000001"}, {"entity_name": "HCA"},
                        {"identifiers": dict(record["identifiers"], enrollment_id="O99999999999999")},
                        {"event_date": date.fromisoformat(record["identifiers"]["association_date"])}):
            with self.subTest(changes=changes):
                self.assertFalse(self.lookup(record, **changes)["issuer_attribution_allowed"])

    def test_coverage_projection_uses_same_denial_without_opening_a_database(self):
        states = _object_states(self.records, self.registry, LOOKUP_AT, {"HCA"})
        self.assertEqual(2, len(states))
        self.assertEqual({"CMS_SNAPSHOT_DATE_MISMATCH"}, {row["status"] for row in states})

    def test_source_only_registry_inventory_and_names_are_preserved_but_old_cache_version_changes(self):
        before = json.loads((EVIDENCE / "before_probe.stdout.json").read_bytes())
        self.assertEqual(("HCA HEALTHCARE INC",), self.registry.query_names(
            "cms", "HCA", "0000860730", as_of=LOOKUP_AT))
        self.assertEqual(2, len(self.records))
        self.assertTrue(all(row["effective_to"] is None for row in self.records))
        self.assertNotEqual(before["cms_fingerprint_at_probe"], self.registry.fingerprint(
            "cms", "HCA", "0000860730", as_of=LOOKUP_AT))
        self.assertEqual("healthcare-object-v1", healthcare_resolver_version("fda"))
        self.assertEqual("healthcare-trial-observed-snapshot-v3", healthcare_resolver_version("clinicaltrials"))

    def test_existing_fda_and_clinical_event_contracts_are_unchanged(self):
        for kind in ("fda_crl", "clinical_trial_sponsor"):
            record = next(row for row in self.registry.records() if row["kind"] == kind)
            result = self.registry.resolve(source=record["source"], kind=record["kind"],
                ticker=record["ticker"], cik=record["issuer_cik"], entity_name=record["entity_name"],
                identifiers=record["identifiers"], event_date=date.fromisoformat(record["effective_from"]),
                as_of=LOOKUP_AT)
            self.assertTrue(result["issuer_attribution_allowed"])
            self.assertEqual("EXACT_DATED_OBJECT", result["identity_resolution"])


if __name__ == "__main__":
    unittest.main()
