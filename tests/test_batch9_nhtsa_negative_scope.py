"""Actual pinned responses plus explicitly synthetic completeness contracts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from market_checker_app.services.nhtsa_negative_scope_review import review_nhtsa_negative_scope

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/batch9_regulatory_20261016"


def load(name):
    plan = json.loads((FOLDER / name).read_text())
    captures = {key: {"metadata": value["metadata"], "body":
        (ROOT / value["body_ref"]["path"]).read_bytes() if value["body_available"] else None}
        for key, value in plan["captures"].items()}
    return plan["request"], captures


class Batch9NhtsaNegativeScopeTests(unittest.TestCase):
    def setUp(self):
        self.request, self.captures = load("actual_review_plan.json")

    def review(self, request=None, captures=None, **kwargs):
        with patch("socket.create_connection", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No database access")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("No HTTP client initialization")):
            return review_nhtsa_negative_scope(request or self.request, captures or self.captures, **kwargs)

    def synthetic_empty(self, **extra):
        # Reuse real exact catalog/query/clock fixtures but explicitly replace
        # the response in memory. This is NOT a genuine publisher HTTP200 empty.
        request, captures = load("plans/chevrolet_brightdrop_2026_http400.json")
        metadata = captures[request["recall_capture_id"]]["metadata"]
        metadata.update(http_status=200, synthetic_test_only=True)
        body = json.dumps({"Count": 0, "Message": "Results returned successfully", "results": [], **extra}).encode()
        packet = captures[request["recall_capture_id"]]
        packet["body"] = body
        metadata.update(raw_response_bytes=len(body), raw_response_sha256=hashlib.sha256(body).hexdigest())
        return request, captures

    def test_actual_new_7500xd_response_is_two_positive_campaigns_not_a_negative(self):
        report = self.review()
        self.assertEqual("POSITIVE_EXACT_MODEL_YEAR", report["status"])
        self.assertEqual(200, report["http_status"])
        self.assertEqual(2, report["publisher_count"])
        self.assertEqual(2, report["matched_rows"])
        self.assertFalse(report["complete_negative"])
        body = self.captures[self.request["recall_capture_id"]]["body"]
        self.assertEqual(2250, len(body))
        self.assertEqual("b9a6e0e9b00e7a39d82903dd66da75c243620c6e8e1194ab9a8fd7273835943a", hashlib.sha256(body).hexdigest())

    def test_actual_retained_http400_success_shaped_empty_remains_partial(self):
        request, captures = load("plans/chevrolet_brightdrop_2026_http400.json")
        report = self.review(request, captures)
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual("HTTP_400_IS_NOT_COMPLETE_NEGATIVE", report["reason"])
        self.assertEqual(0, report["publisher_completeness_audit"]["publisher_count"])
        self.assertTrue(report["publisher_completeness_audit"]["complete_publisher_envelope"])
        self.assertFalse(report["complete_negative"])
        self.assertFalse(report["live_complete_negative_verified"])

    def test_approval_block_is_missing_response_not_a_fabricated_http_failure(self):
        request, captures = load("plans/chevrolet_5500xg_2026_approval_blocked.json")
        missing = captures[request["recall_capture_id"]]
        self.assertIsNone(missing["body"])
        self.assertIsNone(missing["metadata"]["http_status"])
        self.assertIsNone(missing["metadata"]["observed_at"])
        with patch("market_checker_app.services.nhtsa_negative_scope_review.documented_model_scope",
                   side_effect=AssertionError("Unavailable request must not parse catalog")):
            report = self.review(request, captures)
        self.assertEqual("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", report["reason"])
        self.assertNotIn("http_status", report)
        self.assertFalse(report["complete_negative"])

    def test_synthetic_http200_full_empty_contract_is_model_year_only_and_replay_only(self):
        request, captures = self.synthetic_empty()
        report = self.review(request, captures)
        self.assertEqual("COMPLETE_NEGATIVE_MODEL_YEAR", report["status"])
        self.assertTrue(report["complete_negative"])
        self.assertTrue(report["source_complete_negative"])
        self.assertFalse(report["live_complete_negative_verified"])
        self.assertFalse(report["issuer_wide_absence_verified"])
        self.assertFalse(report["complete_current_recall_history_verified"])

    def test_partial_continuation_and_pagination_metadata_prevent_empty_acceptance(self):
        for field, value in (("hasMore", True), ("Truncated", True), ("Partial", True),
                             ("nextPageToken", "SYNTHETIC_NEXT"), ("pagination", {"next": 2}), ("page", 1)):
            with self.subTest(field=field):
                req, captures = self.synthetic_empty(**{field: value})
                report = self.review(req, captures)
                self.assertEqual("PARTIAL", report["status"])
                self.assertFalse(report["complete_negative"])

    def test_contradictory_totals_even_boolean_zero_are_not_complete(self):
        for field in ("TotalCount", "totalResults", "TotalRecords"):
            for value in (1, False, "0"):
                req, captures = self.synthetic_empty(**{field: value})
                report = self.review(req, captures)
                self.assertFalse(report["complete_negative"])
                self.assertIn("TOTAL_COUNT_DOES_NOT_ESTABLISH_FULL_RESPONSE", report["missing_evidence"])
        req, captures = self.synthetic_empty(TotalCount=0, hasMore=False)
        self.assertTrue(self.review(req, captures)["complete_negative"])

    def test_contradictory_filter_echo_and_unknown_metadata_fail_closed(self):
        for extra in ({"Make": "FORD"}, {"model": "Suburban"}, {"ModelYear": 2025},
                      {"SearchCriteria": "Make:CHEVROLET | Model:BrightDrop | ModelYear:2025"},
                      {"SearchCriteria": "Model:BrightDrop"}, {"UnreviewedFilter": "scope-narrowed"}):
            req, captures = self.synthetic_empty(**extra)
            self.assertFalse(self.review(req, captures)["complete_negative"])
        req, captures = self.synthetic_empty(SearchCriteria="Make:CHEVROLET | Model:BrightDrop | ModelYear:2026")
        self.assertTrue(self.review(req, captures)["complete_negative"])

    def test_count_type_duplicate_keys_and_error_envelopes_are_rejected(self):
        for body in (b'{"Count":0,"Count":0,"Message":"Results returned successfully","results":[]}',
                     b'{"Count":true,"Message":"Results returned successfully","results":[]}',
                     b'{"Count":1,"Message":"Results returned successfully","results":[]}',
                     b'{"Count":0,"Message":"Results returned successfully","results":[],"Error":"failed"}'):
            req, captures = self.synthetic_empty()
            packet = captures[req["recall_capture_id"]]
            packet["body"] = body
            packet["metadata"].update(raw_response_bytes=len(body), raw_response_sha256=hashlib.sha256(body).hexdigest())
            self.assertFalse(self.review(req, captures)["complete_negative"])

    def test_catalog_exact_model_query_and_identity_cannot_be_substituted(self):
        for field, value in (("make", "FORD"), ("model", "7500 XD"), ("model_year", 2025)):
            req = deepcopy(self.request); req[field] = value
            self.assertFalse(self.review(req)["catalog_scope_supported"])
        packet = self.captures[self.request["catalog_capture_id"]]
        packet["metadata"]["final_url"] = "https://vpic.nhtsa.dot.gov/api/vehicles/GetModelsForMakeYear/make/FORD/modelyear/2026?format=json"
        self.assertFalse(self.review()["catalog_scope_supported"])

    def test_recall_query_changes_redirects_and_encoded_substitutes_are_rejected(self):
        changes = (("final_url", "https://api.nhtsa.gov/recalls/recallsByVehicle?make=FORD&model=7500XD&modelYear=2026"),
                   ("automatic_redirects", True), ("automatic_redirects", None), ("content_encoding", "gzip"),
                   ("archive_encoding", "base64"), ("content_type", "text/html"))
        for field, value in changes:
            captures = deepcopy(self.captures)
            captures[self.request["recall_capture_id"]]["metadata"][field] = value
            self.assertEqual("PARTIAL", self.review(captures=captures)["status"])

    def test_original_hash_length_and_actual_body_are_pinned(self):
        for field, value in (("raw_response_sha256", "0"*64), ("raw_response_bytes", 1)):
            captures = deepcopy(self.captures)
            captures[self.request["recall_capture_id"]]["metadata"][field] = value
            report = self.review(captures=captures)
            self.assertEqual("ORIGINAL_BYTE_PIN_OR_SIZE_BOUND", report["reason"])
        self.captures[self.request["recall_capture_id"]]["body"] += b" "
        self.assertEqual("ORIGINAL_BYTE_PIN_OR_SIZE_BOUND", self.review()["reason"])

    def test_legacy_catalog_unknown_redirect_policy_is_not_invented(self):
        report = self.review()
        catalog = next(r for r in report["source_reviews"] if r["capture_id"] == self.request["catalog_capture_id"])
        self.assertTrue(report["catalog_scope_supported"])
        self.assertFalse(catalog["automatic_redirects_disabled_verified"])
        self.assertIsNone(self.captures[self.request["catalog_capture_id"]]["metadata"]["automatic_redirects"])

    def test_capture_and_known_clocks_obey_cutoff_and_actual_physical_time(self):
        future = datetime.now(timezone.utc)+timedelta(days=1)
        for field in ("started_at", "observed_at", "known_at"):
            captures = deepcopy(self.captures)
            captures[self.request["recall_capture_id"]]["metadata"][field] = future.isoformat()
            self.assertEqual("PARTIAL", self.review(captures=captures, reviewed_at=future+timedelta(days=1))["status"])
        report = self.review(reviewed_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
        self.assertEqual("SOURCE_NOT_KNOWN_AT_REVIEW_CLOCK", report["reason"])
        self.assertGreater(datetime.fromisoformat(report["known_at"]), datetime(2026, 10, 12, tzinfo=timezone.utc))

    def test_whole_withheld_original_cannot_be_restored_with_other_bytes(self):
        packet = self.captures[self.request["recall_capture_id"]]
        packet["metadata"].update(status="WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", public_raw_available=False)
        report = self.review()
        self.assertEqual("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", report["reason"])
        self.assertIsNone(report["publisher_completeness_audit"])

    def test_http302_307_400_429_or_500_never_becomes_success_empty(self):
        for status in (302, 307, 400, 429, 500):
            req, captures = self.synthetic_empty()
            captures[req["recall_capture_id"]]["metadata"]["http_status"] = status
            report = self.review(req, captures)
            self.assertEqual("PARTIAL", report["status"])
            self.assertEqual(f"HTTP_{status}_IS_NOT_COMPLETE_NEGATIVE", report["reason"])
            self.assertFalse(report["complete_negative"])

    def test_positive_rows_must_match_exact_model_and_source_date(self):
        for field, value in (("Model", "5500XG"), ("ModelYear", "2025"), ("ReportReceivedDate", "01/01/2099")):
            captures = deepcopy(self.captures)
            packet = captures[self.request["recall_capture_id"]]
            payload = json.loads(packet["body"]); payload["results"][0][field] = value
            body = json.dumps(payload).encode(); packet["body"] = body
            packet["metadata"].update(raw_response_bytes=len(body), raw_response_sha256=hashlib.sha256(body).hexdigest(), synthetic_test_only=True)
            self.assertEqual("PARTIAL", self.review(captures=captures)["status"])

    def test_tuple_packets_and_inputs_are_immutable_without_io(self):
        req, captures = self.synthetic_empty()
        tuples = {key: (row["metadata"], row["body"]) for key, row in captures.items()}
        old_request, old_captures = deepcopy(req), deepcopy(tuples)
        report = self.review(req, tuples)
        self.assertTrue(report["complete_negative"])
        self.assertEqual(old_request, req); self.assertEqual(old_captures, tuples)
        self.assertEqual(0, report["http_requests"]); self.assertEqual(0, report["database_actions"])
        for field in ("live_complete_negative_verified", "issuer_attribution_verified", "issuer_wide_absence_verified",
                      "manufacturer_ownership_inferred", "historical_availability_reconstructed", "registry_adoption_allowed",
                      "scoring_applied", "prediction_input", "current_vehicle_production_verified"):
            self.assertFalse(report[field])

    def test_request_count_selectors_and_clocks_are_bounded(self):
        for changes in ({"model_year": True}, {"make": "*"}, {"model": ""},
                        {"recall_capture_id": self.request["catalog_capture_id"]}):
            req = {**self.request, **changes}
            with self.assertRaises(ValueError): self.review(req)
        with self.assertRaises(ValueError): self.review(reviewed_at=datetime(2026, 10, 16))
        with self.assertRaises(ValueError): self.review(captures={"capture_"+str(n): {} for n in range(9)})
        packet = self.captures[self.request["recall_capture_id"]]
        packet["body"] = b"x"*4_000_001
        self.assertEqual("ORIGINAL_BYTE_PIN_OR_SIZE_BOUND", self.review()["reason"])


if __name__ == "__main__":
    unittest.main()
