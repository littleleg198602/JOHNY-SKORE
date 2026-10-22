"""Exact retained legal-name gaps; positive point-roster cases are synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from evidence.batch10_healthcare_20261018.replay_sources import inputs, route_audit
from market_checker_app.collectors import fda_legal_applicant_bridge_review as module
from market_checker_app.services import fda_application_product_review as static
from tests.test_batch9_fda_application_product import fixture as static_fixture, PARTY

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
PARENT = "SYNTHETIC PARENT INC."
CHILD = "Synthetic Applicant Ireland Unlimited Company"
BASE = "https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/"


def captured(body, url, clock="2026-10-18T08:00:00+00:00"):
    if not isinstance(body, bytes): body = body.encode()
    return ({"capture_form": "ORIGINAL_HTTP_RESPONSE", "http_status": 200, "request_url": url, "response_url": url,
             "requested_at": clock, "observed_at": clock, "body_complete_within_byte_budget": True,
             "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(), "byte_budget": 4_000_000}, body)


def filing_body(name=PARENT, day="December 31, 2017", label="21", linked="ex21.htm"):
    return ("<DOCUMENT>\n<TYPE>10-K\n<FILENAME>parent.htm\n<TEXT>\n<html><body>"
            f"<div>FORM 10-K</div><div>For the fiscal year ended {day}</div><div>{name}</div>"
            '<div>(Exact name of registrant as specified in its charter)</div>'
            f'<a href="{linked}">{label}</a></body></html>')


def exhibit_body(rows=None, day="December 31, 2017", direct_column=False, heading="SUBSIDIARIES OF THE COMPANY"):
    rows = rows if rows is not None else [(CHILD, "Ireland")]
    header = ["Company", "Where Incorporated or Organized"] + (["Direct or Indirect"] if direct_column else [])
    cells = "".join("<tr>"+"".join(f"<td>{cell}</td>" for cell in row)+"</tr>" for row in [header,*rows])
    return ("<DOCUMENT>\n<TYPE>EX-21\n<FILENAME>ex21.htm\n<TEXT>\n<html><body>"
            f"<div>EXHIBIT 21</div><div>{heading}</div><p>The following is a list of subsidiaries of the Company as of {day}, omitting some subsidiaries.</p>"
            f"<table>{cells}</table></body></html>")


def fixture():
    app_request, app = static_fixture()
    company_request, company = static_fixture("DRUGSFDA_APPLICATION_PAGE")
    app_request["source_key"] = "application"
    company_request["source_key"] = "company"
    app_request["product_selection"]["source_party_name"] = CHILD
    company_request["product_selection"]["source_party_name"] = PARENT
    app_capture = captured(app["page"][1].replace(PARTY.encode(),CHILD.encode()), app["page"][0]["request_url"], "2026-10-18T09:30:00+00:00")
    company_capture = captured(company["page"][1].replace(PARTY.encode(),PARENT.encode()), company["page"][0]["request_url"], "2026-10-18T09:30:00+00:00")
    app_request["source_version_sha256"] = app_capture[0]["body_sha256"]
    company_request["source_version_sha256"] = company_capture[0]["body_sha256"]
    issuer = captured(json.dumps({"cik":"78003","name":PARENT,"tickers":["PFE"]}), app["issuer"][0]["request_url"], "2026-10-18T09:00:00+00:00")
    request = {"scope":{"issuer_cik":"0000078003","issuer_ticker":"PFE","issuer_name":PARENT,
        "application_type":"BLA","application_number":"761081","applicant_name":CHILD}, "filing_key":"filing", "exhibit_key":"exhibit",
        "roster_as_of":"2017-12-31", "selected_rows":[{"table_index":0,"row_index":1,"entity_name":CHILD,"jurisdiction":"Ireland"}],
        "application_request":app_request,"company_request":company_request}
    captures = {"filing":captured(filing_body(),BASE+"parent.htm"), "exhibit":captured(exhibit_body(),BASE+"ex21.htm", "2026-10-18T08:01:00+00:00"),
        "application":app_capture,"company":company_capture,"issuer":issuer}
    return request,captures


class LegalApplicantBridgeTests(unittest.TestCase):
    def setUp(self):
        self.request,self.captures = fixture()
        for target in (module,static):
            timer=patch.object(target,"_physical_clock",return_value=NOW);timer.start();self.addCleanup(timer.stop)

    def review(self,**kwargs):return module.review_fda_legal_applicant_bridge(self.request,self.captures,**kwargs)

    def body(self,key,body):
        meta,old=self.captures[key]
        if not isinstance(body,bytes):body=body.encode()
        self.captures[key]=(dict(meta,body_sha256=hashlib.sha256(body).hexdigest(),body_bytes=len(body)),body)

    def gap(self,name):
        out=self.review();self.assertIn(name,out["gaps"]);self.assertEqual(out["status"],"PARTIAL");self.assertEqual(out["registry_adoptions"],0);return out

    def test_synthetic_exact_roster_point_is_not_current_control_parent_or_product_ownership(self):
        out=self.review();self.assertTrue(out["exact_applicant_name_match_supported"])
        row=out["selected_roster_facts"][0]
        self.assertEqual(row["jurisdiction_as_displayed"],"Ireland")
        self.assertEqual(row["roster_as_of"],"2017-12-31")
        self.assertIsNone(row["directness_as_stated"]);self.assertIsNone(row["direct_legal_parent_as_stated"])
        self.assertFalse(row["roster_date_is_ownership_start"])
        for key in ("current_applicant_issuer_control_verified","direct_legal_parent_verified","product_owner_verified",
                    "approval_date_is_ownership_start","continuous_control_inferred","similar_name_is_approved_alias","issuer_attribution_allowed","trading_allowed"):
            self.assertFalse(out[key])

    def test_actual_whole_roster_has_exact_historical_name_and_no_Unlimited_alias(self):
        request,captures,_=inputs();out=module.review_fda_legal_applicant_bridge(request,captures)
        self.assertEqual(out["roster_facts"]["entity_row_count"],468)
        self.assertEqual(out["roster_facts"]["table_count"],11)
        self.assertEqual(out["roster_facts"]["duplicate_legal_entity_names"],["Pfizer Limited"])
        self.assertFalse(out["exact_applicant_name_match_supported"])
        self.assertEqual(out["exact_applicant_roster_rows"],[])
        row=out["selected_roster_facts"][0]
        self.assertEqual(row["legal_entity_name_as_displayed"],"Pfizer Ireland Pharmaceuticals")
        self.assertEqual(row["jurisdiction_as_displayed"],"Ireland")
        self.assertFalse(row["legal_entity_name_ambiguous_in_roster"])
        self.assertFalse(out["roster_facts"]["absence_proves_not_a_subsidiary"])

    def test_actual_applicant_products_and_original_incomplete_container_stay_separate(self):
        request,captures,_=inputs();out=module.review_fda_legal_applicant_bridge(request,captures)
        self.assertEqual([r["product_number"] for r in out["applicant_source"]["product_rows"]],["001","002"])
        self.assertFalse(out["applicant_source"]["application_facts"]["detail_container_explicitly_closed"])
        self.assertEqual(out["company_source"]["application_facts"]["source_party_name"],"PFIZER INC")
        self.assertEqual(out["applicant_source"]["application_facts"]["source_party_name"],"Pfizer Ireland Pharmaceuticals Unlimited Company")
        self.assertIn("PRODUCT_DETAIL_OUTER_CONTAINER_IMPLICITLY_CLOSED_SOURCE_DEFECT",out["gaps"])
        self.assertFalse(out["approval_date_is_ownership_start"])

    def test_actual_only_linked_EX21_is_already_captured_and_index403_is_not_retried(self):
        _,captures,_=inputs();audit=route_audit(captures)
        self.assertEqual(audit["index_failure"]["http_status"],403)
        self.assertFalse(audit["new_SEC_research_GET_authorized_by_unattempted_link_condition"])
        self.assertEqual(audit["new_source_requests"],0)
        self.assertTrue(any(r["literal_label"]=="2.1" and not r["eligible_exhibit21_label"] for r in audit["retained_filing_links"]))

    def test_similar_short_name_or_suffix_removal_never_matches_legal_applicant(self):
        short="Synthetic Applicant Ireland"
        self.body("exhibit",exhibit_body([(short,"Ireland")]))
        self.request["selected_rows"][0]["entity_name"]=short
        out=self.gap("EXACT_CURRENT_APPLICANT_LEGAL_NAME_NOT_IN_POINT_ROSTER")
        self.assertFalse(out["exact_applicant_name_match_supported"])

    def test_omitted_subsidiaries_and_missing_exact_name_are_not_complete_negative(self):
        self.body("exhibit",exhibit_body([("Unrelated Entity","Ireland")]))
        self.request["selected_rows"]=[]
        out=self.gap("EXACT_CURRENT_APPLICANT_LEGAL_NAME_NOT_IN_POINT_ROSTER")
        self.assertTrue(out["roster_facts"]["roster_omits_some_subsidiaries"])
        self.assertFalse(out["roster_facts"]["absence_proves_not_a_subsidiary"])

    def test_duplicate_target_rows_are_preserved_as_ambiguous_not_first_selected(self):
        self.body("exhibit",exhibit_body([(CHILD,"Ireland"),(CHILD,"Ireland")]))
        out=self.gap("EXACT_APPLICANT_LEGAL_ENTITY_ROSTER_ROWS_AMBIGUOUS")
        self.assertFalse(out["exact_applicant_name_match_supported"]);self.assertEqual(len(out["exact_applicant_roster_rows"]),2)

    def test_duplicate_unrelated_entity_does_not_erase_unique_selected_point(self):
        self.body("exhibit",exhibit_body([(CHILD,"Ireland"),("Unrelated","Ireland"),("Unrelated","England")]))
        out=self.review();self.assertTrue(out["exact_applicant_name_match_supported"])
        self.assertEqual(out["roster_facts"]["duplicate_legal_entity_names"],["Unrelated"])

    def test_explicit_directness_column_is_only_point_as_stated(self):
        self.body("exhibit",exhibit_body([(CHILD,"Ireland","Direct")],direct_column=True))
        row=self.review()["selected_roster_facts"][0]
        self.assertEqual(row["directness_as_stated"],"Direct");self.assertEqual(row["direct_legal_parent_as_stated"],PARENT)
        self.assertFalse(row["continuous_or_current_control_verified"])
        self.body("exhibit",exhibit_body([(CHILD,"Ireland","Indirect")],direct_column=True))
        self.assertIsNone(self.review()["selected_roster_facts"][0]["direct_legal_parent_as_stated"])

    def test_wrong_row_jurisdiction_or_country_normalization_cannot_be_borrowed(self):
        self.request["selected_rows"][0]["jurisdiction"]="IE"
        self.gap("SELECTED_EXACT_ROSTER_ENTITY_JURISDICTION_ROW_MISSING")

    def test_exhibit2_1_label_or_filename_substring_is_not_EX21_link_proof(self):
        self.body("filing",filing_body(label="2.1"))
        self.gap("EXACT_EXHIBIT21_LABEL_AND_ORIGINAL_LINK_REQUIRED")

    def test_different_accession_or_same_source_is_not_an_independent_exhibit(self):
        meta,raw=self.captures["exhibit"]
        url=meta["request_url"].replace("18000027","18000028")
        self.captures["exhibit"]=(dict(meta,request_url=url,response_url=url),raw)
        self.gap("INDEPENDENT_SAME_ACCESSION_FILING_EXHIBIT_PAIR_REQUIRED")

    def test_original_doc_type_filename_and_redirect_must_bind(self):
        self.body("exhibit",exhibit_body().replace("<TYPE>EX-21","<TYPE>EX-2.1"))
        self.gap("SEC_ORIGINAL_DOCUMENT_TYPE_OR_FILENAME_CONFLICT")
        self.request,self.captures=fixture();self.captures["filing"][0]["response_url"]=BASE+"other.htm"
        self.gap("EXACT_ORIGINAL_SEC_REFERENCE_REQUIRED")

    def test_exact_cover_marker_name_and_fiscal_date_are_one_nearby_source_context(self):
        self.body("filing",filing_body(name="OTHER PARENT INC."))
        self.gap("EXACT_ADJACENT_LEGACY_COVER_REGISTRANT_REQUIRED")
        self.body("filing",filing_body(day="December 31, 2018"))
        self.gap("EXACT_NEARBY_LEGACY_COVER_FISCAL_POINT_REQUIRED")

    def test_roster_point_is_not_borrowed_from_cover_or_another_roster(self):
        self.body("exhibit",exhibit_body(day="December 31, 2018"))
        self.gap("ROSTER_POINT_DATE_CONFLICT_OR_UNAVAILABLE")
        self.body("exhibit",exhibit_body()+"<p>Subsidiaries of OTHER COMPANY as of December 31, 2017</p>")
        self.gap("EXACT_SINGLE_REPORTING_COMPANY_ROSTER_SCOPE_REQUIRED")

    def test_late_original_exhibit_cannot_supply_earlier_applicant_knowledge(self):
        meta,raw=self.captures["exhibit"]
        self.captures["exhibit"]=(dict(meta,known_at="2026-10-18T09:30:00+00:00"),raw)
        self.gap("LEGAL_GROUP_EVIDENCE_NOT_KNOWN_BEFORE_ORIGINAL_APPLICANT_OBJECT")

    def test_original_capture_future_naive_or_reversed_knowledge_fails_before_roster(self):
        for fields in ({"observed_at":"2026-10-19T08:00:00+00:00"},{"known_at":"2026-10-17T08:00:00+00:00"},{"requested_at":"2026-10-18T08:00:00"}):
            self.request,self.captures=fixture();self.captures["exhibit"][0].update(fields)
            with patch.object(module,"_roster",side_effect=AssertionError("No unavailable source parse")):
                self.assertIsNone(self.review()["roster_facts"])

    def test_cutoff_latency_does_not_backdate_new_review_or_change_source_clocks(self):
        out=self.review(reviewed_at=NOW-timedelta(microseconds=10))
        self.assertEqual(out["known_at"],NOW.isoformat())
        with self.assertRaises(ValueError):self.review(reviewed_at=NOW+timedelta(microseconds=1))
        with self.assertRaises(ValueError):self.review(reviewed_at=NOW.replace(tzinfo=None))

    def test_transformed_metadata_only_and_withheld_hashes_never_parse(self):
        for key in ("filing","exhibit"):
            self.request,self.captures=fixture();meta,raw=self.captures[key]
            self.captures[key]=(dict(meta,body_transformed=True),raw)
            self.gap("COMPLETE_UNTRANSFORMED_ORIGINAL_SEC_HTTP200_REQUIRED")
        self.request,self.captures=fixture();self.captures["exhibit"]=({"retention":"MISSING_BODY"},None)
        self.gap("WHOLE_SEC_SOURCE_UNAVAILABLE_NO_SUBSTITUTE")
        for digest in module.WITHHELD:
            self.request,self.captures=fixture();self.captures["exhibit"][0]["body_sha256"]=digest
            with patch.object(module,"_Page",side_effect=AssertionError("No withheld body parser")):
                self.gap("WHOLE_SEC_SOURCE_UNAVAILABLE_NO_SUBSTITUTE")

    def test_hash_cap_HTTP_error_and_unsafe_body_fail_whole_without_projection(self):
        self.captures["exhibit"][0]["body_sha256"]="0"*64
        self.gap("WHOLE_SEC_HASH_LENGTH_CAP_OR_SAFETY_CONFLICT")
        self.request,self.captures=fixture();self.captures["exhibit"][0]["byte_budget"]=4_000_001
        self.gap("WHOLE_SEC_HASH_LENGTH_CAP_OR_SAFETY_CONFLICT")
        self.request,self.captures=fixture();self.captures["exhibit"][0]["http_status"]=403
        self.gap("COMPLETE_UNTRANSFORMED_ORIGINAL_SEC_HTTP200_REQUIRED")
        self.body("exhibit",'password="syntheticcredential123"')
        self.captures["exhibit"][0]["http_status"]=200
        self.gap("WHOLE_SEC_HASH_LENGTH_CAP_OR_SAFETY_CONFLICT")

    def test_hidden_nested_or_competing_roster_columns_are_not_entity_proof(self):
        for old,new in (("<table>","<table hidden>"),("Where Incorporated or Organized","Country"),("<td>Ireland</td>","<td><table><tr><td>Ireland</td></tr></table></td>")):
            self.body("exhibit",exhibit_body().replace(old,new))
            self.assertIsNone(self.review()["roster_facts"])

    def test_bad_request_scope_keys_locator_types_and_cross_application_roles_raise(self):
        for change in ("wrong_cik","bool_row","different_applicant_application"):
            self.request,self.captures=fixture()
            if change=="wrong_cik":self.request["scope"]["issuer_cik"]="78003"
            elif change=="bool_row":self.request["selected_rows"][0]["row_index"]=True
            else:self.request["company_request"]["scope"]["application_number"]="761082"
            with self.assertRaises(ValueError):self.review()

    def test_approval2019_never_becomes_ownership_start_and_country_is_unmapped_literal(self):
        out=self.review();row=out["selected_roster_facts"][0]
        self.assertFalse(row["country_or_ISO_code_inferred"])
        self.assertFalse(out["approval_date_is_ownership_start"])
        self.assertEqual(out["applicant_source"]["application_facts"]["original_approval_day"],"2019-03-11")

    def test_no_HTTP_DB_files_registry_mutation_and_output_detached(self):
        before=deepcopy((self.request,self.captures))
        with patch("socket.socket.connect",side_effect=AssertionError("no HTTP")),patch("sqlite3.connect",side_effect=AssertionError("no DB")),patch("builtins.open",side_effect=AssertionError("no files")):
            first=self.review();second=self.review()
        self.assertEqual(before,(self.request,self.captures))
        first["selected_roster_facts"][0]["jurisdiction_as_displayed"]="consumer change"
        self.assertEqual(second["selected_roster_facts"][0]["jurisdiction_as_displayed"],"Ireland")
        self.assertEqual(second["source_HTTP_requests"],0)

    def test_actual_runtime_plan_pins_five_complete_originals_without_copying_old_bodies(self):
        request,captures,plan=inputs()
        self.assertEqual(len(plan["captures"]),5)
        for key,item in plan["captures"].items():
            self.assertEqual(hashlib.sha256(captures[key][1]).hexdigest(),item["body_ref"]["sha256"])
            self.assertEqual(len(captures[key][1]),item["body_ref"]["bytes"])


if __name__=="__main__":unittest.main()
