from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.award_modification_service import (
    SOURCE_URL, audit_award_modification,
)
from market_checker_app.services.usaspending_scout_service import UsaSpendingScoutService, _award_details
from market_checker_app.storage.scout_store import ScoutStore


CLOCK = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
IDENTITY = {"ticker": "AAA", "uei": "ABCDEFGHIJKL", "recipient_name": "AAA INC",
            "uei_evidence_url": "https://www.usaspending.gov/award/example",
            "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/example",
            "effective_from": "2025-01-01", "effective_to": None,
            "known_at": "2026-09-01T00:00:00+00:00"}


def api_row(**fields):
    return {"generated_internal_id": "CONT_AWD_EXACT_1", "Award ID": "EXACT_1",
            "Recipient UEI": IDENTITY["uei"], "Recipient Name": IDENTITY["recipient_name"],
            "Start Date": "2026-08-01", "Award Amount": 100.1,
            "Total Outlays": 10.0, "Last Modified Date": "2026-09-29", **fields}


def observation(amount=100.1, *, at=CLOCK, modified="2026-09-29", finding="previous", **fields):
    details = _award_details(api_row(**{"Award Amount": amount, "Last Modified Date": modified}),
                             IDENTITY, clock=at, state={"query_end": at.date().isoformat()})
    return {"finding_id": finding, "source": "usaspending", "subject_id": "AAA",
            "source_object_id": IDENTITY["uei"] + ":CONT_AWD_EXACT_1",
            "source_url": SOURCE_URL, "verification_status": "SOURCE_VERIFIED",
            "available_at": at.isoformat(), "first_observed_at": at.isoformat(),
            "details": {**details, **fields}}


