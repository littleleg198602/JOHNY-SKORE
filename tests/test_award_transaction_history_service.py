"""Collector correctness tests; synthetic cases are not live acceptance.

The separately named archived-source test replays the committed actual API
capture. It also remains ARCHIVE_REPLAY, not a new live source observation.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from market_checker_app.services.award_transaction_history_service import (
    CAPTURE_VERSION, SOURCE_URL, ArchivedTransactionsClient,
    AwardTransactionHistoryService, CapturedTransactionPage,
    UsaSpendingTransactionsClient, _TransactionRedirectHandler,
    archive_transaction_capture,
)
from market_checker_app.services.usaspending_scout_service import _award_details, load_verified_identities
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import UsaSpendingCheckpointStore


CLOCK = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
AWARD = "CONT_AWD_TEST_9700_PARENT_9700"
IDENTITY = {"ticker": "AAA", "uei": "ABCDEFGHIJKL", "recipient_name": "AAA INC",
            "uei_evidence_url": "https://www.usaspending.gov/award/example",
            "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/example",
            "effective_from": "2020-01-01", "effective_to": None,
            "known_at": "2026-09-01T00:00:00+00:00"}


def transaction(identifier="FIRST", **updates):
    return {"id": "CONT_TX_" + identifier, "type": "C", "type_description": "DELIVERY ORDER",
            "action_date": "2026-09-01", "action_type": "C", "action_type_description": "FUNDING ONLY ACTION",
            "modification_number": "P00001", "description": "Synthetic collector test",
            "federal_action_obligation": -12.5, "face_value_loan_guarantee": 0,
            "original_loan_subsidy_cost": None, **updates}


def page_payload(rows=(), *, page=1, next_page=False):
    return {"results": list(rows), "page_metadata": {
        "page": page, "next": page + 1 if next_page else None, "previous": page - 1 if page > 1 else None,
        "hasNext": next_page, "hasPrevious": page > 1}}


def capture(payload, *, page=1, mode="FIXTURE", award=AWARD, at=CLOCK - timedelta(seconds=2), limit=100):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return CapturedTransactionPage(body, {
        "schema_version": CAPTURE_VERSION, "capture_mode": mode, "source_url": SOURCE_URL,
        "source_url_final": SOURCE_URL, "method": "POST",
        "request": {"award_id": award, "page": page, "limit": limit, "sort": "action_date", "order": "asc"},
        "request_started_at": (at - timedelta(seconds=1)).isoformat(), "response_completed_at": at.isoformat(),
        "http_status": 200, "response_headers": {}, "complete_response": True,
        "response_bytes": len(body), "response_sha256": hashlib.sha256(body).hexdigest(),
    })


class FixtureClient:
    capture_mode = "FIXTURE"

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def transactions(self, award_id, *, page, limit, timeout):
        self.calls.append((award_id, page, limit, timeout))
        value = self.pages[page - 1]
        if isinstance(value, BaseException):
            raise value
        return value


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, length=None, url=SOURCE_URL, headers=None):
        super().__init__(body)
        self.status, self.length, self.url = status, length, url
        self.headers = headers or {"Content-Type": "application/json"}

    def geturl(self):
        return self.url


class AwardTransactionHistoryTests(unittest.TestCase):
    def test_explicit_application_hook_collects_one_target_and_persists_separate_receipt(self):
        from market_checker_app.scout_runner import run_award_transactions
        client = FixtureClient([capture(page_payload([transaction()]))])
        with patch("market_checker_app.scout_runner.load_verified_identities", return_value=[IDENTITY]), \
             patch("market_checker_app.services.award_transaction_history_service.UsaSpendingTransactionsClient",
                   return_value=client):
            result = run_award_transactions(db_path=self.store.db_path, subject_id="AAA",
                                            award_finding_id=self.finding)
        self.assertEqual(("COMPLETE", 1, "SAVED"),
                         (result["status"], result["accepted_rows"], result["source_archive_status"]))
        self.assertEqual(1, len(client.calls))
        saved = ScoutStore(self.store.db_path).latest_source_runs()["usaspending_transactions"]
        self.assertEqual("FIXTURE", saved["acquisition_mode"])
        self.assertFalse(saved["publisher_publication_time_verified"])
        self.assertFalse(saved["issuer_coverage_complete"])
        self.assertNotIn("source_transactions", saved)
        self.assertNotIn("capture_manifests", saved)
        rows = self.store.latest_findings(["AAA"], as_of=datetime.now(timezone.utc),
                                         source="usaspending", stage="award_transaction")
        self.assertEqual(-12.5, rows[0]["reported_transaction_obligation_usd"])

    def test_application_hook_manifest_failure_is_persisted_without_configured_values(self):
        from market_checker_app.scout_runner import run_award_transactions
        with patch("market_checker_app.scout_runner.load_verified_identities",
                   side_effect=OSError("synthetic-secret must not enter diagnostics")):
            result = run_award_transactions(db_path=self.store.db_path, subject_id="AAA",
                                            award_finding_id=self.finding)
        self.assertEqual(("ERROR", "OSError", "NOT_CREATED"),
                         (result["status"], result["error"], result["source_archive_status"]))
        saved = ScoutStore(self.store.db_path).latest_source_runs()["usaspending_transactions"]
        self.assertNotIn("synthetic-secret", json.dumps(saved))

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = ScoutStore(self.directory / "scout.db")
        self.archive = self.directory / "captures"
        self.finding = self.seed()

    def seed(self, *, identity=IDENTITY, award=AWARD, at=CLOCK - timedelta(days=1), **overrides):
        row = {"generated_internal_id": award, "Award ID": "TEST", "Recipient UEI": identity["uei"],
               "Recipient Name": identity["recipient_name"], "Start Date": "2026-08-01", "Award Amount": 100000,
               "Total Outlays": 99, "Last Modified Date": "2026-10-01"}
        details = _award_details(row, identity, clock=at, state={"query_end": at.date().isoformat()})
        fields = {"source": "usaspending", "subject_id": identity["ticker"], "source_object_id": identity["uei"] + ":" + award,
                  "content_hash": hashlib.sha256(json.dumps({"details": details, "at": at.isoformat(),
                                                              "overrides": overrides}, sort_keys=True, default=str).encode()).hexdigest(),
                  "title": "Synthetic stored prime award",
                  "source_url": "https://api.usaspending.gov/api/v2/search/spending_by_award/", "locator": "seed",
                  "published_at": at, "available_at": at, "observed_at": at, "details": details,
                  "verification_status": "SOURCE_VERIFIED", **overrides}
        return self.store.record_finding(**fields)[0]

    def service(self, client, **options):
        return AwardTransactionHistoryService(self.store, archive_directory=self.archive,
                                             identities=[IDENTITY], client=client, **options)

    def run_service(self, service, *, at=CLOCK, finding=None):
        return service.run(subject_id="AAA", award_finding_id=finding or self.finding, as_of=at)

    def transactions(self):
        return [item for item in self.store.findings_as_of("AAA", as_of=datetime(2030, 1, 1, tzinfo=timezone.utc))
                if json.loads(item["details_json"]).get("stage") == "award_transaction"]

    def test_source_rows_preserve_ids_negative_null_modified_fields_and_actual_capture_time(self):
        first = transaction(last_modified_date="2026-10-03T05:06:07Z")
        second = transaction("SECOND", federal_action_obligation=None, modification_number=None)
        client = FixtureClient([capture(page_payload([first, second]))])
        result = self.run_service(self.service(client))
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual((2, 2, 0), (result["accepted_rows"], result["new_findings"], result["attempted_requests"]))
        self.assertEqual("FIXTURE", result["acquisition_mode"])
        stored = {json.loads(row["details_json"])["transaction_id"]: row for row in self.transactions()}
        for original in (first, second):
            row = stored[original["id"]]
            details = json.loads(row["details_json"])
            self.assertEqual(original, details["source_transaction"])
            self.assertEqual((CLOCK - timedelta(seconds=2)).isoformat(), row["published_at"])
            self.assertEqual("UNVERIFIED", row["verification_status"])
            self.assertFalse(details["action_date_is_publication_time"])
            self.assertFalse(details["source_revision_order_verified"])
            self.assertFalse(details["revenue_inferred"])
            self.assertFalse(details["modification_history_complete"])
        self.assertEqual({"last_modified_date": first["last_modified_date"]},
                         json.loads(stored[first["id"]]["details_json"])["source_modified_fields"])
        self.assertFalse(any(json.loads(row["details_json"]).get("stage") == "award_transaction"
                             for row in self.store.findings_as_of("AAA", as_of=CLOCK - timedelta(seconds=3))))

    def test_repeat_deduplicates_immutable_rows_and_response_bytes_not_observation_manifests(self):
        client = FixtureClient([capture(page_payload([transaction()]))])
        service = self.service(client)
        first = self.run_service(service)
        second = self.run_service(service, at=CLOCK + timedelta(hours=1))
        self.assertEqual((1, 0), (first["new_findings"], second["new_findings"]))
        self.assertEqual(1, len(self.transactions()))
        self.assertEqual(1, len(list(self.archive.glob("body-*.response"))))
        self.assertEqual(2, len(list(self.archive.glob("capture-*.json"))))
        row = self.transactions()[0]
        self.assertLess(row["first_observed_at"], row["retrieved_at"])

    def test_changed_source_row_keeps_both_versions_without_manufacturing_a_transaction_delta(self):
        client = FixtureClient([capture(page_payload([transaction()]))])
        service = self.service(client)
        self.run_service(service)
        client.pages = [capture(page_payload([transaction(federal_action_obligation=-20)]))]
        self.assertEqual(1, self.run_service(service, at=CLOCK + timedelta(hours=1))["new_findings"])
        rows = self.transactions()
        self.assertEqual(2, len(rows))
        self.assertEqual(1, len({row["source_object_id"] for row in rows}))
        self.assertEqual(2, len({row["content_hash"] for row in rows}))
        for row in rows:
            details = json.loads(row["details_json"])
            self.assertNotIn("obligation_delta_usd", details)
            self.assertFalse(details["snapshot_totals_differenced"])

    def test_reordered_date_ties_change_archived_page_bytes_without_duplicate_row_versions(self):
        first, second = transaction("Z"), transaction("A")
        client = FixtureClient([capture(page_payload([first, second]))])
        service = self.service(client)
        self.run_service(service)
        client.pages = [capture(page_payload([second, first]))]
        result = self.run_service(service, at=CLOCK + timedelta(hours=1))
        self.assertEqual(("COMPLETE", 0), (result["status"], result["new_findings"]))
        self.assertEqual([second["id"], first["id"]], [row["transaction_id"] for row in result["source_transactions"]])
        self.assertEqual(2, len(self.transactions()))
        self.assertEqual(2, len(list(self.archive.glob("body-*.response"))))

    def test_changed_cumulative_award_snapshot_is_never_an_input_transaction(self):
        client = FixtureClient([capture(page_payload([transaction()]))])
        service = self.service(client)
        first = self.run_service(service)
        target = self.store.finding_by_id(self.finding, subject_id="AAA")
        details = json.loads(target["details_json"])
        details["reported_award_amount_usd"] = 50000000
        details["reported_total_outlays_usd"] = 20000000
        changed_target = self.seed(details=details)
        second = self.run_service(service, finding=changed_target, at=CLOCK + timedelta(hours=1))
        self.assertEqual((1, 0), (first["new_findings"], second["new_findings"]))
        self.assertEqual(-12.5, second["source_transactions"][0]["federal_action_obligation"])
        self.assertEqual(1, len(self.transactions()))
        self.assertFalse(second["snapshot_totals_differenced"])

    def test_two_pages_keep_publisher_position_and_do_not_invent_order_among_date_ties(self):
        client = FixtureClient([capture(page_payload([transaction("Z")], next_page=True)),
                                capture(page_payload([transaction("A")], page=2), page=2)])
        result = self.run_service(self.service(client))
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual(["CONT_TX_Z", "CONT_TX_A"], [row["transaction_id"] for row in result["source_transactions"]])
        self.assertEqual([(1, 0), (2, 0)], [(row["page"], row["row_index"]) for row in result["source_transactions"]])
        self.assertTrue(result["source_order_consistent"])
        self.assertTrue(result["traversal_complete"])
        self.assertFalse(result["pagination_snapshot_guaranteed"])
        self.assertTrue(all(not json.loads(row["details_json"])["within_action_date_order_verified"] for row in self.transactions()))

    def test_page_and_request_caps_are_partial_and_never_start_excess_io(self):
        for options, reason in (({"max_pages": 1}, "PAGE_BUDGET"), ({"max_requests": 1}, "RUN_BUDGET")):
            with self.subTest(options=options):
                client = FixtureClient([capture(page_payload([transaction()], next_page=True))])
                result = self.run_service(self.service(client, **options))
                self.assertEqual("PARTIAL", result["status"])
                self.assertEqual(reason, result["stop_reason"])
                self.assertFalse(result["traversal_complete"])
                self.assertEqual(1, len(client.calls))

    def test_complete_empty_page_is_no_match_only_for_this_exact_award(self):
        result = self.run_service(self.service(FixtureClient([capture(page_payload())])))
        self.assertEqual("NO_MATCH", result["status"])
        self.assertTrue(result["traversal_complete"])
        self.assertFalse(result["issuer_coverage_complete"])
        self.assertFalse(result["modification_history_complete"])
        self.assertEqual(AWARD, result["generated_internal_id"])

    def test_rejected_terminal_rows_are_partial_including_all_rejected_results(self):
        for row in (transaction(action_date="2026-10-13"), transaction(federal_action_obligation=True),
                    transaction(id="ASST_TX_OTHER"), transaction(action_date="2026-02-30")):
            with self.subTest(row=row):
                result = self.run_service(self.service(FixtureClient([capture(page_payload([row]))])))
                self.assertEqual(("PARTIAL", 0, 1), (result["status"], result["accepted_rows"], result["rejected_rows"]))

    def test_pre_relationship_transaction_is_preserved_without_issuer_attribution(self):
        row = transaction(action_date="2019-01-01")
        result = self.run_service(self.service(FixtureClient([capture(page_payload([row]))])))
        self.assertEqual((1, 0), (result["accepted_rows"], result["attributable_rows"]))
        self.assertFalse(json.loads(self.transactions()[0]["details_json"])["issuer_attribution_allowed"])

    def test_source_order_violation_is_partial_and_keeps_raw_response_order(self):
        rows = [transaction("LATE", action_date="2026-10-01"), transaction("EARLY")]
        result = self.run_service(self.service(FixtureClient([capture(page_payload(rows))])))
        self.assertEqual("PARTIAL", result["status"])
        self.assertFalse(result["source_order_consistent"])
        self.assertEqual([row["id"] for row in rows], [row["transaction_id"] for row in result["source_transactions"]])

    def test_duplicate_and_conflicting_ids_across_pages_make_traversal_partial(self):
        for second, duplicate, conflict in ((transaction(), 1, 0), (transaction(federal_action_obligation=123), 0, 1)):
            with self.subTest(second=second):
                client = FixtureClient([capture(page_payload([transaction()], next_page=True)),
                                        capture(page_payload([second], page=2), page=2)])
                result = self.run_service(self.service(client))
                self.assertEqual("PARTIAL", result["status"])
                self.assertEqual((duplicate, conflict), (result["duplicate_rows"], result["conflicting_transaction_ids"]))

    def test_malformed_page_cannot_be_a_negative_or_complete_result(self):
        for update in ({"next": 9}, {"page": True}, {"hasNext": "false"}, {"hasPrevious": 0}):
            with self.subTest(update=update):
                payload = page_payload()
                payload["page_metadata"].update(update)
                result = self.run_service(self.service(FixtureClient([capture(payload)])))
                self.assertEqual("ERROR", result["status"])
                self.assertFalse(result["traversal_complete"])
                self.assertEqual(0, result["accepted_rows"])
                self.assertEqual(1, len(result["capture_manifests"]))

    def test_missing_unverified_and_different_recipient_targets_never_start_io(self):
        wrong = _award_details({"generated_internal_id": AWARD, "Recipient UEI": IDENTITY["uei"],
                                "Recipient Name": IDENTITY["recipient_name"], "Start Date": "2026-08-01"},
                               IDENTITY, clock=CLOCK, state={"query_end": CLOCK.date().isoformat()})
        wrong["recipient_name"] = "OTHER ENTITY"
        invalid = ["missing", self.seed(verification_status="UNVERIFIED"), self.seed(details=wrong),
                   self.seed(at=CLOCK + timedelta(days=1))]
        for finding in invalid:
            with self.subTest(finding=finding):
                client = FixtureClient([])
                result = self.run_service(self.service(client), finding=finding)
                self.assertEqual("INVALID_TARGET", result["status"])
                self.assertEqual([], client.calls)
                self.assertEqual(0, result["io_attempts"])

    def test_bad_hash_wrong_award_and_future_capture_fail_without_attributing_rows(self):
        original = capture(page_payload([transaction()]))
        altered = CapturedTransactionPage(original.body, {**original.metadata, "response_sha256": "0" * 64})
        wrong = capture(page_payload([transaction()]), award="CONT_AWD_OTHER_9700_PARENT_9700")
        future = capture(page_payload([transaction()]), at=CLOCK + timedelta(days=1))
        for value in (altered, wrong, future):
            with self.subTest(meta=value.metadata):
                result = self.run_service(self.service(FixtureClient([value])))
                self.assertEqual("ERROR", result["status"])
                self.assertEqual(0, result["accepted_rows"])

    def test_live_provider_lease_and_shared_daily_quota_prevent_network(self):
        service = self.service(UsaSpendingTransactionsClient(), daily_request_limit=1)
        token = self.store.claim_provider("usaspending", as_of=CLOCK, seconds=60)
        with patch("market_checker_app.services.award_transaction_history_service.urlopen") as opener:
            result = self.run_service(service)
        self.assertEqual("BUSY", result["status"])
        opener.assert_not_called()
        ledger = UsaSpendingCheckpointStore(self.store)
        reserved = ledger.reserve("awards", "previous", "AAA", page=1, as_of=CLOCK,
                                  daily_limit=1, lease_token=token)
        self.assertEqual("RESERVED", reserved["status"])
        self.store.release_provider("usaspending", token)
        with patch("market_checker_app.services.award_transaction_history_service.urlopen") as opener:
            result = self.run_service(service)
        self.assertEqual("WAIT_QUOTA", result["stop_reason"])
        self.assertEqual(0, result["attempted_requests"])
        self.assertEqual(1, result["daily_requests_used"])
        opener.assert_not_called()
        with self.store._connect() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scout_usaspending_checkpoints").fetchone()[0])

    def test_blocked_live_response_is_archived_charged_and_respects_cooldown(self):
        service = self.service(UsaSpendingTransactionsClient())
        error = HTTPError(SOURCE_URL, 403, "Forbidden", {"Content-Type": "text/plain"}, io.BytesIO(b"Forbidden"))
        with patch("market_checker_app.services.award_transaction_history_service.urlopen", side_effect=error) as opener:
            first = self.run_service(service)
            second = self.run_service(service)
        self.assertEqual("ERROR", first["status"])
        self.assertEqual("ACCESS_BLOCKED", first["stop_reason"])
        self.assertEqual(1, first["attempted_requests"])
        self.assertEqual("ACCESS_BLOCKED", second["status"])
        self.assertEqual(1, opener.call_count)
        manifest = json.loads(Path(first["capture_manifests"][0]).read_bytes())
        self.assertEqual(b"Forbidden", (self.archive / manifest["body_file"]).read_bytes())
        self.assertEqual(403, manifest["http_status"])
        self.assertEqual([], self.transactions())

    def test_expired_budget_never_starts_client_io(self):
        client = FixtureClient([])
        with patch("market_checker_app.services.award_transaction_history_service._ReplayBudget.remaining", return_value=0):
            result = self.run_service(self.service(client))
        self.assertEqual("RUN_BUDGET", result["stop_reason"])
        self.assertEqual([], client.calls)

    def test_configuration_is_bounded_and_requires_acquisition_disclosure(self):
        for options in ({"max_pages": True}, {"max_pages": 6}, {"page_size": 101}, {"max_requests": 0},
                        {"max_run_seconds": 61}, {"max_run_seconds": float("inf")}, {"daily_request_limit": 121}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.service(FixtureClient([]), **options)
        client = FixtureClient([])
        client.capture_mode = "UNKNOWN"
        with self.assertRaises(ValueError):
            self.service(client)


class TransactionCaptureTests(unittest.TestCase):
    def test_http_preserves_exact_bytes_request_and_original_response_times(self):
        raw = b'{ "results" : [] , "page_metadata" : {"hasNext":false} }\n'
        with patch("market_checker_app.services.award_transaction_history_service.urlopen", return_value=Response(raw)) as opener:
            result = UsaSpendingTransactionsClient().transactions(AWARD, page=1, limit=100, timeout=3)
        self.assertEqual(raw, result.body)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result.metadata["response_sha256"])
        self.assertEqual("LIVE", result.metadata["capture_mode"])
        self.assertTrue(result.metadata["complete_response"])
        request = opener.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertEqual({"award_id": AWARD, "page": 1, "limit": 100, "sort": "action_date", "order": "asc"}, json.loads(request.data))
        self.assertLessEqual(result.metadata["request_started_at"], result.metadata["response_completed_at"])

    def test_truncated_response_keeps_bytes_and_cannot_be_success(self):
        raw = b'{"results": []}'
        with patch("market_checker_app.services.award_transaction_history_service.urlopen", return_value=Response(raw, length=100)):
            result = UsaSpendingTransactionsClient().transactions(AWARD, page=1, limit=100)
        self.assertEqual(raw, result.body)
        self.assertFalse(result.metadata["complete_response"])
        self.assertEqual("IncompleteRead", result.metadata["error_type"])

    def test_oversized_and_slow_streams_preserve_bounded_failed_capture(self):
        with patch("market_checker_app.services.award_transaction_history_service.urlopen", return_value=Response(b"x" * 30)):
            result = UsaSpendingTransactionsClient(max_response_bytes=10).transactions(AWARD, page=1, limit=100)
        self.assertEqual(b"x" * 10, result.body)
        self.assertFalse(result.metadata["complete_response"])
        with patch("market_checker_app.services.award_transaction_history_service.urlopen", return_value=Response(b"{}")), \
             patch("market_checker_app.services.award_transaction_history_service.time.monotonic", side_effect=[0, 0, 10]):
            result = UsaSpendingTransactionsClient().transactions(AWARD, page=1, limit=100, timeout=3)
        self.assertEqual(b"{}", result.body)
        self.assertEqual("TimeoutError", result.metadata["error_type"])

    def test_redirects_cannot_leave_the_documented_transaction_endpoint(self):
        for url in ("https://example.com/", "https://api.usaspending.gov/api/v2/recipient/", "http://api.usaspending.gov/api/v2/transactions/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _TransactionRedirectHandler().redirect_request(Request(SOURCE_URL), None, 302, "Found", {}, url)

    def test_replay_rejects_fixture_origin_body_corruption_and_path_traversal(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            fixture = archive_transaction_capture(capture(page_payload()), path)
            with self.assertRaises(ValueError):
                ArchivedTransactionsClient([fixture]).transactions(AWARD, page=1, limit=100)
            live_mock = archive_transaction_capture(capture(page_payload(), mode="LIVE"), path)
            meta = json.loads(live_mock.read_bytes())
            (path / meta["body_file"]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                ArchivedTransactionsClient([live_mock]).transactions(AWARD, page=1, limit=100)
            meta["body_file"] = "../escape.response"
            live_mock.write_text(json.dumps(meta))
            with self.assertRaises(ValueError):
                ArchivedTransactionsClient([live_mock]).transactions(AWARD, page=1, limit=100)

    def test_archive_detects_corrupt_existing_source_object_without_overwriting_it(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            value = capture(page_payload())
            manifest = archive_transaction_capture(value, path)
            metadata = json.loads(manifest.read_bytes())
            body = path / metadata["body_file"]
            body.write_bytes(b"corruption")
            with self.assertRaises(ValueError):
                archive_transaction_capture(value, path)
            self.assertEqual(b"corruption", body.read_bytes())
            self.assertEqual([], list(path.glob("capture-write-*")))


class ActualTransactionArchiveReplayTests(unittest.TestCase):
    def test_actual_archived_14_sikorsky_rows_replay_without_network_or_duplicate_source_objects(self):
        root = Path(__file__).resolve().parents[1]
        directory = root / "evidence" / "batch3_contracts_live_20261012"
        manifests = [path for path in directory.glob("capture-*.json")
                     if json.loads(path.read_bytes()).get("capture_mode") == "LIVE"]
        self.assertEqual(1, len(manifests), "The committed genuine source capture is required")
        metadata = json.loads(manifests[0].read_bytes())
        body = (directory / metadata["body_file"]).read_bytes()
        self.assertEqual("2e74f5f6960faf192b7f1c7256c9b174453bf4ab63d85e935b2ff3debed75447", hashlib.sha256(body).hexdigest())
        self.assertEqual(6220, len(body))
        original_rows = json.loads(body)["results"]
        self.assertEqual(14, len(original_rows))
        capture_time = datetime.fromisoformat(metadata["response_completed_at"])
        identity = next(row for row in load_verified_identities() if row["uei"] == "UTJWTSLMFNG4")
        evidence = json.loads((root / "evidence" / "usaspending_live_20261001.json").read_bytes())
        case = evidence["cases"]["usaspending_positive_contract_uei"]
        award = next(row for row in case["payload"]["results"] if row["generated_internal_id"] == metadata["request"]["award_id"])
        originally_observed = datetime.fromisoformat(case["observed_at"])
        details = _award_details(award, identity, clock=originally_observed, state={"query_end": originally_observed.date().isoformat()})
        with TemporaryDirectory() as temporary:
            store = ScoutStore(Path(temporary) / "scout.db")
            finding = store.record_finding(source="usaspending", subject_id="LMT", source_object_id=identity["uei"] + ":" + award["generated_internal_id"],
                                           content_hash=hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest(),
                                           title="Actual previously archived verified award", source_url=case["source_url"], locator="archived-award",
                                           published_at=originally_observed, available_at=originally_observed, observed_at=originally_observed, details=details)[0]
            service = AwardTransactionHistoryService(store, archive_directory=Path(temporary) / "captures",
                                                     client=ArchivedTransactionsClient(manifests), identities=[identity], max_pages=1)
            with patch("market_checker_app.services.award_transaction_history_service.urlopen") as opener:
                first = service.run(subject_id="LMT", award_finding_id=finding, as_of=capture_time + timedelta(seconds=1))
                second = service.run(subject_id="LMT", award_finding_id=finding, as_of=capture_time + timedelta(seconds=2))
            opener.assert_not_called()
            self.assertEqual(("COMPLETE", 14, 0), (first["status"], first["new_findings"], second["new_findings"]))
            self.assertEqual("ARCHIVE_REPLAY", first["acquisition_mode"])
            self.assertEqual(0, first["attempted_requests"])
            stored = [row for row in store.findings_as_of("LMT", as_of=capture_time + timedelta(minutes=1))
                      if json.loads(row["details_json"])["stage"] == "award_transaction"]
            self.assertEqual(14, len(stored))
            self.assertEqual(14, len({row["source_object_id"] for row in stored}))
            by_id = {row["id"]: row for row in original_rows}
            for row in stored:
                item = json.loads(row["details_json"])
                self.assertEqual(by_id[item["transaction_id"]], item["source_transaction"])
                self.assertEqual({}, item["source_modified_fields"])
                self.assertEqual(capture_time.isoformat(), row["published_at"])
                self.assertFalse(item["revenue_inferred"])
                self.assertFalse(item["publisher_publication_time_verified"])
            with store._connect() as connection:
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='scout_usaspending_requests'").fetchone())


if __name__ == "__main__":
    unittest.main()
