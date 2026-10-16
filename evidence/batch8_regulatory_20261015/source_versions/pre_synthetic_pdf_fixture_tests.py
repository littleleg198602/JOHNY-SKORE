"""Later actual Apple case documents; no source refetch, DB or adoption."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import patch

from market_checker_app.services.doj_case_posture_review import review_doj_case_posture

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/batch8_regulatory_20261015"
NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)  # Synthetic explicit test cutoff.
PDF_PINS = {
    "govinfo_case_opinion": (1344697, "dfc68609ea10dbb6f2e86b99df9b797030c17fc3224e020bd33f8a40478b0efa"),
    "doj_opposition_pdf": (577500, "8d68256e4a356aff8a501eeaa887439185e97ea836e830c434adf51adf6d45c3"),
}


def repin(packet, body, mime="text/html"):
    packet["body"] = body
    packet["metadata"].update(raw_response_sha256=hashlib.sha256(body).hexdigest(),
        raw_response_bytes=len(body), content_type=mime, origin="SYNTHETIC_TEST")


class Batch8DojCasePostureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request_base = json.loads((FOLDER / "request.json").read_text())
        plan = json.loads((FOLDER / "captured_review_plan.json").read_text())
        cls.captures_base = {key: {"metadata": row["metadata"],
            "body": (ROOT / row["body_ref"]["path"]).read_bytes() if row["body_available"] else None}
            for key, row in plan["captures"].items()}
        cls.records = json.loads((ROOT / "market_checker_app/data/verified_counterparty_identities.json").read_text())["records"]

    def setUp(self):
        self.request, self.captures = deepcopy(self.request_base), deepcopy(self.captures_base)

    def review(self, **kwargs):
        with patch("socket.create_connection", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No quota/store access")):
            return review_doj_case_posture(kwargs.pop("request", self.request), kwargs.pop("captures", self.captures),
                as_of=kwargs.pop("as_of", NOW), ingested_at=kwargs.pop("ingested_at", NOW),
                legal_records=kwargs.pop("legal_records", self.records), **kwargs)

    def test_actual_later_opposition_and_judicial_opinion_are_distinct_events(self):
        report = self.review()
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(2, report["supported_procedural_events"])
        self.assertEqual(1, report["court_opinions_supported"])
        self.assertEqual(0, report["original_court_orders_supported"])
        self.assertEqual("2025-06-30", report["latest_supported_documentary_event_day"])
        opposition, opinion = report["event_reviews"]
        self.assertTrue(opposition["party_opposition_filing_supported"])
        self.assertFalse(opposition["court_procedural_decision_supported"])
        self.assertEqual("2024-09-12", opposition["documentary_filed_on"])
        self.assertTrue(opinion["court_procedural_decision_supported"])
        self.assertEqual("MOTION_TO_DISMISS_DENIED", opinion["procedural_posture"])
        self.assertEqual("2025-06-30", opinion["document_dated_on"])
        self.assertEqual("283", opinion["selected_document_number"])
        self.assertNotIn("order_issued_on", opinion)

    def test_original_hashes_and_unavailable_source_bodies_are_exact(self):
        for key, (size, sha) in PDF_PINS.items():
            body = self.captures[key]["body"]
            self.assertEqual(size, len(body))
            self.assertEqual(sha, hashlib.sha256(body).hexdigest())
        self.assertIsNone(self.captures["njd_case_page"]["body"])
        self.assertIsNone(self.captures["njd_case_page"]["metadata"]["archive_path"])
        self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", self.captures["njd_case_page"]["metadata"]["status"])
        self.assertIsNone(self.captures["court_public_order"]["body"])
        self.assertIsNone(self.captures["court_public_order"]["metadata"].get("http_status"))

    def test_opinion_mentioning_accompanying_order_is_not_that_order(self):
        self.request["events"][1]["document_kind"] = "COURT_ORDER"
        report = self.review()
        event = report["event_reviews"][1]
        self.assertFalse(event["document_kind_supported"])
        self.assertFalse(event["original_court_order_supported"])
        self.assertFalse(event["procedural_event_supported"])
        self.assertEqual(0, report["original_court_orders_supported"])

    def test_party_opposition_cannot_become_a_court_decision(self):
        event = self.request["events"][0]
        event.update(document_kind="COURT_OPINION", posture="MOTION_TO_DISMISS_DENIED",
                     motion_document_number="86", document_dated_on="2024-09-12", judge_name="NEALS")
        row = self.review()["event_reviews"][0]
        self.assertFalse(row["judicial_authority_supported"])
        self.assertFalse(row["court_procedural_decision_supported"])
        self.assertFalse(row["procedural_event_supported"])

    def test_wrong_docket_court_document_or_filing_day_is_rejected(self):
        for key, value in [("docket", "2:24-cv-04056"), ("court", "UNITED STATES DISTRICT COURT DISTRICT OF DELAWARE"),
                           ("document_number", "284"), ("filed_on", "2025-07-01")]:
            with self.subTest(key=key):
                req = deepcopy(self.request)
                if key in {"docket", "court"}:
                    req[key] = value
                else:
                    req["events"][1][key] = value
                row = self.review(request=req)["event_reviews"][1]
                self.assertFalse(row["case_docket_and_filing_supported"])
                self.assertFalse(row["procedural_event_supported"])

    def test_caption_variants_never_become_issuer_alias_or_earlier_identity(self):
        report = self.review()
        for row in report["event_reviews"]:
            self.assertEqual("APPLE, INC.", row["caption_entity_name"])
            self.assertFalse(row["caption_literal_matches_issuer"])
            self.assertFalse(row["caption_variant_adopted_as_alias"])
            self.assertEqual("UNMAPPED_NAME", row["registry_identity"]["resolution"])
            self.assertFalse(row["registry_identity"]["case_date_supported"])
            self.assertFalse(row["issuer_attribution_verified"])
        for name in ("Apple", "Apple Inc.", "Apple Operations International Ltd."):
            req = deepcopy(self.request)
            req["events"][1]["caption_entity_name"] = name
            self.assertFalse(self.review(request=req)["event_reviews"][1]["exact_parties_supported"])

    def test_exact_plaintiff_and_motion_number_cannot_be_prefixes(self):
        req = deepcopy(self.request)
        req["plaintiff_name"] = "UNITED STATES"
        self.assertFalse(self.review(request=req)["event_reviews"][1]["exact_parties_supported"])
        for motion in ("8", "87"):
            req = deepcopy(self.request)
            req["events"][1]["motion_document_number"] = motion
            self.assertFalse(self.review(request=req)["event_reviews"][1]["procedural_event_supported"])

    def test_wrong_page_or_action_word_never_inherits_the_real_denial(self):
        for change in ("page", "action", "judge", "date"):
            req = deepcopy(self.request)
            event = req["events"][1]
            if change == "page":
                event["selected_passages"][4]["page"] = 2
            elif change == "action":
                event["posture"] = "MOTION_TO_DISMISS_GRANTED"
            elif change == "judge":
                event["judge_name"] = "NEA"
            else:
                event["selected_passages"][-1]["page"] = 1
            self.assertFalse(self.review(request=req)["event_reviews"][1]["procedural_event_supported"])

    def test_none_cutoff_uses_actual_now_and_explicit_past_never_decodes(self):
        before = datetime.now(timezone.utc)
        report = self.review(as_of=None, ingested_at=before)
        self.assertGreaterEqual(datetime.fromisoformat(report["as_of"]), before)
        self.assertTrue(report["report_available_at_cutoff"])
        self.assertEqual(2, report["supported_procedural_events"])
        with patch("market_checker_app.services.doj_case_posture_review._pdf_pages", side_effect=AssertionError("Future report must not decode")):
            report = self.review(as_of=NOW-timedelta(seconds=1))
        self.assertFalse(report["report_available_at_cutoff"])
        self.assertEqual(["REPORT_NOT_KNOWN_AT_CUTOFF"], report["missing_evidence"])
        self.assertEqual([], report["source_reviews"])

    def test_future_observation_and_known_clock_fail_closed(self):
        for key in ("started_at", "observed_at", "known_at"):
            captures = deepcopy(self.captures)
            captures["govinfo_case_opinion"]["metadata"][key] = (NOW+timedelta(seconds=1)).isoformat()
            row = self.review(captures=captures)["event_reviews"][1]
            self.assertFalse(row["procedural_event_supported"])

    def test_hash_size_status_redirect_and_encoded_substitute_guards(self):
        for key, value in [("raw_response_sha256", "0"*64), ("raw_response_bytes", 0), ("http_status", 400),
                           ("final_url", "https://www.govinfo.gov/another.pdf"), ("automatic_redirects", True), ("archive_encoding", "base64")]:
            captures = deepcopy(self.captures)
            captures["govinfo_case_opinion"]["metadata"][key] = value
            report = self.review(captures=captures)
            self.assertFalse(report["event_reviews"][1]["procedural_event_supported"])
            info = next(r for r in report["source_reviews"] if r["capture_id"] == "govinfo_case_opinion")
            self.assertFalse(info["original_body_pin_verified"])

    def test_whole_withheld_body_cannot_be_restored_by_passing_bytes(self):
        captures = {"njd_case_page": deepcopy(self.captures["njd_case_page"])}
        captures["njd_case_page"]["body"] = self.captures["govinfo_case_opinion"]["body"]
        with patch("market_checker_app.services.doj_case_posture_review._pdf_pages", side_effect=AssertionError("Withheld must not decode")):
            report = self.review(captures=captures)
        self.assertEqual(0, report["supported_procedural_events"])
        self.assertEqual("MISSING_OR_WHOLE_WITHHELD_ORIGINAL", report["source_reviews"][0]["review_reason"])

    def synthetic_order(self, title="ORDER", kind="COURT_ORDER"):
        self.request["events"] = [deepcopy(self.request["events"][1])]
        event = self.request["events"][0]
        event.update(document_kind=kind, document_number="999")
        event["selected_passages"] = [p for p in event["selected_passages"] if p["purpose"] not in {"PLAINTIFF", "DOCUMENT_TYPE"}]
        event["selected_passages"].extend([
            {"purpose": "PLAINTIFF", "page": 1, "excerpt": "UNITED STATES OF AMERICA, et al.,"},
            {"purpose": "DOCUMENT_TYPE", "page": 1, "excerpt": title}])
        for pin in event["selected_passages"]:
            pin["page"] = 1
        body = (f'<html><p>Case 2:24-cv-04055-JXN-LDW Document 999 Filed 06/30/25 Page 1 of 1</p>'
            f'<p>UNITED STATES DISTRICT COURT DISTRICT OF NEW JERSEY</p><p>UNITED STATES OF AMERICA, et al.,</p>'
            f'<p>APPLE, INC.,</p><p>Defendant.</p><h1>{title}</h1><p>NEALS, District Judge;</p>'
            f'<p>For the reasons set forth below, Apple\'s motion to dismiss (ECF No. 86) is DENIED.</p>'
            f'<p>6/30/2025</p></html>').encode()
        self.captures = {"govinfo_case_opinion": deepcopy(self.captures["govinfo_case_opinion"])}
        if kind == "DOJ_REPORT":
            repin(self.captures["govinfo_case_opinion"], body)
        else:
            # A real, in-memory one-page PDF marked synthetic; no new dependency
            # or modification of the original primary PDF fixture is needed.
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            text = re.sub(r"<[^>]+>", "\n", body.decode())
            commands = ["BT /F1 8 Tf 30 750 Td"]
            for line in (line.strip() for line in text.splitlines() if line.strip()):
                line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                commands.append(f"({line}) Tj 0 -18 Td")
            commands.append("ET")
            stream = DecodedStreamObject(); stream.set_data("\n".join(commands).encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
            writer.add_metadata({"/Title": "SYNTHETIC TEST ONLY"})
            output = BytesIO(); writer.write(output)
            repin(self.captures["govinfo_case_opinion"], output.getvalue(), mime="application/pdf")

    def test_actual_order_contract_is_procedural_and_proposed_order_is_not_issued(self):
        self.synthetic_order()
        report = self.review()
        self.assertTrue(report["event_reviews"][0]["original_court_order_supported"])
        self.assertEqual("2025-06-30", report["event_reviews"][0]["order_issued_on"])
        self.assertFalse(report["final_judgment_verified"])
        self.assertFalse(report["merits_adjudication_verified"])
        for title in ("PROPOSED ORDER", "DRAFT ORDER"):
            self.synthetic_order(title=title)
            self.assertFalse(self.review()["event_reviews"][0]["original_court_order_supported"])

    def test_doj_html_report_remains_a_report_without_original_court_order(self):
        self.synthetic_order(kind="DOJ_REPORT")
        packet = self.captures["govinfo_case_opinion"]
        packet["metadata"].update(requested_url="https://www.justice.gov/opa/pr/synthetic-case-report",
                                  final_url="https://www.justice.gov/opa/pr/synthetic-case-report")
        row = self.review()["event_reviews"][0]
        self.assertTrue(row["doj_reported_procedure_only"])
        self.assertFalse(row["original_court_order_supported"])
        self.assertFalse(row["court_procedural_decision_supported"])
        self.assertFalse(row["procedural_event_supported"])
        self.request["events"][0]["document_kind"] = "COURT_ORDER"
        self.assertFalse(self.review()["event_reviews"][0]["original_court_order_supported"])

    def test_missing_decoder_and_oversize_original_are_partial(self):
        with patch.dict("sys.modules", {"pypdf": None}):
            report = self.review()
        self.assertEqual(0, report["supported_procedural_events"])
        self.assertIn("PDF_DECODER_UNAVAILABLE", {r["review_reason"] for r in report["source_reviews"]})
        repin(self.captures["govinfo_case_opinion"], b"x"*4_000_001, mime="application/pdf")
        report = self.review()
        row = next(r for r in report["source_reviews"] if r["capture_id"] == "govinfo_case_opinion")
        self.assertEqual("ORIGINAL_BYTE_PIN_OR_SIZE_BOUND", row["review_reason"])

    def test_request_guards_and_no_adoption_liability_current_docket_or_score(self):
        original_request, original_records = deepcopy(self.request), deepcopy(self.records)
        report = self.review()
        for flag in ("current_complete_docket_verified", "original_complaint_rereviewed", "merits_adjudication_verified",
                     "final_judgment_verified", "issuer_attribution_verified", "company_wide_liability_verified",
                     "issuer_fine_verified", "issuer_conviction_verified", "registry_adoption_allowed",
                     "historical_availability_reconstructed", "publication_time_verified", "live_acceptance_verified", "scoring_applied", "prediction_input"):
            self.assertIs(False, report[flag], flag)
        self.assertEqual(0, report["http_requests"])
        self.assertEqual(original_request, self.request)
        self.assertEqual(original_records, self.records)
        with self.assertRaises(ValueError):
            self.review(captures={str(i): self.captures["govinfo_case_opinion"] for i in range(7)})
        with self.assertRaises(ValueError):
            self.review(as_of=NOW.replace(tzinfo=None))
        req = deepcopy(self.request)
        req["events"][1]["selected_passages"][0]["page"] = True
        with self.assertRaises(ValueError):
            self.review(request=req)


if __name__ == "__main__":
    unittest.main()
