"""Actual charter cases and narrow source-role/date/link mismatch regressions.

Mutated originals and injected clocks below are synthetic negative controls;
only the two unchanged pinned whole originals supply actual source facts.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import patch
import unittest
from pathlib import Path

from evidence.sourcefront_identity_20261020.replay_charter_reviews import load_case
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from market_checker_app.services.sec_corporate_action_bridge import (
    action_text, review_charter_class_disclosure, review_corporate_action_bridge,
)

CLOCK = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
CLOCK_PATCH = "market_checker_app.services.sec_corporate_action_bridge._charter_clock"


def changed_source(service, claim, field, *, body=None, url=None):
    """Synthetic in-memory derivative only; retained files never change."""
    prior = service.snapshots[claim[field]]
    revised = SourceSnapshot(prior.kind, url or prior.url, prior.observed_at,
                             body if body is not None else prior.payload, prior.published_at)
    service.snapshots = {key: value for key, value in service.snapshots.items() if key != prior.snapshot_id}
    service.snapshots[revised.snapshot_id] = revised
    claim[field] = revised.snapshot_id
    return revised


class CharterDisclosureTests(unittest.TestCase):
    def case(self, ticker):
        return load_case(ticker, source_cutoff=CLOCK)

    def review(self, service, claim, *, as_of=None, clocks=None):
        with patch(CLOCK_PATCH, side_effect=clocks or [CLOCK, CLOCK + timedelta(seconds=1)]):
            return review_charter_class_disclosure(service, claim, as_of=as_of)

    def test_actual_leg_surviving_authorized_class_does_not_extend_exchanged_old_instrument(self):
        service, claim = self.case("LEG")
        before = deepcopy(service.snapshots)
        result = self.review(service, claim)
        self.assertEqual("6cd32d55f62c8d5b0a157b63527a07be489d94b8effe67ef4f0f3d892769d5ba", result["charter"]["sha256"])
        self.assertTrue(result["authorized_class_supported"])
        self.assertEqual("SURVIVING_AUTHORIZED_CLASS", result["class_role"])
        self.assertEqual(10000, result["authorized_common_shares"])
        self.assertEqual("USD:0.001", result["authorized_class_terms"]["par_term"])
        self.assertEqual("2026-08-26", result["charter_execution_on"])
        self.assertIsNone(result["charter_effective_on"])
        self.assertEqual(["DIRECT_CHARTER_EFFECTIVE_DAY_UNKNOWN_OR_UNPROVEN"], result["blockers"])
        for flag in ("cusip_supported", "control_supported", "instrument_continuity_supported",
                     "issued_or_outstanding_shares_proven", "identity_transition_approved", "production_registry_changed"):
            self.assertFalse(result[flag], flag)
        self.assertEqual(before, service.snapshots)

    def test_actual_xom_successor_common_not_preferred_or_total_authority(self):
        service, claim = self.case("XOM")
        result = self.review(service, claim)
        self.assertEqual("ef4d1a0c0c71bc3ea536ca3ee333c91db561e99c57448d851225e8b3dcf920ac", result["charter"]["sha256"])
        self.assertEqual("PRIMARY_CHARTER_CLASS_SCOPED", result["status"])
        self.assertTrue(result["authorized_class_supported"])
        self.assertEqual(9000000000, result["authorized_common_shares"])
        self.assertNotEqual(9200000000, result["authorized_common_shares"])
        self.assertNotEqual(200000000, result["authorized_common_shares"])
        self.assertEqual("2026-07-01", result["charter_effective_on"])
        self.assertEqual("2026-07-01", result["charter_execution_on"])
        self.assertFalse(result["historic_shareholders_are_current_owners"])
        self.assertFalse(result["same_accession_is_independent_action_proof"])
        self.assertEqual(0, result["current_quarter_verified_coverage"])
        self.assertEqual(0, result["new_verified_identities"])

    def test_actual_plans_select_current_physical_review_without_a_null_explicit_cutoff(self):
        folder = Path(__file__).resolve().parents[1] / "evidence" / "sourcefront_identity_20261020"
        for filename in ("LEG_pravni_kapital_po_fuzi.json", "XOM_pravni_kapital_nastupce.json"):
            with self.subTest(plan=filename):
                plan = json.loads((folder / filename).read_bytes())
                self.assertNotIn("as_of", plan)
                self.assertEqual("SEC_CHARTER_CLASS", plan["kind"])
                self.assertEqual(4, len(plan["captures"]))
                self.assertEqual({"quarter", "claim"}, set(plan["request"]))

    def test_existing_bridge_entry_dispatches_same_narrow_charter_review(self):
        service, claim = self.case("XOM")
        expected = self.review(service, claim)
        with patch(CLOCK_PATCH, side_effect=[CLOCK, CLOCK + timedelta(seconds=1)]):
            actual = review_corporate_action_bridge(service, claim)
        self.assertEqual(expected, actual)
        digest = actual.pop("review_sha256")
        self.assertEqual(digest, hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_exchanged_par_preferred_quantity_and_aggregate_quantity_cannot_be_borrowed(self):
        for ticker, field, wrong in (("LEG", "class_title", "common stock, par value $0.01 per share"),
                                     ("XOM", "authorized_shares", "200000000"),
                                     ("XOM", "authorized_shares", "9200000000")):
            with self.subTest(ticker=ticker, field=field, wrong=wrong):
                service, claim = self.case(ticker)
                claim[field] = wrong
                result = self.review(service, claim)
                self.assertFalse(result["authorized_class_supported"])
                self.assertIsNone(result["authorized_common_shares"])
                self.assertIn("EXACT_AUTHORIZED_COMMON_CLASS_OR_QUANTITY_UNPROVEN", result["blockers"])

    def test_requested_corporate_role_requires_exact_parent_named_role(self):
        service, claim = self.case("LEG")
        claim["class_role"] = "SUCCESSOR_AUTHORIZED_CLASS"
        result = self.review(service, claim)
        self.assertFalse(result["authorized_class_supported"])
        self.assertIn("EXACT_PARENT_TRANSACTION_ROLE_UNPROVEN", result["blockers"])

    def test_named_charter_and_exact_archive_cik_cannot_be_borrowed(self):
        service, claim = self.case("LEG")
        claim["issuer_name_text"] = "Somnigroup International Inc."
        result = self.review(service, claim)
        self.assertFalse(result["authorized_class_supported"])
        self.assertIn("EXACT_CHARTER_CORPORATION_NAME_UNPROVEN", result["blockers"])
        service, claim = self.case("LEG")
        claim["issuer_cik"] = "0001206264"
        with self.assertRaisesRegex(ValueError, "same exact archive"):
            self.review(service, claim)

    def test_literal_original_link_cannot_be_supplied_from_hidden_or_changed_href(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                service, claim = self.case("LEG")
                parent = service.snapshots[claim["parent_filing_snapshot_id"]]
                if hidden:
                    body = parent.payload.replace(b'<a href="d152590dex31.htm"', b'<a style="display:none" href="d152590dex31.htm"')
                else:
                    body = parent.payload.replace(b'd152590dex31.htm', b'd152590dex32.htm')
                self.assertNotEqual(parent.payload, body)
                changed_source(service, claim, "parent_filing_snapshot_id", body=body)
                result = self.review(service, claim)
                self.assertFalse(result["authorized_class_supported"])
                self.assertIn("ORIGINAL_PARENT_EXHIBIT_LINK_MISSING", result["blockers"])

    def test_another_accession_is_not_the_exact_referring_attachment(self):
        service, claim = self.case("XOM")
        prior = service.snapshots[claim["charter_snapshot_id"]]
        changed_source(service, claim, "charter_snapshot_id", url=prior.url.replace("000119312526291990", "000119312526291991"))
        with self.assertRaisesRegex(ValueError, "same exact archive"):
            self.review(service, claim)

    def test_original_exhibit_envelope_filename_must_match_requested_charter(self):
        service, claim = self.case("LEG")
        prior = service.snapshots[claim["charter_snapshot_id"]]
        body = prior.payload.replace(b'<FILENAME>d152590dex31.htm', b'<FILENAME>unrelated.htm')
        self.assertNotEqual(prior.payload, body)
        changed_source(service, claim, "charter_snapshot_id", body=body)
        with self.assertRaisesRegex(ValueError, "envelope"):
            self.review(service, claim)

    def test_execution_clause_does_not_supply_missing_charter_effective_day(self):
        service, claim = self.case("LEG")
        claim["effective_on"], claim["effective_excerpt"] = claim["execution_on"], claim["execution_excerpt"]
        result = self.review(service, claim)
        self.assertIsNone(result["charter_effective_on"])
        self.assertEqual("2026-08-26", result["charter_execution_on"])
        self.assertFalse(result["effective_day_inferred_from_execution"])

    def test_historic_shareholders_and_wrong_execution_day_stay_unproved(self):
        service, claim = self.case("XOM")
        text = action_text(service.snapshots[claim["charter_snapshot_id"]])
        claim["execution_on"] = "1882-01-01"
        claim["execution_excerpt"] = text[text.index("FIFTH."):text.index("SIXTH.")].strip()[:1100]
        result = self.review(service, claim)
        self.assertIsNone(result["charter_execution_on"])
        self.assertIn("CHARTER_EXECUTION_DAY_UNKNOWN_OR_UNPROVEN", result["blockers"])
        service, claim = self.case("XOM")
        claim["execution_on"] = "2026-07-02"
        result = self.review(service, claim)
        self.assertIsNone(result["charter_execution_on"])

    def test_slow_review_knowledge_is_completion_not_start_or_old_source_date(self):
        service, claim = self.case("XOM")
        end = CLOCK + timedelta(minutes=5)
        result = self.review(service, claim, clocks=[CLOCK, end])
        self.assertEqual(CLOCK.isoformat(), result["review_started_at"])
        self.assertEqual(end.isoformat(), result["knowledge_at"])
        self.assertEqual(end.isoformat(), result["review_recorded_at"])
        self.assertLess(datetime.fromisoformat(result["evidence_known_at"]), end)

    def test_explicit_cutoff_crossed_during_review_withholds_new_support(self):
        service, claim = self.case("XOM")
        result = self.review(service, claim, as_of=CLOCK + timedelta(seconds=1), clocks=[CLOCK, CLOCK + timedelta(seconds=2)])
        self.assertFalse(result["authorized_class_supported"])
        self.assertIsNone(result["charter_effective_on"])
        self.assertIsNone(result["charter_execution_on"])
        self.assertIn("DERIVED_REVIEW_COMPLETION_AFTER_REQUESTED_CUTOFF", result["blockers"])
        self.assertGreater(result["knowledge_at"], result["requested_review_as_of"])

    def test_clock_rollback_or_source_not_yet_available_cannot_be_backdated(self):
        service, claim = self.case("LEG")
        with self.assertRaisesRegex(ValueError, "moved backwards"):
            self.review(service, claim, clocks=[CLOCK, CLOCK - timedelta(seconds=1)])
        with self.assertRaisesRegex(ValueError, "physically available"):
            self.review(service, claim, as_of=datetime(2026, 10, 19, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
