"""Synthetic contract positives; retained real originals only prove WAIT_DATA."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from evidence.batch3_healthcare_20261012.replay import capture
from market_checker_app.services.fda_structured_product_archive import (
    FDA_ENDPOINT, FdaProductArchiveCapture, FdaStructuredProductSelection,
    review_fda_structured_product_archive,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)
NAME = "Synthetic Exact Applicant Inc."
SELECTION = FdaStructuredProductSelection("NDA", "000123", NAME, "PFE", "0000078003",
                                          "001", "TABLET;ORAL", (("SYNTHETIC COMPOUND", "10MG"),), "2026-10-14")
ISSUER_URL = "https://data.sec.gov/submissions/CIK0000078003.json"


def captured(body, url, clock, **changes):
    if not isinstance(body, bytes):
        body = json.dumps(body, separators=(",", ":")).encode()
    meta = {"capture_form": "ORIGINAL_HTTP_RESPONSE", "http_status": 200,
            "request_url": url, "response_url": url, "requested_at": clock, "observed_at": clock,
            "body_complete_within_byte_budget": True, "raw_publication_permitted": True,
            "body_bytes": len(body), "byte_budget": 4_000_000, "body_sha256": hashlib.sha256(body).hexdigest()}
    meta.update(changes)
    return FdaProductArchiveCapture(meta, body)


def payload():
    return {"meta": {"last_updated": "2026-10-14", "results": {"skip": 0, "limit": 100, "total": 1}},
            "results": [{"application_number": "NDA000123", "sponsor_name": NAME,
                         "products": [{"product_number": "001", "dosage_form": "TABLET;ORAL",
                                       "active_ingredients": [{"name": "SYNTHETIC COMPOUND", "strength": "10MG"}],
                                       "brand_name": "SYNTHETIC EXAMPLE"}]}]}


def fixtures():
    url = FDA_ENDPOINT + "?" + urlencode({"search": 'application_number:"NDA000123"', "limit": 100, "skip": 0})
    return {"structured_capture": captured(payload(), url, "2026-10-15T10:00:00+00:00"),
            "issuer_capture": captured({"cik": "78003", "name": NAME, "tickers": ["PFE"]},
                                       ISSUER_URL, "2026-10-15T09:59:00+00:00")}


def body_changed(source, body):
    return FdaProductArchiveCapture(dict(source.metadata, body_bytes=len(body),
                                         body_sha256=hashlib.sha256(body).hexdigest()), body)


def json_changed(source, edit):
    value = json.loads(source.body)
    edit(value)
    return body_changed(source, json.dumps(value, separators=(",", ":")).encode())


class StructuredFdaProductsTests(unittest.TestCase):
    def setUp(self):
        self.inputs = fixtures()

    def review(self, **changes):
        args = dict(self.inputs, selection=SELECTION, reviewed_at=NOW)
        args.update(changes)
        return review_fda_structured_product_archive(**args)

    def reject(self, reason, **changes):
        result = self.review(**changes)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["data_status"], "WAIT_DATA")
        self.assertIsNone(result["applicant_product_candidate"])
        self.assertIn(reason, result["reasons"])
        self.assertFalse(result["issuer_attribution_allowed"])
        return result

    def test_synthetic_positive_is_only_exact_application_applicant_snapshot(self):
        result = self.review()
        self.assertEqual(result["status"], "REVIEWABLE_APPLICANT_PRODUCT_SNAPSHOT")
        self.assertTrue(result["exact_applicant_issuer_bridge_supported"])
        row = result["structured_rows"][0]
        self.assertEqual((row["application_type"], row["application_number"], row["product_number"]), ("NDA", "000123", "001"))
        self.assertEqual(row["active_ingredients"], [{"name": "SYNTHETIC COMPOUND", "strength": "10MG"}])
        candidate = result["applicant_product_candidate"]
        self.assertEqual(candidate["role"], "APPLICATION_SPONSOR_APPLICANT")
        self.assertEqual(candidate["effective_from"], "2026-10-15")
        self.assertEqual(candidate["effective_to"], "2026-10-16")
        self.assertEqual(candidate["known_at"], NOW.isoformat())
        for key in ("legal_owner_verified", "corporate_parent_verified", "adopted", "historical_prediction_knowledge_available"):
            self.assertFalse(candidate[key])
        for key in ("issuer_attribution_allowed", "issuer_wide_scope", "live_acceptance_verified", "registry_modified",
                    "quota_modified", "score_changes", "prediction_input", "negative_issuer_completeness_verified"):
            self.assertFalse(result[key])

    def test_application_type_namespace_cannot_be_substituted(self):
        self.reject("EXACT_BOUNDED_APPLICATION_QUERY_REQUIRED", selection=replace(SELECTION, application_type="BLA"))
        source = self.inputs["structured_capture"]
        raw = source.body.replace(b"NDA000123", b"BLA000123")
        url = source.metadata["request_url"].replace("NDA000123", "BLA000123")
        self.assertTrue(self.review(selection=replace(SELECTION, application_type="BLA"),
                                   structured_capture=captured(raw, url, source.metadata["observed_at"]))[
                                       "exact_applicant_issuer_bridge_supported"])

    def test_leading_zero_and_product_number_types_are_preserved(self):
        source = json_changed(self.inputs["structured_capture"], lambda p: p["results"][0].update(application_number="NDA123"))
        self.reject("APPLICATION_TYPE_NUMBER_OR_QUERY_SCOPE_CONFLICT", structured_capture=source)
        for number in (1, "1", "01", "0001"):
            with self.subTest(number=number):
                source = json_changed(self.inputs["structured_capture"], lambda p: p["results"][0]["products"][0].update(product_number=number))
                self.reject("EXACT_PRODUCT_NUMBER_NOT_CAPTURED", structured_capture=source)

    def test_every_product_keeps_its_form_and_entire_combination_strengths(self):
        def edit(p):
            other = deepcopy(p["results"][0]["products"][0])
            other.update(product_number="002", dosage_form="SOLUTION;INJECTION", active_ingredients=[
                {"name": "SYNTHETIC FIRST", "strength": "1MG/ML"}, {"name": "SYNTHETIC SECOND", "strength": "2MG/ML"}])
            p["results"][0]["products"].append(other)
        source = json_changed(self.inputs["structured_capture"], edit)
        selection = replace(SELECTION, product_number="002", dosage_form="SOLUTION;INJECTION",
                            active_ingredients=[["SYNTHETIC FIRST", "1MG/ML"], ["SYNTHETIC SECOND", "2MG/ML"]])
        result = self.review(structured_capture=source, selection=selection)
        self.assertEqual(len(result["structured_rows"]), 2)
        self.assertEqual(result["applicant_product_candidate"]["structured_product"]["product_number"], "002")
        self.reject("EXACT_APPLICANT_FORM_OR_INGREDIENT_STRENGTH_CONFLICT", structured_capture=source,
                    selection=replace(selection, active_ingredients=(("SYNTHETIC FIRST", "1MG/ML"),)))

    def test_product_form_strength_sponsor_are_exact_not_fuzzy(self):
        for changes in ({"dosage_form": "Tablet;oral"}, {"active_ingredients": (("SYNTHETIC COMPOUND", "10 MG"),)},
                        {"sponsor_name": "Synthetic Exact Applicant"}):
            with self.subTest(changes=changes):
                self.reject("EXACT_APPLICANT_FORM_OR_INGREDIENT_STRENGTH_CONFLICT", selection=replace(SELECTION, **changes))
        self.reject("SELECTED_PRODUCT_NUMBER_NOT_IN_ORIGINAL_APPLICATION", selection=replace(SELECTION, product_number="002"))

    def test_no_text_brand_or_manufacturer_annotation_can_replace_structured_products(self):
        for edit, reason in ((lambda p: p["results"][0].pop("products"), "ORIGINAL_STRUCTURED_PRODUCT_ROWS_MISSING_OR_UNBOUNDED"),
                             (lambda p: p["results"][0].pop("sponsor_name"), "EXACT_BOUNDED_STRUCTURED_TEXT_REQUIRED")):
            source = json_changed(self.inputs["structured_capture"], edit)
            source = json_changed(source, lambda p: p["results"][0].update(text="Synthetic product 001 TABLET;ORAL 10MG",
                        openfda={"manufacturer_name": [NAME], "brand_name": ["SYNTHETIC COMPOUND"]}))
            self.reject(reason, structured_capture=source)

    def test_missing_selection_imports_source_facts_without_guessing_product(self):
        result = self.reject("EXACT_PRODUCT_FORM_STRENGTH_VERSION_SELECTION_MISSING",
                             selection=replace(SELECTION, product_number=None, dosage_form=None, active_ingredients=(), source_version=None))
        self.assertEqual(len(result["structured_rows"]), 1)

    def test_missing_issuer_does_not_erase_complete_structured_source_roles(self):
        result = self.reject("ISSUER_ORIGINAL_CAPTURE_MISSING", issuer_capture=None)
        self.assertEqual(result["structured_rows"][0]["source_sponsor_name"], NAME)

    def test_missing_structured_capture_and_metadata_only_body_are_wait_data_before_parser(self):
        self.reject("STRUCTURED_ORIGINAL_CAPTURE_MISSING", structured_capture=None)
        with patch("market_checker_app.services.fda_structured_product_archive._json", side_effect=AssertionError("must not parse")):
            self.reject("STRUCTURED_WHOLE_BODY_UNAVAILABLE", structured_capture=FdaProductArchiveCapture({"status": "MISSING_BODY"}, None))

    def test_body_withheld_descriptor_does_not_parse_or_substitute(self):
        source = self.inputs["structured_capture"]
        with patch("market_checker_app.services.fda_structured_product_archive._json", side_effect=AssertionError("must not parse")):
            for changes in ({"public_raw_available": False}, {"raw_publication_permitted": False},
                            {"retention": "WITHHELD_SENSITIVE_BODY"}, {"status": "WITHHELD_UNSAFE"}):
                self.reject("STRUCTURED_WHOLE_BODY_UNAVAILABLE", structured_capture=replace(source, metadata=dict(source.metadata, **changes)))

    def test_hash_length_budget_and_projected_capture_are_rejected(self):
        source = self.inputs["structured_capture"]
        for changes in ({"body_sha256": "0" * 64}, {"body_bytes": len(source.body) - 1}, {"body_bytes": True},
                        {"byte_budget": 4_000_001}, {"byte_budget": len(source.body) - 1}):
            self.reject("STRUCTURED_ORIGINAL_HASH_LENGTH_OR_BOUND_MISMATCH",
                        structured_capture=replace(source, metadata=dict(source.metadata, **changes)))
        self.reject("STRUCTURED_ORIGINAL_UNPROJECTED_CAPTURE_REQUIRED", structured_capture=replace(source,
                    metadata=dict(source.metadata, capture_form="PROJECTED_ROWS")))

    def test_original_whole_body_over_four_mb_never_parses(self):
        source = body_changed(self.inputs["structured_capture"], b"x" * 4_000_001)
        with patch("market_checker_app.services.fda_structured_product_archive._json", side_effect=AssertionError("must not parse")):
            self.reject("STRUCTURED_ORIGINAL_HASH_LENGTH_OR_BOUND_MISMATCH", structured_capture=source)

    def test_unsafe_original_and_escaped_decoded_bodies_are_wholly_withheld(self):
        original = body_changed(self.inputs["structured_capture"], b'{"password":"syntheticcredential123"}')
        with patch("market_checker_app.services.fda_structured_product_archive._json", side_effect=AssertionError("must not parse")):
            self.reject("STRUCTURED_UNSAFE_WHOLE_BODY_WITHHELD", structured_capture=original)
        encoded = body_changed(self.inputs["structured_capture"], b'{"pass\\u0077ord":"syntheticcredential123"}')
        self.reject("UNSAFE_DECODED_WHOLE_BODY_WITHHELD", structured_capture=encoded)

    def test_unsafe_or_unbounded_metadata_has_no_exported_pin(self):
        source = self.inputs["structured_capture"]
        for changes in ({"password": "syntheticcredential123"}, {"padding": "x" * 64_001}):
            result = self.reject("STRUCTURED_UNSAFE_OR_UNBOUNDED_WHOLE_METADATA",
                                 structured_capture=replace(source, metadata=dict(source.metadata, **changes)))
            self.assertNotIn("structured", result["source_captures"])

    def test_original_json_duplicates_nonfinite_and_nested_invalid_are_rejected(self):
        for body, reason in ((b'{"meta":{},"meta":{}}', "DUPLICATE_ORIGINAL_JSON_FIELD"),
                             (b'{"meta":NaN}', "NONFINITE_ORIGINAL_JSON_VALUE"),
                             (b'{"unused":1e999}', "NONFINITE_ORIGINAL_JSON_VALUE"),
                             (b'{"unused":"\\ud800"}', "ORIGINAL_STRUCTURED_JSON_INVALID"),
                             (b"[" * 2000 + b"]" * 2000, "ORIGINAL_STRUCTURED_JSON_NESTING_EXCEEDED"),
                             (b"[]", "ORIGINAL_STRUCTURED_OBJECT_REQUIRED")):
            self.reject(reason, structured_capture=body_changed(self.inputs["structured_capture"], body))

    def test_nested_string_punctuation_does_not_create_structure_or_change_source_version_id(self):
        source = json_changed(self.inputs["structured_capture"], lambda p: p.update(
            description="[" * 100 + '\\"synthetic quoted content' + "]" * 100))
        result = self.review(structured_capture=source)
        self.assertEqual(result["status"], "REVIEWABLE_APPLICANT_PRODUCT_SNAPSHOT")
        self.assertNotEqual(result["applicant_product_candidate"]["record_id"],
                            self.review()["applicant_product_candidate"]["record_id"])
        self.assertEqual(result["source_version"]["exact_original_body_version_sha256"], source.metadata["body_sha256"])

    def test_publisher_total_and_bounds_cannot_be_boolean_negative_or_inconsistent(self):
        for changes in ({"limit": 99}, {"total": 0}, {"skip": True}, {"total": -1}, {"limit": True}):
            source = json_changed(self.inputs["structured_capture"], lambda p: p["meta"]["results"].update(**changes))
            self.reject("PUBLISHER_QUERY_TOTAL_OR_LIMIT_CONFLICT", structured_capture=source)

    def test_duplicate_application_product_and_ingredient_are_ambiguous(self):
        edits = [(lambda p: p["results"].append(deepcopy(p["results"][0])), "DUPLICATE_APPLICATION_ROWS_AMBIGUOUS"),
                 (lambda p: p["results"][0]["products"].append(deepcopy(p["results"][0]["products"][0])), "DUPLICATE_PRODUCT_NUMBER_AMBIGUOUS"),
                 (lambda p: p["results"][0]["products"][0]["active_ingredients"].append(
                     {"name": "SYNTHETIC COMPOUND", "strength": "20MG"}), "DUPLICATE_ACTIVE_INGREDIENT_AMBIGUOUS")]
        for edit, reason in edits:
            source = json_changed(self.inputs["structured_capture"], edit)
            if reason == "DUPLICATE_APPLICATION_ROWS_AMBIGUOUS":
                source = json_changed(source, lambda p: p["meta"]["results"].update(total=2))
            self.reject(reason, structured_capture=source)

    def test_malformed_or_unbounded_product_and_ingredient_rows(self):
        edits = [(lambda p: p["results"][0].update(products=[None]), "MALFORMED_STRUCTURED_PRODUCT_ROW"),
                 (lambda p: p["results"][0].update(products=[{}] * 1001), "ORIGINAL_STRUCTURED_PRODUCT_ROWS_MISSING_OR_UNBOUNDED"),
                 (lambda p: p["results"][0]["products"][0].update(active_ingredients=[]), "STRUCTURED_INGREDIENT_STRENGTH_ROWS_REQUIRED"),
                 (lambda p: p["results"][0]["products"][0].update(active_ingredients=[{}] * 51), "STRUCTURED_INGREDIENT_STRENGTH_ROWS_REQUIRED"),
                 (lambda p: p["results"][0]["products"][0].update(active_ingredients=[{"name": "SYNTHETIC COMPOUND"}]), "EXACT_BOUNDED_STRUCTURED_TEXT_REQUIRED")]
        for edit, reason in edits:
            self.reject(reason, structured_capture=json_changed(self.inputs["structured_capture"], edit))

    def test_version_must_be_exact_canonical_and_not_after_original_observation(self):
        self.reject("SELECTED_SOURCE_VERSION_CONFLICT", selection=replace(SELECTION, source_version="2026-10-13"))
        for value, reason in (("2026-10-16", "PUBLISHER_VERSION_AFTER_ORIGINAL_OBSERVATION"),
                              ("20261014", "EXACT_PUBLISHER_VERSION_DAY_REQUIRED"), (None, "EXACT_PUBLISHER_VERSION_DAY_REQUIRED")):
            self.reject(reason, structured_capture=json_changed(self.inputs["structured_capture"], lambda p: p["meta"].update(last_updated=value)))

    def test_incomplete_traversal_keeps_exact_source_fact_but_not_candidate(self):
        source = json_changed(self.inputs["structured_capture"], lambda p: p["meta"]["results"].update(total=101))
        result = self.reject("APPLICATION_QUERY_TRAVERSAL_NOT_PROVEN_COMPLETE", structured_capture=source)
        self.assertEqual(len(result["structured_rows"]), 1)
        self.assertFalse(result["source_version"]["exact_query_complete"])

    def test_complete_empty_and_http_errors_never_certify_negative_product_or_issuer(self):
        source = json_changed(self.inputs["structured_capture"], lambda p: (p.update(results=[]), p["meta"]["results"].update(total=0)))
        self.reject("NO_STRUCTURED_APPLICATION_PRODUCT_ROWS", structured_capture=source)
        for status in (400, 404, 429):
            self.reject("STRUCTURED_COMPLETE_HTTP200_REQUIRED", structured_capture=replace(source, metadata=dict(source.metadata, http_status=status)))

    def test_wrong_query_redirect_scope_duplicate_and_credential_keys_have_no_candidate(self):
        source = self.inputs["structured_capture"]
        for query in ({"search": 'application_number:"NDA000123" OR sponsor_name:"Other"', "limit": 100},
                      {"search": 'application_number:"NDA000123"', "limit": 101},
                      {"search": 'application_number:"NDA000123"', "limit": 100, "skip": 1}):
            url = FDA_ENDPOINT + "?" + urlencode(query)
            self.reject("EXACT_BOUNDED_APPLICATION_QUERY_REQUIRED", structured_capture=replace(source, metadata=dict(source.metadata, request_url=url, response_url=url)))
        for tail in ("&limit=100", "&api_key=short", "&unknown=1"):
            url = source.metadata["request_url"] + tail
            result = self.reject("STRUCTURED_SAFE_EXPLICIT_SOURCE_REFERENCE_REQUIRED", structured_capture=replace(source, metadata=dict(source.metadata, request_url=url, response_url=url)))
            self.assertNotIn("structured", result["source_captures"])
        self.reject("STRUCTURED_ORIGINAL_SOURCE_URL_CONFLICT", structured_capture=replace(source, metadata=dict(source.metadata, response_url=FDA_ENDPOINT)))
        for url in (source.metadata["request_url"].replace("/drug/", "/\ndrug/"),
                    source.metadata["request_url"].replace("api.fda.gov", "api.fda.gov.example.org")):
            self.reject("STRUCTURED_SAFE_EXPLICIT_SOURCE_REFERENCE_REQUIRED",
                        structured_capture=replace(source, metadata=dict(source.metadata, request_url=url, response_url=url)))

    def test_wrong_dataset_is_rejected_before_json_or_text_extraction(self):
        source = self.inputs["structured_capture"]
        url = source.metadata["request_url"].replace("/drug/drugsfda.json", "/transparency/crl.json")
        with patch("market_checker_app.services.fda_structured_product_archive._json", side_effect=AssertionError("must not parse")):
            self.reject("RETAINED_SOURCE_IS_NOT_STRUCTURED_DRUGSFDA_DATASET", structured_capture=replace(source, metadata=dict(source.metadata, request_url=url, response_url=url)))

    def test_wrong_sec_name_cik_ticker_does_not_establish_applicant_bridge(self):
        for changes in ({"name": "Synthetic Exact Applicant, Inc."}, {"name": "Synthetic Exact Applicant"},
                        {"cik": "999"}, {"cik": True}, {"tickers": ["MRK"]}, {"tickers": ["PFE", "PFE"]}):
            self.reject("EXACT_APPLICANT_LEGAL_ISSUER_NAME_CIK_TICKER_BRIDGE_MISSING",
                        issuer_capture=json_changed(self.inputs["issuer_capture"], lambda p: p.update(**changes)))

    def test_annual_cover_or_subsidiary_title_is_not_direct_current_issuer_bridge(self):
        source = self.inputs["issuer_capture"]
        url = "https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/synthetic-ex21.htm"
        source = captured(b"<h1>Subsidiaries of Synthetic Exact Applicant Inc.</h1>", url, source.metadata["observed_at"])
        self.reject("INDEPENDENT_DIRECT_NAMED_ISSUER_SNAPSHOT_REQUIRED", issuer_capture=source)

    def test_late_same_day_or_prior_day_bridge_cannot_backdate_applicant_role(self):
        source = self.inputs["issuer_capture"]
        for clock in ("2026-10-15T10:00:01+00:00", "2026-10-15T10:00:00+00:00", "2026-10-14T09:59:00+00:00"):
            self.reject("ISSUER_BRIDGE_NOT_ACTUALLY_KNOWN_BEFORE_ORIGINAL_FDA_SOURCE",
                        issuer_capture=replace(source, metadata=dict(source.metadata, requested_at=clock, observed_at=clock)))
        self.reject("ISSUER_BRIDGE_NOT_ACTUALLY_KNOWN_BEFORE_ORIGINAL_FDA_SOURCE",
                    issuer_capture=replace(source, metadata=dict(source.metadata, known_at="2026-10-15T10:00:01+00:00")))

    def test_late_during_request_response_interval_is_not_earlier_knowledge(self):
        source = self.inputs["structured_capture"]
        source = replace(source, metadata=dict(source.metadata, requested_at="2026-10-15T09:58:00+00:00"))
        self.reject("ISSUER_BRIDGE_NOT_ACTUALLY_KNOWN_BEFORE_ORIGINAL_FDA_SOURCE", structured_capture=source)

    def test_future_naive_and_inverted_original_clocks_are_rejected(self):
        source = self.inputs["structured_capture"]
        for changes, reason in (({"observed_at": "2026-10-16T10:00:00+00:00"}, "STRUCTURED_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT"),
                                ({"requested_at": "2026-10-15T11:00:00+00:00"}, "STRUCTURED_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT"),
                                ({"known_at": "2026-10-15T09:00:00+00:00"}, "STRUCTURED_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT"),
                                ({"requested_at": "2026-10-15T10:00:00"}, "AWARE_CAPTURE_KNOWLEDGE_CLOCK_REQUIRED")):
            self.reject(reason, structured_capture=replace(source, metadata=dict(source.metadata, **changes)))
        with self.assertRaises(ValueError):
            self.review(reviewed_at=NOW.replace(tzinfo=None))

    def test_clock_offsets_preserve_actual_utc_instant(self):
        source = self.inputs["structured_capture"]
        result = self.review(structured_capture=replace(source, metadata=dict(source.metadata,
                             requested_at="2026-10-15T12:00:00+02:00", observed_at="2026-10-15T12:00:00+02:00")))
        self.assertEqual(result["status"], "REVIEWABLE_APPLICANT_PRODUCT_SNAPSHOT")
        self.assertEqual(result["source_captures"]["structured"]["observed_at"], "2026-10-15T10:00:00+00:00")

    def test_snapshot_ids_are_stable_and_outputs_detached_without_changing_ingestion_time(self):
        first = self.review()
        second = self.review(reviewed_at=NOW + timedelta(days=1))
        self.assertEqual(first["applicant_product_candidate"]["record_id"], second["applicant_product_candidate"]["record_id"])
        first["structured_rows"][0]["active_ingredients"][0]["strength"] = "changed consumer data"
        self.assertEqual(second["structured_rows"][0]["active_ingredients"][0]["strength"], "10MG")
        self.assertNotEqual(first["ingested_at"], second["ingested_at"])
        self.assertEqual(self.inputs["structured_capture"].body, fixtures()["structured_capture"].body)

    def test_invalid_exact_selection_is_not_silently_normalized(self):
        cases = ({"application_type": []}, {"application_type": "nda"}, {"application_number": "123"},
                 {"application_number": 123}, {"product_number": 1}, {"issuer_cik": "78003"}, {"issuer_ticker": "pfe"},
                 {"active_ingredients": (("A", "1MG"), ("A", "2MG"))}, {"sponsor_name": "password=syntheticcredential123"})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.review(selection=replace(SELECTION, **changes))

    def test_actual_five_retained_crl_events_are_not_structured_product_objects(self):
        cases = (("fda_crl_pfizer_exact_phrase", "BLA", "761081", "Pfizer Inc.", "PFE", "0000078003", 1),
                 ("fda_crl_merck_exact_phrase", "NDA", "204569", "Merck & Co., Inc.", "MRK", "0000310158", 1),
                 ("fda_crl_abbvie_exact_phrase", "NDA", "216962", "AbbVie Inc.", "ABBV", "0001551152", 3))
        count = 0
        for key, kind, number, name, ticker, cik, rows in cases:
            meta, raw = capture(key)
            count += len(json.loads(raw)["results"])
            self.assertEqual(len(json.loads(raw)["results"]), rows)
            result = self.reject("RETAINED_SOURCE_IS_NOT_STRUCTURED_DRUGSFDA_DATASET",
                                 selection=FdaStructuredProductSelection(kind, number, name, ticker, cik),
                                 structured_capture=FdaProductArchiveCapture(meta, raw), issuer_capture=None)
            self.assertEqual(result["structured_rows"], [])
            self.assertEqual(result["source_captures"]["structured"]["body_sha256"], meta["body_sha256"])
        self.assertEqual(count, 5)

    def test_actual_capture_plan_uses_unchanged_raw_pins_and_missing_structured_body(self):
        path = ROOT / "evidence/batch8_healthcare_20261015/retained_pfe_review_plan.json"
        plan = json.loads(path.read_text())
        captures = {}
        for key, item in plan["captures"].items():
            raw = (ROOT / item["body_ref"]["path"]).read_bytes() if item["body_available"] else None
            if raw is not None:
                self.assertEqual(len(raw), item["body_ref"]["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item["body_ref"]["sha256"])
            else:
                self.assertIsNone(item["body_ref"])
            captures[key + "_capture"] = FdaProductArchiveCapture(item["metadata"], raw)
        result = review_fda_structured_product_archive(selection=FdaStructuredProductSelection(**plan["request"]["selection"]),
                                                      reviewed_at=NOW, **captures)
        self.assertEqual(result["structured_rows"], [])
        self.assertEqual(result["data_status"], "WAIT_DATA")
        self.assertIn("STRUCTURED_WHOLE_BODY_UNAVAILABLE", result["reasons"])
        self.assertIsNone(result["applicant_product_candidate"])

    def test_adapter_has_no_network_file_database_registry_or_quota_side_effects(self):
        before = deepcopy(self.inputs)
        with patch("socket.socket", side_effect=AssertionError("no network")), \
             patch("sqlite3.connect", side_effect=AssertionError("no database")), \
             patch("builtins.open", side_effect=AssertionError("no filesystem")):
            result = self.review()
        self.assertEqual(self.inputs, before)
        self.assertEqual(result["http_requests"], 0)
        self.assertFalse(result["quota_modified"])


if __name__ == "__main__":
    unittest.main()
