"""Original court-PDF captions plus bounded synthetic cross-binding regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from market_checker_app.collectors.doj_case_caption_review import review_doj_case_caption

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "evidence/batch8_regulatory_20261015"
OPINION = "govinfo_case_opinion"
OPPOSITION = "doj_opposition_pdf"
ACTUAL_PINS = {OPINION: (1344697, "dfc68609ea10dbb6f2e86b99df9b797030c17fc3224e020bd33f8a40478b0efa"),
               OPPOSITION: (577500, "8d68256e4a356aff8a501eeaa887439185e97ea836e830c434adf51adf6d45c3")}


def pdf(lines_by_page):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for lines in lines_by_page:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        commands = ["BT /F1 9 Tf 30 750 Td"]
        for line in lines:
            line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({line}) Tj 0 -18 Td")
        commands.append("ET")
        stream = DecodedStreamObject(); stream.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "SYNTHETIC CONTRACT TEST ONLY"})
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def synthetic_lines(page=1, count=1):
    return ["UNITED STATES DISTRICT COURT", "DISTRICT OF NEW JERSEY", "UNITED STATES OF AMERICA, et al.,",
        "Plaintiffs,", "v.", "APPLE, INC.,", "Defendant.", "Civil Action No. 24-cv-4055 (JXN)(LDW)", "OPINION",
        f"Case 2:24-cv-04055-JXN-LDW Document 283 Filed 06/30/25 Page {page} of {count}"]


class DojCaseCaptionReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        plan = json.loads((OLD / "captured_review_plan.json").read_text())
        cls.actual = {key: {"metadata": row["metadata"], "body": (ROOT / row["body_ref"]["path"]).read_bytes()}
                      for key, row in plan["captures"].items() if key in ACTUAL_PINS}
        cls.page_texts = {key: [page.extract_text() or "" for page in PdfReader(BytesIO(row["body"]), strict=True).pages]
                          for key, row in cls.actual.items()}
        cls.actual_request = {"case_docket": "2:24-cv-04055", "court": "UNITED STATES DISTRICT COURT DISTRICT OF NEW JERSEY",
            "documents": [{"capture_id": OPPOSITION, "document_number": "106", "filed_on": "2024-09-12", "document_kind": "PARTY_OPPOSITION"},
                          {"capture_id": OPINION, "document_number": "283", "filed_on": "2025-06-30", "document_kind": "COURT_OPINION"}]}

    def review(self, request=None, captures=None, **kwargs):
        with patch("socket.create_connection", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No database")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("No real HTTP client")):
            return review_doj_case_caption(request or self.actual_request, captures or self.actual, **kwargs)

    def synthetic(self, pages=None):
        body = pdf(pages or [synthetic_lines()])
        request = deepcopy(self.actual_request); request["documents"] = [request["documents"][1]]
        packet = deepcopy(self.actual[OPINION]); packet["body"] = body
        packet["metadata"].update(raw_response_sha256=hashlib.sha256(body).hexdigest(), raw_response_bytes=len(body),
                                 synthetic_test_only=True)
        return request, {OPINION: packet}

    def test_actual_two_whole_originals_and_all_95_page_stamps_support_case_documents(self):
        report = self.review()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(2, report["supported_case_documents"])
        self.assertEqual(3, report["supported_caption_blocks"])
        self.assertTrue(report["same_case_corroborated_by_distinct_originals"])
        self.assertEqual(95, sum(len(d["page_reviews"]) for d in report["document_reviews"]))
        for key, (size, sha) in ACTUAL_PINS.items():
            self.assertEqual(size, len(self.actual[key]["body"]))
            self.assertEqual(sha, hashlib.sha256(self.actual[key]["body"]).hexdigest())

    def test_actual_party_spelling_and_role_variants_stay_in_their_own_page(self):
        report = self.review(); opposition, opinion = report["document_reviews"]
        self.assertTrue(opposition["caption_party_label_variants_observed"])
        first, certificate = opposition["captions"]
        self.assertEqual(1, first["page"]); self.assertEqual(62, certificate["page"])
        self.assertEqual("APPLE, INC.", first["party_labels"]["defendant"])
        self.assertEqual("APPLE INC.", certificate["party_labels"]["defendant"])
        self.assertEqual("Defendants", next(f["source_literal"] for f in certificate["field_facts"] if f["field"] == "caption.defendant.role"))
        self.assertEqual("APPLE, INC.,", opinion["captions"][0]["party_labels"]["defendant"])
        self.assertEqual("UNITED STATES OF AMERICA, ef at,", opinion["captions"][0]["party_labels"]["plaintiff"])
        self.assertFalse(first["named_party_list_complete"])
        self.assertFalse(opinion["captions"][0]["named_party_list_complete"])
        self.assertFalse(opposition["caption_variants_unified_as_aliases"])

    def test_every_actual_field_span_and_text_hash_match_the_original_page(self):
        report = self.review()
        for document in report["document_reviews"]:
            fields = [f for p in document["page_reviews"] for f in p["field_facts"]]
            fields += [f for c in document["captions"] for f in c["field_facts"]]
            fields += document["separate_order_references"]
            for fact in fields:
                text = self.page_texts[document["capture_id"]][fact["page"]-1]
                self.assertEqual(text[fact["start_character"]:fact["end_character"]], fact["source_literal"])
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), fact["page_text_sha256"])
                self.assertEqual(hashlib.sha256(fact["source_literal"].encode()).hexdigest(), fact["source_literal_sha256"])
                self.assertEqual(ACTUAL_PINS[document["capture_id"]][1], fact["original_pdf_sha256"])

    def test_abbreviated_opinion_docket_does_not_inherit_omitted_components(self):
        report = self.review(); opinion = report["document_reviews"][1]
        docket = opinion["captions"][0]["caption_docket_fields"][0]["value"]
        self.assertEqual("24-cv-4055", docket["literal_docket"])
        self.assertIsNone(docket["division_literal"]); self.assertIsNone(docket["suffix_literal"])
        self.assertEqual("(JXN)(LDW)", docket["parenthetical_literal"])
        self.assertFalse(docket["omitted_components_filled"])
        self.assertTrue(docket["compatible_with_same_page_stamped_base"])
        self.assertEqual(["2:24-cv-04055-JXN-LDW"], opinion["full_docket_literals_observed"])

    def test_party_assertion_opinion_and_order_reference_are_not_merits_or_separate_order(self):
        report = self.review(); opposition, opinion = report["document_reviews"]
        self.assertEqual("PARTY_FILING_CASE_FACTS", opposition["source_authority"])
        self.assertEqual("COURT_OPINION_CASE_FACTS", opinion["source_authority"])
        self.assertEqual(1, len(opinion["separate_order_references"]))
        self.assertEqual(33, opinion["separate_order_references"][0]["page"])
        for row in (report, opposition, opinion):
            self.assertFalse(row["original_separate_order_present"])
            self.assertFalse(row["merits_adjudication_verified"])
            self.assertFalse(row["current_disposition_verified"])
        self.assertFalse(report["party_assertions_are_court_findings"])

    def test_wrong_selected_case_document_date_or_court_never_binds_party_facts(self):
        for field, value in (("case_docket", "2:24-cv-04056"), ("court", "UNITED STATES DISTRICT COURT DISTRICT OF DELAWARE"),
                             ("document_number", "106"), ("filed_on", "2025-07-01")):
            request, captures = self.synthetic()
            if field in request: request[field] = value
            else: request["documents"][0][field] = value
            self.assertFalse(self.review(request, captures)["document_reviews"][0]["source_case_identity_supported"])

    def test_foreign_case_number_in_same_caption_is_not_stitched_to_header(self):
        lines = synthetic_lines(); lines[7] = "Civil Action No. 24-cv-9999 (JXN)(LDW)"
        request, captures = self.synthetic([lines])
        document = self.review(request, captures)["document_reviews"][0]
        self.assertFalse(document["source_case_identity_supported"])
        self.assertFalse(document["captions"][0]["case_identity_binding_supported"])

    def test_roles_split_across_pages_do_not_become_one_caption(self):
        first = synthetic_lines(1, 2); second = synthetic_lines(2, 2)
        first = [line for line in first if line not in {"Defendant.", "APPLE, INC.,"}]
        second = [line for line in second if line not in {"Plaintiffs,", "UNITED STATES OF AMERICA, et al.,"}]
        request, captures = self.synthetic([first, second])
        document = self.review(request, captures)["document_reviews"][0]
        self.assertFalse(document["source_case_identity_supported"])
        self.assertEqual([], document["captions"])

    def test_foreign_clause_between_role_blocks_is_not_a_party_name(self):
        lines = synthetic_lines(); lines.insert(5, "Case 1:25-cv-09999 Document 9 Filed 06/30/25 Page 1 of 1")
        request, captures = self.synthetic([lines])
        self.assertFalse(self.review(request, captures)["document_reviews"][0]["source_case_identity_supported"])

    def test_conflicting_page_stamp_docket_suffix_date_or_count_is_partial(self):
        for change in ("case", "suffix", "date", "page", "count"):
            first, second = synthetic_lines(1, 2), synthetic_lines(2, 2)
            if change == "case": second[-1] = second[-1].replace("04055", "04056")
            elif change == "suffix": second[-1] = second[-1].replace("JXN-LDW", "ABC-XYZ")
            elif change == "date": second[-1] = second[-1].replace("06/30/25", "07/01/25")
            elif change == "page": second[-1] = second[-1].replace("Page 2", "Page 1")
            else: second[-1] = second[-1].replace("of 2", "of 3")
            request, captures = self.synthetic([first, second])
            document = self.review(request, captures)["document_reviews"][0]
            self.assertFalse(document["source_case_identity_supported"])
            self.assertFalse(document["all_document_pages_stamp_consistent"])

    def test_two_stamps_on_a_page_are_ambiguous_not_silently_selected(self):
        lines = synthetic_lines(); lines.append(lines[-1].replace("04055", "04056"))
        request, captures = self.synthetic([lines])
        report = self.review(request, captures)
        self.assertFalse(report["document_reviews"][0]["source_case_identity_supported"])

    def test_repeated_same_original_is_not_independent_case_corroboration(self):
        request, captures = self.synthetic()
        clone = deepcopy(captures[OPINION]); clone["metadata"]["capture_id"] = "same_original_again"
        captures["same_original_again"] = clone
        request["documents"].append({**request["documents"][0], "capture_id": "same_original_again"})
        report = self.review(request, captures)
        self.assertEqual(2, report["supported_case_documents"])
        self.assertFalse(report["same_case_corroborated_by_distinct_originals"])

    def test_wrong_document_title_or_proposed_opinion_is_not_adopted(self):
        for title in ("ORDER", "PROPOSED OPINION"):
            lines = synthetic_lines(); lines[8] = title
            request, captures = self.synthetic([lines])
            self.assertFalse(self.review(request, captures)["document_reviews"][0]["source_case_identity_supported"])

    def test_earlier_cutoff_or_future_source_clock_never_decodes(self):
        request, captures = self.synthetic()
        with patch("market_checker_app.collectors.doj_case_caption_review._decode", side_effect=AssertionError("No future-source decode")):
            report = self.review(request, captures, reviewed_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
        self.assertEqual(0, report["supported_case_documents"])
        self.assertEqual("SOURCE_NOT_KNOWN_AT_REVIEW_CLOCK", report["source_reviews"][0]["reason"])
        future = datetime.now(timezone.utc)+timedelta(days=1)
        captures[OPINION]["metadata"]["observed_at"] = future.isoformat()
        with patch("market_checker_app.collectors.doj_case_caption_review._decode", side_effect=AssertionError("No future-source decode")):
            report = self.review(request, captures, reviewed_at=future+timedelta(days=1))
        self.assertEqual(0, report["supported_case_documents"])

    def test_publisher_hash_length_http_and_redirects_gate_original_bytes(self):
        for field, value in (("raw_response_sha256", "0"*64), ("raw_response_bytes", 1), ("http_status", 400),
                             ("automatic_redirects", True), ("archive_encoding", "base64"), ("content_encoding", "gzip"),
                             ("content_type", "text/html"), ("final_url", "https://www.govinfo.gov/other.pdf")):
            request, captures = self.synthetic(); captures[OPINION]["metadata"][field] = value
            with patch("market_checker_app.collectors.doj_case_caption_review._decode", side_effect=AssertionError("Invalid pin must not decode")):
                self.assertEqual(0, self.review(request, captures)["supported_case_documents"])

    def test_whole_withheld_body_cannot_be_restored_by_supplying_pdf_bytes(self):
        request, captures = self.synthetic()
        captures[OPINION]["metadata"].update(status="WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", public_raw_available=False)
        with patch("market_checker_app.collectors.doj_case_caption_review._decode", side_effect=AssertionError("Never inspect withheld body")):
            report = self.review(request, captures)
        self.assertEqual(0, report["supported_case_documents"])

    def test_sensitive_field_on_late_pdf_page_withholds_entire_document(self):
        pages = [synthetic_lines(1, 2), synthetic_lines(2, 2)+['passcode: SYNTHETIC_TEST_ONLY']]
        request, captures = self.synthetic(pages)
        report = self.review(request, captures)
        self.assertEqual(0, report["supported_case_documents"])
        self.assertEqual([], report["document_reviews"][0]["page_reviews"])
        self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", report["source_reviews"][0]["reason"])

    def test_missing_decoder_page_and_text_bounds_fail_closed(self):
        request, captures = self.synthetic()
        with patch.dict("sys.modules", {"pypdf": None}):
            report = self.review(request, captures)
        self.assertEqual("PDF_DECODER_UNAVAILABLE", report["source_reviews"][0]["reason"])
        request, captures = self.synthetic([synthetic_lines(1, 2), synthetic_lines(2, 2)])
        with patch("market_checker_app.collectors.doj_case_caption_review.MAX_PAGES", 1):
            self.assertEqual(0, self.review(request, captures)["supported_case_documents"])
        with patch("market_checker_app.collectors.doj_case_caption_review.MAX_TEXT_CHARACTERS", 20):
            self.assertEqual(0, self.review(request, captures)["supported_case_documents"])

    def test_tuple_packets_are_immutable_and_financial_identity_never_inferred(self):
        request, captures = self.synthetic(); request["issuer_cik"] = "0000320193"
        tuples = {k: (v["metadata"], v["body"]) for k, v in captures.items()}
        old_request, old_packets = deepcopy(request), deepcopy(tuples)
        before = datetime.now(timezone.utc); report = self.review(request, tuples)
        self.assertEqual(old_request, request); self.assertEqual(old_packets, tuples)
        self.assertGreaterEqual(datetime.fromisoformat(report["known_at"]), before)
        self.assertNotIn("issuer_cik", report["case_scope"])
        for flag in ("issuer_identity_verified", "financial_cik_identity_verified", "legal_continuity_verified",
                     "original_separate_order_present", "complete_current_docket_verified", "merits_adjudication_verified",
                     "final_judgment_verified", "current_disposition_verified", "registry_adoption_allowed",
                     "historical_availability_reconstructed", "scoring_applied", "prediction_input"):
            self.assertFalse(report[flag])
        self.assertEqual(0, report["http_requests"]); self.assertEqual(0, report["database_actions"])

    def test_request_limits_missing_capture_and_naive_clock_are_rejected(self):
        request, captures = self.synthetic()
        for modified in ({**request, "case_docket": "04055"}, {**request, "documents": []},
                         {**request, "documents": request["documents"]*4},
                         {**request, "documents": [{**request["documents"][0], "filed_on": "1925-06-30"}]}):
            with self.assertRaises(ValueError): self.review(modified, captures)
        with self.assertRaises(ValueError): self.review(request, captures, reviewed_at=datetime(2026, 10, 18))
        request["documents"][0]["capture_id"] = "missing_selected"
        self.assertEqual(0, self.review(request, captures)["supported_case_documents"])


if __name__ == "__main__":
    unittest.main()
