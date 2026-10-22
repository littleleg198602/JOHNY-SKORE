"""Independent CMS runtime safety regressions using retained projected rows.

The two HCA rows are genuine retained projections, not original HTTP captures.
The wrong-object provenance is an explicitly adversarial mechanism control.
No database, collector, network request or registry write is performed.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from market_checker_app.services.cms_provider_ownership_review import (
    CapturedSource, CmsProviderOwnerScope, review_cms_provider_ownership,
)
from market_checker_app.services.healthcare_identity_service import HealthcareIdentityRegistry
from market_checker_app.services.healthcare_scout_service import CMS_URL, _candidate, _identity_details


ROOT = Path(__file__).resolve().parents[1]
PROJECTED_PATH = ROOT / "tests/fixtures/healthcare/hca_cms_captured_20261011.json"
PROJECTED_SHA256 = "5645620e60368e6f76bec5c13f84c7a5f88e4ca782c7f247b898b8fe25f9d3a2"
REGISTRY_PATH = ROOT / "market_checker_app/data/verified_healthcare_identities.json"
CURRENT_CLOCK = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
HCA = {"subject_id": "HCA", "cik": "0000860730"}


def retained_rows():
    body = PROJECTED_PATH.read_bytes()
    if hashlib.sha256(body).hexdigest() != PROJECTED_SHA256:
        raise AssertionError("The independently reviewed retained projection changed")
    return json.loads(body)


def runtime_resolution(row, clock, *, detail_changes=None, issuer=None):
    source_row = deepcopy(row)
    result = _candidate("cms", source_row, "HCA HEALTHCARE INC", clock)
    if result is None:
        raise AssertionError("The retained exact-name source lead was lost")
    object_id, detail = result
    if detail_changes:
        detail.update(deepcopy(detail_changes))
    resolution = _identity_details(HealthcareIdentityRegistry.load(REGISTRY_PATH), "cms", detail,
                                   dict(HCA if issuer is None else issuer), clock)
    return object_id, detail, resolution


def strict_projection_resolution(row, clock):
    body = PROJECTED_PATH.read_bytes()
    return review_cms_provider_ownership(
        scope=CmsProviderOwnerScope(row["ENROLLMENT ID"], row["ASSOCIATE ID - OWNER"],
                                    row["ROLE CODE - OWNER"]),
        owner_capture=CapturedSource({"capture_form": "PROJECTED_PUBLISHER_FIELDS",
                                      "raw_publication_permitted": True,
                                      "body_sha256": PROJECTED_SHA256, "body_bytes": len(body)}, body),
        provider_capture=None, issuer_capture=None, reviewed_at=clock)


class CmsSnapshotAttributionPeerTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch("urllib.request.urlopen", side_effect=AssertionError("Peer is offline"))
        self.scout_network_guard = patch("market_checker_app.services.healthcare_scout_service.urlopen",
                                         side_effect=AssertionError("Peer is offline"))
        self.database_guard = patch("sqlite3.connect", side_effect=AssertionError("Peer never opens SQLite"))
        for guard in (self.network_guard, self.scout_network_guard, self.database_guard):
            guard.start()
            self.addCleanup(guard.stop)

    def assert_unattributed(self, result):
        self.assertIs(False, result["issuer_attribution_allowed"], result)
        self.assertNotIn(result["identity_status"],
                         {"DATED_IDENTITY_VERIFIED", "OBSERVED_SNAPSHOT_IDENTITY_VERIFIED"}, result)
        self.assertIsNone(result["identity_evidence"], result)

    def test_actual_current_runtime_cannot_override_strict_original_byte_withhold(self):
        for row in retained_rows():
            with self.subTest(enrollment=row["ENROLLMENT ID"]):
                strict = strict_projection_resolution(row, CURRENT_CLOCK)
                self.assertEqual("WITHHOLD", strict["decision"])
                self.assertIs(False, strict["issuer_attribution_allowed"])
                self.assertIsNone(strict["proposed_record"])
                self.assert_unattributed(runtime_resolution(row, CURRENT_CLOCK)[2])

    def test_exact_recorded_day_without_original_binding_is_also_insufficient(self):
        registry = HealthcareIdentityRegistry.load(REGISTRY_PATH)
        known = max(datetime.fromisoformat(record["known_at"]) for record in registry.records()
                    if record["kind"] == "cms_provider_owner")
        clock = known + timedelta(seconds=1)
        self.assertEqual(date(2026, 10, 11), clock.date())
        for row in retained_rows():
            with self.subTest(enrollment=row["ENROLLMENT ID"]):
                self.assert_unattributed(runtime_resolution(row, clock)[2])

    def test_other_object_capture_ccn_and_provider_pac_cannot_ride_legacy_identifiers(self):
        rows = retained_rows()
        for index, row in enumerate(rows):
            other = rows[1 - index]
            # Adversarial metadata, not an acquired source body or adopted CCN.
            changes = {
                "CCN": "000001", "ASSOCIATE ID": "0000000001",
                "source_observation": {
                    "source_url": CMS_URL + "?" + urlencode({"filter[ENROLLMENT ID]": other["ENROLLMENT ID"]}),
                    "requested_at": "2026-10-20T11:59:00+00:00",
                    "observed_at": "2026-10-20T11:59:01+00:00",
                    "body_sha256": PROJECTED_SHA256,
                    "body_bytes": len(PROJECTED_PATH.read_bytes()),
                    "clock_basis": "ARCHIVED_ORIGINAL_HTTP_RETRIEVAL",
                    "capture_form": "PROJECTED_PUBLISHER_FIELDS",
                },
            }
            with self.subTest(enrollment=row["ENROLLMENT ID"], other_enrollment=other["ENROLLMENT ID"]):
                self.assert_unattributed(runtime_resolution(row, CURRENT_CLOCK, detail_changes=changes)[2])

    def test_query_leads_and_exact_source_objects_survive_withholding(self):
        rows = retained_rows()
        before = deepcopy(rows)
        registry = HealthcareIdentityRegistry.load(REGISTRY_PATH)
        self.assertIn("HCA HEALTHCARE INC", registry.query_names("cms", "HCA", HCA["cik"], as_of=CURRENT_CLOCK))
        expected = {"O20021115000002:1052593506:35:2007-06-28",
                    "O20030204000016:1052593506:35:2011-08-05"}
        actual = set()
        for row in rows:
            object_id, detail, _ = runtime_resolution(row, CURRENT_CLOCK)
            actual.add(object_id)
            self.assertEqual(row["ORGANIZATION NAME"], detail["ORGANIZATION NAME"])
            self.assertEqual(row["PERCENTAGE OWNERSHIP"], detail["PERCENTAGE OWNERSHIP"])
            self.assertIs(False, detail["association_date_is_publication_time"])
            self.assertEqual("cms_hospital_owner_name_candidate", detail["stage"])
        self.assertEqual(expected, actual)
        self.assertEqual(before, rows)

    def test_explicit_wrong_enrollment_and_wrong_issuer_controls_remain_denied(self):
        for row in retained_rows():
            with self.subTest(enrollment=row["ENROLLMENT ID"]):
                wrong_object = dict(row, **{"ENROLLMENT ID": "O19990101000001"})
                self.assert_unattributed(runtime_resolution(wrong_object, CURRENT_CLOCK)[2])
                self.assert_unattributed(runtime_resolution(row, CURRENT_CLOCK,
                    issuer={"subject_id": "UHS", "cik": "0000352915"})[2])

    def test_unrelated_exact_dated_fda_control_remains_available(self):
        registry = HealthcareIdentityRegistry.load(REGISTRY_PATH)
        record = next(row for row in registry.records()
                      if row["record_id"] == "lly-crl-bla761215-original2-2021-12-17")
        result = registry.resolve(source="fda", kind="fda_crl", ticker="LLY",
            cik=record["issuer_cik"], entity_name=record["entity_name"],
            identifiers=deepcopy(record["identifiers"]), event_date=date(2021, 12, 17), as_of=CURRENT_CLOCK)
        self.assertIs(True, result["issuer_attribution_allowed"])
        self.assertEqual("DATED_IDENTITY_VERIFIED", result["identity_status"])
        self.assertEqual(record["record_id"], result["identity_evidence"]["record_id"])


if __name__ == "__main__":
    unittest.main()
