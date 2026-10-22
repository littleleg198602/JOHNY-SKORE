"""Actual retained role facts plus explicitly synthetic invalid-scope controls."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from evidence.batch10_role_claim_fix_20261018.build_review import (
    ROOT as EVIDENCE, REPO, PINS, BODY_PATHS, SELECTION, load_inputs,
    build_review_artifact, build_review_plan,
)
from market_checker_app.collectors.sec_person_role_claim_review import (
    PersonRoleClaimCapture, PersonRoleClaimReviewError, review_sec_person_role_claims,
)

MODULE = "market_checker_app.collectors.sec_person_role_claim_review"


def changed(capture, body, **fields):
    return replace(capture, body=body, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), **fields)


def synthetic_inputs(account, *, accession="0001193125-25-245150"):
    """Small synthetic markup; this is never a new captured-source acceptance."""
    context = "period"
    person = "Satya Nadella Age: 58 | Director Since: 2014 | Birthplace: India"
    career = "Experience: Microsoft Corporation (1992-present) • Chairman and Chief Executive Officer (2021-present) • Chief Executive Officer and Director (2014-2021)"
    raw = f'''<html><body><ix:header><ix:hidden>
<ix:nonNumeric name="dei:DocumentType" contextRef="{context}">DEF 14A</ix:nonNumeric>
<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="{context}">0000789019</ix:nonNumeric>
</ix:hidden><ix:resources><xbrli:context id="{context}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000789019</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2024-07-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context></ix:resources></ix:header>
<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="{context}">Microsoft Corporation</ix:nonNumeric>
<div><table><tr><td>{person}</td></tr></table>
<table><tr><td>{career}</td></tr></table></div></body></html>'''.encode()
    folder = "https://www.sec.gov/Archives/edgar/data/789019/" + accession.replace("-", "") + "/"
    url = folder + "synthetic-def14a.htm"
    index = f'''<html><span class="companyName">MICROSOFT CORP <a href="/cgi-bin/browse-edgar?CIK=0000789019">CIK</a></span>
<div class="infoHead">Filing Date</div><div class="info">2025-10-21</div>
<div class="infoHead">Accepted</div><div class="info">2025-10-21 16:16:13</div>
<table><tr><td>1</td><td>PROXY</td><td><a href="{url}">synthetic-def14a.htm</a></td><td>DEF 14A</td><td>{len(raw)}</td></tr></table></html>'''.encode()
    observed = datetime(2026,10,16,12,30,tzinfo=timezone.utc)
    def capture(record,url,body):
        return PersonRoleClaimCapture(record,url,body,hashlib.sha256(body).hexdigest(),len(body),observed)
    selection = deepcopy(SELECTION)
    selection.update(person_context_locator="table:1", role_context_locator="table:2/row:1/cell:1",
                     person_context_quote=person, role_context_quote=career)
    return {"primary":capture("synthetic-role-primary",url,raw),
        "index":capture("synthetic-role-index",folder+accession+"-index.html",index),
        "account_packet":account},selection


class SecPersonRoleClaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual,cls.actual_selection,cls.receipts=load_inputs()
        cls.golden=json.loads((EVIDENCE/"person_role_claim_review_v2.json").read_bytes())
        cls.physical=datetime.fromisoformat(cls.golden["review"]["review_ingested_at"])

    def setUp(self):
        self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def review(self,*,captures=None,selection=None,knowledge_at=None,clock=None):
        captures=self.captures if captures is None else captures
        with patch(MODULE+"._physical_clock",return_value=clock or self.physical):
            return review_sec_person_role_claims(primary_capture=captures["primary"],index_capture=captures["index"],
                account_packet_capture=captures["account_packet"],selection=self.selection if selection is None else selection,
                knowledge_at=knowledge_at)

    def actual_review(self):
        return self.review(captures=self.actual,selection=self.actual_selection)

    def mutate_primary(self,raw):
        primary=self.captures["primary"]
        index=self.captures["index"]
        self.captures["primary"]=changed(primary,raw)
        self.captures["index"]=changed(index,index.body.replace((">"+str(primary.size_bytes)+"<").encode(),(">"+str(len(raw))+"<").encode()))

    def test_actual_whole_originals_and_exact_interpretation_replay_are_pinned(self):
        clocks=[self.golden["review"][key] for key in ("review_ingested_at","review_available_at")]
        self.assertEqual(self.golden,build_review_artifact(replay_clocks=clocks))
        for key,capture in self.actual.items():
            self.assertEqual(PINS[key],(len(capture.body),hashlib.sha256(capture.body).hexdigest()))
        self.assertEqual(0,self.golden["actual_new_http_requests"])

    def test_actual_independent_bio_has_seven_named_roles_and_one_generic_career_range(self):
        report=self.actual_review()
        self.assertTrue(report["source_role_claims_supported"])
        self.assertEqual((7,8),(report["role_claim_count"],report["career_assertion_count"]))
        expected=[("Director","2014",None),
            ("Chairman and Chief Executive Officer","2021","present"),
            ("Chief Executive Officer and Director","2014","2021"),
            ("Executive Vice President, Cloud and Enterprise","2013","2014"),
            ("President, Server and Tools","2011","2013"),
            ("Senior Vice President, Online Services Division","2009","2011"),
            ("Senior Vice President, Search, Portal, and Advertising","2008","2009"),
            ("Various positions of increasing authority","1992","2008")]
        self.assertEqual(expected,[(c["title_literal"],c["start_assertion"]["literal"],None if c["end_assertion"] is None else c["end_assertion"]["literal"]) for c in report["role_claims"]])
        self.assertEqual("GENERIC_CAREER_POSITION_RANGE_ASSERTION",report["role_claims"][-1]["kind"])

    def test_actual_since_is_only_a_year_assertion_not_a_historical_knowledge_date(self):
        claim=self.actual_review()["role_claims"][0]
        self.assertEqual("Director Since: 2014",claim["source_clause"])
        self.assertEqual({"literal":"2014","precision":"YEAR","year":2014,"exact_day":None},claim["start_assertion"])
        self.assertIsNone(claim["end_assertion"])
        self.assertFalse(claim["legal_interval_verified"])
        self.assertFalse(claim["continuous_role_verified"])

    def test_actual_present_is_anchored_to_2025_disclosure_not_2026_capture_or_form4(self):
        report=self.actual_review();end=report["role_claims"][1]["end_assertion"]
        self.assertEqual("SOURCE_RELATIVE_PRESENT",end["precision"])
        self.assertEqual("2025-10-21",end["anchor_filing_day"])
        self.assertIsNone(end["exact_end_day"])
        self.assertFalse(end["continues_through_capture_or_current_form4"])
        self.assertEqual("2026-08-31",report["comparison_account_facts"]["period_day"])
        self.assertEqual(2026,self.actual["primary"].observed_at.year)

    def test_actual_combined_2014_2021_role_is_not_two_roles_or_exact_cessation_day(self):
        claim=self.actual_review()["role_claims"][2]
        self.assertEqual("Chief Executive Officer and Director",claim["title_literal"])
        self.assertEqual("YEAR",claim["end_assertion"]["precision"])
        self.assertIsNone(claim["end_assertion"]["exact_day"])
        self.assertFalse(claim["exact_appointment_or_cessation_day_verified"])

    def test_actual_issuer_experience_range_is_not_legal_employment_continuity(self):
        experience=self.actual_review()["issuer_experience_assertion"]
        self.assertEqual("Experience: Microsoft Corporation (1992-present)",experience["source_clause"])
        self.assertEqual(1992,experience["start_assertion"]["year"])
        self.assertFalse(experience["employment_or_legal_continuity_verified"])

    def test_actual_context_byte_spans_and_full_normalized_quotes_are_exact(self):
        report=self.actual_review();body=self.actual["primary"].body
        for key in ("named_person_clause","issuer_career_clause"):
            context=report[key]
            raw=body[context["original_context_byte_start"]:context["original_context_byte_end"]]
            self.assertEqual(context["original_context_sha256"],hashlib.sha256(raw).hexdigest())
            self.assertEqual(context["quote_sha256"],hashlib.sha256(context["quote"].encode()).hexdigest())
            self.assertIn(b"<table" if key=="named_person_clause" else b"<td",raw[:20])
        self.assertEqual(SELECTION["person_context_quote"],report["named_person_clause"]["quote"])
        self.assertEqual(SELECTION["role_context_quote"],report["issuer_career_clause"]["quote"])

    def test_actual_dei_financial_period_is_distinct_from_role_and_filing_dates(self):
        report=self.actual_review();scope=report["source_issuer_scope"]
        self.assertEqual("0000789019",scope["issuer_cik"])
        self.assertEqual("Microsoft Corporation",scope["issuer_name_literal"])
        self.assertEqual(("2024-07-01","2025-06-30"),(scope["report_period_start_day"],scope["report_period_end_day"]))
        self.assertFalse(scope["report_period_is_biography_role_interval"])
        self.assertEqual("2025-10-21",report["source_document"]["filing_day"])
        self.assertFalse(report["source_document"]["original_biography_sgml_header_available"])
        self.assertTrue(report["source_document"]["original_inline_dei_header_validated"])

    def test_actual_unqualified_source_clocks_and_110_byte_conflict_are_preserved(self):
        report=self.actual_review();source=report["source_document"]
        self.assertEqual("2025-10-21 16:16:13",source["acceptance_datetime_unqualified_raw"])
        self.assertEqual("UNKNOWN",source["publication_timezone"])
        self.assertFalse(source["acceptance_timezone_inferred"])
        self.assertEqual(110,source["captured_primary_bytes"]-source["index_declared_primary_bytes"])
        self.assertFalse(source["exact_as_filed_body_version_verified"])
        self.assertIn("PUBLISHER_INDEX_SERVED_BODY_SIZE_CONFLICT_PRESERVED",report["gaps"])
        self.assertEqual("20260901180827",report["comparison_account_facts"]["acceptance_datetime_unqualified_raw"])
        self.assertEqual("UNKNOWN",report["comparison_account_facts"]["publication_timezone"])

    def test_actual_role_component_agreement_does_not_approve_names_or_cik_binding(self):
        report=self.actual_review();comparison=report["source_comparison"]
        self.assertEqual("Nadella Satya",report["comparison_account_facts"]["reporting_account"]["name"])
        self.assertTrue(comparison["literal_officer_title_component_agreement"])
        self.assertEqual([1,2],comparison["matching_bio_claim_indices"])
        self.assertFalse(comparison["bio_person_name_equals_raw_account_name"])
        self.assertFalse(comparison["issuer_raw_names_equal"])
        self.assertTrue(comparison["issuer_cik_scope_equal"])
        self.assertFalse(comparison["approved_person_or_issuer_name_alias"])
        self.assertFalse(comparison["independent_natural_person_account_binding"])

    def test_actual_assertions_and_packet_documents_are_not_multiple_independent_people_sources(self):
        comparison=self.actual_review()["source_comparison"]
        self.assertTrue(comparison["distinct_accessions"])
        self.assertEqual((2,1),(comparison["underlying_disclosure_count"],comparison["named_person_disclosure_count"]))
        self.assertFalse(comparison["account_packet_document_count_is_independent_disclosure_count"])
        self.assertFalse(comparison["distinct_disclosures_are_independent_legal_identity_proof"])

    def test_actual_plan_is_root_relative_whole_originals_with_exact_selected_request(self):
        plan=build_review_plan()
        self.assertEqual(plan,json.loads((EVIDENCE/"actual_review_plan.json").read_bytes()))
        self.assertEqual("SEC_PERSON_ROLE_CLAIM",plan["kind"])
        self.assertEqual({"selection":SELECTION},plan["request"])
        for key,row in plan["captures"].items():
            self.assertEqual(BODY_PATHS[key],row["body_ref"]["path"])
            self.assertNotIn("\\",row["body_ref"]["path"])
            self.assertFalse(Path(row["body_ref"]["path"]).is_absolute())
            self.assertEqual(PINS[key],(row["body_ref"]["bytes"],row["body_ref"]["sha256"]))

    def test_all_identity_continuity_scoring_registry_and_trading_promotions_remain_false(self):
        report=self.review()
        self.assertEqual("PARTIAL",report["status"])
        for key in ("natural_person_account_binding_verified","approved_name_alias","legal_role_interval_verified",
                    "continuous_role_verified","instrument_identity_verified","cusip_class_validity_verified",
                    "historical_machine_availability_inferred","issuer_attribution_allowed","registry_modified",
                    "scoring_applied","trading_allowed","quota_or_lease_modified"):
            self.assertIs(report[key],False,key)
        self.assertEqual(0,report["network_requests_performed"])

    def test_rehashed_person_name_and_exact_quote_drift_cannot_bind_another_biography(self):
        self.mutate_primary(self.captures["primary"].body.replace(b"Satya Nadella Age",b"Another Person Age"))
        with self.assertRaisesRegex(ValueError,"quote changed"):self.review()

    def test_neighboring_another_person_career_table_cannot_be_selected(self):
        selection={**self.selection,"role_context_locator":"table:4/row:1/cell:1"}
        with self.assertRaisesRegex(ValueError,"neighboring"):self.review(selection=selection)

    def test_intervening_other_person_clause_breaks_biography_career_association(self):
        self.mutate_primary(self.captures["primary"].body.replace(b"</table>\n<table>",b"</table><p>Another Person</p>\n<table>",1))
        with self.assertRaisesRegex(ValueError,"Intervening content"):self.review()

    def test_different_parent_scope_cannot_bind_adjacent_global_table_numbers(self):
        raw=self.captures["primary"].body.replace(b"</table>\n<table>",b"</table></div><div>\n<table>",1)
        self.mutate_primary(raw)
        with self.assertRaisesRegex(ValueError,"sibling table"):self.review()

    def test_hidden_biography_career_or_role_text_does_not_become_source_clause(self):
        for old,new in ((b"<table><tr><td>Satya",b'<table style="display:none"><tr><td>Satya'),
                        (b"<td>Experience:",b'<td hidden>Experience:'),
                        (b"Chairman and Chief Executive Officer",b'<span style="visibility:hidden">Chairman and Chief Executive Officer</span>')):
            original=self.captures["primary"].body
            self.mutate_primary(original.replace(old,new,1))
            with self.assertRaises(ValueError):self.review()
            self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def test_visible_script_injection_cannot_supply_a_missing_role_clause(self):
        raw=self.captures["primary"].body.replace(b"Chairman and Chief Executive Officer",b"<script>Chairman and Chief Executive Officer</script>",1)
        self.mutate_primary(raw)
        with self.assertRaisesRegex(ValueError,"quote changed"):self.review()

    def test_unclosed_selected_table_or_cell_is_rejected(self):
        self.mutate_primary(self.captures["primary"].body.replace(b"</td></tr></table>",b"</tr></table>"))
        with self.assertRaises(ValueError):self.review()

    def test_person_descriptor_cannot_silently_reorder_name_or_use_name_only(self):
        for selection in ({**self.selection,"person_name":"Nadella Satya"},
                          {**self.selection,"person_context_quote":"Satya Nadella"}):
            with self.assertRaises(ValueError):self.review(selection=selection)

    def test_dei_cik_name_type_context_and_identifier_are_bound_together(self):
        for old,new in ((b'name="dei:DocumentType" contextRef="period">DEF 14A',b'name="dei:DocumentType" contextRef="period">10-K'),
                        (b'name="dei:EntityCentralIndexKey" contextRef="period">0000789019',b'name="dei:EntityCentralIndexKey" contextRef="period">0000789020'),
                        (b'name="dei:EntityRegistrantName" contextRef="period"',b'name="dei:EntityRegistrantName" contextRef="other"'),
                        (b'<xbrli:identifier scheme="http://www.sec.gov/CIK">0000789019',b'<xbrli:identifier scheme="http://www.sec.gov/CIK">0000789020')):
            original=self.captures["primary"].body
            self.mutate_primary(original.replace(old,new,1))
            with self.assertRaises(ValueError):self.review()
            self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def test_duplicated_dei_fact_or_dimensional_issuer_context_is_rejected(self):
        fact=b'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="period">Microsoft Corporation</ix:nonNumeric>'
        original=self.captures["primary"].body
        for raw in (original.replace(fact,fact+fact),original.replace(b"<xbrli:entity>",b"<xbrli:entity><xbrli:segment></xbrli:segment>",1)):
            self.mutate_primary(raw)
            with self.assertRaises(ValueError):self.review()
            self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def test_financial_reporting_period_cannot_follow_filing_day_or_reverse(self):
        for old,new in ((b"2025-06-30",b"2026-06-30"),(b"2024-07-01",b"2025-07-01")):
            self.mutate_primary(self.captures["primary"].body.replace(old,new))
            with self.assertRaises(ValueError):self.review()
            self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def test_future_rehashed_role_year_is_not_supported_even_at_later_review_clock(self):
        raw=self.captures["primary"].body.replace(b"(2021-present)",b"(2099-present)")
        self.mutate_primary(raw)
        selection={**self.selection,"role_context_quote":self.selection["role_context_quote"].replace("(2021-present)","(2099-present)")}
        with self.assertRaisesRegex(ValueError,"disclosure scope"):self.review(selection=selection,clock=datetime(2100,1,1,tzinfo=timezone.utc))

    def test_reversed_role_range_or_unsupported_month_day_precision_is_not_invented(self):
        for old,new in (("(2014-2021)","(2021-2014)"),("(2021-present)","(2021-06-present)")):
            self.mutate_primary(self.captures["primary"].body.replace(old.encode(),new.encode()))
            selection={**self.selection,"role_context_quote":self.selection["role_context_quote"].replace(old,new)}
            with self.assertRaises(ValueError):self.review(selection=selection)
            self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def test_issuer_career_scope_cannot_be_replaced_by_other_employer_name(self):
        raw=self.captures["primary"].body.replace(b"Experience: Microsoft Corporation",b"Experience: Another Corporation")
        self.mutate_primary(raw)
        selection={**self.selection,"role_context_quote":self.selection["role_context_quote"].replace("Experience: Microsoft Corporation","Experience: Another Corporation")}
        with self.assertRaisesRegex(ValueError,"issuer experience scope"):self.review(selection=selection)

    def test_index_basename_accession_issuer_and_original_primary_row_are_exact(self):
        for index in (replace(self.captures["index"],source_url=self.captures["index"].source_url.replace("245150-index","245151-index")),
                      changed(self.captures["index"],self.captures["index"].body.replace(b">DEF 14A<",b">10-K<"))):
            with self.assertRaises(ValueError):self.review(captures={**self.captures,"index":index})

    def test_future_index_filing_day_cannot_be_backed_by_current_capture(self):
        index=changed(self.captures["index"],self.captures["index"].body.replace(b"2025-10-21",b"2099-10-21"))
        with self.assertRaises(ValueError):self.review(captures={**self.captures,"index":index},clock=datetime(2100,1,1,tzinfo=timezone.utc))

    def test_index_unqualified_acceptance_day_conflict_is_preserved_not_converted(self):
        raw=self.captures["index"].body.replace(b"2025-10-21 16:16:13",b"2025-10-22 16:16:13")
        report=self.review(captures={**self.captures,"index":changed(self.captures["index"],raw)})
        self.assertFalse(report["dated_role_source_clock_consistent"])
        self.assertIn("INDEX_ACCEPTANCE_DATE_DIFFERS_FROM_FILING_DAY",report["gaps"])
        self.assertEqual("UNKNOWN",report["source_document"]["publication_timezone"])

    def test_missing_or_invalid_acceptance_display_does_not_invent_publication_time(self):
        for value in (b"UNKNOWN",b"2025-13-21 16:16:13"):
            raw=self.captures["index"].body.replace(b"2025-10-21 16:16:13",value)
            with self.assertRaises(ValueError):self.review(captures={**self.captures,"index":changed(self.captures["index"],raw)})

    def test_account_descriptor_names_ciks_title_form_and_source_must_match_original_packet(self):
        for key,value in (("owner_name","Satya Nadella"),("owner_cik","0001513143"),("issuer_name","Microsoft Corporation"),
                          ("officer_title","Chairman and CEO"),("form","3"),("accession","0000789019-26-000161")):
            selection=deepcopy(self.selection);selection["account_descriptor"][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.review(selection=selection)

    def test_rehashed_account_header_drift_cannot_inherit_original_xml_names(self):
        account=self.captures["account_packet"]
        raw=account.body.replace(b"Nadella Satya",b"Satya Nadella",1)
        with self.assertRaises(ValueError):self.review(captures={**self.captures,"account_packet":changed(account,raw)})

    def test_account_future_source_date_and_wrong_packet_url_are_rejected(self):
        account=self.captures["account_packet"]
        for capture in (changed(account,account.body.replace(b"DATE AS OF CHANGE:\t\t20260901",b"DATE AS OF CHANGE:\t\t20990901",1)),
                        replace(account,source_url=account.source_url.replace("000157.txt","000158.txt"))):
            with self.assertRaises(ValueError):self.review(captures={**self.captures,"account_packet":capture})

    def test_same_accession_is_counted_as_one_disclosure_not_two_independent_sources(self):
        captures,selection=synthetic_inputs(self.actual["account_packet"],accession="0000789019-26-000157")
        comparison=self.review(captures=captures,selection=selection)["source_comparison"]
        self.assertFalse(comparison["distinct_accessions"])
        self.assertEqual(1,comparison["underlying_disclosure_count"])
        self.assertFalse(comparison["independent_natural_person_account_binding"])

    def test_missing_withheld_or_access_bearing_whole_body_is_partial_before_any_parser(self):
        for key,capture in self.captures.items():
            withheld=replace(capture,body=None,retention="WITHHELD_SENSITIVE_BODY")
            with patch(MODULE+"._RoleDocument",side_effect=AssertionError("No body parser")):
                report=self.review(captures={**self.captures,key:withheld})
            self.assertFalse(report["source_role_claims_supported"])
            self.assertEqual(["REQUIRED_WHOLE_ORIGINAL_UNAVAILABLE_NO_SUBSTITUTE"],report["gaps"])
        primary=self.captures["primary"]
        raw=primary.body+b' api_key="synthetic-test-value-never-real-credential"'
        report=self.review(captures={**self.captures,"primary":changed(primary,raw)})
        self.assertEqual("WITHHELD_SENSITIVE_BODY",report["captures"]["primary"]["retention"])
        self.assertNotIn("synthetic-test-value",json.dumps(report))

    def test_incomplete_or_withheld_body_cannot_have_replacement_bytes(self):
        primary=self.captures["primary"]
        for capture in (replace(primary,retention="WITHHELD_SENSITIVE_BODY"),
                        replace(primary,retention="NOT_CAPTURED_COMPLETE_BODY")):
            with self.assertRaises(ValueError):self.review(captures={**self.captures,"primary":capture})

    def test_hash_size_ids_and_encoded_source_bodies_are_checked(self):
        primary=self.captures["primary"]
        for capture in (replace(primary,sha256="0"*64),replace(primary,size_bytes=primary.size_bytes+1),
                        replace(primary,record_id=self.captures["index"].record_id),
                        changed(primary,primary.body.decode().encode("utf-16"))):
            with self.assertRaises(ValueError):self.review(captures={**self.captures,"primary":capture})

    def test_new_offline_import_never_backdates_knowledge_to_the_old_source_capture(self):
        cutoff=self.actual["account_packet"].observed_at
        report=self.review(knowledge_at=cutoff)
        self.assertEqual(cutoff.isoformat(),report["requested_knowledge_at"])
        self.assertEqual(["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"],report["gaps"])
        self.assertGreater(datetime.fromisoformat(report["review_available_at"]),cutoff)
        self.assertFalse(report["source_role_claims_supported"])
        self.assertNotIn("role_claims",report)

    def test_default_none_uses_physical_import_with_original_input_clocks_preserved(self):
        report=self.review()
        self.assertIsNone(report["requested_knowledge_at"])
        self.assertEqual(self.physical.isoformat(),report["review_ingested_at"])
        self.assertEqual(max(c.available_at for c in self.captures.values()).isoformat(),report["source_inputs_available_at"])

    def test_future_cutoff_input_availability_and_physical_rollback_do_not_produce_facts(self):
        with self.assertRaises(ValueError):self.review(knowledge_at=self.physical+timedelta(seconds=1))
        primary=replace(self.captures["primary"],known_at=self.physical+timedelta(seconds=1))
        report=self.review(captures={**self.captures,"primary":primary})
        self.assertEqual(["ORIGINAL_INPUT_NOT_YET_KNOWN"],report["gaps"])
        with patch(MODULE+"._physical_clock",side_effect=[self.physical,self.physical-timedelta(seconds=1)]):
            with self.assertRaisesRegex(ValueError,"rolled back"):
                review_sec_person_role_claims(primary_capture=self.captures["primary"],index_capture=self.captures["index"],
                    account_packet_capture=self.captures["account_packet"],selection=self.selection)

    def test_bounded_selection_and_original_complexity_limits_reject_unbounded_work(self):
        for selection in ({**self.selection,"extra":"unsupported"},{**self.selection,"person_context_quote":"x"*20_000},
                          {**self.selection,"role_context_locator":"table:999999999/row:1/cell:1"}):
            with self.assertRaises(ValueError):self.review(selection=selection)
        primary=self.captures["primary"]
        for raw in (primary.body+b"<div>"*130,primary.body+b"<br>"*200_001):
            self.mutate_primary(raw)
            with self.assertRaises(ValueError):self.review()
            self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])

    def test_exact_source_pinning_rejects_same_length_rehashed_local_body_drift(self):
        original_read=Path.read_bytes
        target=REPO/BODY_PATHS["primary"]
        def read(path):
            raw=original_read(path)
            return raw[:-1]+b" " if path==target else raw
        with patch("pathlib.Path.read_bytes",read):
            with self.assertRaisesRegex(ValueError,"source bytes changed"):load_inputs()

    def test_pure_review_never_opens_files_network_database_or_publishes_changes(self):
        with patch("socket.socket.connect",side_effect=AssertionError("No HTTP")),\
             patch("sqlite3.connect",side_effect=AssertionError("No DB")),\
             patch("pathlib.Path.open",side_effect=AssertionError("No file IO in pure review")):
            report=self.review()
        self.assertTrue(report["source_role_claims_supported"])

    def test_unselected_other_subject_inside_career_table_cannot_donate_role_rows(self):
        raw=self.captures["primary"].body.replace(b"<table><tr><td>Experience:",
            b"<table><tr><td>Amy Hood Age: 54 | Director Since: 2019 | Birthplace: USA</td></tr><tr><td>Experience:",1)
        donor="Experience: Microsoft Corporation (2002-present) • Chief Financial Officer (2013-present)"
        raw=raw.replace(self.selection["role_context_quote"].encode(),donor.encode(),1)
        self.mutate_primary(raw)
        selection={**self.selection,"role_context_locator":"table:2/row:2/cell:1","role_context_quote":donor}
        with self.assertRaisesRegex(ValueError,"inside career table"):self.review(selection=selection)

    def test_even_identical_unselected_prefix_role_text_cannot_donate_a_later_selected_cell(self):
        career=self.selection["role_context_quote"].encode()
        raw=self.captures["primary"].body.replace(b"<table><tr><td>Experience:",
            b"<table><tr><td>"+career+b"</td></tr><tr><td>Experience:",1)
        self.mutate_primary(raw)
        with self.assertRaisesRegex(ValueError,"inside career table"):
            self.review(selection={**self.selection,"role_context_locator":"table:2/row:2/cell:1"})

    def test_unselected_trailing_named_biography_makes_career_table_subject_scope_ambiguous(self):
        raw=self.captures["primary"].body.replace(b"</table></div>",
            b"<tr><td>Amy Hood Age: 54 | Director Since: 2019 | Birthplace: USA</td></tr></table></div>",1)
        self.mutate_primary(raw)
        with self.assertRaisesRegex(ValueError,"inside career table"):self.review()

    def test_required_index_clock_labels_and_values_must_be_visible_in_own_context(self):
        original=self.captures["index"]
        for old,new in ((b'<div class="info">2025-10-21</div>',b'<div class="info" hidden="hidden">2025-10-21</div>'),
            (b'<div class="infoHead">Filing Date</div>',b'<div class="infoHead" style="display: none">Filing Date</div>'),
            (b'<div class="info">2025-10-21 16:16:13</div>',b'<div class="info" aria-hidden="true">2025-10-21 16:16:13</div>'),
            (b'<div class="info">2025-10-21</div>',b'<div hidden><div class="info">2025-10-21</div></div>'),
            (b'<div class="info">2025-10-21</div>',b'<div class="info"><span style="visibility:hidden">2025-10-21</span></div>')):
            self.assertIn(old,original.body)
            with self.subTest(new=new),self.assertRaisesRegex(ValueError,"clock is hidden"):
                self.review(captures={**self.captures,"index":changed(original,original.body.replace(old,new,1))})

    def test_hidden_clock_cannot_borrow_equal_visible_day_from_an_unrelated_index_paragraph(self):
        index=self.captures["index"]
        raw=index.body.replace(b'<div class="info">2025-10-21</div>',b'<div class="info" hidden>2025-10-21</div>',1)
        raw+=b"<p>Filing Date 2025-10-21 Accepted 2025-10-21 16:16:13</p>"
        with self.assertRaisesRegex(ValueError,"clock is hidden"):
            self.review(captures={**self.captures,"index":changed(index,raw)})

    def test_completion_after_explicit_cutoff_withholds_interpreted_facts_and_retains_physical_availability(self):
        ended=self.physical+timedelta(seconds=2)
        with patch(MODULE+"._physical_clock",side_effect=[self.physical,ended]):
            report=review_sec_person_role_claims(primary_capture=self.captures["primary"],index_capture=self.captures["index"],
                account_packet_capture=self.captures["account_packet"],selection=self.selection,knowledge_at=self.physical)
        self.assertFalse(report["source_role_claims_supported"])
        self.assertFalse(report["dated_role_source_clock_consistent"])
        self.assertEqual(self.physical.isoformat(),report["requested_knowledge_at"])
        self.assertEqual(ended.isoformat(),report["review_available_at"])
        self.assertEqual(["INTERPRETATION_COMPLETED_AFTER_REQUESTED_KNOWLEDGE_CUTOFF"],report["gaps"])
        for key in ("role_claims","comparison_account_facts","source_comparison","source_document","source_issuer_scope"):
            self.assertNotIn(key,report)

    def test_default_current_interpretation_uses_actual_completion_without_an_artificial_start_cutoff(self):
        ended=self.physical+timedelta(seconds=2)
        with patch(MODULE+"._physical_clock",side_effect=[self.physical,ended]):
            report=review_sec_person_role_claims(primary_capture=self.captures["primary"],index_capture=self.captures["index"],
                account_packet_capture=self.captures["account_packet"],selection=self.selection)
        self.assertTrue(report["source_role_claims_supported"])
        self.assertIsNone(report["requested_knowledge_at"])
        self.assertEqual(ended.isoformat(),report["review_available_at"])

    def test_actual_current_career_table_scope_has_first_visible_content_inside_selected_cell(self):
        report=self.actual_review();table=report["career_table_scope"];cell=report["issuer_career_clause"]
        self.assertEqual("table:164",table["locator"])
        self.assertGreaterEqual(table["first_visible_data_byte_start"],cell["original_context_byte_start"])
        self.assertLess(table["first_visible_data_byte_start"],cell["original_context_byte_end"])


if __name__=="__main__":
    unittest.main()
