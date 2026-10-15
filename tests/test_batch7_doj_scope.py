"""Original-byte DOJ scope regressions. Fixtures never authorize live I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch

from market_checker_app.services.doj_enforcement_scope_review import (
    MAX_CAPTURE_BYTES, _linked_filing, review_doj_enforcement_scope,
)

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/batch7_regulatory_20261015"
NOW = datetime(2026, 10, 15, 5, tzinfo=timezone.utc)  # Synthetic test clock only.
PINS = {
    "doj_apple_complaint": (1096132, "8179346e0243ba8e5650821b7f88eccf96284332a7ffa413756cafefc1cfbe1b"),
    "doj_apple_release": (85447, "e7d3e7fdf4d0f87f8a40fbc74e907b85a6f5d1b5656d97059941461b085783da"),
    "sec_apple_2023_10k": (1559034, "d76de55ead22cb553096b3495f55c8e0f6cd94f36242d9c87737b55952816724"),
    "sec_apple_2023_index": (12838, "ce2b5125c09b615c9bc47ac81aebd2b2a396004f7da7c5fc4b75ab4878edf433"),
}


def replace_body(packet, body, *, mime="text/html"):
    packet["body"] = body
    packet["metadata"].update(content_type=mime, raw_response_bytes=len(body),
                               raw_response_sha256=hashlib.sha256(body).hexdigest(), origin="SYNTHETIC_TEST")


class Batch7DojScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_request = json.loads((FOLDER / "request.json").read_text())
        index = json.loads((FOLDER / "capture_index.json").read_text())
        cls.base_captures = {row["capture_id"]: {
            "metadata": json.loads((FOLDER / "raw" / (row["capture_id"] + ".metadata.json")).read_text()),
            "body": (ROOT / row["archive_path"]).read_bytes()} for row in index["captures"]}
        cls.records = json.loads((ROOT / "market_checker_app/data/verified_counterparty_identities.json").read_text())["records"]

    def setUp(self):
        self.request, self.captures = deepcopy(self.base_request), deepcopy(self.base_captures)

    def review(self, **kwargs):
        with patch("socket.create_connection", side_effect=AssertionError("No source HTTP permitted")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No quota/store access permitted")):
            return review_doj_enforcement_scope(kwargs.pop("request", self.request),
                kwargs.pop("captures", self.captures), as_of=kwargs.pop("as_of", NOW),
                ingested_at=kwargs.pop("ingested_at", NOW), legal_records=kwargs.pop("legal_records", self.records), **kwargs)

    def test_actual_original_case_and_prior_filing_replay_remains_partial(self):
        report = self.review()
        self.assertEqual("PARTIAL", report["status"])
        self.assertTrue(report["case_document_scope_supported"])
        self.assertTrue(report["exact_case_entity_supported"])
        self.assertTrue(report["selected_passages_all_supported"])
        self.assertTrue(report["document_stage_supported"])
        self.assertEqual("ALLEGATIONS_ONLY", report["recorded_case_stage"])
        self.assertEqual("1", report["court_document_number"])
        self.assertEqual("2024-03-21", report["documentary_filed_on"])
        self.assertTrue(report["independent_earlier_legal_identity_supported"])
        self.assertEqual({"issuer_name": "Apple Inc.", "issuer_cik": "0000320193",
                          "anchor_date": "2023-09-30", "context_ref": "c-1"}, report["issuer_identity_fact"])
        self.assertEqual(["2023-11-02 18:08:27"], report["issuer_index_accepted_display"])
        self.assertFalse(report["issuer_index_accepted_timezone_verified"])
        self.assertEqual("2023-11-03", report["issuer_documentary_published_on"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", report["registry_identity"]["resolution"])
        self.assertEqual(["MISSING_CASE_DATE_REGISTERED_IDENTITY", "MISSING_IDENTITY_KNOWN_BEFORE_CASE_DAY",
                          "MISSING_INDEPENDENT_LEGAL_CONTINUITY"], report["missing_evidence"])
        self.assertTrue(report["release_publication_supported"])
        self.assertTrue(report["release_legal_description_supported"])
        self.assertEqual(["February 6, 2025"], report["release_updated_display"])

    def test_all_four_actual_original_bodies_remain_byte_pinned(self):
        for identifier, packet in self.captures.items():
            size, digest = PINS[identifier]
            self.assertEqual(size, len(packet["body"]))
            self.assertEqual(digest, hashlib.sha256(packet["body"]).hexdigest())
            self.assertEqual((size, digest), (packet["metadata"]["raw_response_bytes"], packet["metadata"]["raw_response_sha256"]))
        report = self.review(captures=list(self.captures.values()))
        self.assertTrue(all(c["original_body_pin_verified"] for c in report["captures"]))
        self.assertEqual(88, report["captures"][0]["pdf_page_count"])

    def test_original_prayer_for_adjudge_is_never_a_judgment(self):
        self.request["document_kind"] = "FINAL_JUDGMENT"
        pin = self.request["selected_passages"][2]
        pin.update(page=76, excerpt="To remedy these illegal acts, Plaintiffs request that the Court:")
        report = self.review()
        self.assertTrue(report["case_document_scope_supported"])
        self.assertTrue(report["passage_reviews"][2]["supported"])
        self.assertFalse(report["document_stage_supported"])
        self.assertEqual("UNKNOWN", report["recorded_case_stage"])
        self.assertFalse(report["adjudication_verified"])
        self.assertFalse(report["issuer_conviction_verified"])

    def synthetic_court(self, title):
        # Explicit synthetic HTML transport; no synthetic bytes replace a pin.
        stamp = "Case 2:24-cv-04055 Document 9 Filed 03/21/24 Page 1 of 1 PageID: 1"
        body = (f"<html><body>{stamp}<p>{self.request['court']}</p>"
                f"<p>APPLE INC.</p><p>One Apple Park Way Cupertino, CA 95014</p><p>Defendant.</p>"
                f"<h1>{title}</h1><p>Requested relief is not a finding.</p></body></html>").encode()
        packet = self.captures["doj_apple_complaint"]
        replace_body(packet, body)
        packet["metadata"].update(requested_url="https://www.justice.gov/atr/case-document/synthetic-fixture",
                                  final_url="https://www.justice.gov/atr/case-document/synthetic-fixture")
        for pin in self.request["selected_passages"][:3]:
            pin["page"] = 1
        self.request["selected_passages"][0]["excerpt"] = stamp
        self.request["selected_passages"][2]["excerpt"] = f"Defendant. {title}"

    def test_proposed_final_judgment_and_judgment_title_do_not_prove_outcome(self):
        for kind, stage in [("PROPOSED_FINAL_JUDGMENT", "PROPOSED_RELIEF_NOT_ADJUDICATION"),
                            ("FINAL_JUDGMENT", "COURT_DISPOSITION_DOCUMENT_REQUIRES_OUTCOME_REVIEW")]:
            with self.subTest(kind=kind):
                self.synthetic_court(kind.replace("_", " "))
                self.request["document_kind"] = kind
                report = self.review()
                self.assertTrue(report["document_stage_supported"])
                self.assertEqual(stage, report["recorded_case_stage"])
                self.assertFalse(report["adjudication_verified"])
                self.assertFalse(report["company_wide_liability_verified"])

    def test_wrong_docket_court_or_filed_day_cannot_inherit_source(self):
        for key, value in [("docket", "2:24-cv-04056"), ("court", "UNITED STATES DISTRICT COURT FOR THE DISTRICT OF DELAWARE"),
                           ("filed_on", "2024-03-22"), ("filed_on", "1924-03-21")]:
            with self.subTest(key=key, value=value):
                request = deepcopy(self.request)
                request[key] = value
                report = self.review(request=request)
                self.assertFalse(report["case_document_scope_supported"])
                self.assertFalse(report["document_stage_supported"])

    def test_wrong_entity_role_brand_or_parent_never_credits_case_entity(self):
        for key, value in [("case_entity_name", "Apple"), ("case_entity_name", "Apple Operations International Ltd."),
                           ("case_entity_role", "RESPONDENT")]:
            with self.subTest(key=key):
                request = deepcopy(self.request)
                request[key] = value
                report = self.review(request=request)
                self.assertFalse(report["exact_case_entity_supported"])
                self.assertFalse(report["issuer_attribution_verified"])

    def test_excerpt_mismatch_wrong_page_or_hidden_script_text_is_rejected(self):
        for page, excerpt in [(2, self.request["selected_passages"][1]["excerpt"]), (3, "Apple Inc. Subsidiary Defendant.")]:
            with self.subTest(page=page):
                request = deepcopy(self.request)
                request["selected_passages"][1].update(page=page, excerpt=excerpt)
                report = self.review(request=request)
                self.assertFalse(report["exact_case_entity_supported"])
                self.assertFalse(report["selected_passages_all_supported"])
        self.synthetic_court("COMPLAINT")
        packet = self.captures["doj_apple_complaint"]
        replace_body(packet, packet["body"].replace(b"<h1>COMPLAINT</h1>", b"<script>COMPLAINT</script>"))
        self.assertFalse(self.review()["document_stage_supported"])

    def test_missing_decoder_or_bad_pdf_remains_partial_without_html_fallback(self):
        with patch.dict("sys.modules", {"pypdf": None}):
            report = self.review()
        self.assertFalse(report["case_document_scope_supported"])
        self.assertEqual("PDF_DECODER_UNAVAILABLE", report["captures"][0]["review_reason"])
        replace_body(self.captures["doj_apple_complaint"], b"<html>COMPLAINT</html>", mime="application/pdf")
        report = self.review()
        self.assertEqual("INVALID_PRIMARY_PDF", report["captures"][0]["review_reason"])

    def test_pdf_page_bound_is_checked_before_any_page_extraction(self):
        reader = Mock()
        reader.is_encrypted = False
        reader.pages = [Mock()] * 121
        with patch("pypdf.PdfReader", return_value=reader):
            report = self.review()
        reader.pages[0].extract_text.assert_not_called()
        self.assertEqual("PDF_PAGE_BOUND", report["captures"][0]["review_reason"])

    def test_decoded_text_bound_and_selected_page_bound_are_explicit(self):
        reader = Mock()
        reader.is_encrypted = False
        page = Mock()
        page.extract_text.return_value = "x" * 2_000_001
        reader.pages = [page] * 88
        with patch("pypdf.PdfReader", return_value=reader):
            report = self.review()
        self.assertEqual("DECODED_TEXT_BOUND", report["captures"][0]["review_reason"])
        self.request["selected_passages"][1]["page"] = 120
        report = self.review()
        self.assertEqual("PDF_SELECTED_PAGE_BOUND", report["captures"][0]["review_reason"])

    def test_changed_hash_length_redirect_or_error_status_never_credits_original(self):
        for field, value in [("raw_response_sha256", "0" * 64), ("raw_response_bytes", 0),
                             ("http_status", 400), ("automatic_redirects", True),
                             ("final_url", "https://www.justice.gov/d9/another.pdf")]:
            with self.subTest(field=field):
                captures = deepcopy(self.captures)
                captures["doj_apple_complaint"]["metadata"][field] = value
                report = self.review(captures=captures)
                self.assertFalse(report["case_document_scope_supported"])
                self.assertFalse(report["captures"][0]["original_body_pin_verified"])

    def test_original_byte_bound_and_whole_body_withholding(self):
        packet = self.captures["doj_apple_complaint"]
        replace_body(packet, b"x" * (MAX_CAPTURE_BYTES + 1), mime="application/pdf")
        report = self.review()
        self.assertEqual("ORIGINAL_BODY_PIN_MISMATCH_OR_BYTE_BOUND", report["captures"][0]["review_reason"])
        packet["metadata"].update(public_raw_available=False, status="WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE")
        packet["body"] = None
        report = self.review()
        self.assertEqual("MISSING_OR_WITHHELD_ORIGINAL_BODY", report["captures"][0]["review_reason"])
        self.assertFalse(report["case_document_scope_supported"])

    def test_access_field_body_is_rejected_without_echo_or_sanitized_substitute(self):
        # Only a structural cue with an empty value; never a real credential.
        packet = self.captures["doj_apple_complaint"]
        replace_body(packet, b'<html><script>{"azure_map_key":""}</script><p>COMPLAINT</p></html>')
        report = self.review()
        self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", report["captures"][0]["review_reason"])
        self.assertNotIn("azure_map_key", json.dumps(report))
        self.assertFalse(report["captures"][0]["original_body_pin_verified"])

    def test_report_physical_clock_cutoff_never_backdates_capture(self):
        with patch("pypdf.PdfReader", side_effect=AssertionError("Unknown report must not decode")):
            report = self.review(as_of=NOW-timedelta(seconds=1))
        self.assertFalse(report["report_available_at_cutoff"])
        self.assertEqual(["REPORT_NOT_KNOWN_AT_CUTOFF"], report["missing_evidence"])
        self.assertEqual([], report["captures"])
        for field, value in [("observed_at", (NOW+timedelta(seconds=1)).isoformat()),
                             ("started_at", (NOW+timedelta(seconds=1)).isoformat())]:
            captures = deepcopy(self.captures)
            captures["doj_apple_complaint"]["metadata"][field] = value
            report = self.review(captures=captures)
            self.assertEqual("INVALID_SOURCE_CAPTURE_CLOCK", report["captures"][0]["review_reason"])
            self.assertFalse(report["case_document_scope_supported"])

    def test_prior_sec_tuple_date_and_index_pair_cannot_move_between_contexts(self):
        for field, value in [("issuer_cik", "0000000001"), ("issuer_legal_name", "Apple Computer, Inc."),
                             ("issuer_anchor_date", "2023-09-29"), ("issuer_published_on", "2023-11-02"),
                             ("issuer_publication_capture_id", "doj_apple_release")]:
            with self.subTest(field=field):
                request = deepcopy(self.request)
                request[field] = value
                self.assertFalse(self.review(request=request)["independent_earlier_legal_identity_supported"])

    def test_exact_sec_ix_wrapper_is_a_reference_not_redirect_permission(self):
        index = self.captures["sec_apple_2023_index"]["metadata"]["requested_url"]
        issuer = self.captures["sec_apple_2023_10k"]["metadata"]["requested_url"]
        path = issuer.removeprefix("https://www.sec.gov")
        self.assertTrue(_linked_filing(index, ["/ix?doc="+path], issuer))
        self.assertTrue(_linked_filing(index, [issuer], issuer))
        for wrong in ["https://example.org/ix?doc="+path, "/ix?doc="+path+"&doc="+path,
                      "/ix?doc="+path+"&next=https://example.org", "/ix?doc="+path+"#fragment"]:
            self.assertFalse(_linked_filing(index, [wrong], issuer))

    def test_newer_legal_registry_point_is_not_a_2024_identity_or_prior_knowledge(self):
        original = deepcopy(self.records)
        report = self.review()
        self.assertFalse(report["registry_identity"]["case_date_supported"])
        self.assertFalse(report["registry_identity"]["known_before_case_day"])
        self.assertFalse(report["registry_identity"]["legal_continuity_supported"])
        self.assertEqual(original, self.records)
        self.assertFalse(self.review(legal_records=[])["registry_identity"]["case_date_supported"])
        self.assertEqual("UNMAPPED_NAME", self.review(legal_records=[])["registry_identity"]["resolution"])

    def test_none_of_the_scope_facts_grants_adoption_coverage_liability_or_scoring(self):
        report = self.review()
        for flag in ("adjudication_verified", "issuer_attribution_verified", "parent_liability_verified",
                     "company_wide_liability_verified", "issuer_fine_verified", "issuer_conviction_verified",
                     "current_case_status_verified", "complete_issuer_enforcement_coverage", "historical_availability_reconstructed",
                     "publication_time_verified", "registry_adoption_allowed", "live_acceptance_verified", "scoring_applied", "prediction_input"):
            self.assertIs(False, report[flag], flag)
        self.assertEqual(0, report["http_requests"])
        self.assertEqual(NOW.isoformat(), report["known_at"])
        report["scope"]["issuer_ticker"] = "MUTATED"
        report["captures"].clear()
        self.assertEqual("AAPL", self.request["issuer_ticker"])
        self.assertEqual(4, len(self.captures))
        self.assertTrue(self.review()["case_document_scope_supported"])

    def test_capture_map_identity_count_duplicate_and_timezone_guards(self):
        with self.assertRaises(ValueError):
            self.review(captures={"different": self.captures["doj_apple_complaint"]})
        with self.assertRaises(ValueError):
            self.review(captures=list(self.captures.values()) * 2)
        with self.assertRaises(ValueError):
            self.review(captures=[self.captures["doj_apple_complaint"]] * 2)
        with self.assertRaises(ValueError):
            self.review(as_of=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            request = deepcopy(self.request)
            request["selected_passages"][0]["page"] = True
            self.review(request=request)


if __name__ == "__main__":
    unittest.main()
