"""Synthetic mechanism checks; these fixtures are not public source evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services import usaspending_prime_archive_recovery as recovery
from market_checker_app.services.award_transaction_history_service import _verified_target
from market_checker_app.services.usaspending_scout_service import API_URL, FIELDS, _award_details
from market_checker_app.storage.scout_store import ScoutStore


CAPTURED = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
INGESTED = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
IDENTITY = {"ticker": "LMT", "uei": "ABCDEFGHIJKL", "recipient_name": "SYNTHETIC TEST RECIPIENT",
            "uei_evidence_url": "https://www.usaspending.gov/award/test",
            "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/test",
            "continuity_evidence_url": None, "effective_from": "2020-01-01", "effective_to": None,
            "known_at": "2026-09-01T00:00:00+00:00"}


def _row(identifier="TEST0001", **changes):
    return {"internal_id": 1, "Award ID": identifier,
            "generated_internal_id": f"CONT_AWD_{identifier}_9700_TESTBASE_9700",
            "Recipient UEI": IDENTITY["uei"], "Recipient Name": IDENTITY["recipient_name"],
            "Start Date": "2026-09-15", "Last Modified Date": "2026-09-30 12:03:00",
            "Award Amount": 100.0, "Total Outlays": 0.0,
            "Awarding Agency": "Synthetic agency", "Contract Award Type": "DELIVERY ORDER", **changes}


class PrimeArchiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = ScoutStore(self.directory / "scout.db")
        self.clock = patch.object(recovery, "_physical_clock", return_value=INGESTED)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def _page(self, rows=None, *, page=1, has_next=False, mode="LIVE", **changes):
        # LIVE-shaped metadata exercises import mechanics only. Its generated
        # bytes remain temporary fixtures, never evidence or acceptance captures.
        rows = [_row()] if rows is None else rows
        body = json.dumps({"spending_level": "awards", "limit": 100, "results": rows,
                           "page_metadata": {"page": page, "hasNext": has_next}}, allow_nan=False).encode()
        digest = hashlib.sha256(body).hexdigest()
        name = f"body-{digest}.response"
        (self.directory / name).write_bytes(body)
        started = CAPTURED + timedelta(seconds=(page - 1) * 3)
        meta = {"schema_version": recovery.CAPTURE_SCHEMA, "capture_mode": mode,
                "method": "POST", "source_url": API_URL, "source_url_final": API_URL,
                "http_status": 200, "complete_response": True, "response_bytes": len(body),
                "response_sha256": digest, "body_file": name,
                "request_started_at": started.isoformat(),
                "response_completed_at": (started + timedelta(seconds=1)).isoformat(),
                "request": {"filters": {"award_type_codes": ["A", "B", "C", "D"],
                                        "recipient_search_text": [IDENTITY["uei"]],
                                        "time_period": [{"start_date": "2026-01-01", "end_date": "2026-10-01"}]},
                            "fields": FIELDS, "limit": 100, "page": page,
                            "sort": "Last Modified Date", "order": "desc"}, **changes}
        path = self.directory / f"capture-{page}.json"
        path.write_text(json.dumps(meta, allow_nan=False), encoding="utf-8")
        return path

    def _rewrite(self, path, **changes):
        data = json.loads(path.read_bytes())
        data.update(changes)
        path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        return path

    def _pins(self, paths):
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def _recover(self, paths, **changes):
        kwargs = {"manifests": paths, "expected_manifest_sha256": self._pins(paths),
                  "subject_id": "LMT", "as_of": INGESTED, "identities": [deepcopy(IDENTITY)], **changes}
        return recovery.recover_prime_awards_from_archives(self.store, **kwargs)

    def _stored(self):
        with self.store._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM scout_findings ORDER BY finding_id")]

    def test_complete_synthetic_import_separates_capture_from_actual_ingestion(self):
        source = _row()
        result = self._recover([self._page([source])], as_of=CAPTURED + timedelta(seconds=2))
        self.assertEqual("RECOVERED", result["status"])
        self.assertEqual(1, result["new_findings"])
        self.assertEqual(INGESTED.isoformat(), result["recovery_recorded_at"])
        finding = self._stored()[0]
        self.assertEqual((CAPTURED + timedelta(seconds=1)).isoformat(), finding["available_at"])
        self.assertEqual(INGESTED.isoformat(), finding["first_observed_at"])
        self.assertEqual([], self.store.findings_as_of("LMT", as_of=CAPTURED + timedelta(seconds=2)))
        details = json.loads(finding["details_json"])
        provenance = details["archive_recovery"]
        self.assertEqual(source["Last Modified Date"], provenance["original_modified_date"])
        self.assertEqual("TIMEZONE_UNKNOWN", provenance["modified_clock_audit"]["status"])
        self.assertFalse(provenance["publisher_revision_time_verified"])
        core = _award_details(source, IDENTITY, clock=CAPTURED, state={"query_end": "2026-10-01"})
        expected = hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()
        self.assertEqual(expected, finding["content_hash"])
        target, _, _ = _verified_target(self.store, [IDENTITY], subject_id="LMT",
                                        finding_id=finding["finding_id"], clock=INGESTED)
        self.assertEqual(finding["finding_id"], target["finding_id"])
        with self.assertRaises(ValueError):
            _verified_target(self.store, [IDENTITY], subject_id="LMT", finding_id=finding["finding_id"],
                             clock=CAPTURED + timedelta(seconds=2))
        for key in ("current_live_collection", "historical_availability_reconstructed", "issuer_coverage_complete",
                    "revenue_inferred", "modification_history_complete", "scoring_applied", "quota_reconstructed"):
            self.assertFalse(result[key])
        self.assertEqual(0, result["http_attempts"])

    def test_complete_two_pages_and_exact_replay_preserve_every_existing_byte(self):
        pages = [self._page([_row()], has_next=True),
                 self._page([_row("TEST0002", internal_id=2)], page=2)]
        first = self._recover(list(reversed(pages)))
        self.assertEqual(2, first["new_findings"])
        before = self._stored()
        with patch.object(recovery, "_physical_clock", return_value=INGESTED + timedelta(days=1)):
            repeated = self._recover(pages)
        self.assertEqual("UNCHANGED", repeated["status"])
        self.assertEqual(0, repeated["new_findings"])
        self.assertEqual(before, self._stored())

    def test_changed_source_award_adds_version_without_erasing_original(self):
        self._recover([self._page()])
        old = self._stored()[0]
        changed = self._recover([self._page([_row(**{"Award Amount": 125.0})])])
        self.assertEqual(1, changed["new_findings"])
        self.assertEqual(2, len(self._stored()))
        self.assertIn(old, self._stored())

    def test_incomplete_terminal_gap_or_extra_pages_cannot_promote_targets(self):
        for case in ("incomplete", "gap", "extra"):
            paths = ([self._page(has_next=True)] if case == "incomplete" else
                     [self._page(page=2)] if case == "gap" else
                     [self._page(), self._page([_row("TEST0002")], page=2)])
            with self.subTest(case=case):
                self.assertEqual("REJECTED", self._recover(paths)["status"])
                self.assertEqual([], self._stored())

    def test_duplicate_or_changed_award_within_traversal_rejects_entire_import(self):
        for second in (_row(), _row(**{"Award Amount": 125.0})):
            result = self._recover([self._page([_row()], has_next=True), self._page([second], page=2)])
            self.assertEqual("DUPLICATE_OR_CONFLICTING_AWARD_SOURCE_ID", result["reason"])
            self.assertEqual([], self._stored())

    def test_scope_change_or_overlapping_clocks_rejects_before_database_write(self):
        p1, p2 = self._page(has_next=True), self._page([_row("TEST0002")], page=2)
        request = json.loads(p2.read_bytes())["request"]
        request["filters"]["time_period"][0]["start_date"] = "2026-02-01"
        self._rewrite(p2, request=request)
        self.assertEqual("PAGE_SEQUENCE_OR_QUERY_SCOPE_MISMATCH", self._recover([p1, p2])["reason"])
        self._page([_row("TEST0002")], page=2, request_started_at=CAPTURED.isoformat())
        self.assertEqual("OVERLAPPING_OR_REORDERED_CAPTURE_CLOCKS", self._recover([p1, p2])["reason"])
        self.assertEqual([], self._stored())

    def test_body_and_manifest_hashes_and_lengths_are_mandatory(self):
        path = self._page()
        pins = self._pins([path])
        self._rewrite(path, response_bytes=123)
        self.assertEqual("MANIFEST_HASH_MISMATCH", self._recover([path], expected_manifest_sha256=pins)["reason"])
        self.assertEqual("BODY_HASH_OR_LENGTH_MISMATCH", self._recover([path])["reason"])
        path = self._page()
        meta = json.loads(path.read_bytes())
        (path.parent / meta["body_file"]).write_bytes(b"{}")
        self.assertEqual("BODY_HASH_OR_LENGTH_MISMATCH", self._recover([path])["reason"])
        self.assertEqual([], self._stored())

    def test_fixture_replay_projection_wrong_endpoint_and_error_are_rejected(self):
        changes = [{"capture_mode": "FIXTURE"}, {"capture_mode": "ARCHIVE_REPLAY"},
                   {"schema_version": "award_transaction_capture_v1"},
                   {"source_url": "https://api.usaspending.gov/api/v2/transactions/"},
                   {"source_url_final": API_URL + "?access_token=unsafe"},
                   {"body_transformed": True}, {"complete_response": False}, {"http_status": 400},
                   {"content_encoding": "gzip"}]
        for update in changes:
            with self.subTest(update=update):
                self.assertEqual("REJECTED", self._recover([self._page(**update)])["status"])
        self.assertEqual([], self._stored())

    def test_withheld_body_is_rejected_without_opening_its_reference(self):
        path = self._page(body_withheld=True)
        with patch.object(recovery, "_read", wraps=recovery._read) as read:
            result = self._recover([path])
        self.assertEqual("WITHHELD_BODY_UNAVAILABLE", result["reason"])
        self.assertEqual([path], [call.args[0] for call in read.call_args_list])

    def test_access_value_anywhere_rejects_whole_original_without_echo_or_mutation(self):
        path = self._page([_row(access_token="synthetic_sensitive_value")])
        body_path = path.parent / json.loads(path.read_bytes())["body_file"]
        body_before = body_path.read_bytes()
        result = self._recover([path])
        self.assertEqual("UNSAFE_SOURCE_BODY", result["reason"])
        self.assertNotIn("synthetic_sensitive_value", json.dumps(result))
        self.assertEqual(body_before, body_path.read_bytes())
        self.assertEqual([], self._stored())

    def test_unsafe_identity_reference_or_access_values_cannot_enter_recovered_details(self):
        for identity in ({**IDENTITY, "uei_evidence_url": "https://www.usaspending.gov/award/test?key=hidden"},
                         {**IDENTITY, "client_secret": "synthetic_sensitive_value"}):
            result = self._recover([self._page()], identities=[identity])
            self.assertEqual("REJECTED", result["status"])
            self.assertNotIn("synthetic_sensitive_value", json.dumps(result))
            self.assertEqual([], self._stored())

    def test_bounded_read_rejects_oversized_manifest_and_body(self):
        path = self._page()
        path.write_bytes(b" " * (recovery.MAX_MANIFEST_BYTES + 1))
        self.assertEqual("ARCHIVE_BYTE_BOUND_EXCEEDED", self._recover([path])["reason"])
        path = self._page()
        meta = json.loads(path.read_bytes())
        (path.parent / meta["body_file"]).write_bytes(b" " * (recovery.MAX_RESPONSE_BYTES + 1))
        self.assertEqual("ARCHIVE_BYTE_BOUND_EXCEEDED", self._recover([path])["reason"])

    def test_duplicate_json_keys_and_nonfinite_numbers_cannot_pass_hash_verification(self):
        for body in (b'{"spending_level":"awards","results":[],"results":[]}', b'{"value":NaN}'):
            path = self._page()
            digest = hashlib.sha256(body).hexdigest()
            name = f"body-{digest}.response"
            (path.parent / name).write_bytes(body)
            self._rewrite(path, response_sha256=digest, response_bytes=len(body), body_file=name)
            result = self._recover([path])
            self.assertIn(result["reason"], {"DUPLICATE_JSON_KEY", "NONFINITE_JSON_VALUE"})

    def test_declared_source_length_encoding_and_optional_pagination_must_agree(self):
        for headers in ({"Content-Length": "1"}, {"Content-Length": "false"},
                        {"Content-Encoding": "gzip"}, {"X-API-Key": "synthetic_sensitive_value"}):
            with self.subTest(headers=headers):
                self.assertEqual("REJECTED", self._recover([self._page(response_headers=headers)])["status"])
        for update in ({"next": 2}, {"previous": 1}, {"hasPrevious": True}, {"last_record_unique_id": 2}):
            path = self._page()
            meta = json.loads(path.read_bytes())
            body_path = path.parent / meta["body_file"]
            payload = json.loads(body_path.read_bytes())
            payload["page_metadata"].update(update)
            body = json.dumps(payload).encode()
            digest = hashlib.sha256(body).hexdigest()
            name = f"body-{digest}.response"
            (path.parent / name).write_bytes(body)
            self._rewrite(path, body_file=name, response_sha256=digest, response_bytes=len(body))
            self.assertEqual("INVALID_PRIME_PAGINATION", self._recover([path])["reason"])
        self.assertEqual([], self._stored())

    def test_future_naive_or_identity_after_original_capture_cannot_be_backdated(self):
        for changes in ({"request_started_at": "2026-10-01T12:00:00"},
                        {"response_completed_at": "2026-10-15T12:00:00+00:00"}):
            self.assertEqual("REJECTED", self._recover([self._page(**changes)])["status"])
        for identity in ({**IDENTITY, "known_at": "2026-10-02T00:00:00+00:00"},
                         {**IDENTITY, "effective_from": "2026-02-01"},
                         {**IDENTITY, "effective_to": "2026-09-30"}):
            self.assertEqual("REJECTED", self._recover([self._page()], identities=[identity])["status"])
        self.assertEqual([], self._stored())

    def test_unsafe_rows_reject_batch_even_when_previous_row_was_valid(self):
        cases = [{"Recipient UEI": "ZZZZZZZZZZZZ"}, {"Recipient Name": "SIMILAR NAME"},
                 {"generated_internal_id": "CONT_TX_123"}, {"Award ID": "DIFFERENT"},
                 {"Start Date": "2019-12-31"}, {"Start Date": "2026-10-02"},
                 {"Award Amount": True}, {"Total Outlays": "0"}, {"internal_id": False},
                 {"Contract Award Type": ""}, {"Last Modified Date": "invalid"}]
        for changes in cases:
            with self.subTest(changes=changes):
                result = self._recover([self._page([_row(), _row("TEST0002", **changes)])])
                self.assertEqual("REJECTED", result["status"])
                self.assertEqual([], self._stored())

    def test_modified_clocks_remain_unknown_or_source_only_and_future_exact_time_rejects(self):
        for value, status in ((None, "UNKNOWN"), ("2026-09-30", "DATE_ONLY"),
                              ("2026-09-30 12:03:00", "TIMEZONE_UNKNOWN"),
                              ("2026-09-30T12:03:00Z", "SOURCE_TIMESTAMP")):
            result = self._recover([self._page([_row(**{"Last Modified Date": value})])])
            self.assertEqual("RECOVERED", result["status"])
            finding = self.store.finding_by_id(result["finding_ids"][0], subject_id="LMT")
            audit = json.loads(finding["details_json"])["archive_recovery"]["modified_clock_audit"]
            self.assertEqual(status, audit["status"])
            self.assertFalse(audit["publisher_revision_time_verified"])
        result = self._recover([self._page([_row(**{"Last Modified Date": "2026-10-01T12:02:00Z"})])])
        self.assertEqual("PRIME_MODIFIED_CLOCK_AFTER_CAPTURE", result["reason"])

    def test_validation_only_does_not_connect_create_schema_or_collect_http(self):
        path = self._page()
        with patch.object(self.store, "_connect", side_effect=AssertionError("database access")), \
                patch.object(ScoutStore, "ensure_schema", side_effect=AssertionError("schema constructor")), \
                patch("market_checker_app.services.usaspending_request_budget.urlopen", side_effect=AssertionError("HTTP")):
            self.assertEqual("VALIDATED", self._recover([path], validate_only=True)["status"])
        self.assertEqual([], self._stored())
        result = recovery.recover_prime_awards_from_archives(None, manifests=[path],
            expected_manifest_sha256=self._pins([path]), subject_id="LMT", as_of=INGESTED,
            identities=[IDENTITY], validate_only=True)
        self.assertEqual("VALIDATED", result["status"])

    def test_complete_empty_is_only_selected_uei_window_and_does_not_create_negative_finding(self):
        with patch.object(self.store, "_connect", side_effect=AssertionError("database access")):
            result = self._recover([self._page([])])
        self.assertEqual("COMPLETE_EMPTY", result["status"])
        self.assertTrue(result["traversal_complete"])
        self.assertFalse(result["issuer_coverage_complete"])
        self.assertFalse(result["historical_availability_reconstructed"])
        self.assertEqual(0, result["new_findings"])

    def test_existing_conflict_rolls_back_earlier_inserts_and_closes_native_connection(self):
        old = self._recover([self._page([_row("TEST0002", internal_id=2)])])
        with self.store._connect() as conn:
            conn.execute("UPDATE scout_findings SET details_json='{}' WHERE finding_id=?", (old["finding_ids"][0],))
        before = self._stored()
        references = []
        original = self.store._connect
        def opened():
            connection = original()
            references.append(connection)
            return connection
        pages = [self._page(has_next=True), self._page([_row("TEST0002", internal_id=2)], page=2)]
        with patch.object(self.store, "_connect", side_effect=opened):
            result = self._recover(pages)
        self.assertEqual("EXISTING_FINDING_CONFLICT", result["reason"])
        self.assertEqual(before, self._stored())
        self.assertEqual(1, len(references))
        with self.assertRaises(sqlite3.ProgrammingError):
            references[0].execute("SELECT 1")

    def test_success_connection_closes_and_no_request_or_checkpoint_tables_are_created(self):
        references = []
        original = self.store._connect
        def opened():
            connection = original()
            references.append(connection)
            return connection
        with patch.object(self.store, "_connect", side_effect=opened):
            self.assertEqual("RECOVERED", self._recover([self._page()])["status"])
        with self.assertRaises(sqlite3.ProgrammingError):
            references[0].execute("SELECT 1")
        with self.store._connect() as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(any(name.startswith("scout_usaspending") for name in names))

    def test_path_references_pins_limits_and_canonical_scope_fail_closed(self):
        path = self._page()
        for changes in ({"expected_manifest_sha256": {}}, {"manifests": [path, path]},
                        {"maximum_pages": True}, {"maximum_awards": 0}, {"maximum_pages": 21},
                        {"maximum_awards": 1}, {"subject_id": "ZZNOTCANON"}, {"identities": []}):
            with self.subTest(changes=changes):
                if changes.get("maximum_awards") == 1:
                    paths = [self._page([_row(), _row("TEST0002")])]
                else:
                    paths = [self._page()]
                self.assertEqual("REJECTED", self._recover(paths, **changes)["status"])
        self._rewrite(path, body_file="../other.response")
        self.assertEqual("INVALID_PRIME_BODY_REFERENCE", self._recover([path])["reason"])

    def test_published_incomplete_prime_and_actual_transaction_archives_are_not_importable(self):
        repository = Path(__file__).resolve().parents[1]
        original = repository / "evidence/usaspending_live_20261001.json"
        body = original.read_bytes()
        self.assertEqual("57c1c8dfd8d33491765f2993eb99a3b1957ba91b93f0bb491c137123e1694cef",
                         hashlib.sha256(body).hexdigest())
        payload = json.loads(body)["cases"]["usaspending_positive_contract_uei"]["payload"]
        self.assertEqual(100, len(payload["results"]))
        self.assertIs(True, payload["page_metadata"]["hasNext"])
        transaction = repository / "evidence/batch3_contracts_live_20261012/capture-9cfe102b3b3a421292a3e20e0b0c39b2.json"
        for path in (original, repository / "evidence/usaspending_noc_live_20261002.json", transaction):
            with self.subTest(archive=path.name), patch.object(self.store, "_connect", side_effect=AssertionError("database access")):
                result = self._recover([path])
                self.assertEqual("ARCHIVE_BYTE_BOUND_EXCEEDED" if path == original else "NOT_ORIGINAL_PRIME_CAPTURE",
                                 result["reason"])
                self.assertEqual(0, result["new_findings"])
        self.assertEqual(body, original.read_bytes())
        self.assertEqual([], self._stored())


if __name__ == "__main__":
    unittest.main()