class AwardModificationAuditTests(unittest.TestCase):
    def audit(self, current, previous=(), *, at=None):
        return audit_award_modification(current, previous, as_of=at or CLOCK + timedelta(days=1))

    def test_baseline_never_infers_a_new_win_revenue_or_complete_modification_history(self):
        result = self.audit(observation(finding="current"))
        self.assertEqual("BASELINE_OBSERVATION", result["status"])
        self.assertFalse(result["comparable"])
        self.assertFalse(result["new_award_inferred"])
        self.assertFalse(result["revenue_inferred"])
        self.assertFalse(result["modification_history_complete"])
        self.assertEqual("ABCDEFGHIJKL", result["recipient_uei"])
        self.assertEqual("CONT_AWD_EXACT_1", result["generated_internal_id"])

    def test_positive_obligation_delta_uses_decimal_arithmetic_and_keeps_outlays_separate(self):
        old = observation()
        current = observation(100.4, at=CLOCK + timedelta(days=1), modified="2026-10-01",
                              finding="current", reported_total_outlays_usd=30.0)
        original = deepcopy([old, current])
        result = self.audit(current, [old])
        self.assertEqual("OBSERVED_REVISION", result["status"])
        self.assertTrue(result["comparable"])
        self.assertTrue(result["source_revision_order_confirmed"])
        self.assertEqual(0.3, result["obligation_delta_usd"])
        self.assertEqual("0.3", result["obligation_delta_usd_decimal"])
        self.assertEqual(20, result["outlays_delta_usd"])
        self.assertEqual("INCREASE", result["obligation_direction"])
        self.assertFalse(result["delta_is_transaction"])
        self.assertEqual(original, [old, current])

    def test_deobligation_full_reversal_and_negative_cumulative_amount_are_visible(self):
        for amount, delta in ((80.1, -20), (0, -100.1), (-5.1, -105.2)):
            with self.subTest(amount=amount):
                result = self.audit(observation(amount, at=CLOCK + timedelta(days=1),
                                               modified="2026-10-01", finding="current"), [observation()])
                self.assertEqual("OBSERVED_REVISION", result["status"])
                self.assertEqual(delta, result["obligation_delta_usd"])
                self.assertEqual("DECREASE", result["obligation_direction"])

    def test_same_source_revision_changes_are_restatements_without_proven_source_order(self):
        current = observation(110, at=CLOCK + timedelta(days=1), finding="current")
        result = self.audit(current, [observation()])
        self.assertEqual("OBSERVED_RESTATEMENT", result["status"])
        self.assertTrue(result["comparable"])
        self.assertFalse(result["source_revision_order_confirmed"])
        self.assertEqual(9.9, result["obligation_delta_usd"])

    def test_timestamp_offsets_and_date_precision_do_not_invent_source_revision_order(self):
        old = observation(modified="2026-09-30T09:00:00+01:00")
        current = observation(120, at=CLOCK + timedelta(hours=1),
                              modified="2026-09-30T12:30:00+00:00", finding="current")
        self.assertTrue(self.audit(current, [old])["source_revision_order_confirmed"])
        current["details"]["last_modified_date"] = "2026-09-30T10:00:00+03:00"
        self.assertEqual("OUT_OF_ORDER_SOURCE_REVISION", self.audit(current, [old])["status"])
        current["details"]["last_modified_date"] = "2026-09-30"
        result = self.audit(current, [old])
        self.assertEqual("OBSERVED_RESTATEMENT", result["status"])
        self.assertFalse(result["source_revision_order_confirmed"])

    def test_earlier_source_revision_cannot_be_compared_as_an_increase(self):
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-09-28", finding="current")
        result = self.audit(current, [observation()])
        self.assertEqual("OUT_OF_ORDER_SOURCE_REVISION", result["status"])
        self.assertFalse(result["comparable"])
        self.assertIsNone(result["obligation_delta_usd"])
        self.assertEqual(120, result["current"]["cumulative_obligation_usd"])

    def test_revision_dates_do_not_substitute_for_availability_and_observation_times(self):
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-09-29", finding="current")
        self.assertEqual("NOT_AVAILABLE_AS_OF", self.audit(current, [observation()], at=CLOCK)["status"])
        future = observation(900, at=CLOCK + timedelta(days=2), modified="2026-09-29", finding="future")
        result = self.audit(current, [observation(), future], at=CLOCK + timedelta(days=3))
        self.assertEqual("previous", result["previous"]["finding_id"])
        self.assertEqual(19.9, result["obligation_delta_usd"])

    def test_future_availability_is_excluded_even_when_observation_field_is_earlier(self):
        future = observation(900)
        future["available_at"] = (CLOCK + timedelta(days=2)).isoformat()
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        result = self.audit(current, [future])
        self.assertEqual("BASELINE_OBSERVATION", result["status"])
        self.assertIsNone(result["obligation_delta_usd"])

    def test_naive_or_unavailable_identity_timestamps_fail_closed(self):
        for field, value in (("first_observed_at", "2026-09-30T12:00:00"),
                             ("available_at", "2026-10-01T00:00:00+00:00")):
            with self.subTest(field=field):
                current = observation()
                current[field] = value
                self.assertEqual("INVALID_OBSERVATION", self.audit(current)["status"])
        current = observation(relationship_known_at="2026-10-01T00:00:00+00:00")
        self.assertEqual("INVALID_OBSERVATION", self.audit(current)["status"])

    def test_known_future_publication_cannot_be_hidden_by_earlier_available_at(self):
        current = observation()
        current["published_at"] = (CLOCK + timedelta(days=1)).isoformat()
        self.assertEqual("INVALID_OBSERVATION", self.audit(current)["status"])

    def test_recipient_uei_and_legal_name_must_remain_exact(self):
        for fields in ({"uei": "MNOPQRSTUVWX"}, {"recipient_name": "AAA SUBSIDIARY INC"}):
            with self.subTest(fields=fields):
                current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01",
                                      finding="current", **fields)
                current["source_object_id"] = current["details"]["uei"] + ":CONT_AWD_EXACT_1"
                result = self.audit(current, [observation()])
                self.assertEqual("RECIPIENT_CHANGED", result["status"])
                self.assertIsNone(result["obligation_delta_usd"])

    def test_normalized_spacing_and_case_are_not_fuzzy_recipient_matching(self):
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01",
                              finding="current", recipient_name="aaa   inc")
        self.assertTrue(self.audit(current, [observation()])["comparable"])

    def test_award_scope_and_relationship_changes_block_comparable_deltas(self):
        for fields in ({"award_id": "OTHER"}, {"award_start": "2026-08-02"},
                       {"awarding_agency": "OTHER"}, {"contract_award_type": "OTHER"},
                       {"relationship_effective_from": "2025-02-01"},
                       {"relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/other"}):
            with self.subTest(fields=fields):
                result = self.audit(observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01",
                                               finding="current", **fields), [observation()])
                self.assertEqual("SCOPE_CHANGED", result["status"])
                self.assertFalse(result["comparable"])
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        current["subject_id"] = "BBB"
        self.assertEqual("SCOPE_CHANGED", self.audit(current, [observation()])["status"])

    def test_unrelated_awards_are_not_predecessors_even_with_identical_display_award_id(self):
        other = observation(900, generated_internal_id="CONT_OTHER")
        other["source_object_id"] = IDENTITY["uei"] + ":CONT_OTHER"
        self.assertEqual("BASELINE_OBSERVATION", self.audit(observation(finding="current"), [other])["status"])

    def test_changed_or_unsupported_currency_is_never_converted(self):
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01",
                              finding="current", currency="EUR")
        result = self.audit(current, [observation(currency="USD")])
        self.assertEqual("CURRENCY_CHANGED", result["status"])
        self.assertIsNone(result["obligation_delta_usd"])
        self.assertEqual("UNSUPPORTED_CURRENCY", self.audit(current, [observation(currency="EUR")])["status"])

    def test_nonfinite_boolean_text_and_overflow_amounts_fail_closed(self):
        for field in ("reported_award_amount_usd", "reported_total_outlays_usd"):
            for value in (True, float("nan"), float("inf"), "120", 10 ** 400):
                with self.subTest(field=field, value_type=type(value).__name__):
                    current = observation(**{field: value})
                    result = self.audit(current)
                    self.assertEqual("INVALID_OBSERVATION", result["status"])
                    json.dumps(result, allow_nan=False)

    def test_missing_obligation_never_means_zero_but_missing_outlay_leaves_obligation_comparable(self):
        current = observation(None, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        self.assertEqual("MISSING_OBLIGATION", self.audit(current, [observation()])["status"])
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01",
                              finding="current", reported_total_outlays_usd=None)
        result = self.audit(current, [observation()])
        self.assertTrue(result["comparable"])
        self.assertEqual(19.9, result["obligation_delta_usd"])
        self.assertIsNone(result["outlays_delta_usd"])
        self.assertEqual("MISSING_AMOUNT", result["outlays_comparison_status"])

    def test_missing_or_future_source_revision_produces_no_delta(self):
        current = observation(120, at=CLOCK + timedelta(days=1), modified=None, finding="current")
        self.assertEqual("MISSING_SOURCE_REVISION", self.audit(current, [observation()])["status"])
        current["details"]["last_modified_date"] = "2026-10-01T13:00:00+00:00"
        self.assertEqual("INVALID_OBSERVATION", self.audit(current, [observation()])["status"])

    def test_equal_clock_conflicts_do_not_use_finding_id_or_row_order_as_a_tiebreaker(self):
        first, second = observation(finding="a"), observation(200, finding="z")
        current = observation(300, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        for previous in ([first, second], [second, first]):
            self.assertEqual("AMBIGUOUS_PREDECESSOR", self.audit(current, previous)["status"])
        current = observation(200, finding="current")
        self.assertEqual("AMBIGUOUS_OBSERVATION_ORDER", self.audit(current, [first])["status"])

    def test_latest_actual_observation_is_used_not_latest_retrieval_or_maximum_amount(self):
        first = observation(900)
        first["retrieved_at"] = (CLOCK + timedelta(days=30)).isoformat()
        second = observation(100, at=CLOCK + timedelta(hours=1), finding="second")
        current = observation(130, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        result = self.audit(current, [first, second])
        self.assertEqual("second", result["previous"]["finding_id"])
        self.assertEqual(30, result["obligation_delta_usd"])

    def test_invalid_same_award_predecessor_cannot_be_skipped_to_create_a_favourable_delta(self):
        old = observation()
        invalid = observation(200, at=CLOCK + timedelta(hours=1), finding="invalid")
        invalid["verification_status"] = "UNVERIFIED"
        current = observation(300, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        result = self.audit(current, [old, invalid])
        self.assertEqual("INVALID_PREDECESSOR", result["status"])
        self.assertIsNone(result["obligation_delta_usd"])

    def test_finite_operands_whose_difference_overflows_produce_json_safe_blocker(self):
        old = observation(-1e308)
        current = observation(1e308, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        result = self.audit(current, [old])
        self.assertEqual("DELTA_OUT_OF_RANGE", result["status"])
        self.assertFalse(result["comparable"])
        json.dumps(result, allow_nan=False)

    def test_unchanged_totals_do_not_become_another_win(self):
        current = observation(at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        result = self.audit(current, [observation()])
        self.assertEqual("UNCHANGED_TOTALS", result["status"])
        self.assertEqual(0, result["obligation_delta_usd"])
        self.assertEqual("UNCHANGED", result["obligation_direction"])
        self.assertFalse(result["new_award_inferred"])

    def test_reading_an_existing_observation_excludes_itself_and_accepts_stored_json(self):
        old = observation()
        current = observation(120, at=CLOCK + timedelta(days=1), modified="2026-10-01", finding="current")
        current["details_json"] = json.dumps(current.pop("details"))
        result = self.audit(current, [old, current])
        self.assertEqual("previous", result["previous"]["finding_id"])
        self.assertEqual(19.9, result["obligation_delta_usd"])


class AwardModificationScoutIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ScoutStore(Path(directory.name) / "test.db")
        self.row = api_row()
        owner = self

        class Awards:
            def __init__(self):
                self.calls = []

            def awards(self, uei, *, start, end, page):
                self.calls.append((uei, start, end, page))
                return {"results": [owner.row], "page_metadata": {"hasNext": False}}

        self.client = Awards()
        self.service = UsaSpendingScoutService(self.store, client=self.client, identities=[IDENTITY])

    def records(self, at):
        return self.store.findings_as_of("AAA", as_of=at)

    def test_real_storage_keeps_pit_versions_and_revision_audit_without_replay_duplicates(self):
        self.assertEqual(1, self.service.run(as_of=CLOCK)["new_findings"])
        before = self.records(CLOCK)[0]
        self.assertEqual("BASELINE_OBSERVATION", json.loads(before["details_json"])["modification_audit"]["status"])
        self.row.update({"Award Amount": 120, "Last Modified Date": "2026-10-01"})
        later = CLOCK + timedelta(days=1)
        self.assertEqual(1, self.service.run(as_of=later)["new_findings"])
        self.row["Award Amount"] = 120.0
        self.assertEqual(0, self.service.run(as_of=later + timedelta(days=1))["new_findings"])
        self.assertEqual(1, len(self.records(CLOCK)))
        self.assertEqual(2, len(self.records(later + timedelta(days=1))))
        revised = next(row for row in self.records(later) if row["finding_id"] != before["finding_id"])
        audit = json.loads(revised["details_json"])["modification_audit"]
        self.assertEqual("OBSERVED_REVISION", audit["status"])
        self.assertEqual(before["finding_id"], audit["previous"]["finding_id"])
        self.assertEqual(19.9, audit["obligation_delta_usd"])
        self.assertEqual([1, 1, 1], [call[3] for call in self.client.calls])

    def test_legacy_content_hash_is_preserved_and_old_findings_are_not_mutated(self):
        details = _award_details(self.row, IDENTITY, clock=CLOCK,
                                 state={"query_end": CLOCK.date().isoformat()})
        content_hash = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
        self.store.record_finding(source="usaspending", subject_id="AAA",
                                  source_object_id=IDENTITY["uei"] + ":CONT_AWD_EXACT_1",
                                  content_hash=content_hash, title="legacy", source_url=SOURCE_URL, locator="exact",
                                  published_at=CLOCK, available_at=CLOCK, observed_at=CLOCK, details=details)
        self.assertEqual(0, self.service.run(as_of=CLOCK)["new_findings"])
        self.assertEqual(1, len(self.records(CLOCK)))
        self.assertNotIn("modification_audit", json.loads(self.records(CLOCK)[0]["details_json"]))
        self.row["Award Amount"] = 50
        later = CLOCK + timedelta(days=1)
        self.assertEqual(1, self.service.run(as_of=later)["new_findings"])
        revised = next(json.loads(row["details_json"]) for row in self.records(later)
                       if "modification_audit" in json.loads(row["details_json"]))
        self.assertEqual("OBSERVED_RESTATEMENT", revised["modification_audit"]["status"])
        self.assertEqual(-50.1, revised["modification_audit"]["obligation_delta_usd"])
        self.assertEqual(2, len(self.records(later)))

    def test_wrong_recipient_row_is_still_rejected_before_it_can_create_a_delta(self):
        self.service.run(as_of=CLOCK)
        self.row.update({"Recipient UEI": "MNOPQRSTUVWX", "Award Amount": 500})
        result = self.service.run(as_of=CLOCK + timedelta(days=1))
        self.assertEqual(1, result["rejected_rows"])
        self.assertEqual(0, result["new_findings"])
        self.assertEqual(1, len(self.records(CLOCK + timedelta(days=1))))


if __name__ == "__main__":
    unittest.main()
