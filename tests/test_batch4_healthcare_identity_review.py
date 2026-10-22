"""Owner review rejects historical attribution unsupported by actual source dates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evidence.batch4_identity_import_20261013.review_healthcare import (
    PROPOSAL, PROPOSAL_SHA256, STUDY_KEYS, build_review, study_fact,
)
from evidence.batch4_healthcare_20261013.replay import capture
from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry,
)


class HealthcareOwnerTemporalReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = DEFAULT_HEALTHCARE_IDENTITIES.read_bytes()
        cls.report = build_review()
        cls.proposals = json.loads(PROPOSAL.read_bytes())["records"]

    def test_exact_content_owner_handoff_preserves_production_and_legacy_seed(self):
        self.assertEqual(PROPOSAL_SHA256, hashlib.sha256(PROPOSAL.read_bytes()).hexdigest())
        self.assertEqual(self.before, DEFAULT_HEALTHCARE_IDENTITIES.read_bytes())
        production = HealthcareIdentityRegistry.load().records()
        self.assertEqual(len(production), self.report["preserved_production_records"])
        self.assertEqual(self.report["production_sha256_before"], self.report["production_sha256_after"])
        seed = next(row for row in production if row["record_id"] == "lly-trial-nct03954834-version-2021-10-20")
        self.assertEqual(seed, self.report["old_lly_trial_seed"])
        baseline = json.loads((Path(__file__).parent / "fixtures" / "healthcare" /
                               "reviewed_seed_identities_20261011.json").read_text())
        self.assertEqual(seed, next(row for row in baseline["records"] if row["record_id"] == seed["record_id"]))
        self.assertFalse(self.report["production_registry_changed"])

    def test_valid_snapshot_pin_does_not_fill_the_missing_historical_issuer_day(self):
        self.assertEqual("WITHHELD_TEMPORAL_ISSUER_SPONSOR_BINDING", self.report["status"])
        self.assertEqual((2, 0, 2), (self.report["proposed_records"], self.report["adopted_records"],
                                   self.report["withheld_records"]))
        for row in self.report["records"]:
            self.assertEqual("EVENT_DATE", row["requested_time_basis"])
            self.assertEqual([], row["exact_event_day_issuer_facts"])
            anchors = {fact["anchor_date"] for issuer in row["issuer_evidence"] for fact in issuer["facts"]}
            self.assertEqual({"2021-12-31", "2024-12-31", "2025-12-31"}, anchors)
            self.assertNotIn(row["requested_effective_interval"][0], anchors)
            self.assertFalse(row["historical_sponsor_to_cik_bridge_proven"])
            self.assertFalse(row["publisher_posting_day_is_immutable_historical_version"])
            self.assertEqual("WITHHOLD", row["decision"])

    def test_current_resolver_schema_success_is_not_sufficient_owner_acceptance(self):
        for row in self.report["records"]:
            # This captures the concrete risk: schema-valid inputs would grant
            # EXACT_DATED_OBJECT despite the source-day gap found by the owner.
            self.assertEqual("EXACT_DATED_OBJECT", row["schema_valid_proposal_resolution"])
            self.assertEqual("UNMAPPED_OBJECT", row["unchanged_production_resolution"])
            self.assertFalse(row["adopted"])
            self.assertIn("NO_DATED_SPONSOR_CIK_BRIDGE_FOR_EFFECTIVE_DAY", row["reasons"])
        self.assertFalse(self.report["score_changed"])
        self.assertFalse(self.report["specialist_done_changed"])
        self.assertFalse(self.report["issuer_wide_coverage_claimed"])

    def test_actual_sponsor_scope_keeps_comparators_unattributed(self):
        records = {row["nct_id"]: row for row in self.report["records"]}
        self.assertEqual(["Semaglutide"], records["NCT03987919"]["source_snapshot"]["unattributed_interventions"])
        self.assertEqual(["Placebo"], records["NCT04847557"]["source_snapshot"]["unattributed_interventions"])
        self.assertTrue(all(row["source_snapshot"]["selected_intervention"] == "Tirzepatide" for row in records.values()))
        self.assertFalse(self.report["comparators_attributed"])

    def test_changed_publisher_sponsor_or_date_cannot_inherit_the_observed_pin(self):
        record = self.proposals[0]
        _, raw = capture(STUDY_KEYS[record["identifiers"]["nct_id"]])
        original = json.loads(raw)
        for mode in ("sponsor", "date", "intervention"):
            changed = deepcopy(original)
            protocol = changed["protocolSection"]
            if mode == "sponsor":
                protocol["sponsorCollaboratorsModule"]["leadSponsor"]["name"] = "Unproven Company"
            elif mode == "date":
                protocol["statusModule"]["lastUpdatePostDateStruct"]["date"] = "2022-02-15"
            else:
                protocol["armsInterventionsModule"]["interventions"][0]["name"] = "Different Intervention"
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "exact sponsor/intervention snapshot"):
                study_fact(record, changed)

    def test_changed_proposal_file_is_rejected_before_any_production_mutation(self):
        with TemporaryDirectory() as directory:
            changed = Path(directory) / "proposal.json"
            changed.write_bytes(PROPOSAL.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "exact-content handoff"):
                build_review(changed)
        self.assertEqual(self.before, DEFAULT_HEALTHCARE_IDENTITIES.read_bytes())


if __name__ == "__main__":
    unittest.main()
