"""Actual static-source boundaries; positive issuer/product contracts synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import fda_application_product_review as module
from evidence.batch9_healthcare_20261016.replay_sources import build_inputs, capture

NOW = datetime(2026, 10, 16, 13, tzinfo=timezone.utc)
PARTY = "SYNTHETIC APPLICANT INC."
FDA_URL = "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=761081"
PB_URL = "https://purplebooksearch.fda.gov/index.cfm?blaNo=761081&event=productdetails"
SEC_URL = "https://data.sec.gov/submissions/CIK0000078003.json"


def captured(raw, url, clock="2026-10-16T10:00:00+00:00"):
    if not isinstance(raw, bytes): raw = raw.encode()
    return ({"capture_form": "ORIGINAL_HTTP_RESPONSE", "request_url": url, "response_url": url,
             "http_status": 200, "body_complete_within_byte_budget": True, "raw_publication_permitted": True,
             "requested_at": clock, "observed_at": clock, "body_bytes": len(raw), "byte_budget": 4_000_000,
             "body_sha256": hashlib.sha256(raw).hexdigest()}, raw)


def purple_html():
    headers = "".join(f"<div>{h}</div>" for h in module.PURPLE_HEADERS)
    row = "".join(f"<div>{v}</div>" for v in ("001", "For Injection", "Intravenous", "420MG", "Vial", "351(k) Biosimilar", "SYNTHETIC PRODUCT", "Rx"))
    fields = "".join(f"<div><strong>{k}</strong><br>{v}</div>" for k,v in (
        ("BLA Number", "761081"), ("Applicant", PARTY), ("Proper Name", "SYNTHETIC COMPOUND"), ("Original Approval Date", "March 11, 2019")))
    return f'<section class="main-content"><div class="product-grid"><div class="grid-row grid-header">{headers}</div><div class="grid-row">{row}</div></div><div class="product-details">{fields}</div></section>'


def drugs_html():
    headers = "".join(f"<th>{h}</th>" for h in module.FDA_HEADERS)
    row = "".join(f"<td>{v}</td>" for v in ("SYNTHETIC PRODUCT", "SYNTHETIC COMPOUND", "420MG", "INJECTABLE;INJECTION", "Prescription", "None", "No", "No"))
    return (f'<span><span class="prodBoldText">Biologic License Application (BLA):</span><span class="appl-details-top">761081</span>'
            f'<span class="prodBoldText">Company:</span><span class="appl-details-top">{PARTY}</span></span>'
            '<a href="#collapseProduct">Products on BLA 761081</a><div id="collapseProduct">'
            f'<table id="exampleProd" summary="FDA Approved Drug Products"><thead><tr>{headers}</tr></thead><tbody><tr>{row}</tr></tbody></table></div>')


def fixture(kind="PURPLE_BOOK_PRODUCT_DETAIL"):
    page = captured(purple_html() if kind.startswith("PURPLE") else drugs_html(), PB_URL if kind.startswith("PURPLE") else FDA_URL)
    issuer = captured(json.dumps({"cik": "78003", "name": PARTY, "tickers": ["PFE"]}), SEC_URL, "2026-10-16T09:59:00+00:00")
    selected = {"source_row_index": 0, "product_number": "001" if kind.startswith("PURPLE") else None,
                "proprietary_name": "SYNTHETIC PRODUCT", "proper_name": "SYNTHETIC COMPOUND", "strength": "420MG",
                "dosage_form": "For Injection" if kind.startswith("PURPLE") else None,
                "route": "Intravenous" if kind.startswith("PURPLE") else None,
                "dosage_form_route_text": None if kind.startswith("PURPLE") else "INJECTABLE;INJECTION", "source_party_name": PARTY}
    request = {"scope": {"application_type": "BLA", "application_number": "761081", "issuer_cik": "0000078003", "issuer_ticker": "PFE"},
               "source_kind": kind, "source_key": "page", "issuer_key": "issuer", "source_version_sha256": page[0]["body_sha256"], "product_selection": selected}
    return request, {"page": page, "issuer": issuer}


class ApplicationProductTests(unittest.TestCase):
    def setUp(self):
        self.request, self.captures = fixture()
        self.timer = patch.object(module, "_physical_clock", return_value=NOW)
        self.timer.start(); self.addCleanup(self.timer.stop)

    def review(self, **changes):
        return module.review_fda_application_product(self.request, self.captures, **changes)

    def page(self, body):
        self.captures["page"] = captured(body, self.captures["page"][0]["request_url"])
        self.request["source_version_sha256"] = self.captures["page"][0]["body_sha256"]

    def partial(self, gap):
        result = self.review()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIsNone(result["applicant_product_candidate"])
        self.assertIn(gap, result["gaps"])
        return result

    def test_synthetic_exact_applicant_product_snapshot_does_not_prove_owner_or_history(self):
        result = self.review()
        self.assertEqual(result["status"], "REVIEWABLE_APPLICATION_APPLICANT_PRODUCT_SNAPSHOT")
        self.assertTrue(result["exact_named_issuer_bridge_supported"])
        self.assertEqual(result["product_rows"][0]["product_number"], "001")
        self.assertEqual(result["known_at"], NOW.isoformat())
        self.assertFalse(result["applicant_product_candidate"]["adopted"])
        for key in ("issuer_attribution_allowed", "sponsor_role_proven", "legal_owner_verified", "corporate_parent_verified",
                    "approval_date_is_ownership_since", "historical_machine_availability_inferred", "live_acceptance_verified", "trading_allowed"):
            self.assertFalse(result[key])

    def test_company_label_is_not_applicant_and_row_ordinal_never_becomes_product_number(self):
        self.request, self.captures = fixture("DRUGSFDA_APPLICATION_PAGE")
        result = self.partial("COMPANY_LABEL_IS_NOT_EXPLICIT_APPLICANT_OR_SPONSOR")
        self.assertTrue(result["exact_named_issuer_bridge_supported"])
        self.assertIsNone(result["product_rows"][0]["product_number"])
        self.assertIsNone(result["product_rows"][0]["route"])
        self.assertEqual(result["product_rows"][0]["dosage_form_route_text"], "INJECTABLE;INJECTION")

    def test_actual_two_static_pages_keep_different_roles_products_and_unrepaired_source_defect(self):
        for sequence, count in ((2, 1), (3, 2)):
            request, captures, _ = build_inputs(sequence)
            result = module.review_fda_application_product(request, captures)
            self.assertEqual(len(result["product_rows"]), count)
            self.assertIsNone(result["applicant_product_candidate"])
            if sequence == 2:
                self.assertTrue(result["exact_named_issuer_bridge_supported"])
                self.assertEqual(result["application_facts"]["source_party_role"], "COMPANY_AS_DISPLAYED")
            else:
                self.assertFalse(result["exact_named_issuer_bridge_supported"])
                self.assertEqual(result["application_facts"]["source_party_name"], "Pfizer Ireland Pharmaceuticals Unlimited Company")
                self.assertEqual([r["product_number"] for r in result["product_rows"]], ["001", "002"])
                self.assertEqual([r["strength"] for r in result["product_rows"]], ["420MG", "150MG"])
                self.assertFalse(result["application_facts"]["approval_date_is_applicant_since"])
                self.assertIn("PRODUCT_DETAIL_OUTER_CONTAINER_IMPLICITLY_CLOSED_SOURCE_DEFECT", result["gaps"])
            self.assertEqual(result["registry_adoptions"], 0)

    def test_exact_original_hash_length_cap_and_transformation_flags_precede_html_parser(self):
        meta, raw = self.captures["page"]
        with patch.object(module, "_Page", side_effect=AssertionError("no parser")):
            for changes in ({"body_sha256": "0"*64}, {"body_bytes": len(raw)-1}, {"byte_budget": 4_000_001}):
                self.captures["page"] = (dict(meta, **changes), raw)
                self.partial("ORIGINAL_WHOLE_HASH_LENGTH_OR_CAP_CONFLICT")
            self.captures["page"] = (dict(meta, capture_form="PROJECTED_ROWS"), raw)
            self.partial("COMPLETE_ORIGINAL_HTTP200_REQUIRED")

    def test_missing_metadata_only_and_withheld_originals_stay_unavailable_before_parser(self):
        with patch.object(module, "_Page", side_effect=AssertionError("no parser")):
            self.captures["page"] = ({"retention": "MISSING_BODY"}, None)
            self.partial("WHOLE_ORIGINAL_SOURCE_UNAVAILABLE")

    def test_transformed_body_declaration_cannot_keep_original_source_role(self):
        self.captures["page"][0]["body_transformed"] = True
        with patch.object(module, "_Page", side_effect=AssertionError("transformed original must not parse")):
            self.partial("COMPLETE_ORIGINAL_HTTP200_REQUIRED")

    def test_whole_encoded_withheld_retention_cannot_restore_unflagged_original_bytes(self):
        self.captures["page"][0]["retention"] = "WITHHELD_ENCODED_BODY"
        with patch.object(module, "_Page", side_effect=AssertionError("withheld original must not parse")):
            self.partial("WHOLE_ORIGINAL_SOURCE_UNAVAILABLE")
            self.captures["page"] = ({"raw_publication_permitted": False}, b"not a restored original")
            self.partial("WHOLE_ORIGINAL_SOURCE_UNAVAILABLE")

    def test_unsafe_whole_body_and_metadata_are_not_sanitized_or_projected(self):
        self.page(b'<p>password="syntheticcredential123"</p>')
        self.partial("WHOLE_UNSAFE_SOURCE_WITHHELD")
        self.request, self.captures = fixture()
        self.captures["page"][0]["access_token"] = "syntheticcredential123"
        self.partial("BOUNDED_SAFE_WHOLE_METADATA_REQUIRED")

    def test_source_query_duplicates_api_key_wrong_dataset_and_redirects_rejected(self):
        meta, raw = self.captures["page"]
        for url in (PB_URL+"&blaNo=761081", PB_URL+"&api_key=short", FDA_URL,
                    PB_URL.replace("761081", "761082"), PB_URL.replace("purplebooksearch.fda.gov", "example.test")):
            self.captures["page"] = (dict(meta, request_url=url, response_url=url), raw)
            self.assertIsNone(self.review()["applicant_product_candidate"])
            self.assertEqual(self.review()["product_rows"], [])
        self.captures["page"] = (dict(meta, response_url=FDA_URL), raw)
        self.partial("ORIGINAL_URL_REDIRECT_OR_VERSION_CONFLICT")

    def test_NDA_cannot_take_BLA_page_or_same_numeric_application(self):
        self.request["scope"]["application_type"] = "NDA"
        self.partial("EXACT_STATIC_PURPLE_BOOK_BLA_SOURCE_REQUIRED")
        self.request, self.captures = fixture("DRUGSFDA_APPLICATION_PAGE")
        self.request["scope"]["application_type"] = "NDA"
        self.partial("APPLICATION_TYPE_NUMBER_OR_COMPANY_LABEL_CONFLICT")

    def test_exact_source_version_and_product_tuple_cannot_be_inferred_from_nearby_row(self):
        self.request["source_version_sha256"] = "0"*64
        self.partial("SELECTED_ORIGINAL_SOURCE_VERSION_CONFLICT")
        self.request, self.captures = fixture()
        for key, value in (("product_number", "002"), ("strength", "420 MG"), ("route", "Intravenous infusion"), ("source_row_index", 1)):
            self.request["product_selection"][key] = value
            self.partial("EXACT_PRODUCT_TUPLE_NOT_IN_SELECTED_ORIGINAL_VERSION")
            self.request, self.captures = fixture()

    def test_no_selection_or_missing_issuer_preserves_source_facts_without_candidate(self):
        self.request["product_selection"] = None
        self.assertEqual(len(self.partial("EXACT_PRODUCT_SELECTION_NOT_PROVIDED")["product_rows"]), 1)
        self.request, self.captures = fixture()
        self.captures["issuer"] = ({}, None)
        self.assertEqual(len(self.partial("WHOLE_ORIGINAL_SOURCE_UNAVAILABLE")["product_rows"]), 1)

    def test_duplicate_grid_product_number_and_field_labels_are_ambiguous(self):
        original = purple_html()
        self.page(original.replace('<div class="product-grid">', '<div class="product-grid"></div><div class="product-grid">'))
        self.partial("EXACT_VISIBLE_SOURCE_OBJECT_MISSING_OR_AMBIGUOUS")
        self.page(original.replace('<div><strong>Applicant</strong>', '<div><strong>Applicant</strong><br>Other</div><div><strong>Applicant</strong>'))
        self.partial("EXACT_PRODUCT_DETAIL_LABEL_MISSING_OR_AMBIGUOUS")
        duplicate = original[original.index('<div class="grid-row">'):original.index('</div></div><div class="product-details">')+6]
        self.page(original.replace('</div><div class="product-details">', duplicate+'</div><div class="product-details">'))
        self.assertEqual(self.review()["product_rows"], [])

    def test_hidden_wrong_schema_and_nested_cells_do_not_supply_product_role(self):
        for old,new in (('class="product-grid"','class="product-grid" hidden'), ('>Product Number<','>Other Number<'),
                        ('<div>001</div>','<div><div>001</div></div>')):
            self.page(purple_html().replace(old,new))
            self.assertEqual(self.review()["product_rows"], [])

    def test_text_mentions_and_glossary_applicant_do_not_replace_structured_fields(self):
        self.page(purple_html().replace('<strong>Applicant</strong>', '<strong>Brand</strong>') + f'<div>Applicant {PARTY} product001</div>')
        self.partial("EXACT_PRODUCT_DETAIL_LABEL_MISSING_OR_AMBIGUOUS")

    def test_unclosed_outer_container_recovers_only_explicit_rows_and_withholds_candidate(self):
        self.page(purple_html().replace('</div></section>', '</section>'))
        result = self.partial("PRODUCT_DETAIL_OUTER_CONTAINER_IMPLICITLY_CLOSED_SOURCE_DEFECT")
        self.assertEqual(len(result["product_rows"]), 1)
        self.page(purple_html().replace('<div>420MG</div>', '<div>420MG'))
        self.assertEqual(self.review()["product_rows"], [])

    def test_wrong_issuer_name_CIK_ticker_and_aliases_cannot_bridge(self):
        for fields in ({"name": "SYNTHETIC APPLICANT"}, {"name": "Synthetic Subsidiary Inc."}, {"cik": "999"}, {"tickers": ["MRK"]}, {"tickers": ["PFE","PFE"]}):
            self.captures["issuer"] = captured(json.dumps({"cik":"78003","name":PARTY,"tickers":["PFE"],**fields}), SEC_URL, "2026-10-16T09:59:00+00:00")
            self.partial("EXACT_SOURCE_PARTY_NAME_CIK_TICKER_ISSUER_BRIDGE_MISSING")

    def test_late_equal_and_previous_day_issuer_cannot_be_retrospective_bridge(self):
        meta, raw = self.captures["issuer"]
        for value in ("2026-10-16T10:00:00+00:00", "2026-10-16T10:00:01+00:00", "2026-10-15T09:59:00+00:00"):
            self.captures["issuer"] = (dict(meta, requested_at=value, observed_at=value), raw)
            self.partial("ISSUER_BRIDGE_NOT_KNOWN_STRICTLY_BEFORE_ORIGINAL_SOURCE")
        self.captures["issuer"] = (dict(meta, known_at="2026-10-16T10:00:01+00:00"), raw)
        self.partial("ISSUER_BRIDGE_NOT_KNOWN_STRICTLY_BEFORE_ORIGINAL_SOURCE")

    def test_future_inverted_and_naive_source_clocks_are_unavailable(self):
        meta, raw = self.captures["page"]
        for fields in ({"observed_at": "2026-10-17T10:00:00+00:00"}, {"requested_at":"2026-10-16T11:00:00+00:00"}, {"known_at":"2026-10-16T09:00:00+00:00"}):
            self.captures["page"] = (dict(meta, **fields), raw)
            self.partial("ORIGINAL_CAPTURE_OR_KNOWLEDGE_CLOCK_CONFLICT")
        self.captures["page"] = (dict(meta, requested_at="2026-10-16T10:00:00"), raw)
        self.assertEqual(self.review()["product_rows"], [])

    def test_source_read_cutoff_can_precede_invocation_without_backdating_new_review(self):
        result = self.review(reviewed_at=NOW-timedelta(microseconds=50))
        self.assertEqual(result["known_at"], NOW.isoformat())
        self.assertNotEqual(result["source_read_cutoff"], result["known_at"])
        with self.assertRaises(ValueError): self.review(reviewed_at=NOW+timedelta(microseconds=1))
        with self.assertRaises(ValueError): self.review(reviewed_at=NOW.replace(tzinfo=None))
        self.assertEqual(self.review(reviewed_at=NOW-timedelta(hours=4))["product_rows"], [])

    def test_original_approval_is_not_current_applicant_since_and_future_date_is_a_conflict(self):
        self.page(purple_html().replace("March 11, 2019", "March 11, 2099"))
        result = self.partial("ORIGINAL_APPROVAL_AFTER_SOURCE_OBSERVATION")
        self.assertFalse(result["application_facts"]["approval_date_is_applicant_since"])
        self.page(purple_html().replace("March 11, 2019", "March 99, 2019"))
        self.partial("ORIGINAL_APPROVAL_CALENDAR_DAY_UNVERIFIED")

    def test_duplicate_json_or_encoded_unsafe_issuer_is_not_an_independent_bridge(self):
        for body in ('{"cik":"78003","cik":"78003"}', '{"pass\\u0077ord":"syntheticcredential123"}'):
            self.captures["issuer"] = captured(body, SEC_URL, "2026-10-16T09:59:00+00:00")
            self.assertFalse(self.review()["exact_named_issuer_bridge_supported"])

    def test_HTTP_errors_empty_rows_and_missing_cells_never_become_negative_coverage(self):
        meta, raw = self.captures["page"]
        for status in (400,404,429):
            self.captures["page"] = (dict(meta, http_status=status), raw)
            self.partial("COMPLETE_ORIGINAL_HTTP200_REQUIRED")
        self.page(purple_html().replace('<div>420MG</div>', ''))
        self.assertEqual(self.review()["product_rows"], [])
        self.assertFalse(self.review()["issuer_wide_scope"])

    def test_complexity_duplicate_attributes_and_original_over_cap_rejected(self):
        self.page('<div>'*130+purple_html()+'</div>'*130)
        self.partial("ORIGINAL_HTML_COMPLEXITY_CAP_EXCEEDED")
        self.page(purple_html().replace('class="product-grid"', 'class="product-grid" class="product-grid"'))
        self.partial("DUPLICATE_HTML_ATTRIBUTE_AMBIGUOUS")
        self.page(b'x'*4_000_001)
        self.partial("ORIGINAL_WHOLE_HASH_LENGTH_OR_CAP_CONFLICT")

    def test_actual_plan_exact_byte_pins_are_replayable_without_source_rewrites(self):
        for sequence in (2,3):
            request,captures,plan = build_inputs(sequence)
            for key,item in plan["captures"].items():
                self.assertEqual(hashlib.sha256(captures[key][1]).hexdigest(),item["body_ref"]["sha256"])
                self.assertEqual(len(captures[key][1]),item["body_ref"]["bytes"])
            self.assertEqual(module.review_fda_application_product(request,captures)["new_verified_identities"],0)

    def test_repeated_review_is_detached_and_has_no_HTTP_DB_file_or_registry_side_effect(self):
        before=deepcopy((self.request,self.captures))
        with patch('socket.socket.connect',side_effect=AssertionError('no HTTP')),patch('sqlite3.connect',side_effect=AssertionError('no DB')),patch('builtins.open',side_effect=AssertionError('no files')):
            first=self.review();second=self.review()
        self.assertEqual(before,(self.request,self.captures))
        self.assertEqual(first['applicant_product_candidate']['record_id'],second['applicant_product_candidate']['record_id'])
        first['product_rows'][0]['strength']='consumer mutation'
        self.assertEqual(second['product_rows'][0]['strength'],'420MG')

    def test_invalid_request_bounds_identifiers_and_extra_capture_are_not_guessed(self):
        for field,value in (('application_type',[]),('application_number','76181'),('issuer_cik','78003'),('issuer_ticker','pfe')):
            self.request,self.captures=fixture();self.request['scope'][field]=value
            with self.assertRaises(ValueError):self.review()
        self.request,self.captures=fixture();self.captures['extra']=self.captures['page']
        with self.assertRaises(ValueError):self.review()


if __name__ == '__main__':
    unittest.main()
