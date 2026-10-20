"""Offline synthetic containment controls and the actual retained missing-body case."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import time
import unittest
from unittest.mock import patch

from market_checker_app.services import epa_frs_program_scope_review as target

ROOT = Path(__file__).resolve().parents[1]
FACILITY_ID = "110000000001"
OTHER_ID = "110000000002"
FACILITY_POINTER = "/Results/Facilities/0"
PROGRAM_POINTER = FACILITY_POINTER+"/ProgramFacilities/0"
INTEREST_POINTER = PROGRAM_POINTER+"/Interests/0"
CAPTURE = "synthetic_epa_facility"


def fixture():
    # These names/fields are explicitly synthetic contract examples, never live
    # EPA schema documentation, issuer evidence or an ownership acceptance case.
    program = {"ProgramSystemAcronym": "EXAMPLE", "ProgramSystemId": "SYNTHETIC-001",
        "ProgramFacilityName": "Synthetic program-specific name",
        "ParentName": "Synthetic parent only", "StartDate": "1999-01-01",
        "Interests": [{"RoleLabel": "REGISTERED PROGRAM INTEREST", "StartDate": "2020-02-01",
            "EndDate": None, "LastReportedDate": "2025-12-31"},
            {"RoleLabel": "DONOR RECORD", "EndDate": "2030-01-01"}]}
    row = {"RegistryId": FACILITY_ID, "FacilityName": "Synthetic facility",
           "OperatorName": "Synthetic operator only", "ProgramFacilities": [program]}
    payload = {"Results": {"Message": "Success", "QueryRows": "1", "Facilities": [row]}}
    request = {"capture_id": CAPTURE, "frs_registry_id": FACILITY_ID,
        "facility_name": "Synthetic facility", "facility_pointer": FACILITY_POINTER,
        "programs": [{"program_pointer": PROGRAM_POINTER, "program_system_acronym": "EXAMPLE",
            "program_system_id": "SYNTHETIC-001", "interests": [{"interest_pointer": INTEREST_POINTER,
                "role_field": "RoleLabel", "date_fields": {"start": "StartDate", "end": "EndDate", "last_reported": "LastReportedDate"}}]}]}
    observed = datetime(2026, 10, 2, 11, 52, 53, tzinfo=timezone.utc)
    url = "https://ofmpub.epa.gov/frs_public2/frs_rest_services.get_facilities?registry_id="+FACILITY_ID
    meta = {"capture_id": CAPTURE, "origin": "SYNTHETIC_CONTRACT_ONLY", "requested_url": url, "final_url": url,
        "started_at": (observed-timedelta(seconds=1)).isoformat(), "observed_at": observed.isoformat(),
        "known_at": observed.isoformat(), "http_status": 200, "automatic_redirects": False,
        "status": "ORIGINAL_BYTES_CAPTURED", "content_type": "application/json", "content_encoding": "identity",
        "public_raw_available": True}
    captures = {CAPTURE: {"metadata": meta, "body": None}}
    replace(captures, payload)
    return request, captures, payload


def replace(captures, payload=None, *, raw=None):
    body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode()
    packet = captures[CAPTURE]
    packet["body"] = body
    packet["metadata"].update(raw_response_bytes=len(body), raw_response_sha256=hashlib.sha256(body).hexdigest())


def interest(report):
    return report["selected_program_reviews"][0]["interest_reviews"][0]


class EpaFrsProgramScopeReviewTests(unittest.TestCase):
    def review(self, request, captures, **kwargs):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No source HTTP")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No database")):
            return target.review_epa_frs_program_scope(request, captures, **kwargs)

    def test_exact_program_and_interest_source_literals_never_prove_ownership_or_permission(self):
        request, captures, _ = fixture()
        report = self.review(request, captures)
        self.assertTrue(report["facility_identity_supported"])
        self.assertTrue(report["program_inventory_supported"])
        self.assertTrue(report["selected_program_reviews"][0]["program_identity_supported"])
        row = interest(report)
        self.assertEqual("REGISTERED PROGRAM INTEREST", row["role_literal"])
        self.assertEqual("2020-02-01", row["dates"]["start"]["iso_date"])
        self.assertEqual("NULL_SOURCE_VALUE", row["dates"]["end"]["status"])
        self.assertTrue(row["source_record_has_role_and_parseable_date"])
        self.assertTrue(report["source_review"]["synthetic_contract_only"])
        self.assertEqual("PARTIAL", report["status"])
        for flag in ("legal_owner_verified", "permitted_operator_verified", "issuer_identity_verified",
            "continuous_issuer_exposure_verified", "environmental_liability_verified", "registry_adoption_allowed",
            "all_program_interests_complete", "negative_facility_or_program_absence_verified", "live_acceptance_verified",
            "historical_availability_reconstructed", "scoring_applied", "prediction_input"):
            self.assertIs(False, report[flag], flag)
        self.assertFalse(row["dated_program_interest_verified"])
        self.assertFalse(row["program_interest_role_semantics_verified"])

    def test_field_evidence_resolves_to_its_exact_object_and_whole_original_hash(self):
        request, captures, payload = fixture()
        report = self.review(request, captures)
        evidence = report["facility_field_evidence"]+report["program_inventory"][0]["field_evidence"]
        evidence += [interest(report)["role_evidence"]]
        evidence += [row["evidence"] for row in interest(report)["dates"].values() if row["evidence"]]
        for fact in evidence:
            value = payload
            for token in fact["json_pointer"][1:].split("/"):
                value = value[int(token)] if isinstance(value, list) else value[token]
            self.assertEqual(value, fact["source_value"])
            self.assertEqual(hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
                             fact["source_value_json_sha256"])
            self.assertEqual(captures[CAPTURE]["metadata"]["raw_response_sha256"], fact["original_body_sha256"])

    def test_program_name_is_literal_and_missing_name_never_inherits_facility_or_parent(self):
        request, captures, payload = fixture()
        report = self.review(request, captures)
        self.assertEqual("Synthetic program-specific name", report["program_inventory"][0]["program_facility_name"])
        del payload["Results"]["Facilities"][0]["ProgramFacilities"][0]["ProgramFacilityName"]
        replace(captures, payload)
        report = self.review(request, captures)
        self.assertIsNone(report["program_inventory"][0]["program_facility_name"])
        self.assertNotIn("ParentName", [x["source_field"] for x in report["program_inventory"][0]["field_evidence"]])

    def test_other_facility_program_pointer_is_rejected_before_body_decode(self):
        request, captures, _ = fixture()
        request["programs"][0]["program_pointer"] = "/Results/Facilities/1/ProgramFacilities/0"
        with patch.object(target, "_json", side_effect=AssertionError("No out-of-scope body read")):
            with self.assertRaises(ValueError):
                self.review(request, captures)

    def test_another_program_interest_pointer_cannot_borrow_the_selected_program(self):
        request, captures, _ = fixture()
        request["programs"][0]["interests"][0]["interest_pointer"] = FACILITY_POINTER+"/ProgramFacilities/1/Interests/0"
        with self.assertRaises(ValueError):
            self.review(request, captures)

    def test_interest_dates_are_never_borrowed_from_parent_or_sibling_record(self):
        request, captures, payload = fixture()
        row = payload["Results"]["Facilities"][0]["ProgramFacilities"][0]["Interests"][0]
        del row["StartDate"]; del row["EndDate"]; del row["LastReportedDate"]
        replace(captures, payload)
        report = self.review(request, captures)
        self.assertTrue(interest(report)["role_literal_supported"])
        self.assertFalse(interest(report)["source_record_has_role_and_parseable_date"])
        for observed in interest(report)["dates"].values():
            self.assertEqual("SOURCE_FIELD_NOT_PRESENT", observed["status"])
            self.assertIsNone(observed["iso_date"])

    def test_mismatched_acronym_or_system_id_blocks_interest_fields(self):
        for key, value in (("program_system_acronym", "OTHER"), ("program_system_id", "SYNTHETIC-OTHER")):
            request, captures, _ = fixture(); request["programs"][0][key] = value
            with self.subTest(field=key):
                report = self.review(request, captures)
                selected = report["selected_program_reviews"][0]
                self.assertFalse(selected["program_identity_supported"])
                self.assertEqual([], selected["interest_reviews"])
                self.assertTrue(report["facility_identity_supported"])

    def test_contained_interest_with_contradictory_local_identity_is_not_rebound(self):
        for field, value in (("RegistryId", OTHER_ID), ("ProgramSystemId", "SYNTHETIC-OTHER"),
                             ("ProgramSystemAcronym", "OTHER")):
            request, captures, payload = fixture()
            payload["Results"]["Facilities"][0]["ProgramFacilities"][0]["Interests"][0][field] = value
            replace(captures, payload)
            with self.subTest(field=field):
                row = interest(self.review(request, captures))
                self.assertFalse(row["role_literal_supported"])
                self.assertEqual({}, row["dates"])
                self.assertIn("CONTRADICTORY_RECORD_LOCAL_FACILITY_OR_PROGRAM_ID", row["missing_evidence"])

    def test_duplicate_facility_or_registry_alias_is_ambiguous(self):
        for change in ("duplicate", "alias"):
            request, captures, payload = fixture()
            envelope = payload["Results"]
            if change == "duplicate":
                envelope["Facilities"].append(deepcopy(envelope["Facilities"][0])); envelope["QueryRows"] = "2"
            else:
                envelope["Facilities"][0]["RegistryID"] = FACILITY_ID
            replace(captures, payload)
            with self.subTest(change=change):
                report = self.review(request, captures)
                self.assertFalse(report["facility_identity_supported"])
                self.assertEqual([], report["program_inventory"])

    def test_same_named_other_facility_row_does_not_supply_programs(self):
        request, captures, payload = fixture()
        donor = deepcopy(payload["Results"]["Facilities"][0]); donor["RegistryId"] = OTHER_ID
        payload["Results"]["Facilities"].insert(0, donor); payload["Results"]["QueryRows"] = "2"
        replace(captures, payload)
        report = self.review(request, captures)
        self.assertFalse(report["facility_identity_supported"])
        self.assertIn("SELECTED_FACILITY_ID_OR_NAME_MISMATCH", report["missing_evidence"])

    def test_failed_or_error_envelope_never_supports_positive_or_absence_claims(self):
        for scope in ("message", "outer_error", "inner_error"):
            request, captures, payload = fixture()
            if scope == "message": payload["Results"]["Message"] = "Failure"
            elif scope == "outer_error": payload["Error"] = "synthetic source error"
            else: payload["Results"]["Errors"] = []
            replace(captures, payload)
            with self.subTest(scope=scope):
                report = self.review(request, captures)
                self.assertFalse(report["facility_identity_supported"])
                self.assertFalse(report["negative_facility_or_program_absence_verified"])

    def test_truncated_or_empty_queries_never_prove_complete_negative(self):
        request, captures, payload = fixture()
        payload["Results"]["QueryRows"] = "2"; replace(captures, payload)
        report = self.review(request, captures)
        self.assertTrue(report["facility_identity_supported"])
        self.assertFalse(report["response_row_inventory_complete"])
        self.assertFalse(report["all_program_interests_complete"])
        self.assertFalse(report["negative_facility_or_program_absence_verified"])
        payload["Results"].update(QueryRows="0", Facilities=[]); replace(captures, payload)
        report = self.review(request, captures)
        self.assertTrue(report["response_row_inventory_complete"])
        self.assertFalse(report["facility_identity_supported"])
        self.assertFalse(report["negative_facility_or_program_absence_verified"])

    def test_duplicate_program_key_blocks_program_inventory_and_all_interest_selections(self):
        request, captures, payload = fixture()
        payload["Results"]["Facilities"][0]["ProgramFacilities"] *= 2
        replace(captures, payload)
        report = self.review(request, captures)
        self.assertTrue(report["facility_identity_supported"])
        self.assertFalse(report["program_inventory_supported"])
        self.assertEqual([], report["selected_program_reviews"])
        self.assertIn("DUPLICATE_FACILITY_LOCAL_PROGRAM_KEY", report["missing_evidence"])

    def test_missing_facility_local_programs_never_uses_global_donor_collection(self):
        request, captures, payload = fixture()
        payload["Results"]["ProgramFacilities"] = payload["Results"]["Facilities"][0].pop("ProgramFacilities")
        replace(captures, payload)
        report = self.review(request, captures)
        self.assertTrue(report["facility_identity_supported"])
        self.assertFalse(report["program_inventory_supported"])
        self.assertEqual([], report["program_inventory"])

    def test_missing_invalid_and_unrecognized_dates_remain_exact_field_level_partial(self):
        request, captures, payload = fixture()
        row = payload["Results"]["Facilities"][0]["ProgramFacilities"][0]["Interests"][0]
        row.update(StartDate="2020", EndDate="", LastReportedDate="2025-02-30")
        replace(captures, payload)
        dates = interest(self.review(request, captures))["dates"]
        self.assertEqual("2020", dates["start"]["literal"])
        self.assertEqual("UNRECOGNIZED_DATE_LITERAL", dates["start"]["status"])
        self.assertEqual("EMPTY_SOURCE_VALUE", dates["end"]["status"])
        self.assertEqual("UNRECOGNIZED_DATE_LITERAL", dates["last_reported"]["status"])
        self.assertTrue(all(row["iso_date"] is None for row in dates.values()))

    def test_date_order_and_even_parseable_future_literals_do_not_prove_operating_interval(self):
        request, captures, payload = fixture()
        row = payload["Results"]["Facilities"][0]["ProgramFacilities"][0]["Interests"][0]
        row.update(StartDate="2031-01-01", EndDate="2030-01-01")
        replace(captures, payload)
        result = interest(self.review(request, captures))
        self.assertFalse(result["lexical_start_end_order_coherent"])
        self.assertEqual("2031-01-01", result["dates"]["start"]["iso_date"])
        self.assertFalse(result["dates"]["start"]["semantic_date_role_verified"])
        self.assertFalse(result["dates"]["start"]["event_or_validity_verified"])
        self.assertFalse(result["dated_program_interest_verified"])

    def test_transport_hash_length_redirect_and_encoding_guards_precede_json_decode(self):
        changes = [("raw_response_sha256", "0"*64), ("raw_response_bytes", 1), ("raw_response_bytes", True),
            ("http_status", 400), ("http_status", True), ("automatic_redirects", True),
            ("final_url", "https://echodata.epa.gov/echo/echo_rest_services.get_facility_info"),
            ("content_type", "text/html"), ("archive_encoding", "gzip"), ("content_encoding", "gzip")]
        for field, value in changes:
            request, captures, _ = fixture(); captures[CAPTURE]["metadata"][field] = value
            with self.subTest(field=field, value=value), patch.object(target, "_json", side_effect=AssertionError("No invalid-transport decode")):
                report = self.review(request, captures)
                self.assertFalse(report["source_review"]["original_body_pin_verified"])
                self.assertFalse(report["facility_identity_supported"])

    def test_missing_and_whole_withheld_records_never_inspect_supplied_body(self):
        for field, value in (("public_raw_available", False), ("status", "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"),
                             ("publication_status", "WITHHELD_SENSITIVE_BODY")):
            request, captures, _ = fixture(); captures[CAPTURE]["metadata"][field] = value
            with self.subTest(field=field), patch.object(target, "_json", side_effect=AssertionError("No withheld body read")), \
                 patch.object(target, "_sha", side_effect=AssertionError("No withheld body hash")):
                report = self.review(request, captures)
                self.assertEqual("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", report["source_review"]["reason"])
        request, captures, _ = fixture(); captures[CAPTURE]["body"] = None
        self.assertFalse(self.review(request, captures)["facility_identity_supported"])

    def test_access_field_cue_withholds_whole_synthetic_document_without_export(self):
        request, captures, _ = fixture()
        replace(captures, raw=b'{"api_key":null}')  # No credential value; a synthetic cue only.
        with patch.object(target, "_json", side_effect=AssertionError("No access-bearing body decode")):
            report = self.review(request, captures)
        self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", report["source_review"]["reason"])
        self.assertNotIn("api_key", json.dumps(report))
        self.assertNotIn("source_url", report["source_review"])

    def test_json_escaped_access_field_cue_is_whole_withheld_after_decode(self):
        request, captures, _ = fixture()
        replace(captures, raw=b'{"a\\u0070i_key":null}')  # Synthetic field cue, no access value.
        report = self.review(request, captures)
        self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", report["source_review"]["reason"])
        self.assertFalse(report["source_review"]["original_body_pin_verified"])
        self.assertNotIn("api_key", json.dumps(report))

    def test_actual_dow_summary_stays_missing_original_with_unknown_programs_and_dates(self):
        old = ROOT/"evidence/batch6_regulatory_20261014/capture_plan.json"
        meta = json.loads(old.read_text())[0]
        request = {"capture_id": meta["capture_id"], "frs_registry_id": "110019986586", "facility_name": "DOW INC",
                   "facility_pointer": "/Results/Facilities/0", "programs": []}
        before = (ROOT/"evidence/epa_echo_live_20261002.json").read_bytes()
        with patch.object(target, "_json", side_effect=AssertionError("No summary substitute decode")):
            report = self.review(request, {meta["capture_id"]: (meta, None)})
        self.assertEqual("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", report["source_review"]["reason"])
        self.assertFalse(report["facility_identity_supported"])
        self.assertEqual([], report["program_inventory"])
        self.assertIsNone(report["publisher_query_rows"])
        self.assertIsNone(report["observed_program_rows"])
        self.assertEqual(before, (ROOT/"evidence/epa_echo_live_20261002.json").read_bytes())

    def test_summary_bytes_cannot_replace_the_recorded_unknown_original(self):
        request, captures, _ = fixture()
        replace(captures, raw=(ROOT/"evidence/epa_echo_live_20261002.json").read_bytes())
        report = self.review(request, captures)
        self.assertFalse(report["facility_identity_supported"])
        self.assertIn("UNSUPPORTED_PRIMARY_FRS_JSON_ENVELOPE", report["missing_evidence"])

    def test_future_cutoff_or_future_source_is_not_silently_clamped_or_decoded(self):
        request, captures, _ = fixture(); future = datetime.now(timezone.utc)+timedelta(hours=1)
        with patch.object(target, "_json", side_effect=AssertionError("No future read")):
            with self.assertRaisesRegex(ValueError, "FUTURE_FRS_PROGRAM"):
                self.review(request, captures, reviewed_at=future)
            captures[CAPTURE]["metadata"]["known_at"] = future.isoformat()
            report = self.review(request, captures)
        self.assertEqual("SOURCE_NOT_KNOWN_AT_SOURCE_READ_CUTOFF", report["source_review"]["reason"])

    def test_actual_completion_follows_delayed_parsing_and_all_supported_source_clocks(self):
        request, captures, _ = fixture(); parser = target._json; completed = []
        def delayed(body):
            result = parser(body); time.sleep(0.01)
            completed.append(datetime.now(timezone.utc)); return result
        with patch.object(target, "_json", side_effect=delayed):
            report = self.review(request, captures)
        end = datetime.fromisoformat(report["known_at"])
        self.assertGreaterEqual(end, completed[0])
        self.assertEqual(report["known_at"], report["review_completed_at"])
        self.assertLessEqual(datetime.fromisoformat(report["source_review"]["source_known_at"]), end)
        self.assertFalse(report["source_read_cutoff_is_output_availability"])

    def test_clock_rollback_is_rejected_on_normal_and_missing_original_paths(self):
        for missing in (False, True):
            request, captures, _ = fixture(); start = datetime.now(timezone.utc)
            if missing: captures[CAPTURE]["body"] = None
            with self.subTest(missing=missing), patch.object(target, "_physical_clock", side_effect=[start, start-timedelta(microseconds=1)]):
                with self.assertRaisesRegex(ValueError, "CLOCK_ROLLBACK"):
                    self.review(request, captures)

    def test_nested_review_clock_after_physical_end_rejects_the_output(self):
        request, captures, _ = fixture(); start = datetime.now(timezone.utc); end = start+timedelta(seconds=1)
        producer = target._review_rows
        def nested(result, *args):
            producer(result, *args)
            result["derived_review"] = {"known_at": (end+timedelta(microseconds=1)).isoformat()}
        with patch.object(target, "_review_rows", side_effect=nested), patch.object(target, "_physical_clock", side_effect=[start, end]):
            with self.assertRaisesRegex(ValueError, "COMPLETION_PRECEDES_SUPPORTING_CLOCK"):
                self.review(request, captures)

    def test_past_source_read_cutoff_does_not_backdate_output_availability(self):
        request, captures, _ = fixture(); cutoff = datetime(2026, 10, 3, tzinfo=timezone.utc)
        start = datetime.now(timezone.utc)
        report = self.review(request, captures, reviewed_at=cutoff)
        self.assertEqual(cutoff.isoformat(), report["source_read_cutoff"])
        self.assertGreaterEqual(datetime.fromisoformat(report["known_at"]), start)
        self.assertFalse(report["historical_availability_reconstructed"])

    def test_duplicate_json_keys_constants_trailing_text_and_structure_bounds_fail_closed(self):
        bodies = [b'{"Results":{},"Results":{}}', b'{"Results":NaN}', b'{} trailing',
                  (b'['*34+b'0'+b']'*34)]
        for raw in bodies:
            request, captures, _ = fixture(); replace(captures, raw=raw)
            with self.subTest(raw_prefix=raw[:16]):
                report = self.review(request, captures)
                self.assertFalse(report["facility_identity_supported"])
                self.assertFalse(report["source_review"]["original_body_pin_verified"])

    def test_explicit_request_bounds_aliases_and_financial_identity_selectors_are_rejected(self):
        for change in ("cik", "too_many_programs", "duplicate_program", "duplicate_interest", "donor_date_field",
                       "deep_interest", "bad_facility_pointer", "too_many_captures", "naive_cutoff"):
            request, captures, _ = fixture()
            if change == "cik": request["issuer_cik"] = "0000000001"
            elif change == "too_many_programs": request["programs"] *= target.MAX_SELECTED_PROGRAMS+1
            elif change == "duplicate_program": request["programs"] *= 2
            elif change == "duplicate_interest": request["programs"][0]["interests"] *= 2
            elif change == "donor_date_field": request["programs"][0]["interests"][0]["date_fields"]["end"] = "StartDate"
            elif change == "deep_interest": request["programs"][0]["interests"][0]["interest_pointer"] += "/Child/0"
            elif change == "bad_facility_pointer": request["facility_pointer"] = "/Results/Facilities/00"
            elif change == "too_many_captures": captures = {str(i): captures[CAPTURE] for i in range(target.MAX_CAPTURES+1)}
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.review(request, captures, **({"reviewed_at": datetime(2026, 10, 3)} if change == "naive_cutoff" else {}))

    def test_inputs_and_independent_callback_results_are_not_mutated(self):
        request, captures, _ = fixture(); before = deepcopy((request, captures))
        first = self.review(request, captures)
        first["program_inventory"][0]["field_evidence"][0]["source_value"] = "tampered callback copy"
        second = self.review(request, {CAPTURE: (captures[CAPTURE]["metadata"], captures[CAPTURE]["body"])})
        self.assertEqual("EXAMPLE", second["program_inventory"][0]["field_evidence"][0]["source_value"])
        self.assertEqual(before, (request, captures))

    def test_unselected_capture_never_becomes_a_program_or_date_donor(self):
        request, captures, _ = fixture()
        donor_request, donor, payload = fixture()
        del captures[CAPTURE]["metadata"]["known_at"]
        payload["Results"]["Facilities"][0]["ProgramFacilities"][0]["Interests"][0]["EndDate"] = "2030-01-01"
        replace(donor, payload)
        captures["unused_donor"] = donor[CAPTURE]
        report = self.review(request, captures)
        self.assertIsNone(interest(report)["dates"]["end"]["iso_date"])
        self.assertEqual(CAPTURE, report["source_review"]["capture_id"])


if __name__ == "__main__":
    unittest.main()
