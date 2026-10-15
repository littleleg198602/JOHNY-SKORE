"""Actual archived CRL facts plus explicitly synthetic legal-entity contracts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from evidence.batch3_healthcare_20261012.replay import capture
from market_checker_app.services.healthcare_product_scope_review import (
    CapturedHealthcareSource, FdaProductApplicationScope, SecOwnershipSelection,
    review_healthcare_product_scope,
)


ROOT = Path(__file__).resolve().parents[1]
REVIEWED = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)
COMPANY = "Synthetic Pfizer Subsidiary Ltd."
SCOPE = FdaProductApplicationScope("PFE", "0000078003", "BLA761081",
                                 "761081_2019_Orig1s000OtherActionLtrs.pdf", "2018-04-20", COMPANY,
                                 ("synthetic medicine",))
SELECTION = SecOwnershipSelection(0, 1, COMPANY, "parent", "2018-04-20")
BASE = "https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/"


def captured(body, url, clock, **changes):
    if not isinstance(body, bytes):
        body = json.dumps(body, separators=(",", ":")).encode()
    meta = {"capture_form": "ORIGINAL_HTTP_RESPONSE", "http_status": 200,
            "request_url": url, "response_url": url, "requested_at": clock, "observed_at": clock,
            "body_complete_within_byte_budget": True, "raw_publication_permitted": True,
            "body_bytes": len(body), "byte_budget": 4_000_000, "body_sha256": hashlib.sha256(body).hexdigest()}
    meta.update(changes)
    return CapturedHealthcareSource(meta, body)


def fda_body():
    return {"meta": {"last_updated": "2026-08-13", "results": {"skip": 0, "limit": 100, "total": 1}},
            "results": [{"company_name": COMPANY, "application_number": ["BLA 761081"],
                         "file_name": SCOPE.file_name, "letter_date": "04/20/2018", "letter_year": "2018",
                         "letter_type": "COMPLETE RESPONSE", "approval_status": "Approved",
                         "text": "Synthetic letter mentioning synthetic medicine; no product assignment."}]}


def parent_body():
    return b'''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:dei="http://xbrl.sec.gov/dei/2018">
      <xbrli:context id="parent"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000078003</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:startDate>2017-04-21</xbrli:startDate><xbrli:endDate>2018-04-20</xbrli:endDate></xbrli:period></xbrli:context>
      <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="parent">Pfizer Inc.</ix:nonNumeric>
      <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="parent">0000078003</ix:nonNumeric>
      <ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="parent">2018-04-20</ix:nonNumeric>
      <ix:nonNumeric name="dei:DocumentType" contextRef="parent">10-K</ix:nonNumeric></html>'''


def owner_body():
    return ("<html><h1>EXHIBIT 21</h1><h2>Subsidiaries of Pfizer Inc.</h2><p>As of April 20, 2018</p>"
            "<table><tr><th>Name</th><th>Jurisdiction</th></tr><tr><td>" + COMPANY +
            "</td><td>Synthetic jurisdiction</td></tr></table></html>").encode()


def fixtures():
    url = "https://api.fda.gov/transparency/crl.json?" + urlencode({
        "search": f'company_name:"{COMPANY}"', "limit": 100, "skip": 0})
    return {"fda_capture": captured(fda_body(), url, "2026-10-12T14:44:00+00:00"),
            "ownership_capture": captured(owner_body(), BASE + "synthetic-ex21.htm", "2026-10-12T14:40:00+00:00"),
            "issuer_capture": captured(parent_body(), BASE + "synthetic-parent.htm", "2026-10-12T14:39:00+00:00")}


def changed_json(capture, change):
    payload = json.loads(capture.body)
    change(payload)
    body = json.dumps(payload, separators=(",", ":")).encode()
    return changed_bytes(capture, body)


def changed_bytes(capture, body):
    return CapturedHealthcareSource(dict(capture.metadata, body_bytes=len(body),
                                         body_sha256=hashlib.sha256(body).hexdigest()), body)


class HealthcareProductScopeTests(unittest.TestCase):
    def setUp(self):
        self.inputs = fixtures()

    def review(self, **changes):
        args = dict(self.inputs, scope=SCOPE, ownership_selection=SELECTION, reviewed_at=REVIEWED)
        args.update(changes)
        return review_healthcare_product_scope(**args)

    def reject(self, code, **changes):
        result = self.review(**changes)
        self.assertEqual("WITHHOLD", result["decision"])
        self.assertIn(code, result["reasons"])
        self.assertIsNone(result["proposed_record"])
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertFalse(result["product_ownership_verified"])
        return result

    def test_synthetic_exact_legal_entity_candidate_does_not_certify_product_or_holder_ownership(self):
        result = self.review()
        self.assertEqual("PROPOSE_NARROW_LEGAL_ENTITY_FOR_OWNER_REVIEW", result["decision"])
        self.assertTrue(result["candidate_legal_bridge_complete"])
        self.assertEqual("LISTED_SUBSIDIARY_ONLY", result["ownership_facts"]["relationship"])
        self.assertEqual("2018-04-20", result["ownership_facts"]["as_of_date"])
        self.assertEqual("CRL_ADDRESSEE_ONLY", result["source_object_facts"]["company_field_role"])
        self.assertEqual("LETTER_TEXT_MENTION_ONLY", result["source_object_facts"]["product_mentions"][0]["role"])
        self.assertEqual(REVIEWED.isoformat(), result["proposed_record"]["known_at"])
        for field in ("issuer_attribution_allowed", "product_ownership_verified", "application_holder_role_inferred",
                      "historical_ownership_attribution_allowed", "historical_knowledge_reconstructed", "issuer_wide_scope",
                      "negative_issuer_completeness_verified", "live_acceptance_verified", "registry_modified",
                      "quota_modified", "score_changes", "prediction_input"):
            self.assertFalse(result[field])

    def test_source_object_survives_missing_ownership_and_identity_inputs(self):
        result = self.reject("OWNERSHIP_ORIGINAL_CAPTURE_MISSING", ownership_capture=None,
                             issuer_capture=None, ownership_selection=None)
        self.assertEqual(SCOPE.application_number, result["source_object_facts"]["application_number"])
        self.assertEqual("2026-08-13", result["source_object_facts"]["publisher_last_updated"])
        self.assertTrue(result["source_object_facts"]["exact_query_complete"])
        self.assertIn("ISSUER_ORIGINAL_CAPTURE_MISSING", result["reasons"])
        self.assertIn("EXACT_DATED_OWNERSHIP_SELECTION_MISSING", result["reasons"])

    def test_actual_five_event_objects_have_distinct_source_dates_and_exact_product_mentions(self):
        cases = [("fda_crl_pfizer_exact_phrase", "PFE", "0000078003", "BLA761081", "2018-04-20", "Pfizer Inc.", ("trastuzumab",), 0),
                 ("fda_crl_merck_exact_phrase", "MRK", "0000310158", "NDA204569", "2013-06-28", "Merck & Co., Inc.", ("suvorexant",), 0),
                 ("fda_crl_abbvie_exact_phrase", "ABBV", "0001551152", "NDA216962", "2023-03-17", "AbbVie Inc.", ("foscarbidopa", "foslevodopa"), 0),
                 ("fda_crl_abbvie_exact_phrase", "ABBV", "0001551152", "NDA216962", "2024-06-18", "AbbVie Inc.", ("foscarbidopa", "foslevodopa"), 1),
                 ("fda_crl_abbvie_exact_phrase", "ABBV", "0001551152", "BLA761459", "2026-04-22", "AbbVie Inc.", (), 2)]
        dates = []
        for key, ticker, cik, app, event_day, company, markers, index in cases:
            meta, body = capture(key)
            row = json.loads(body)["results"][index]
            scope = FdaProductApplicationScope(ticker, cik, app, row["file_name"], event_day, company, markers)
            with self.subTest(scope=scope):
                result = self.review(scope=scope, fda_capture=CapturedHealthcareSource(meta, body),
                                     ownership_capture=None, issuer_capture=None, ownership_selection=None)
                facts = result["source_object_facts"]
                self.assertEqual(event_day, facts["letter_date"])
                self.assertEqual(f"/results/{index}", facts["source_object_locator"])
                self.assertEqual(row["application_number"], facts["original_application_values"])
                self.assertEqual(meta["observed_at"], facts["original_capture_observed_at"])
                self.assertEqual("2026-08-13", facts["publisher_last_updated"])
                self.assertEqual(len(markers), len(facts["product_mentions"]))
                self.assertFalse(facts["structured_product_identity_available"])
                self.assertFalse(facts["product_ownership_proven_by_letter_text"])
                self.assertTrue(facts["exact_query_complete"])
                self.assertEqual("WITHHOLD", result["decision"])
                dates.append(facts["letter_date"])
        self.assertEqual(5, len(set(dates)))

    def test_actual_new_exhibit_is_later_than_original_fda_capture_not_historical_availability(self):
        p = ROOT / "evidence/batch7_healthcare_20261015"
        meta = json.loads((p / "sec_pfe_2017_ex21.metadata.json").read_bytes())
        body = (p / "sec_pfe_2017_ex21.body.html").read_bytes()
        self.assertEqual("61edde7790c9765d7108d7660eddd4a3ef8241abb1f855de42cbebf66beb831f", hashlib.sha256(body).hexdigest())
        fda_meta, fda_body = capture("fda_crl_pfizer_exact_phrase")
        issuer_meta, issuer_body = capture("sec_pfe_2017_10k")
        scope = FdaProductApplicationScope("PFE", "0000078003", "BLA761081", SCOPE.file_name,
                                          "2018-04-20", "Pfizer Inc.", ("trastuzumab",))
        result = self.reject("LEGAL_OWNER_EVIDENCE_NOT_AVAILABLE_BEFORE_ORIGINAL_FDA_OBJECT", scope=scope,
                             fda_capture=CapturedHealthcareSource(fda_meta, fda_body),
                             ownership_capture=CapturedHealthcareSource(meta, body),
                             issuer_capture=CapturedHealthcareSource(issuer_meta, issuer_body), ownership_selection=None)
        self.assertEqual("2018-04-20", result["source_object_facts"]["letter_date"])
        self.assertIn("EXACT_DATED_OWNERSHIP_SELECTION_MISSING", result["reasons"])
        self.assertEqual(meta["observed_at"], result["original_capture_pins"]["ownership"]["observed_at"])
        self.assertFalse(result["historical_knowledge_reconstructed"])

    def test_archived_record_does_not_inherit_file_year_or_current_approval_state(self):
        meta, body = capture("fda_crl_pfizer_exact_phrase")
        scope = FdaProductApplicationScope("PFE", "0000078003", "BLA761081", SCOPE.file_name,
                                          "2018-04-20", "Pfizer Inc.", ())
        result = self.review(scope=scope, fda_capture=CapturedHealthcareSource(meta, body), ownership_capture=None)
        facts = result["source_object_facts"]
        self.assertIn("2019", facts["file_name"])
        self.assertEqual("2018-04-20", facts["letter_date"])
        self.assertEqual("Approved", facts["approval_status_as_displayed"])
        self.assertFalse(facts["approval_status_is_event_day_state"])

    def test_multi_application_row_is_not_assigned_to_one_selected_product(self):
        source = changed_json(self.inputs["fda_capture"], lambda b: b["results"][0].update(application_number=["BLA 761081", "BLA 761082"]))
        self.reject("FDA_MULTI_APPLICATION_OBJECT_SCOPE_WITHHELD", fda_capture=source)

    def test_wrong_event_file_date_and_product_marker_do_not_select_nearby_object(self):
        for values in (("761081_OTHER.pdf", "2018-04-20", ("synthetic medicine",)),
                       (SCOPE.file_name, "2018-04-21", ("synthetic medicine",)),
                       (SCOPE.file_name, "2018-04-20", ("unmentioned brand",))):
            scope = FdaProductApplicationScope("PFE", "0000078003", SCOPE.application_number,
                                              values[0], values[1], COMPANY, values[2])
            with self.subTest(values=values):
                self.assertIsNone(self.review(scope=scope)["source_object_facts"])

    def test_duplicate_event_is_ambiguous_and_not_silently_first_selected(self):
        def duplicate(body):
            body["results"].append(deepcopy(body["results"][0]))
            body["meta"]["results"]["total"] = 2
        self.reject("FDA_EXACT_EVENT_OBJECT_MISSING_OR_AMBIGUOUS", fda_capture=changed_json(self.inputs["fda_capture"], duplicate))

    def test_partial_query_keeps_exact_object_facts_and_withholds_legal_proposal(self):
        source = changed_json(self.inputs["fda_capture"], lambda b: b["meta"]["results"].update(total=2))
        result = self.reject("FDA_QUERY_SCOPE_NOT_PROVEN_COMPLETE", fda_capture=source)
        self.assertIsNotNone(result["source_object_facts"])
        self.assertFalse(result["source_object_facts"]["exact_query_complete"])
        self.assertFalse(result["negative_issuer_completeness_verified"])

    def test_http400_and_404_empty_are_not_complete_negatives(self):
        source = self.inputs["fda_capture"]
        for status in (400, 404, 429, 302, True, 200.0):
            with self.subTest(status=status):
                result = self.reject("FDA_COMPLETE_HTTP_200_BODY_REQUIRED", fda_capture=CapturedHealthcareSource(
                    dict(source.metadata, http_status=status), b"{}"))
                self.assertIsNone(result["source_object_facts"])
                self.assertFalse(result["negative_issuer_completeness_verified"])

    def test_wrong_query_broad_filters_token_parameters_and_redirects_fail_without_pin_export(self):
        source = self.inputs["fda_capture"]
        for url in (source.metadata["request_url"] + "&api_key=synthetic_unsafe_value",
                    source.metadata["request_url"] + "&limit=1",
                    source.metadata["request_url"].replace("api.fda.gov", "untrusted.example"),
                    "https://api.fda.gov/transparency/crl.json?" + urlencode({"search": "company_name:Pfizer", "limit": 100, "skip": 0})):
            with self.subTest(url=url):
                result = self.review(fda_capture=CapturedHealthcareSource(dict(source.metadata, request_url=url, response_url=url), source.body))
                self.assertEqual("WITHHOLD", result["decision"])
                self.assertNotIn("fda", result["original_capture_pins"])
                self.assertNotIn("synthetic_unsafe_value", json.dumps(result))

    def test_changed_original_body_or_unbounded_metadata_are_not_accepted(self):
        source = self.inputs["fda_capture"]
        self.reject("FDA_ORIGINAL_BODY_HASH_LENGTH_OR_BOUND_MISMATCH", fda_capture=CapturedHealthcareSource(source.metadata, source.body + b"\n"))
        self.reject("FDA_UNSAFE_OR_UNBOUNDED_WHOLE_METADATA", fda_capture=CapturedHealthcareSource(dict(source.metadata, padding="x" * 65000), source.body))
        self.reject("FDA_ORIGINAL_BODY_HASH_LENGTH_OR_BOUND_MISMATCH", fda_capture=CapturedHealthcareSource(dict(source.metadata, byte_budget=1), source.body))

    def test_unavailable_unsafe_and_json_escaped_access_bodies_never_become_source_facts(self):
        source = self.inputs["fda_capture"]
        self.reject("FDA_WHOLE_BODY_WITHHELD_OR_UNAVAILABLE", fda_capture=CapturedHealthcareSource(dict(source.metadata, raw_publication_permitted=False), None))
        body = b'{"access_token":"synthetic_access_value_123456789"}'
        self.reject("FDA_UNSAFE_WHOLE_BODY_WITHHELD", fda_capture=changed_bytes(source, body))
        body = b'{"\\u0061ccess_token":"synthetic_access_value_123456789"}'
        result = self.reject("FDA_UNSAFE_DECODED_WHOLE_BODY_WITHHELD", fda_capture=changed_bytes(source, body))
        self.assertIsNone(result["source_object_facts"])
        self.assertNotIn("synthetic_access_value", json.dumps(result))

    def test_metadata_access_values_are_withheld_before_url_or_body_parsing(self):
        source = self.inputs["fda_capture"]
        self.reject("FDA_UNSAFE_OR_UNBOUNDED_WHOLE_METADATA", fda_capture=CapturedHealthcareSource(
            dict(source.metadata, authorization="Bearer synthetic_access_value_123456789"), source.body))

    def test_duplicate_json_fields_and_nonfinite_values_are_not_reinterpreted(self):
        source = self.inputs["fda_capture"]
        for body in (b'{"meta":{},"meta":{}}', b'{"meta":NaN}'):
            self.reject("FDA_ORIGINAL_JSON_INVALID", fda_capture=changed_bytes(source, body))

    def test_version_date_and_letter_year_conflicts_remain_rejected(self):
        for change in (lambda b: b["meta"].update(last_updated="2017-01-01"),
                       lambda b: b["meta"].update(last_updated="2027-01-01"),
                       lambda b: b["results"][0].update(letter_year="2019"),
                       lambda b: b["results"][0].update(letter_type="APPROVAL")):
            self.reject("FDA_LETTER_TYPE_OR_PUBLISHER_CLOCK_CONFLICT", fda_capture=changed_json(self.inputs["fda_capture"], change))

    def test_late_owner_or_issuer_capture_cannot_use_old_fiscal_day_as_knowledge(self):
        for key in ("ownership_capture", "issuer_capture"):
            source = self.inputs[key]
            later = "2026-10-14T23:30:00+00:00"
            with self.subTest(key=key):
                result = self.reject("LEGAL_OWNER_EVIDENCE_NOT_AVAILABLE_BEFORE_ORIGINAL_FDA_OBJECT", **{
                    key: CapturedHealthcareSource(dict(source.metadata, requested_at=later, observed_at=later), source.body)})
                self.assertIsNotNone(result["source_object_facts"])

    def test_capture_inside_fda_request_response_interval_is_still_too_late(self):
        fda = self.inputs["fda_capture"]
        fda = CapturedHealthcareSource(dict(fda.metadata, observed_at="2026-10-12T14:46:00+00:00"), fda.body)
        owner = self.inputs["ownership_capture"]
        owner = CapturedHealthcareSource(dict(owner.metadata, requested_at="2026-10-12T14:45:00+00:00",
                                              observed_at="2026-10-12T14:45:00+00:00"), owner.body)
        self.reject("LEGAL_OWNER_EVIDENCE_NOT_AVAILABLE_BEFORE_ORIGINAL_FDA_OBJECT", fda_capture=fda, ownership_capture=owner)

    def test_original_offsets_preserve_instant_and_bad_capture_clocks_fail(self):
        source = self.inputs["ownership_capture"]
        converted = CapturedHealthcareSource(dict(source.metadata, requested_at="2026-10-12T16:40:00+02:00",
                                                   observed_at="2026-10-12T16:40:00+02:00"), source.body)
        self.assertTrue(self.review(ownership_capture=converted)["candidate_legal_bridge_complete"])
        self.reject("OWNERSHIP_CAPTURE_NOT_AVAILABLE_AT_ACTUAL_REVIEW", ownership_capture=CapturedHealthcareSource(
            dict(source.metadata, requested_at="2026-10-12T14:41:00+00:00"), source.body))
        with self.assertRaises(ValueError):
            self.review(reviewed_at=datetime(2026, 10, 15))

    def test_different_accession_or_same_body_is_not_an_independent_ownership_document(self):
        owner = self.inputs["ownership_capture"]
        url = owner.metadata["request_url"].replace("000007800318000027", "000007800318000028")
        self.reject("OWNERSHIP_AND_PARENT_DOCUMENT_DISCLOSURE_PAIR_CONFLICT", ownership_capture=CapturedHealthcareSource(
            dict(owner.metadata, request_url=url, response_url=url), owner.body))
        self.reject("OWNERSHIP_AND_PARENT_DOCUMENT_DISCLOSURE_PAIR_CONFLICT", ownership_capture=self.inputs["issuer_capture"])

    def test_wrong_routing_cik_and_unavailable_owner_body_cannot_be_replaced_by_a_title(self):
        owner = self.inputs["ownership_capture"]
        url = owner.metadata["request_url"].replace("data/78003/", "data/310158/")
        self.reject("OWNERSHIP_EXACT_SAFE_PRIMARY_REFERENCE_REQUIRED", ownership_capture=CapturedHealthcareSource(
            dict(owner.metadata, request_url=url, response_url=url), owner.body))
        self.reject("OWNERSHIP_WHOLE_BODY_WITHHELD_OR_UNAVAILABLE", ownership_capture=CapturedHealthcareSource(
            dict(owner.metadata, status="WITHHELD_SENSITIVE_BODY"), b"<h1>Subsidiaries of Pfizer Inc.</h1>"))

    def test_parent_name_cik_period_must_pair_in_one_unsegmented_context(self):
        parent = self.inputs["issuer_capture"]
        for old, new in ((b">0000078003<", b">0000310158<"),
                         (b"contextRef=\"parent\">0000078003", b"contextRef=\"unpaired\">0000078003"),
                         (b">2018-04-20<", b">2017-12-31<")):
            with self.subTest(old=old):
                result = self.review(issuer_capture=changed_bytes(parent, parent.body.replace(old, new)))
                self.assertEqual("WITHHOLD", result["decision"])
                self.assertIsNotNone(result["source_object_facts"])

    def test_parent_context_dimensions_or_xml_entities_are_not_guessed_or_expanded(self):
        parent = self.inputs["issuer_capture"]
        segmented = parent.body.replace(b"</xbrli:entity>", b"<xbrli:segment/></xbrli:entity>")
        self.reject("PARENT_NAME_CIK_PERIOD_SAME_CONTEXT_CONFLICT", issuer_capture=changed_bytes(parent, segmented))
        self.reject("UNSUPPORTED_PARENT_XML_ENTITY_OR_DTD", issuer_capture=changed_bytes(parent, b'<!DOCTYPE html>'+parent.body))

    def test_direct_issuer_addressee_is_not_its_own_subsidiary_or_product_ownership_record(self):
        scope = FdaProductApplicationScope("PFE", "0000078003", SCOPE.application_number, SCOPE.file_name,
                                          SCOPE.letter_date, "Pfizer Inc.", SCOPE.product_markers)
        selection = SecOwnershipSelection(0, 1, "Pfizer Inc.", "parent", "2018-04-20")
        source = changed_json(self.inputs["fda_capture"], lambda b: b["results"][0].update(company_name="Pfizer Inc."))
        url = "https://api.fda.gov/transparency/crl.json?"+urlencode({"search":'company_name:"Pfizer Inc."',"limit":100,"skip":0})
        source = CapturedHealthcareSource(dict(source.metadata, request_url=url, response_url=url), source.body)
        self.reject("DIRECT_ISSUER_NAME_IS_NOT_SUBSIDIARY_OWNERSHIP_PROOF", scope=scope, ownership_selection=selection, fda_capture=source)

    def test_prior_point_ownership_day_is_not_extended_to_fda_letter_day(self):
        owner = self.inputs["ownership_capture"]
        parent = self.inputs["issuer_capture"]
        selection = SecOwnershipSelection(0, 1, COMPANY, "parent", "2017-12-31")
        body = owner.body.replace(b"April 20, 2018", b"December 31, 2017")
        result = self.reject("OWNERSHIP_POINT_DAY_DOES_NOT_PROVE_FDA_EVENT_DAY", ownership_selection=selection,
                             ownership_capture=changed_bytes(owner, body),
                             issuer_capture=changed_bytes(parent, parent.body.replace(b"2018-04-20", b"2017-12-31")))
        self.assertFalse(result["historical_ownership_attribution_allowed"])

    def test_wrong_row_duplicate_legal_entity_and_unattributed_company_title_are_not_proof(self):
        owner = self.inputs["ownership_capture"]
        for body in (owner.body.replace(COMPANY.encode(), b"Different Legal Company Ltd."),
                     owner.body.replace(b"</table>", b"<tr><td>"+COMPANY.encode()+b"</td><td>Other place</td></tr></table>"),
                     owner.body.replace(b"Subsidiaries of Pfizer Inc.", b"Subsidiaries of the Company")):
            with self.subTest(body_sha=hashlib.sha256(body).hexdigest()):
                self.assertEqual("WITHHOLD", self.review(ownership_capture=changed_bytes(owner, body))["decision"])

    def test_selected_table_and_row_indices_are_bounded_not_globbed(self):
        self.reject("SELECTED_OWNERSHIP_ROW_NOT_CAPTURED", ownership_selection=SecOwnershipSelection(5, 1, COMPANY, "parent", "2018-04-20"))
        with self.assertRaises(ValueError):
            self.review(ownership_selection=SecOwnershipSelection(-1, 1, COMPANY, "parent", "2018-04-20"))

    def test_same_original_sources_keep_snapshot_id_and_detached_outputs_at_later_ingestion(self):
        original = deepcopy(self.inputs)
        first = self.review()
        later = self.review(reviewed_at=REVIEWED+timedelta(days=2))
        self.assertEqual(first["proposed_record"]["record_id"], later["proposed_record"]["record_id"])
        self.assertNotEqual(first["ingested_at"], later["ingested_at"])
        first["source_object_facts"]["product_mentions"][0]["requested_marker"] = "mutated"
        self.assertEqual(original, self.inputs)
        self.assertEqual("synthetic medicine", self.review()["source_object_facts"]["product_mentions"][0]["requested_marker"])

    def test_invalid_scope_cannot_smuggle_name_only_ids_noncanonical_issuer_or_unsafe_marker(self):
        for scope in (FdaProductApplicationScope("UNKNOWN", "0000078003", SCOPE.application_number, SCOPE.file_name, SCOPE.letter_date, COMPANY),
                      FdaProductApplicationScope("PFE", "0000310158", SCOPE.application_number, SCOPE.file_name, SCOPE.letter_date, COMPANY),
                      FdaProductApplicationScope("PFE", "0000078003", None, SCOPE.file_name, SCOPE.letter_date, COMPANY),
                      FdaProductApplicationScope("PFE", "0000078003", "BLA 761081", SCOPE.file_name, SCOPE.letter_date, COMPANY)):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.review(scope=scope)
        from_list = FdaProductApplicationScope("PFE", "0000078003", SCOPE.application_number, SCOPE.file_name, SCOPE.letter_date, COMPANY, ["synthetic medicine"])
        self.assertIsInstance(from_list.product_markers, tuple)

    def test_pure_review_has_no_network_database_quota_or_registry_mutation(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")), \
                patch("market_checker_app.storage.scout_store.ScoutStore.__init__", side_effect=AssertionError("Unexpected DB")), \
                patch("pathlib.Path.write_bytes", side_effect=AssertionError("Unexpected filesystem write")), \
                patch("pathlib.Path.write_text", side_effect=AssertionError("Unexpected filesystem write")):
            self.assertTrue(self.review()["candidate_legal_bridge_complete"])

    def test_selected_table_cannot_borrow_an_earlier_other_parent_heading_or_point_day(self):
        source = self.inputs["ownership_capture"]
        body = source.body.replace(COMPANY.encode(), b"Other Synthetic Legal Entity Ltd.").replace(
            b"</html>", b"<h2>Subsidiaries of Different Parent Inc.</h2><p>As of April 20, 2018</p>"
            b"<table><tr><th>Name</th><th>Jurisdiction</th></tr><tr><td>" + COMPANY.encode() +
            b"</td><td>Synthetic jurisdiction</td></tr></table></html>")
        self.reject("SELECTED_TABLE_PARENT_AND_POINT_DAY_SCOPE_NOT_PROVEN",
                    ownership_capture=changed_bytes(source, body),
                    ownership_selection=SecOwnershipSelection(1, 1, COMPANY, "parent", "2018-04-20"))
        body = source.body.replace(b"</p><table>", b"</p><p>As of April 21, 2018</p><table>")
        self.reject("SELECTED_TABLE_PARENT_AND_POINT_DAY_SCOPE_NOT_PROVEN", ownership_capture=changed_bytes(source, body))

    def test_conflicting_exact_entity_rows_in_distinct_tables_are_ambiguous(self):
        source = self.inputs["ownership_capture"]
        body = source.body.replace(b"</html>", b"<table><tr><td>"+COMPANY.encode()+
                                   b"</td><td>Different jurisdiction</td></tr></table></html>")
        self.reject("EXACT_LEGAL_ENTITY_OWNERSHIP_ROW_MISSING_OR_AMBIGUOUS", ownership_capture=changed_bytes(source, body))

    def test_actual_review_plan_loads_exact_original_pins_and_preserves_withheld_object_facts(self):
        p = ROOT / "evidence/batch7_healthcare_20261015"
        plan = json.loads((p / "retained_pfe_review_plan.json").read_bytes())
        self.assertEqual("HEALTHCARE_PRODUCT_SCOPE", plan["kind"])
        inputs = {}
        for key, item in plan["captures"].items():
            raw = (ROOT / item["body_ref"]["path"]).read_bytes() if item["body_available"] else None
            if raw is not None:
                self.assertLessEqual(len(raw), 4_000_000)
                self.assertEqual(item["body_ref"]["bytes"], len(raw))
                self.assertEqual(item["body_ref"]["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(item["metadata"]["body_sha256"], hashlib.sha256(raw).hexdigest())
            inputs[key + "_capture"] = CapturedHealthcareSource(item["metadata"], raw)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP")), \
                patch("market_checker_app.storage.scout_store.ScoutStore.__init__", side_effect=AssertionError("Unexpected DB")):
            result = review_healthcare_product_scope(
                scope=FdaProductApplicationScope(**plan["request"]["scope"]),
                ownership_selection=None, reviewed_at=REVIEWED, **inputs)
        self.assertEqual("WITHHOLD", result["decision"])
        self.assertEqual("BLA761081", result["source_object_facts"]["application_number"])
        self.assertEqual("2018-04-20", result["source_object_facts"]["letter_date"])
        self.assertIn("LEGAL_OWNER_EVIDENCE_NOT_AVAILABLE_BEFORE_ORIGINAL_FDA_OBJECT", result["reasons"])
        self.assertFalse(result["product_ownership_verified"])

    def test_replay_is_read_only_and_original_capture_attempt_is_not_repeated(self):
        from evidence.batch7_healthcare_20261015.capture_ownership import main as resume_capture
        from evidence.batch7_healthcare_20261015.review_sources import build_review_report
        p = ROOT / "evidence/batch7_healthcare_20261015"
        report = json.loads((p / "source_review.json").read_bytes())
        meta_path = p / "sec_pfe_2017_ex21.metadata.json"
        before = meta_path.read_bytes()
        with patch("market_checker_app.services.ir_primary_scout_service.IRPrimaryHTTPClient.fetch",
                   side_effect=AssertionError("Reserved attempt repeated")), \
                patch("pathlib.Path.write_bytes", side_effect=AssertionError("Unexpected mutation")), \
                patch("pathlib.Path.write_text", side_effect=AssertionError("Unexpected mutation")), \
                patch("builtins.print"):
            resume_capture()
            replay = build_review_report(ingested_at=datetime.fromisoformat(report["ingested_at"]))
        self.assertEqual(before, meta_path.read_bytes())
        self.assertEqual(report["cases"], replay["cases"])
        self.assertEqual(report["original_prior_captures"], replay["original_prior_captures"])
        self.assertEqual(1, replay["bounded_sec_research_requests"])
        self.assertEqual(0, replay["proposal_count"])
        self.assertEqual(5, replay["coverage"]["source_object_facts_preserved"])


if __name__ == "__main__":
    unittest.main()
