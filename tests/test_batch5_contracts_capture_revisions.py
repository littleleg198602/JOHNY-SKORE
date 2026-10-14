"""Capture comparison regressions; synthetic row changes are not live revisions."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services import usaspending_transaction_scout_service as revision
from market_checker_app.services.award_transaction_history_service import CapturedTransactionPage, archive_transaction_capture
from market_checker_app.services.usaspending_scout_service import _award_details, load_verified_identities
from market_checker_app.storage.scout_store import ScoutStore
from tests import test_award_transaction_history_service as helpers


class TransactionCaptureRevisionTests(unittest.TestCase):
    setUp = helpers.AwardTransactionHistoryTests.setUp
    seed = helpers.AwardTransactionHistoryTests.seed
    service = helpers.AwardTransactionHistoryTests.service

    def archived(self, rows=(), *, at=helpers.CLOCK - timedelta(seconds=2), page=1, next_page=False,
                 mode="FIXTURE", limit=100, award=helpers.AWARD, **metadata):
        captured = helpers.capture(helpers.page_payload(rows, page=page, next_page=next_page),
                                   at=at, page=page, mode=mode, limit=limit, award=award)
        captured = CapturedTransactionPage(captured.body, {**captured.metadata, **metadata})
        return archive_transaction_capture(captured, self.archive)

    def audit(self, baseline, current, **overrides):
        return revision.audit_award_transaction_capture_versions(
            self.store, subject_id="AAA", award_finding_id=self.finding,
            baseline_manifests=baseline, current_manifests=current, identities=[helpers.IDENTITY],
            as_of=helpers.CLOCK + timedelta(hours=2), **overrides)

    def later(self, rows=(), **kwargs):
        return self.archived(rows, at=helpers.CLOCK + timedelta(hours=1), **kwargs)

    def assert_no_deletion_claim(self, result):
        self.assertFalse(result["publisher_deletion_verified"])
        self.assertFalse(result["publisher_revision_time_verified"])
        self.assertFalse(result["modification_history_complete"])
        self.assertFalse(result["company_revenue_inferred"])
        for event in result["events"]:
            self.assertIsNone(event["publisher_deleted_at"])
            self.assertIsNone(event["publisher_revision_at"])

    def test_identical_payload_in_distinct_captures_is_unchanged_without_a_revision(self):
        row = helpers.transaction()
        result = self.audit([self.archived([row])], [self.later([row])])
        self.assertEqual("COMPLETE_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(1, result["counts"]["unchanged"])
        self.assertEqual([], result["events"])
        self.assertTrue(result["new_source_capture_comparison"])
        self.assertEqual("FIXTURE", result["source_origin"])
        self.assertFalse(result["fixtures_count_as_live"])
        self.assert_no_deletion_claim(result)

    def test_same_source_replay_keeps_identity_despite_new_filename_and_replay_mode(self):
        baseline = self.archived([helpers.transaction()], mode="LIVE")
        metadata = json.loads(baseline.read_bytes())
        replay = {**metadata, "capture_mode": "ARCHIVE_REPLAY", "original_capture_mode": "LIVE",
                  "original_capture_manifest": baseline.name}
        current = self.archive / "different-local-replay.json"
        current.write_text(json.dumps(replay))
        result = self.audit([baseline], [current])
        self.assertEqual("SAME_SOURCE_CAPTURE", result["status"])
        self.assertFalse(result["new_source_capture_comparison"])
        self.assertEqual(1, result["counts"]["unchanged"])
        self.assertEqual([], result["events"])

    def test_changed_same_source_id_preserves_reported_clocks_without_revision_date_or_obligation_delta(self):
        old = helpers.transaction(last_modified_date="2026-10-03T05:06:07Z", federal_action_obligation=10)
        new = helpers.transaction(last_modified_date="2026-10-04T07:06:07+02:00", federal_action_obligation=-20)
        result = self.audit([self.archived([old])], [self.later([new])])
        self.assertEqual("COMPLETE_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(1, result["counts"]["payload_changed"])
        event = result["events"][0]
        self.assertEqual("PAYLOAD_CHANGED", event["kind"])
        self.assertEqual(["federal_action_obligation", "last_modified_date"], event["changed_fields"])
        self.assertEqual(new["last_modified_date"], event["after_modified_clock_audit"]["fields"]["last_modified_date"]["raw"])
        self.assertFalse(event["obligation_delta_inferred"])
        self.assertNotIn("obligation_delta_usd", event)
        self.assert_no_deletion_claim(result)

    def test_complete_empty_response_records_absence_window_but_never_a_deletion_date(self):
        baseline = self.archived([helpers.transaction()])
        current = self.later()
        result = self.audit([baseline], [current])
        self.assertEqual("COMPLETE_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(1, result["counts"]["not_reported_in_later_traversal"])
        event = result["events"][0]
        self.assertEqual("NOT_REPORTED_IN_LATER_TRAVERSAL", event["kind"])
        self.assertEqual((helpers.CLOCK + timedelta(hours=1)).isoformat(), event["absence_observation_window"]["completed_at"])
        self.assertFalse(event["pagination_snapshot_guaranteed"])
        self.assert_no_deletion_claim(result)

    def test_partial_current_page_cannot_establish_absence_but_can_report_captured_payload_difference(self):
        baseline = self.archived([helpers.transaction("A"), helpers.transaction("B")])
        current = self.later([helpers.transaction("A", federal_action_obligation=99)], next_page=True)
        result = self.audit([baseline], [current])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual((1, 0, 1), (result["counts"]["payload_changed"],
                                    result["counts"]["not_reported_in_later_traversal"], result["counts"]["presence_unresolved"]))
        self.assertEqual({"PAYLOAD_CHANGED", "PRESENCE_UNRESOLVED"}, {event["kind"] for event in result["events"]})
        self.assert_no_deletion_claim(result)

    def test_partial_baseline_cannot_establish_that_a_current_row_is_new(self):
        result = self.audit([self.archived([helpers.transaction("A")], next_page=True)],
                            [self.later([helpers.transaction("A"), helpers.transaction("B")])])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual((0, 1), (result["counts"]["newly_reported"], result["counts"]["presence_unresolved"]))
        self.assertEqual("PRESENCE_UNRESOLVED", result["events"][0]["kind"])

    def test_two_complete_pages_revalidate_terminal_links_and_observe_added_and_absent_ids(self):
        baseline = [self.archived([helpers.transaction("A")], next_page=True),
                    self.archived([helpers.transaction("B")], page=2, at=helpers.CLOCK + timedelta(seconds=2))]
        current = [self.later([helpers.transaction("A")], next_page=True),
                   self.archived([helpers.transaction("C")], page=2, at=helpers.CLOCK + timedelta(hours=1, seconds=2))]
        result = self.audit(baseline[::-1], current[::-1])
        self.assertEqual("COMPLETE_CAPTURE_COMPARISON", result["status"])
        self.assertEqual((1, 1, 1), (result["counts"]["unchanged"], result["counts"]["newly_reported"],
                                    result["counts"]["not_reported_in_later_traversal"]))
        self.assert_no_deletion_claim(result)

    def test_only_tail_page_never_counts_as_complete_negative_or_new_presence(self):
        result = self.audit([self.archived([helpers.transaction()])], [self.later([], page=2)])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(0, result["counts"]["not_reported_in_later_traversal"])
        self.assertFalse(result["current"]["traversal_complete"])

    def test_http400_empty_payload_is_not_a_complete_negative(self):
        result = self.audit([self.archived([helpers.transaction()])], [self.later(http_status=400)])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual((0, 1), (result["counts"]["not_reported_in_later_traversal"], result["counts"]["presence_unresolved"]))
        self.assertFalse(result["current"]["traversal_complete"])

    def test_claimed_complete_metadata_does_not_override_raw_nonterminal_pagination(self):
        result = self.audit([self.archived([helpers.transaction("A"), helpers.transaction("B")])],
                            [self.later([helpers.transaction("A")], next_page=True,
                                        status="COMPLETE", traversal_complete=True, modification_history_complete=True)])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(0, result["counts"]["not_reported_in_later_traversal"])
        self.assertFalse(result["current"]["traversal_complete"])
        self.assertFalse(result["modification_history_complete"])

    def test_different_page_size_or_award_scope_never_compares_even_complete_captures(self):
        baseline = self.archived([helpers.transaction()])
        for kwargs in ({"limit": 50}, {"award": "CONT_AWD_DIFFERENT"}):
            with self.subTest(kwargs=kwargs):
                result = self.audit([baseline], [self.later(**kwargs)])
                self.assertEqual("INCOMPARABLE_CAPTURE_SCOPES", result["status"])
                self.assertEqual([], result["events"])

    def test_mixed_award_pages_do_not_inherit_attribution_from_the_first_page(self):
        result = self.audit([self.archived([helpers.transaction()])],
                            [self.later([helpers.transaction()], next_page=True),
                             self.archived([], page=2, at=helpers.CLOCK + timedelta(hours=1, seconds=2), award="CONT_AWD_OTHER")])
        self.assertEqual("INCOMPARABLE_CAPTURE_SCOPES", result["status"])
        self.assertEqual([], result["events"])

    def test_conflicting_duplicate_ids_are_quarantined_and_cannot_generate_absence(self):
        result = self.audit([self.archived([helpers.transaction()])],
                            [self.later([helpers.transaction(), helpers.transaction(federal_action_obligation=99)])])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual((0, 0, 1), (result["counts"]["payload_changed"], result["counts"]["not_reported_in_later_traversal"],
                                    result["counts"]["presence_unresolved"]))
        self.assertEqual([helpers.transaction()["id"]], result["current"]["ambiguous_transaction_ids"])

    def test_modified_clock_conflict_preserves_payload_change_but_disables_absence(self):
        result = self.audit([self.archived([helpers.transaction("A"), helpers.transaction("B")])],
                            [self.later([helpers.transaction("A", last_modified_date="2027-01-01T00:00:00Z")])])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual((1, 0, 1), (result["counts"]["payload_changed"], result["counts"]["not_reported_in_later_traversal"],
                                    result["counts"]["presence_unresolved"]))
        event = next(event for event in result["events"] if event["kind"] == "PAYLOAD_CHANGED")
        self.assertEqual("CONFLICT", event["after_modified_clock_audit"]["status"])
        self.assert_no_deletion_claim(result)

    def test_missing_to_null_field_and_numeric_json_type_changes_are_not_silently_lost(self):
        result = self.audit([self.archived([helpers.transaction(federal_action_obligation=1)])],
                            [self.later([helpers.transaction(federal_action_obligation=1.0, source_note=None)])])
        self.assertEqual(["federal_action_obligation", "source_note"], result["events"][0]["changed_fields"])

    def test_unordered_or_overlapping_capture_windows_do_not_create_later_presence_claims(self):
        baseline = self.archived([helpers.transaction()])
        current = self.archived([], at=helpers.CLOCK - timedelta(milliseconds=1500))
        result = self.audit([baseline], [current])
        self.assertEqual("UNORDERED_CAPTURE_WINDOWS", result["status"])
        self.assertEqual([], result["events"])
        self.assertFalse(result["new_source_capture_comparison"])

    def test_capture_after_cutoff_is_not_used_to_establish_absence(self):
        result = self.audit([self.archived([helpers.transaction()])],
                            [self.archived([], at=helpers.CLOCK + timedelta(hours=3))])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(0, result["counts"]["not_reported_in_later_traversal"])
        self.assertFalse(result["current"]["traversal_complete"])

    def test_hash_corruption_and_malformed_pagination_cannot_be_complete_negative(self):
        baseline = self.archived([helpers.transaction()])
        current = self.later()
        metadata = json.loads(current.read_bytes())
        (current.parent / metadata["body_file"]).write_bytes(b'{"results":[]}')
        result = self.audit([baseline], [current])
        self.assertEqual("INCOMPARABLE_CAPTURE_SCOPES", result["status"])
        self.assertEqual([], result["events"])
        broken = helpers.page_payload([])
        broken["page_metadata"]["next"] = 2
        captured = helpers.capture(broken, at=helpers.CLOCK + timedelta(hours=1))
        current = archive_transaction_capture(captured, self.archive)
        result = self.audit([baseline], [current])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertEqual(0, result["counts"]["not_reported_in_later_traversal"])

    def test_withheld_bodies_are_not_read_and_have_no_mirror_or_rewritten_fallback(self):
        baseline = self.archived([helpers.transaction()])
        current = self.later()
        metadata = json.loads(current.read_bytes())
        metadata["publication_status"] = "WITHHELD_ACCESS_VALUE"
        current.write_text(json.dumps(metadata))
        forbidden = current.parent / metadata["body_file"]
        original, visited = revision._read_file, []

        def tracked(path, maximum):
            visited.append(path)
            return original(path, maximum)

        with patch.object(revision, "_read_file", side_effect=tracked):
            result = self.audit([baseline], [current])
        self.assertNotIn(forbidden, visited)
        self.assertEqual("INCOMPARABLE_CAPTURE_SCOPES", result["status"])
        self.assertEqual([], result["events"])

    def test_invalid_target_starts_no_capture_read_or_http(self):
        with patch.object(revision, "_manifest") as reader, \
                patch("market_checker_app.services.award_transaction_history_service.urlopen") as network:
            result = revision.audit_award_transaction_capture_versions(
                self.store, subject_id="AAA", award_finding_id="missing", baseline_manifests=[], current_manifests=[],
                identities=[helpers.IDENTITY], as_of=helpers.CLOCK)
        reader.assert_not_called()
        network.assert_not_called()
        self.assertEqual("INVALID_COMPARISON", result["status"])

    def test_bounds_signal_truncation_and_do_not_change_counts_or_infer_deletion_dates(self):
        before = self.archived([helpers.transaction()])
        after = self.later([helpers.transaction(federal_action_obligation=99)])
        with patch.object(revision, "MAX_AUDIT_EVENT_BYTES", 1):
            result = self.audit([before], [after])
        self.assertEqual("PARTIAL_CAPTURE_COMPARISON", result["status"])
        self.assertTrue(result["events_truncated"])
        self.assertEqual(1, result["counts"]["payload_changed"])
        self.assertEqual([], result["events"])
        self.assertEqual("INVALID_COMPARISON", self.audit([before] * 6, [after])["status"])

    def test_runtime_hook_is_read_only_and_repeats_preserve_original_findings_and_bytes(self):
        baseline, current = self.archived([helpers.transaction()]), self.later()
        service = self.service(helpers.FixtureClient([]))
        target_before = self.store.finding_by_id(self.finding, subject_id="AAA")
        source_before = {str(path): path.read_bytes() for path in self.archive.iterdir()}
        with patch("market_checker_app.services.award_transaction_history_service.urlopen") as network:
            first = service.audit_capture_versions(subject_id="AAA", award_finding_id=self.finding,
                                                    baseline_manifests=[baseline], current_manifests=[current],
                                                    as_of=helpers.CLOCK + timedelta(hours=2))
            second = service.audit_capture_versions(subject_id="AAA", award_finding_id=self.finding,
                                                     baseline_manifests=[baseline], current_manifests=[current],
                                                     as_of=helpers.CLOCK + timedelta(hours=2))
        network.assert_not_called()
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(target_before, self.store.finding_by_id(self.finding, subject_id="AAA"))
        self.assertEqual(source_before, {str(path): path.read_bytes() for path in self.archive.iterdir()})
        with self.store._connect() as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM scout_findings").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scout_award_transaction_observations").fetchone()[0])
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='scout_usaspending_requests'").fetchone())


class ActualAwardCaptureComparisonTests(unittest.TestCase):
    def test_two_original_fourteen_row_captures_are_unchanged_and_oct13_replay_is_not_a_new_version(self):
        root = Path(__file__).resolve().parents[1]
        baseline = root / "evidence/batch5_contracts_20261014/capture-probe-import.json"
        current = root / "evidence/batch3_contracts_live_20261012/capture-9cfe102b3b3a421292a3e20e0b0c39b2.json"
        replay = root / "evidence/batch3_contracts_live_20261012/capture-6524208d7c5a40c4b5e36637aded7d32.json"
        metadata = json.loads(current.read_bytes())
        expected = "2e74f5f6960faf192b7f1c7256c9b174453bf4ab63d85e935b2ff3debed75447"
        self.assertEqual(expected, hashlib.sha256((current.parent / metadata["body_file"]).read_bytes()).hexdigest())
        identity = next(row for row in load_verified_identities() if row["uei"] == "UTJWTSLMFNG4")
        case = json.loads((root / "evidence/usaspending_live_20261001.json").read_bytes())["cases"]["usaspending_positive_contract_uei"]
        originally_observed = datetime.fromisoformat(case["observed_at"])
        award = next(row for row in case["payload"]["results"] if row["generated_internal_id"] == metadata["request"]["award_id"])
        details = _award_details(award, identity, clock=originally_observed, state={"query_end": originally_observed.date().isoformat()})
        with TemporaryDirectory() as temporary:
            store = ScoutStore(Path(temporary) / "scout.db")
            target, _ = store.record_finding(
                source="usaspending", subject_id="LMT", source_object_id=identity["uei"] + ":" + award["generated_internal_id"],
                content_hash=hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest(),
                title="Actual prior archived prime award", source_url=case["source_url"], locator="archived-award",
                published_at=originally_observed, available_at=originally_observed, observed_at=originally_observed, details=details)
            before = store.finding_by_id(target, subject_id="LMT")
            with patch("market_checker_app.services.award_transaction_history_service.urlopen") as network:
                first = revision.audit_award_transaction_capture_versions(
                    store, subject_id="LMT", award_finding_id=target, baseline_manifests=[baseline],
                    current_manifests=[current], identities=[identity])
                repeated = revision.audit_award_transaction_capture_versions(
                    store, subject_id="LMT", award_finding_id=target, baseline_manifests=[current],
                    current_manifests=[replay], identities=[identity])
            network.assert_not_called()
            self.assertEqual(("COMPLETE_CAPTURE_COMPARISON", 14), (first["status"], first["counts"]["unchanged"]))
            self.assertEqual([], first["events"])
            self.assertEqual("PUBLISHER_CAPTURE", first["source_origin"])
            self.assertEqual("SAME_SOURCE_CAPTURE", repeated["status"])
            self.assertFalse(repeated["new_source_capture_comparison"])
            self.assertEqual(before, store.finding_by_id(target, subject_id="LMT"))
            self.assertFalse(first["publisher_revision_time_verified"])
            self.assertFalse(first["publisher_deletion_verified"])
            self.assertEqual(0, first["http_attempts"])


if __name__ == "__main__":
    unittest.main()
