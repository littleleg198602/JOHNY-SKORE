"""New original Doc51 support and explicitly synthetic interpretation controls.

No synthetic decoder output is acceptance evidence. No source is fetched or
rewritten, and no store, registry file or quota database is opened.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from market_checker_app.services import doj_enforcement_scope_review as scope

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/sourcefront_regulatory_20261020"
CAPTURE = "doj_first_amended_complaint"
NOW = datetime(2026, 10, 20, 21, tzinfo=timezone.utc)  # Synthetic test clock only.


class FirstAmendedOriginalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((FOLDER / "AAPL_puvodni_doplnene_podani.json").read_text())
        descriptor = cls.plan["captures"][CAPTURE]
        body = (ROOT / descriptor["body_ref"]["path"]).read_bytes()
        if (len(body), hashlib.sha256(body).hexdigest()) != (
                3_404_671, "3c8eaebc6ebc5106ac3dfbe1751023cf4b16d297ca5e54c65ed9a75bc80fc2a6"):
            raise AssertionError("Retained whole original bytes changed")
        cls.captures = {CAPTURE: {"metadata": descriptor["metadata"], "body": body}}
        cls.pages, cls.count = scope._pdf_pages(body, {1, 3, 17})

    def review(self, request=None, captures=None):
        with patch("socket.create_connection", side_effect=AssertionError("Offline source review")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No store or quota I/O")):
            return scope.review_doj_enforcement_scope(request or self.plan["request"],
                captures or self.captures, as_of=NOW, ingested_at=NOW, legal_records=())

    def synthetic_decoder_review(self, pages, request=None):
        captures = deepcopy(self.captures)
        captures[CAPTURE]["metadata"]["origin"] = "SYNTHETIC_DECODER_CONTROL_NOT_ACCEPTANCE"
        with patch.object(scope, "_pdf_pages", return_value=(pages, self.count)):
            return self.review(request=request, captures=captures)

    def test_actual_whole_91_page_original_exact_stamp_title_and_paragraph19(self):
        report = self.review()
        self.assertEqual("PARTIAL", report["status"])
        self.assertTrue(report["case_document_scope_supported"])
        self.assertTrue(report["exact_case_entity_supported"])
        self.assertTrue(report["document_stage_supported"])
        self.assertTrue(report["selected_passages_all_supported"])
        self.assertTrue(report["selected_document_stamps_consistent"])
        self.assertEqual("ALLEGATIONS_ONLY", report["recorded_case_stage"])
        self.assertEqual("FIRST AMENDED COMPLAINT", report["document_title_literal"])
        self.assertEqual("51", report["court_document_number"])
        self.assertEqual("2024-06-11", report["documentary_filed_on"])
        self.assertEqual(91, report["captures"][0]["pdf_page_count"])
        self.assertEqual([1, 2, 3, 17], [s["page"] for s in report["court_stamp_observations"]])
        for stamp in report["court_stamp_observations"]:
            self.assertEqual("2:24-cv-04055-JXN-LDW", stamp["docket_full_literal"])
            self.assertEqual("2:24-cv-04055", stamp["docket"])
            self.assertEqual("-JXN-LDW", stamp["docket_suffix_literal"])
            self.assertEqual("51", stamp["document_number"])
            self.assertEqual("2024-06-11", stamp["filed_on"])
            self.assertEqual(91, stamp["page_count"])
            text = self.pages[stamp["page"]]
            start, end = stamp["page_text_stamp_span"]
            self.assertEqual(stamp["literal"], text[start:end])
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), stamp["page_text_sha256"])
        paragraph = report["passage_reviews"][-1]
        self.assertEqual(("LEGAL_ENTITY", 17, True),
                         (paragraph["purpose"], paragraph["page"], paragraph["supported"]))
        self.assertFalse(report["independent_earlier_legal_identity_supported"])
        self.assertIsNone(report["issuer_identity_fact"])
        self.assertEqual("UNMAPPED_NAME", report["registry_identity"]["resolution"])
        for field in ("issuer_attribution_verified", "adjudication_verified", "company_wide_liability_verified",
                      "current_case_status_verified", "historical_availability_reconstructed",
                      "publication_time_verified", "live_acceptance_verified", "registry_adoption_allowed",
                      "prediction_input", "scoring_applied"):
            self.assertFalse(report[field], field)
        self.assertEqual(0, report["http_requests"])

    def test_full_literal_suffix_and_date_mismatch_do_not_inherit_actual_case(self):
        for key, value in (("docket_full_literal", "2:24-cv-04055-JXN-OTHER"),
                           ("docket_full_literal", "2:24-cv-04055"),
                           ("filed_on", "2024-06-12")):
            with self.subTest(key=key, value=value):
                request = deepcopy(self.plan["request"])
                request[key] = value
                report = self.review(request=request)
                self.assertFalse(report["case_document_scope_supported"])
                self.assertFalse(report["document_stage_supported"])
                self.assertEqual("2:24-cv-04055-JXN-LDW",
                                 report["court_stamp_observations"][0]["docket_full_literal"])

    def test_full_selector_cannot_change_or_invent_its_base(self):
        request = deepcopy(self.plan["request"])
        for literal in ("2:24-cv-04056-JXN-LDW", "2:24-cv-04055-JXN-LDW-UNBOUNDED-SUFFIX-EXTRA",
                        "2:24-cv-04055-JXN LDW"):
            with self.subTest(literal=literal):
                request["docket_full_literal"] = literal
                with self.assertRaises(ValueError):
                    self.review(request=request)

    def test_base_only_compatibility_retains_observed_suffix_without_financial_binding(self):
        request = deepcopy(self.plan["request"])
        del request["docket_full_literal"]
        report = self.review(request=request)
        self.assertTrue(report["case_document_scope_supported"])
        self.assertEqual("-JXN-LDW", report["court_stamp_observations"][0]["docket_suffix_literal"])
        self.assertNotIn("docket_full_literal", report["scope"])
        self.assertFalse(report["issuer_attribution_verified"])
        self.assertFalse(report["registry_identity"]["legal_continuity_supported"])

    def test_actual_first_amended_title_is_neither_original_complaint_nor_judgment(self):
        for kind in ("COMPLAINT", "FINAL_JUDGMENT", "PROPOSED_FINAL_JUDGMENT"):
            with self.subTest(kind=kind):
                request = deepcopy(self.plan["request"])
                request["document_kind"] = kind
                report = self.review(request=request)
                self.assertTrue(report["case_document_scope_supported"])
                self.assertTrue(report["exact_case_entity_supported"])
                self.assertFalse(report["document_stage_supported"])
                self.assertEqual("UNKNOWN", report["recorded_case_stage"])
                self.assertFalse(report["adjudication_verified"])

    def test_changed_synthetic_title_with_matching_selected_passage_still_rejects_stage(self):
        pages = deepcopy(self.pages)
        pages[3] = pages[3].replace("FIRST AMENDED COMPLAINT", "SECOND AMENDED COMPLAINT")
        request = deepcopy(self.plan["request"])
        request["selected_passages"][2]["excerpt"] = "Defendant. SECOND AMENDED COMPLAINT"
        report = self.synthetic_decoder_review(pages, request=request)
        self.assertTrue(report["selected_passages_all_supported"])
        self.assertTrue(report["case_document_scope_supported"])
        self.assertTrue(report["exact_case_entity_supported"])
        self.assertFalse(report["document_stage_supported"])
        self.assertEqual("UNKNOWN", report["recorded_case_stage"])
        self.assertFalse(report["captures"][0]["actual_primary_capture"])

    def test_selected_page_cannot_borrow_first_page_case_document_date_or_count(self):
        for page, before, after in (
                (17, "2:24-cv-04055-JXN-LDW", "2:24-cv-04056-JXN-LDW"),
                (17, "2:24-cv-04055-JXN-LDW", "2:24-cv-04055-JXN-OTHER"),
                (3, "Document 51", "Document 52"),
                (17, "06/11/24", "06/12/24"),
                (17, "of 91", "of 92")):
            with self.subTest(page=page, after=after):
                pages = deepcopy(self.pages)
                self.assertIn(before, pages[page])
                pages[page] = pages[page].replace(before, after, 1)
                report = self.synthetic_decoder_review(pages)
                self.assertTrue(report["selected_passages_all_supported"])
                self.assertFalse(report["selected_document_stamps_consistent"])
                self.assertFalse(report["case_document_scope_supported"])
                self.assertFalse(report["document_stage_supported"])

    def test_missing_or_quoted_header_does_not_support_selected_paragraph(self):
        pages = deepcopy(self.pages)
        pages[17] = "Unstamped selected page.\n" + pages[17].split("II. Defendant Apple", 1)[-1]
        report = self.synthetic_decoder_review(pages)
        self.assertTrue(report["selected_passages_all_supported"])
        self.assertFalse(report["selected_document_stamps_consistent"])
        self.assertFalse(report["case_document_scope_supported"])
        self.assertIsNone(scope._document_stamp("x" * 801 + self.pages[1], 1))
        self.assertIsNone(scope._document_stamp(self.pages[1][:200] + self.pages[1][:200], 1))

    def test_changed_original_pin_is_rejected_before_decoder(self):
        captures = deepcopy(self.captures)
        captures[CAPTURE]["metadata"]["raw_response_sha256"] = "0" * 64
        with patch.object(scope, "_pdf_pages", side_effect=AssertionError("Do not decode changed bytes")):
            report = self.review(captures=captures)
        self.assertFalse(report["case_document_scope_supported"])
        self.assertEqual("ORIGINAL_BODY_PIN_MISMATCH_OR_BYTE_BOUND", report["captures"][0]["review_reason"])
        self.assertEqual([], report["court_stamp_observations"])


if __name__ == "__main__":
    unittest.main()
