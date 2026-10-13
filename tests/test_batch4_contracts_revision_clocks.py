"""Revision observation/knowledge tests; synthetic clocks are not live acceptance."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from market_checker_app.services.award_transaction_history_service import (
    ArchivedTransactionsClient, AwardTransactionHistoryService, CapturedTransactionPage, archive_transaction_capture,
)
from market_checker_app.services.usaspending_scout_service import _award_details, load_verified_identities
from tests import test_award_transaction_history_service as helpers


class TransactionRevisionClockTests(unittest.TestCase):
    setUp = helpers.AwardTransactionHistoryTests.setUp
    seed = helpers.AwardTransactionHistoryTests.seed
    service = helpers.AwardTransactionHistoryTests.service
    run_service = helpers.AwardTransactionHistoryTests.run_service
    transactions = helpers.AwardTransactionHistoryTests.transactions

    def audit(self, service, *, at=helpers.CLOCK + timedelta(days=1), limit=500):
        return service.transaction_store.observations_for_award(
            subject_id="AAA", generated_award_id=helpers.AWARD, as_of=at, limit=limit)

    def test_exact_modified_clock_offset_is_validated_without_backdating_release_or_availability(self):
        raw = helpers.transaction(last_modified_date="2026-10-03T07:06:07+02:00")
        client = helpers.FixtureClient([helpers.capture(helpers.page_payload([raw]))])
        service = self.service(client)
        result = self.run_service(service)
        self.assertEqual(("COMPLETE", 1, 0),
                         (result["status"], result["new_source_observations"], result["modified_clock_conflicts"]))
        clock = result["source_transactions"][0]["source_modified_clock_audit"]
        self.assertEqual("VALIDATED_UTC", clock["status"])
        self.assertEqual(raw["last_modified_date"], clock["fields"]["last_modified_date"]["raw"])
        self.assertEqual("2026-10-03T05:06:07+00:00", clock["fields"]["last_modified_date"]["utc"])
        self.assertFalse(clock["publisher_clock_semantics_verified"])
        self.assertFalse(clock["publication_or_revision_order_inferred"])
        stored = self.transactions()[0]
        self.assertEqual((helpers.CLOCK - timedelta(seconds=2)).isoformat(), stored["available_at"])
        self.assertEqual(raw, json.loads(stored["details_json"])["source_transaction"])

    def test_date_only_and_unzoned_modified_fields_keep_exact_precision_without_inventing_utc(self):
        raw = helpers.transaction(last_modified_date="2026-10-03", updated_at="2026-10-03T05:06:07")
        service = self.service(helpers.FixtureClient([helpers.capture(helpers.page_payload([raw]))]))
        result = self.run_service(service)
        self.assertEqual("COMPLETE", result["status"])
        audit = result["source_transactions"][0]["source_modified_clock_audit"]
        self.assertEqual("UNRESOLVED_PRECISION", audit["status"])
        self.assertEqual("DATE_ONLY", audit["fields"]["last_modified_date"]["status"])
        self.assertEqual("TIMEZONE_UNSPECIFIED", audit["fields"]["updated_at"]["status"])
        self.assertTrue(all(field["utc"] is None for field in audit["fields"].values()))
        self.assertFalse(audit["timezone_inferred"])

    def test_missing_and_null_modified_clocks_are_explicit_unknowns_not_bad_or_complete_history(self):
        rows = [helpers.transaction("MISSING"), helpers.transaction("NULL", last_modified_date=None)]
        service = self.service(helpers.FixtureClient([helpers.capture(helpers.page_payload(rows))]))
        result = self.run_service(service)
        self.assertEqual(("COMPLETE", 0), (result["status"], result["modified_clock_conflicts"]))
        for row in result["source_transactions"]:
            self.assertEqual("UNREPORTED", row["source_modified_clock_audit"]["status"])
        self.assertFalse(result["modification_history_complete"])
        self.assertFalse(self.audit(service)["historical_revisions_complete"])

    def test_future_aware_modified_clock_is_partial_and_retains_exact_source_row_and_bytes(self):
        raw = helpers.transaction(last_modified_date="2027-01-01T00:00:00Z")
        captured = helpers.capture(helpers.page_payload([raw]))
        service = self.service(helpers.FixtureClient([captured]))
        result = self.run_service(service)
        self.assertEqual(("PARTIAL", True, 1, 1),
                         (result["status"], result["traversal_complete"], result["accepted_rows"],
                          result["modified_clock_conflicts"]))
        field = result["source_transactions"][0]["source_modified_clock_audit"]["fields"]["last_modified_date"]
        self.assertEqual(("AFTER_CAPTURE", "AFTER"), (field["status"], field["relation_to_capture"]))
        self.assertEqual(raw, json.loads(self.transactions()[0]["details_json"])["source_transaction"])
        body = next(self.archive.glob("body-*.response")).read_bytes()
        self.assertEqual(captured.body, body)
        self.assertEqual(1, len(self.audit(service)["observations"]))

    def test_malformed_modified_values_are_audited_and_cannot_silently_pass_terminal_completion(self):
        malformed = (True, 1, {}, [], "", "2026-02-30", "2026-10-03T25:06:07Z", "20261003", "2026-W40-1")
        for index, value in enumerate(malformed):
            with self.subTest(value=value):
                raw = helpers.transaction(str(index), last_modified_date=value)
                service = self.service(helpers.FixtureClient([helpers.capture(helpers.page_payload([raw]))]))
                result = self.run_service(service)
                self.assertEqual(("PARTIAL", 1), (result["status"], result["modified_clock_conflicts"]))
                self.assertEqual(value, result["source_transactions"][0]["source_modified_fields"]["last_modified_date"])
                self.assertEqual("INVALID", result["source_transactions"][0]["source_modified_clock_audit"]["fields"]["last_modified_date"]["status"])

    def test_repeat_of_same_capture_adds_no_source_observation_despite_distinct_local_manifests(self):
        service = self.service(helpers.FixtureClient([helpers.capture(helpers.page_payload([helpers.transaction()]))]))
        first = self.run_service(service)
        before = self.audit(service)["observations"]
        second = self.run_service(service, at=helpers.CLOCK + timedelta(hours=1))
        self.assertEqual((1, 0), (first["new_source_observations"], second["new_source_observations"]))
        self.assertEqual(first["source_transactions"][0]["source_observation"]["observation_id"],
                         second["source_transactions"][0]["source_observation"]["observation_id"])
        self.assertEqual(before, self.audit(service)["observations"])
        self.assertEqual(2, len(list(self.archive.glob("capture-*.json"))))

    def test_new_capture_of_unchanged_row_preserves_both_capture_clocks_but_one_finding(self):
        row = helpers.transaction()
        client = helpers.FixtureClient([helpers.capture(helpers.page_payload([row]))])
        service = self.service(client)
        first = self.run_service(service)
        second_time = helpers.CLOCK + timedelta(hours=1)
        client.pages = [helpers.capture(helpers.page_payload([row]), at=second_time - timedelta(seconds=2))]
        second = self.run_service(service, at=second_time)
        self.assertEqual((1, 1, 0), (first["new_source_observations"], second["new_source_observations"], second["new_findings"]))
        observations = self.audit(service)["observations"]
        self.assertEqual(2, len(observations))
        self.assertEqual(1, len({row["finding_id"] for row in observations}))
        self.assertEqual(2, len({row["response_completed_at"] for row in observations}))
        self.assertEqual(0, second["source_transactions"][0]["source_observation"]["different_rows_in_prior_captures"])

    def test_changed_same_transaction_id_retains_versions_and_audits_capture_order_without_source_revision_claim(self):
        original = helpers.transaction(last_modified_date="2026-10-03T05:06:07Z")
        client = helpers.FixtureClient([helpers.capture(helpers.page_payload([original]))])
        service = self.service(client)
        self.run_service(service)
        later = helpers.CLOCK + timedelta(hours=1)
        correction = helpers.transaction(last_modified_date="2026-10-02T05:06:07Z", federal_action_obligation=35)
        client.pages = [helpers.capture(helpers.page_payload([correction]), at=later - timedelta(seconds=2))]
        result = self.run_service(service, at=later)
        observation = result["source_transactions"][0]["source_observation"]
        self.assertEqual(("COMPLETE", 1, 1), (result["status"], result["new_findings"], observation["different_rows_in_prior_captures"]))
        saved = self.transactions()
        self.assertEqual(2, len(saved))
        self.assertEqual(1, len({row["source_object_id"] for row in saved}))
        self.assertEqual(2, len({row["content_hash"] for row in saved}))
        self.assertFalse(observation["source_revision_order_verified"])
        for row in saved:
            details = json.loads(row["details_json"])
            self.assertNotIn("obligation_delta_usd", details)
            self.assertFalse(details["revenue_inferred"])

    def test_older_capture_imported_after_newer_capture_is_not_declared_a_later_source_revision(self):
        earlier = helpers.capture(helpers.page_payload([helpers.transaction(federal_action_obligation=25)]))
        newer = helpers.capture(helpers.page_payload([helpers.transaction(federal_action_obligation=50)]),
                               at=helpers.CLOCK + timedelta(hours=1))
        client = helpers.FixtureClient([newer])
        service = self.service(client)
        self.run_service(service, at=helpers.CLOCK + timedelta(hours=2))
        client.pages = [earlier]
        result = self.run_service(service, at=helpers.CLOCK + timedelta(hours=3))
        self.assertEqual(0, result["source_transactions"][0]["source_observation"]["different_rows_in_prior_captures"])
        self.assertFalse(self.audit(service)["capture_order_is_publisher_revision_order"])

    def test_live_then_replay_reuses_source_observation_and_keeps_initial_provenance(self):
        captured = helpers.capture(helpers.page_payload([helpers.transaction()]), mode="LIVE")
        service = self.service(helpers.FixtureClient([captured]))
        service.client.capture_mode = "LIVE"
        with patch("market_checker_app.services.award_transaction_history_service._ingestion_clock", return_value=helpers.CLOCK):
            live = self.run_service(service)
        replay = self.service(ArchivedTransactionsClient([Path(live["capture_manifests"][0])]))
        with patch("market_checker_app.services.award_transaction_history_service.urlopen") as opener, \
                patch("market_checker_app.services.award_transaction_history_service._ingestion_clock", return_value=helpers.CLOCK + timedelta(hours=1)):
            repeated = self.run_service(replay, at=helpers.CLOCK + timedelta(hours=1))
        opener.assert_not_called()
        self.assertEqual(("ARCHIVE_REPLAY", 0, 0),
                         (repeated["acquisition_mode"], repeated["new_source_observations"], repeated["attempted_requests"]))
        saved = self.audit(service)["observations"]
        self.assertEqual(1, len(saved))
        self.assertEqual("LIVE", saved[0]["audit"]["ingestion_mode"])
        self.assertEqual(live["source_transactions"][0]["source_observation"]["observation_id"],
                         repeated["source_transactions"][0]["source_observation"]["observation_id"])

    def test_old_archive_replay_cannot_backdate_actual_local_ingestion(self):
        captured = helpers.capture(helpers.page_payload([helpers.transaction()]), mode="LIVE")
        manifest = archive_transaction_capture(captured, self.archive / "original")
        service = self.service(ArchivedTransactionsClient([manifest]))
        today = helpers.CLOCK + timedelta(days=1)
        with patch("market_checker_app.services.award_transaction_history_service._ingestion_clock", return_value=today):
            result = self.run_service(service, at=today)
        self.assertEqual(("COMPLETE", 1), (result["status"], result["new_source_observations"]))
        self.assertEqual([], self.audit(service, at=helpers.CLOCK + timedelta(hours=1))["observations"])
        observations = self.audit(service, at=today + timedelta(minutes=1))["observations"]
        self.assertEqual(1, len(observations))
        first_ingested = observations[0]["first_ingested_at"]
        # A subsequent historical request must not rewrite the actual first import.
        with patch("market_checker_app.services.award_transaction_history_service._ingestion_clock", return_value=today):
            self.run_service(service, at=helpers.CLOCK + timedelta(hours=1))
        self.assertEqual([], self.audit(service, at=helpers.CLOCK + timedelta(hours=1))["observations"])
        self.assertEqual(first_ingested, self.audit(service, at=today + timedelta(minutes=1))["observations"][0]["first_ingested_at"])

    def test_initial_historical_replay_does_not_set_ingestion_clock_from_requested_as_of(self):
        captured = helpers.capture(helpers.page_payload([helpers.transaction()]), mode="LIVE")
        manifest = archive_transaction_capture(captured, self.archive / "original")
        service = self.service(ArchivedTransactionsClient([manifest]))
        actual_import = helpers.CLOCK + timedelta(days=1)
        historical_cutoff = helpers.CLOCK + timedelta(minutes=1)
        with patch("market_checker_app.services.award_transaction_history_service._ingestion_clock", return_value=actual_import):
            result = self.run_service(service, at=historical_cutoff)
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual([], self.audit(service, at=historical_cutoff + timedelta(minutes=1))["observations"])
        rows = self.audit(service, at=actual_import + timedelta(minutes=1))["observations"]
        self.assertEqual(1, len(rows))
        self.assertEqual(actual_import.isoformat(), rows[0]["first_ingested_at"])

    def test_bound_queries_signal_truncation_and_cannot_claim_complete_revision_history(self):
        rows = [helpers.transaction("FIRST"), helpers.transaction("SECOND")]
        service = self.service(helpers.FixtureClient([helpers.capture(helpers.page_payload(rows))]))
        self.run_service(service)
        result = self.audit(service, limit=1)
        self.assertEqual(1, len(result["observations"]))
        self.assertTrue(result["truncated"])
        self.assertFalse(result["historical_revisions_complete"])
        for limit in (0, 501, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.audit(service, limit=limit)

    def test_future_live_capture_is_archived_but_never_used_as_current_knowledge(self):
        captured = helpers.capture(helpers.page_payload([helpers.transaction()]), mode="LIVE",
                                   at=helpers.CLOCK + timedelta(hours=1))
        client = helpers.FixtureClient([captured])
        client.capture_mode = "LIVE"
        service = self.service(client)
        result = self.run_service(service)
        self.assertEqual(("ERROR", 0, 0), (result["status"], result["accepted_rows"], result["new_source_observations"]))
        self.assertEqual(1, len(result["capture_manifests"]))
        self.assertEqual([], self.transactions())
        self.assertEqual([], self.audit(service)["observations"])

    def test_real_time_capture_clock_includes_pre_budget_target_validation(self):
        from market_checker_app.services import award_transaction_history_service as implementation

        class WallClockFixtureClient:
            capture_mode = "LIVE"

            def transactions(client_self, award_id, *, page, limit, timeout):
                return helpers.capture(helpers.page_payload([helpers.transaction()]), mode="LIVE",
                                       at=datetime.now(timezone.utc))

        original = implementation._verified_target

        def delayed_validation(*args, **kwargs):
            time.sleep(0.03)
            return original(*args, **kwargs)

        service = self.service(WallClockFixtureClient())
        with patch.object(implementation, "_verified_target", side_effect=delayed_validation):
            result = service.run(subject_id="AAA", award_finding_id=self.finding)
        self.assertEqual(("COMPLETE", 1), (result["status"], result["new_source_observations"]))

    def test_capture_headers_are_preserved_without_promoting_http_dates_to_transaction_revision_clocks(self):
        captured = helpers.capture(helpers.page_payload([helpers.transaction()]))
        headers = {"Date": "Mon, 12 Oct 2026 11:59:58 GMT", "Last-Modified": "Sun, 11 Oct 2026 09:00:00 GMT", "ETag": '"publisher-page"'}
        captured = CapturedTransactionPage(captured.body, {**captured.metadata, "response_headers": headers})
        service = self.service(helpers.FixtureClient([captured]))
        self.run_service(service)
        saved = self.audit(service)["observations"][0]["audit"]
        self.assertEqual(headers, saved["capture_metadata"]["response_headers"])
        self.assertEqual("UNREPORTED", saved["modified_clock_audit"]["status"])
        self.assertFalse(saved["http_headers_are_transaction_revision_timestamps"])

    def test_different_header_metadata_with_same_body_and_capture_times_remains_immutable_observation(self):
        captured = helpers.capture(helpers.page_payload([helpers.transaction()]))
        client = helpers.FixtureClient([captured])
        service = self.service(client)
        self.run_service(service)
        changed = CapturedTransactionPage(captured.body, {**captured.metadata, "response_headers": {"ETag": '"changed"'}})
        client.pages = [changed]
        result = self.run_service(service, at=helpers.CLOCK + timedelta(hours=1))
        self.assertEqual((0, 1), (result["new_findings"], result["new_source_observations"]))
        observations = self.audit(service)["observations"]
        self.assertEqual(2, len(observations))
        headers = [item["audit"]["capture_metadata"]["response_headers"] for item in observations]
        self.assertIn({}, headers)
        self.assertIn({"ETag": '"changed"'}, headers)


class ActualTransactionObservationUpgradeTests(unittest.TestCase):
    def test_prior_actual_fourteen_finding_versions_gain_audit_without_rewriting_hashes_or_backdating_import(self):
        from tempfile import TemporaryDirectory
        from market_checker_app.storage.scout_store import ScoutStore

        root = Path(__file__).resolve().parents[1]
        acceptance = json.loads((root / "evidence/batch3_contracts_acceptance_20261012.json").read_bytes())
        directory = root / "evidence/batch3_contracts_live_20261012"
        manifests = [p for p in directory.glob("capture-*.json") if json.loads(p.read_bytes())["capture_mode"] == "LIVE"]
        self.assertEqual(1, len(manifests))
        metadata = json.loads(manifests[0].read_bytes())
        self.assertEqual("2e74f5f6960faf192b7f1c7256c9b174453bf4ab63d85e935b2ff3debed75447",
                         hashlib.sha256((directory / metadata["body_file"]).read_bytes()).hexdigest())
        identity = next(row for row in load_verified_identities() if row["uei"] == "UTJWTSLMFNG4")
        case = json.loads((root / "evidence/usaspending_live_20261001.json").read_bytes())["cases"]["usaspending_positive_contract_uei"]
        original = datetime.fromisoformat(case["observed_at"])
        award = next(row for row in case["payload"]["results"] if row["generated_internal_id"] == metadata["request"]["award_id"])
        details = _award_details(award, identity, clock=original, state={"query_end": original.date().isoformat()})
        today = datetime.now(timezone.utc)
        with TemporaryDirectory() as temporary:
            store = ScoutStore(Path(temporary) / "scout.db")
            target, _ = store.record_finding(
                source="usaspending", subject_id="LMT", source_object_id=identity["uei"] + ":" + award["generated_internal_id"],
                content_hash=hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest(),
                title="Previously archived real award", source_url=case["source_url"], locator="archived-award",
                published_at=original, available_at=original, observed_at=original, details=details)
            for item in acceptance["transaction_findings"]:
                old = {key: item[key] for key in ("source", "subject_id", "source_object_id", "content_hash", "title",
                                                  "source_url", "locator", "details", "verification_status")}
                found, new = store.record_finding(
                    **old, published_at=datetime.fromisoformat(item["published_at"]),
                    available_at=datetime.fromisoformat(item["available_at"]),
                    observed_at=datetime.fromisoformat(item["first_observed_at"]))
                self.assertEqual(item["finding_id"], found)
                self.assertTrue(new)
            service = AwardTransactionHistoryService(store, archive_directory=Path(temporary) / "captures",
                                                     client=ArchivedTransactionsClient(manifests), identities=[identity], max_pages=1)
            with patch("market_checker_app.services.award_transaction_history_service.urlopen") as opener:
                first = service.run(subject_id="LMT", award_finding_id=target, as_of=today)
                repeat = service.run(subject_id="LMT", award_finding_id=target, as_of=today + timedelta(seconds=1))
            opener.assert_not_called()
            self.assertEqual(("COMPLETE", 14, 0, 0),
                             (first["status"], first["new_source_observations"], first["new_findings"], repeat["new_source_observations"]))
            past = service.transaction_store.observations_for_award(
                subject_id="LMT", generated_award_id=award["generated_internal_id"], as_of=today - timedelta(days=1))
            self.assertEqual([], past["observations"])
            present = service.transaction_store.observations_for_award(
                subject_id="LMT", generated_award_id=award["generated_internal_id"], as_of=today + timedelta(minutes=1))
            self.assertEqual(14, len(present["observations"]))
            expected = {row["finding_id"]: row for row in acceptance["transaction_findings"]}
            self.assertEqual(set(expected), {row["finding_id"] for row in present["observations"]})
            for item in present["observations"]:
                self.assertEqual("UNREPORTED", item["audit"]["modified_clock_audit"]["status"])
                self.assertEqual(metadata["response_completed_at"], item["audit"]["capture_metadata"]["response_completed_at"])
                self.assertEqual("ARCHIVE_REPLAY", item["audit"]["ingestion_mode"])
                saved = store.finding_by_id(item["finding_id"], subject_id="LMT")
                self.assertEqual(expected[item["finding_id"]]["content_hash"], saved["content_hash"])
                self.assertEqual(expected[item["finding_id"]]["details"], json.loads(saved["details_json"]))
            self.assertFalse(first["modification_history_complete"])
            self.assertFalse(present["historical_revisions_complete"])


if __name__ == "__main__":
    unittest.main()
