"""Actual withheld bodies plus explicitly synthetic positive issuer fixtures.

The positive fixture is NOT a newly retrieved SEC identity or LIVE evidence.
Original ClinicalTrials/SEC archive bytes are never changed by these tests.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from evidence.batch4_healthcare_20261013.replay import capture
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, build_clinical_trial_snapshot_record,
)
from market_checker_app.services.healthcare_scout_service import (
    ClinicalTrialsClient, _ObservedJson, _candidate, _finding_digest, _identity_details, _json,
)
from market_checker_app.services.healthcare_coverage_service import build_healthcare_coverage_report


REVIEW = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
ISSUER = {"subject_id": "LLY", "cik": "0000059478", "company_name": "Eli Lilly and Company"}


def synthetic_named_issuer():
    """Synthetic exact-name JSON; this fixture supplies no real issuer bridge."""
    meta, _ = capture("sec_lly_submissions")
    body = json.dumps({"cik": ISSUER["cik"], "name": ISSUER["company_name"], "tickers": ["LLY"]}).encode()
    meta = deepcopy(meta)
    meta.update(body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest(),
                requested_at="2026-10-13T08:52:00+00:00", observed_at="2026-10-13T08:53:00+00:00")
    return meta, body


def synthetic_record(key="trial_surpass2_complete_record", intervention="Tirzepatide", **changes):
    meta, body = capture(key)
    issuer_meta, issuer_body = synthetic_named_issuer()
    args = dict(ticker="LLY", cik=ISSUER["cik"], intervention=intervention, role="LEAD_SPONSOR",
                trial_metadata=meta, trial_body=body, issuer_metadata=issuer_meta,
                issuer_body=issuer_body, reviewed_at=REVIEW)
    args.update(changes)
    return build_clinical_trial_snapshot_record(**args)


class Batch5HealthcareSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.meta, self.body = capture("trial_surpass2_complete_record")
        self.row = json.loads(self.body)
        self.record = synthetic_record()
        self.registry = HealthcareIdentityRegistry([self.record])

    def resolve(self, **changes):
        observed = datetime.fromisoformat(self.meta["observed_at"])
        args = dict(source="clinicaltrials", kind="clinical_trial_sponsor_snapshot", ticker="LLY",
                    cik=ISSUER["cik"], entity_name=ISSUER["company_name"], identifiers=self.record["identifiers"],
                    event_date=observed.date(), observed_at=observed, as_of=REVIEW)
        args.update(changes)
        return self.registry.resolve(**args)

    def detail(self, provenance=None):
        _, detail = _candidate("clinicaltrials", self.row, ISSUER["company_name"], REVIEW)
        detail.update(trial_attribution_time_basis="OBSERVED_SNAPSHOT", source_observation={
            "source_url": self.meta["response_url"], "requested_at": self.meta["requested_at"],
            "observed_at": self.meta["observed_at"], "body_sha256": self.meta["body_sha256"],
            "body_bytes": self.meta["body_bytes"], "clock_basis": "ARCHIVED_ORIGINAL_HTTP_RETRIEVAL"})
        if provenance is not None:
            detail["source_observation"].update(provenance)
        return detail

    def test_actual_submissions_name_mismatch_is_withheld_without_alias_normalization(self):
        issuer_meta, issuer_body = capture("sec_lly_submissions")
        self.assertEqual("ELI LILLY & Co", json.loads(issuer_body)["name"])
        for key in ("trial_surpass2_complete_record", "trial_summit_complete_record"):
            meta, body = capture(key)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "EXACT_SPONSOR_ISSUER_NAME_BRIDGE_MISSING"):
                build_clinical_trial_snapshot_record(ticker="LLY", cik=ISSUER["cik"], intervention="Tirzepatide",
                    role="LEAD_SPONSOR", trial_metadata=meta, trial_body=body,
                    issuer_metadata=issuer_meta, issuer_body=issuer_body, reviewed_at=REVIEW)

    def test_synthetic_positive_is_observed_only_and_retains_exact_raw_clock_and_scope(self):
        result = self.resolve()
        self.assertTrue(result["issuer_attribution_allowed"])
        self.assertEqual("EXACT_OBSERVED_SNAPSHOT", result["identity_resolution"])
        self.assertEqual("OBSERVED_SNAPSHOT_IDENTITY_VERIFIED", result["identity_status"])
        self.assertFalse(result["historical_trial_sponsor_attribution_allowed"])
        self.assertEqual(self.meta["observed_at"], result["identity_evidence"]["matched_observed_at"])
        self.assertNotIn("matched_event_date", result["identity_evidence"])
        self.assertEqual("2026-10-13", self.record["effective_from"])
        self.assertEqual("2026-10-14", self.record["effective_to"])
        self.assertEqual("2022-02-14", self.record["identifiers"]["record_version_date"])
        self.assertEqual(self.meta["body_sha256"], self.record["identifiers"]["source_body_sha256"])
        self.assertEqual("EXPERIMENTAL_ARM_SPONSORSHIP_ONLY", self.record["intervention_scope"]["type"])

    def test_publisher_event_day_does_not_become_an_observed_issuer_day(self):
        event = date.fromisoformat(self.record["identifiers"]["record_version_date"])
        self.assertEqual("INVALID_SNAPSHOT_OBSERVATION", self.resolve(event_date=event)["identity_resolution"])
        self.assertFalse(self.resolve(kind="clinical_trial_sponsor", event_date=event)["issuer_attribution_allowed"])

    def test_snapshot_requires_explicit_observation_and_denies_earlier_knowledge(self):
        self.assertEqual("MISSING_SNAPSHOT_OBSERVATION", self.resolve(observed_at=None)["identity_resolution"])
        self.assertEqual("NOT_YET_KNOWN", self.resolve(as_of=REVIEW - timedelta(microseconds=1))["identity_resolution"])
        self.assertEqual("INVALID_SNAPSHOT_OBSERVATION", self.resolve(
            observed_at=REVIEW, event_date=REVIEW.date())["identity_resolution"])

    def test_changed_body_or_capture_clock_cannot_inherit_a_pinned_record(self):
        for key, value in (("source_body_sha256", "0" * 64),
                           ("source_observed_at", "2026-10-13T08:54:55+00:00")):
            ids = dict(self.record["identifiers"], **{key: value})
            with self.subTest(key=key):
                self.assertFalse(self.resolve(identifiers=ids)["issuer_attribution_allowed"])

    def test_schema_denies_extended_or_backdated_snapshot_intervals(self):
        for changes in ({"effective_from": "2022-02-14"}, {"effective_to": "2026-10-15"},
                        {"effective_to": None}, {"time_basis": "EVENT_DATE"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                HealthcareIdentityRegistry([dict(self.record, **changes)])

    def test_snapshot_schema_cannot_be_used_to_assert_broader_or_historical_claims(self):
        for key in ("historical_trial_sponsor_attribution_allowed",
                    "trial_sponsor_identity_is_product_ownership", "issuer_wide_scope"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "cannot assert"):
                HealthcareIdentityRegistry([dict(self.record, **{key: True})])

    def test_wrong_named_cik_or_unrelated_day_bridge_is_not_accepted(self):
        for changes in ({"issuer_name": "ELI LILLY & Co"}, {"issuer_cik": "0000078003"},
                        {"ticker": "PFE"}, {"observed_at": "2026-10-14T00:01:00+00:00"},
                        {"known_at": "2026-10-12T00:00:00+00:00"}):
            record = deepcopy(self.record)
            record["issuer_bridge"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                HealthcareIdentityRegistry([record])

    def test_later_issuer_capture_does_not_backdate_a_prospective_bridge(self):
        record = deepcopy(self.record)
        record["issuer_bridge"]["observed_at"] = "2026-10-13T09:00:00+00:00"
        record["evidence"][1]["observed_at"] = record["issuer_bridge"]["observed_at"]
        with self.assertRaisesRegex(ValueError, "observed cohort"):
            HealthcareIdentityRegistry([record])
        metadata, body = synthetic_named_issuer()
        metadata["observed_at"] = "2026-10-13T09:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "ISSUER_EVIDENCE_NOT_AVAILABLE_AT_SOURCE_OBSERVATION"):
            synthetic_record(issuer_metadata=metadata, issuer_body=body)

    def test_annual_report_or_unverified_name_only_bridge_cannot_replace_submissions(self):
        record = deepcopy(self.record)
        record["issuer_bridge"]["source_url"] = "https://www.sec.gov/Archives/edgar/data/59478/000005947826000013/lly-20251231.htm"
        with self.assertRaises(ValueError):
            HealthcareIdentityRegistry([record])
        record = deepcopy(self.record)
        record.pop("issuer_bridge")
        with self.assertRaisesRegex(ValueError, "independent dated issuer bridge"):
            HealthcareIdentityRegistry([record])

    def test_original_evidence_hashes_are_mandatory_even_with_a_valid_name_bridge(self):
        for position in (0, 1):
            record = deepcopy(self.record)
            record["evidence"][position]["body_sha256"] = "0" * 64
            with self.subTest(position=position), self.assertRaisesRegex(ValueError, "original source bodies"):
                HealthcareIdentityRegistry([record])

    def test_actual_comparator_and_placebo_arm_types_cannot_receive_experimental_scope(self):
        for key, intervention in (("trial_surpass2_complete_record", "Semaglutide"),
                                  ("trial_summit_complete_record", "Placebo")):
            with self.subTest(intervention=intervention), self.assertRaisesRegex(
                    ValueError, "COMPARATOR_OR_UNDOCUMENTED_INTERVENTION_SCOPE"):
                synthetic_record(key, intervention)

    def test_missing_arm_relationship_is_not_inferred_from_an_intervention_name(self):
        row = deepcopy(self.row)
        row["protocolSection"]["armsInterventionsModule"].pop("armGroups")
        raw = json.dumps(row).encode()
        metadata = dict(self.meta, body_sha256=hashlib.sha256(raw).hexdigest(), body_bytes=len(raw))
        with self.assertRaisesRegex(ValueError, "EXPERIMENTAL_ARM_SCOPE_NOT_CAPTURED"):
            synthetic_record(trial_body=raw, trial_metadata=metadata)

    def test_distinct_experimental_objects_in_one_capture_have_distinct_record_ids(self):
        row = deepcopy(self.row)
        arms = row["protocolSection"]["armsInterventionsModule"]
        arms["interventions"].append({"name": "Synthetic second experimental drug", "type": "DRUG",
                                       "armGroupLabels": ["Synthetic second arm"]})
        arms["armGroups"].append({"label": "Synthetic second arm", "type": "EXPERIMENTAL",
                                  "interventionNames": ["Drug: Synthetic second experimental drug"]})
        raw = json.dumps(row).encode()
        metadata = dict(self.meta, body_sha256=hashlib.sha256(raw).hexdigest(), body_bytes=len(raw))
        first = synthetic_record(trial_body=raw, trial_metadata=metadata)
        second = synthetic_record(intervention="Synthetic second experimental drug",
                                  trial_body=raw, trial_metadata=metadata)
        self.assertNotEqual(first["record_id"], second["record_id"])
        self.assertEqual(2, len(HealthcareIdentityRegistry([first, second]).records()))

    def test_withheld_or_incomplete_bodies_cannot_be_sanitized_into_accepted_evidence(self):
        for changes in ({"status": "WITHHELD_SECRET_CONTENT"}, {"raw_publication_permitted": False},
                        {"body_complete_within_byte_budget": False}, {"http_status": 403}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "WITHHELD_OR_INCOMPLETE"):
                synthetic_record(trial_metadata=dict(self.meta, **changes))

    def test_capture_tampering_wrong_publisher_and_clock_inversion_fail_closed(self):
        for changes, reason in (({"body_sha256": "0" * 64}, "INTEGRITY"),
                               ({"response_url": "https://example.org/study"}, "PUBLISHER"),
                               ({"requested_at": "2026-10-15T00:00:00+00:00"}, "NOT_AVAILABLE")):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, reason):
                synthetic_record(trial_metadata=dict(self.meta, **changes))

    def test_runtime_snapshot_dispatch_never_falls_back_to_historical_gates(self):
        detail = self.detail()
        result = _identity_details(self.registry, "clinicaltrials", detail, ISSUER, REVIEW)
        self.assertEqual("EXACT_OBSERVED_SNAPSHOT", result["identity_resolution"])
        self.assertEqual(["Tirzepatide"], result["attributed_intervention_names"])
        self.assertEqual(["Semaglutide"], result["unattributed_intervention_names"])
        self.assertFalse(result["trial_sponsor_identity_is_product_ownership"])
        without_proof = self.detail()
        without_proof.pop("source_observation")
        self.assertEqual("MISSING_SNAPSHOT_OBSERVATION", _identity_details(
            self.registry, "clinicaltrials", without_proof, ISSUER, REVIEW)["identity_resolution"])
        withheld = self.detail({"status": "WITHHELD_SECRET_CONTENT"})
        self.assertFalse(_identity_details(self.registry, "clinicaltrials", withheld, ISSUER, REVIEW)["issuer_attribution_allowed"])

    def test_wrong_issuer_and_role_do_not_inherit_observed_sponsor_attribution(self):
        self.assertEqual("WRONG_ISSUER", self.resolve(ticker="PFE", cik="0000078003")["identity_resolution"])
        ids = dict(self.record["identifiers"], role="COLLABORATOR")
        self.assertFalse(self.resolve(identifiers=ids)["issuer_attribution_allowed"])

    def test_single_source_only_result_exposes_explicit_false_claim_flags(self):
        detail = self.detail()
        detail["intervention_names"] = ["Tirzepatide"]
        result = _identity_details(HealthcareIdentityRegistry([]), "clinicaltrials", detail, ISSUER, REVIEW)
        self.assertEqual("OBSERVED_SNAPSHOT", result["trial_attribution_time_basis"])
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertFalse(result["historical_trial_sponsor_attribution_allowed"])
        self.assertFalse(result["trial_sponsor_identity_is_product_ownership"])

    def test_snapshot_runtime_rejects_unrelated_url_and_inverted_source_request_clock(self):
        for change in ({"source_url": "https://example.org/study"},
                       {"source_url": "https://clinicaltrials.gov/api/v2/studies/NCT04847557"},
                       {"requested_at": "2026-10-14T00:00:00+00:00"},
                       {"requested_at": "2026-10-13T08:00:00"}):
            with self.subTest(change=change):
                result = _identity_details(self.registry, "clinicaltrials", self.detail(change), ISSUER, REVIEW)
                self.assertEqual("MISSING_SNAPSHOT_OBSERVATION", result["identity_resolution"])
                self.assertFalse(result["issuer_attribution_allowed"])

    def test_saved_actual_review_replays_original_hashes_and_withholds_every_candidate(self):
        from evidence.batch5_healthcare_20261014.review_sources import EVIDENCE, build_review_report
        stored = json.loads((EVIDENCE / "source_review.json").read_text())
        replay = build_review_report(as_of=datetime.fromisoformat(stored["reviewed_at"]))
        self.assertEqual(stored, replay)
        self.assertEqual(0, replay["proposal_count"])
        self.assertEqual(0, replay["source_requests"])
        self.assertEqual("WAIT_QUOTA_HISTORY", replay["source_history_status"])
        self.assertEqual(2, replay["historical_event_proposals"]["withheld_count"])
        for candidate in replay["candidates"]:
            self.assertEqual("WITHHOLD", candidate["decision"])
            self.assertIn("EXACT_SPONSOR_ISSUER_NAME_BRIDGE_MISSING", candidate["reasons"])
            self.assertIn("NO_CURRENT_OBSERVED_CLINICAL_PAGE", candidate["reasons"])

    def test_coverage_distinguishes_archived_snapshot_from_current_and_event_records(self):
        report = build_healthcare_coverage_report(as_of=REVIEW, identities=self.registry)
        self.assertEqual(687, report["universe"]["subjects"])
        clinical = report["sources"]["clinicaltrials"]
        self.assertEqual(1, clinical["documented_observed_trial_snapshots"])
        self.assertEqual(0, clinical["current_observed_trial_snapshots"])
        self.assertEqual(0, clinical["historical_event_only_records"])
        self.assertEqual("2026-10-13", report["object_diagnostics"][0]["coverage_date"])
        self.assertFalse(report["object_diagnostics"][0]["historical_publisher_day_attribution_allowed"])
        self.assertFalse(clinical["live_verified"])

    def test_identical_content_heartbeat_is_idempotent_and_does_not_mutate_callback_data(self):
        first = self.detail()
        later = deepcopy(first)
        later["source_observation"]["observed_at"] = "2026-10-13T10:00:00+00:00"
        preserved = deepcopy(first)
        self.assertEqual(_finding_digest(first), _finding_digest(later))
        self.assertEqual(preserved, first)
        later["source_observation"]["body_sha256"] = "0" * 64
        self.assertNotEqual(_finding_digest(first), _finding_digest(later))

    def test_http_reader_hashes_original_bytes_and_records_actual_retrieval_clock(self):
        class Response(BytesIO):
            status = 200
            def geturl(inner):
                return self.meta["response_url"]
        with patch("market_checker_app.services.healthcare_scout_service.urlopen", return_value=Response(self.body)):
            payload = _json(self.meta["response_url"], "clinicaltrials.gov")
        self.assertIsInstance(payload, _ObservedJson)
        self.assertEqual(self.meta["body_sha256"], payload.provenance["body_sha256"])
        self.assertEqual(json.loads(self.body), dict(payload))
        self.assertEqual("ACTUAL_HTTP_RETRIEVAL", payload.provenance["clock_basis"])
        self.assertLessEqual(datetime.fromisoformat(payload.provenance["requested_at"]),
                             datetime.fromisoformat(payload.provenance["observed_at"]))

    def test_exact_study_api_preserves_provenance_and_rejects_nct_redirect(self):
        provenance = self.detail()["source_observation"]
        client = ClinicalTrialsClient()
        with patch("market_checker_app.services.healthcare_scout_service._json",
                   return_value=_ObservedJson(self.row, provenance)):
            result = client.study("NCT03987919")
        self.assertEqual(self.row, result)
        self.assertEqual(provenance, client.last_page_provenance)
        client.last_page_provenance["observed_at"] = "changed"
        self.assertEqual(self.meta["observed_at"], provenance["observed_at"])
        with patch("market_checker_app.services.healthcare_scout_service._json", return_value=self.row):
            with self.assertRaisesRegex(ValueError, "exact NCT scope"):
                client.study("NCT04847557")

    def test_unseen_snapshot_knowledge_changes_scope_without_rewriting_old_event_records(self):
        empty = HealthcareIdentityRegistry([])
        scope = dict(source="clinicaltrials", ticker="LLY", cik=ISSUER["cik"], as_of=REVIEW - timedelta(seconds=1))
        self.assertEqual(empty.fingerprint(**scope), self.registry.fingerprint(**scope))
        scope["as_of"] = REVIEW
        self.assertNotEqual(empty.fingerprint(**scope), self.registry.fingerprint(**scope))
        before = Path("evidence/batch4_healthcare_20261013/identity_proposal.json").read_bytes()
        event = json.loads(before)["records"][0]
        historical = HealthcareIdentityRegistry([event])
        result = historical.resolve(source="clinicaltrials", kind="clinical_trial_sponsor", ticker="LLY",
            cik=ISSUER["cik"], entity_name=ISSUER["company_name"], identifiers=event["identifiers"],
            event_date=date.fromisoformat(event["effective_from"]), as_of=REVIEW)
        self.assertEqual("EXACT_DATED_OBJECT", result["identity_resolution"])
        self.assertEqual(before, Path("evidence/batch4_healthcare_20261013/identity_proposal.json").read_bytes())
        self.assertFalse(_identity_details(historical, "clinicaltrials", self.detail(), ISSUER, REVIEW)["issuer_attribution_allowed"])


if __name__ == "__main__":
    unittest.main()
