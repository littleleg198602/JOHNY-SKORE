"""Actual temporal exclusion and bounded synthetic day/precision controls."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from evidence.batch11_sec_20261018.build_review import (
    ROOT as EVIDENCE, REPO, PINS, BODY_PATHS, TEMPORAL_REQUEST,
    load_inputs, build_review_artifact, build_review_plan,
)
from tests.test_sec_person_role_claim_review import changed, synthetic_inputs
from market_checker_app.services.sec_person_role_temporal_review import (
    MAX_TRANSACTIONS, review_sec_person_role_temporal,
)

MODULE="market_checker_app.services.sec_person_role_temporal_review"
SOURCE="market_checker_app.collectors.sec_person_role_claim_review"


class SecPersonRoleTemporalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual,cls.actual_selection,cls.actual_request,cls.receipts=load_inputs()
        cls.golden=json.loads((EVIDENCE/"person_role_temporal_review_v1.json").read_bytes())
        cls.physical=datetime.fromisoformat(cls.golden["review"]["review_ingested_at"])

    def setUp(self):
        self.captures,self.selection=synthetic_inputs(self.actual["account_packet"])
        self.request=deepcopy(TEMPORAL_REQUEST)

    def review(self,*,captures=None,selection=None,request=None,knowledge_at=None,outer_clocks=None,source_clocks=None):
        captures=self.captures if captures is None else captures
        outer={"side_effect":outer_clocks} if outer_clocks is not None else {"return_value":self.physical}
        inner={"side_effect":source_clocks} if source_clocks is not None else {"return_value":self.physical}
        with patch(MODULE+"._physical_clock",**outer),patch(SOURCE+"._physical_clock",**inner):
            return review_sec_person_role_temporal(primary_capture=captures["primary"],index_capture=captures["index"],
                account_packet_capture=captures["account_packet"],selection=self.selection if selection is None else selection,
                temporal_request=self.request if request is None else request,knowledge_at=knowledge_at)

    def actual_review(self,**kwargs):
        return self.review(captures=self.actual,selection=self.actual_selection,request=self.actual_request,**kwargs)

    def fixture_day(self,day,*,role="Chief Executive Officer and Director"):
        """Rehashed hypothetical packet, not a new source disclosure or execution."""
        packet=self.captures["account_packet"]
        raw=packet.body.replace(b"2026-08-31",day.encode()).replace(b"20260831",day.replace("-","").encode())
        self.captures["account_packet"]=changed(packet,raw,record_id="synthetic-temporal-packet")
        self.request.update(requested_transaction_day=day,role_title_literal=role)

    def replace_first_date(self,clause):
        packet=self.captures["account_packet"]
        original=self.golden["review"]["transaction"]["original_date_element_quote"].encode()
        self.assertIn(original,packet.body)
        self.captures["account_packet"]=changed(packet,packet.body.replace(original,clause,1),record_id="synthetic-date-packet")

    def test_actual_three_whole_originals_and_full_temporal_replay_are_hash_pinned(self):
        review=self.golden["review"]
        clocks={"temporal":[review[key] for key in ("review_ingested_at","review_available_at")],
            "source":[review["source_role_review"][key] for key in ("review_ingested_at","review_available_at")]}
        self.assertEqual(self.golden,build_review_artifact(replay_clocks=clocks))
        for key,capture in self.actual.items():
            self.assertEqual(PINS[key],(len(capture.body),hashlib.sha256(capture.body).hexdigest()))
        self.assertEqual(0,self.golden["actual_new_http_requests"])

    def test_actual_2026_transaction_is_outside_2025_proxy_present_scope(self):
        report=self.actual_review();bounds=report["selected_role_temporal_assertion"]
        self.assertTrue(report["source_temporal_assessment_supported"])
        self.assertTrue(report["requested_transaction_binding_supported"])
        self.assertEqual("UNVERIFIED",report["role_at_transaction_day_status"])
        self.assertEqual("2026-08-31",report["transaction"]["date_literal"])
        self.assertEqual("2025-10-21",bounds["source_extent_upper_day"])
        self.assertFalse(bounds["source_assertion_temporally_compatible"])
        self.assertEqual("OUTSIDE_SOURCE_ASSERTION_SCOPE",bounds["source_temporal_fit"])
        self.assertIn("REQUESTED_DAY_AFTER_PROXY_ASSERTION_ANCHOR_NO_CONTINUITY",report["gaps"])

    def test_actual_xml_row_and_date_quote_bind_to_exact_original_packet_bytes(self):
        report=self.actual_review();transaction=report["transaction"];packet=self.actual["account_packet"].body
        self.assertEqual((8214,"c71fd4237b551e70fd8054cfd54b9d101e63d689d0a26b10bb8bd123a0a4c810"),
            (transaction["original_xml_bytes"],transaction["original_xml_sha256"]))
        raw=packet[transaction["original_packet_row_byte_start"]:transaction["original_packet_row_byte_end"]]
        self.assertEqual(transaction["original_row_sha256"],hashlib.sha256(raw).hexdigest())
        self.assertEqual("9aac2a24af5bffdd4cc4641175d10aecec28d4c702aea39b5552d2df15823118",transaction["original_row_sha256"])
        date_raw=transaction["original_date_element_quote"].encode()
        self.assertIn(date_raw,raw)
        self.assertEqual(transaction["original_date_element_sha256"],hashlib.sha256(date_raw).hexdigest())
        self.assertEqual("DAY",transaction["date_precision"])

    def test_actual_account_role_is_as_filed_not_natural_person_or_transaction_role_identity(self):
        report=self.actual_review();account=report["source_account"]
        self.assertEqual({"cik":"0001513142","name":"Nadella Satya"},account["reporting_account"])
        self.assertEqual("Chief Executive Officer",account["role_as_filed"]["officerTitle"])
        self.assertEqual(("2026-08-31","2026-09-01"),(account["period_day"],account["filing_day"]))
        self.assertFalse(report["as_filed_account_role_is_transaction_day_role_proof"])
        self.assertFalse(report["natural_person_account_binding_verified"])
        self.assertFalse(report["source_comparison"]["bio_person_name_equals_raw_account_name"])
        self.assertFalse(report["transaction"]["date_is_execution_or_person_identity_proof"])

    def test_actual_index_conflict_and_unqualified_publication_clocks_remain_explicit(self):
        report=self.actual_review();source=report["source_document"]
        self.assertEqual(110,source["captured_primary_bytes"]-source["index_declared_primary_bytes"])
        self.assertIn("PUBLISHER_INDEX_SERVED_BODY_SIZE_CONFLICT_PRESERVED",report["gaps"])
        self.assertFalse(source["exact_as_filed_body_version_verified"])
        self.assertEqual("2025-10-21 16:16:13",source["acceptance_datetime_unqualified_raw"])
        self.assertEqual("UNKNOWN",source["publication_timezone"])
        self.assertEqual("20260901180827",report["source_account"]["acceptance_datetime_unqualified_raw"])
        self.assertEqual("UNKNOWN",report["source_account"]["publication_timezone"])

    def test_actual_one_selected_assertion_does_not_repeat_or_multiply_seven_bio_roles(self):
        report=self.actual_review()
        self.assertNotIn("role_claims",report)
        self.assertEqual("Chairman and Chief Executive Officer",report["selected_role_temporal_assertion"]["literal_title"])
        self.assertEqual((2,1),(report["source_comparison"]["underlying_disclosure_count"],report["source_comparison"]["named_person_disclosure_count"]))
        self.assertTrue(report["source_role_review"]["rebuilt_from_whole_originals"])
        self.assertFalse(report["source_role_review"]["caller_report_accepted"])

    def test_actual_plan_has_only_safe_whole_references_and_exact_temporal_selection(self):
        plan=build_review_plan()
        self.assertEqual(plan,json.loads((EVIDENCE/"actual_review_plan.json").read_bytes()))
        self.assertEqual("SEC_PERSON_ROLE_TEMPORAL",plan["kind"])
        self.assertEqual({"selection","temporal_request"},set(plan["request"]))
        self.assertEqual(self.actual_request,plan["request"]["temporal_request"])
        for key,row in plan["captures"].items():
            self.assertEqual(BODY_PATHS[key],row["body_ref"]["path"])
            self.assertNotIn("\\",row["body_ref"]["path"])
            raw=(REPO/row["body_ref"]["path"]).read_bytes()
            self.assertEqual((row["body_ref"]["bytes"],row["body_ref"]["sha256"]),(len(raw),hashlib.sha256(raw).hexdigest()))

    def test_since_start_year_remains_a_day_uncertainty_not_january_first_appointment(self):
        self.fixture_day("2014-01-01",role="Director")
        bounds=self.review()["selected_role_temporal_assertion"]
        self.assertEqual("YEAR_BOUNDARY_DAY_UNCERTAIN",bounds["source_temporal_fit"])
        self.assertIsNone(bounds["source_assertion_temporally_compatible"])
        self.assertEqual(("2014-01-01","2014-12-31"),(bounds["start_day_bounds"]["earliest_start_day_in_stated_year"],bounds["start_day_bounds"]["latest_start_day_in_stated_year"]))
        self.assertIsNone(bounds["start_day_bounds"]["exact_appointment_day"])
        self.assertTrue(bounds["bounds_are_precision_envelopes_not_effective_role_dates"])

    def test_last_day_of_start_year_does_not_gain_exact_appointment_or_inclusivity(self):
        self.fixture_day("2014-12-31",role="Director")
        bounds=self.review()["selected_role_temporal_assertion"]
        self.assertIsNone(bounds["source_assertion_temporally_compatible"])
        self.assertIn("START_YEAR_APPOINTMENT_DAY_UNRESOLVED",bounds["precision_uncertainties"])

    def test_day_before_start_year_is_excluded_without_asserting_legal_nonemployment(self):
        self.fixture_day("2013-12-31",role="Director")
        report=self.review();bounds=report["selected_role_temporal_assertion"]
        self.assertFalse(bounds["source_assertion_temporally_compatible"])
        self.assertIn("REQUESTED_DAY_BEFORE_ASSERTED_START_YEAR",report["gaps"])
        self.assertEqual("UNVERIFIED",report["role_at_transaction_day_status"])

    def test_interior_of_stated_year_range_is_only_coarse_compatibility(self):
        self.fixture_day("2015-01-01")
        report=self.review();bounds=report["selected_role_temporal_assertion"]
        self.assertTrue(bounds["source_assertion_temporally_compatible"])
        self.assertEqual("COARSE_LITERAL_RANGE_COMPATIBLE_NO_LEGAL_INTERVAL",bounds["source_temporal_fit"])
        self.assertFalse(bounds["continuous_legal_interval_established"])
        self.assertFalse(report["exact_role_interval_verified"])
        self.assertEqual("UNVERIFIED",report["role_at_transaction_day_status"])

    def test_first_and_last_days_of_end_year_keep_unknown_cessation_and_inclusivity(self):
        for day in ("2021-01-01","2021-12-31"):
            self.fixture_day(day)
            bounds=self.review()["selected_role_temporal_assertion"]
            self.assertIsNone(bounds["source_assertion_temporally_compatible"])
            self.assertIn("END_YEAR_CESSATION_DAY_AND_INCLUSIVITY_UNRESOLVED",bounds["precision_uncertainties"])
            self.assertEqual("UNKNOWN",bounds["stated_end_day_bounds"]["boundary_inclusivity"])
            self.setUp()

    def test_day_after_stated_end_year_has_no_automatic_role_extension(self):
        self.fixture_day("2022-01-01")
        report=self.review()
        self.assertFalse(report["selected_role_temporal_assertion"]["source_assertion_temporally_compatible"])
        self.assertIn("REQUESTED_DAY_AFTER_ASSERTED_END_YEAR",report["gaps"])

    def test_present_anchor_day_is_only_a_source_disclosure_point_not_a_cessation_date(self):
        self.fixture_day("2025-10-21",role="Chairman and Chief Executive Officer")
        bounds=self.review()["selected_role_temporal_assertion"]
        self.assertEqual("SOURCE_DISCLOSURE_POINT_ASSERTION_ONLY",bounds["source_temporal_fit"])
        self.assertTrue(bounds["source_assertion_temporally_compatible"])
        self.assertIsNone(bounds["stated_end_day_bounds"])
        self.assertFalse(bounds["source_extent_upper_is_cessation_date"])
        self.assertFalse(bounds["exact_role_at_day_established"])

    def test_next_day_after_present_anchor_is_excluded_even_with_matching_as_filed_officer(self):
        self.fixture_day("2025-10-22",role="Chairman and Chief Executive Officer")
        report=self.review()
        self.assertFalse(report["selected_role_temporal_assertion"]["source_assertion_temporally_compatible"])
        self.assertEqual("Chief Executive Officer",report["source_account"]["role_as_filed"]["officerTitle"])
        self.assertFalse(report["as_filed_account_role_is_transaction_day_role_proof"])

    def test_financial_reporting_period_is_not_used_to_exclude_older_retrospective_role_assertions(self):
        self.fixture_day("2015-06-15")
        report=self.review()
        self.assertEqual("2024-07-01",report["source_issuer_scope"]["report_period_start_day"])
        self.assertTrue(report["selected_role_temporal_assertion"]["source_assertion_temporally_compatible"])
        self.assertFalse(report["proxy_financial_period_used_as_role_validity_interval"])
        self.assertFalse(report["historical_machine_availability_inferred"])

    def test_requested_day_must_match_original_transaction_and_not_the_period_or_caller_hint(self):
        report=self.review(request={**self.request,"requested_transaction_day":"2026-08-30"})
        self.assertFalse(report["requested_transaction_binding_supported"])
        self.assertFalse(report["source_temporal_assessment_supported"])
        self.assertEqual("2026-08-31",report["transaction"]["date_literal"])
        self.assertIn("REQUESTED_DAY_DOES_NOT_MATCH_ORIGINAL_XML_TRANSACTION",report["gaps"])

    def test_two_same_day_transactions_have_distinct_original_row_identity(self):
        first=self.review()["transaction"]
        second=self.review(request={**self.request,"transaction_ordinal":2})["transaction"]
        self.assertEqual(first["date_literal"],second["date_literal"])
        self.assertNotEqual(first["original_row_sha256"],second["original_row_sha256"])
        self.assertLess(first["original_packet_row_byte_start"],second["original_packet_row_byte_start"])

    def test_absent_derivative_or_third_row_is_not_a_fabricated_dated_transaction(self):
        for request in ({**self.request,"transaction_kind":"derivativeTransaction"},{**self.request,"transaction_ordinal":3}):
            report=self.review(request=request)
            self.assertIsNone(report["transaction"])
            self.assertFalse(report["source_temporal_assessment_supported"])
            self.assertIn("SELECTED_ORIGINAL_TRANSACTION_ROW_ABSENT",report["gaps"])

    def test_missing_original_date_does_not_fall_back_to_report_period_or_request_day(self):
        self.replace_first_date(b"<transactionDate></transactionDate>")
        report=self.review()
        self.assertIsNone(report["transaction"]["date_literal"])
        self.assertEqual("UNRESOLVED",report["transaction"]["date_precision"])
        self.assertIn("ORIGINAL_TRANSACTION_DATE_MISSING_NO_PERIOD_OR_REQUEST_SUBSTITUTE",report["gaps"])
        self.assertFalse(report["requested_transaction_binding_supported"])

    def test_invalid_or_after_filing_original_date_retains_conflict_without_temporal_support(self):
        for day,gap in (("2026-99-99","ORIGINAL_TRANSACTION_DATE_INVALID"),("2026-09-02","ORIGINAL_TRANSACTION_DATE_AFTER_FILING_DAY")):
            self.replace_first_date(("<transactionDate><value>"+day+"</value></transactionDate>").encode())
            report=self.review()
            self.assertIn(gap,report["gaps"])
            self.assertFalse(report["source_temporal_assessment_supported"])
            self.setUp()

    def test_date_before_report_period_is_a_source_conflict_not_an_alternative_event_clock(self):
        self.replace_first_date(b"<transactionDate><value>2026-08-30</value></transactionDate>")
        report=self.review(request={**self.request,"requested_transaction_day":"2026-08-30"})
        self.assertIn("ORIGINAL_TRANSACTION_DATE_BEFORE_REPORT_PERIOD",report["gaps"])
        self.assertFalse(report["source_temporal_assessment_supported"])

    def test_qualified_date_is_not_promoted_without_footnote_review(self):
        self.replace_first_date(b'<transactionDate><value>2026-08-31</value><footnoteId id="F1"/></transactionDate>')
        report=self.review()
        self.assertEqual(["F1"],report["transaction"]["date_qualification_footnote_ids"])
        self.assertIn("TRANSACTION_DATE_FOOTNOTE_QUALIFICATION_NOT_REVIEWED",report["gaps"])
        self.assertFalse(report["source_temporal_assessment_supported"])

    def test_comments_with_fake_transaction_markup_are_not_rows_or_date_evidence(self):
        packet=self.captures["account_packet"]
        fake=b"<!-- <nonDerivativeTransaction><transactionDate><value>2014-01-01</value></transactionDate></nonDerivativeTransaction> -->"
        raw=packet.body.replace(b"<footnotes>",b"<footnotes>"+fake,1)
        self.assertNotEqual(raw,packet.body)
        report=self.review(captures={**self.captures,"account_packet":changed(packet,raw)})
        self.assertEqual(2,report["original_transaction_row_counts"]["nonDerivativeTransaction"])
        self.assertEqual("2026-08-31",report["transaction"]["date_literal"])

    def test_holding_generic_career_component_alias_and_unbounded_selectors_are_rejected(self):
        bad=[{**self.request,"transaction_kind":"nonDerivativeHolding"},{**self.request,"transaction_kind":[]},
             {**self.request,"transaction_ordinal":True},{**self.request,"transaction_ordinal":0},
             {**self.request,"transaction_ordinal":MAX_TRANSACTIONS+1},{**self.request,"requested_transaction_day":"20260831"},
             {**self.request,"role_title_literal":"Chief Executive Officer"},
             {**self.request,"role_title_literal":"Various positions of increasing authority"}]
        for request in bad:
            with self.subTest(request=request),self.assertRaises(ValueError):self.review(request=request)

    def test_prefilled_report_or_verified_flag_cannot_replace_whole_original_captures(self):
        with self.assertRaises(ValueError):self.review(request={**self.request,"role_at_day_verified":True})
        with self.assertRaises(ValueError):self.review(captures={**self.captures,"primary":{"source_role_claims_supported":True}})
        with self.assertRaises(ValueError):self.review(selection={"role_claims":[{"title":"Chief Executive Officer","verified":True}]})

    def test_actual_generic_career_label_cannot_be_selected_as_a_named_role(self):
        with self.assertRaisesRegex(ValueError,"named-role assertion"):
            self.review(captures=self.actual,selection=self.actual_selection,
                request={**self.actual_request,"role_title_literal":"Various positions of increasing authority"})

    def test_original_transaction_count_is_bounded_even_if_caller_selects_only_first_row(self):
        packet=self.captures["account_packet"]
        transaction=self.golden["review"]["transaction"]
        row=packet.body[transaction["original_packet_row_byte_start"]:transaction["original_packet_row_byte_end"]]
        raw=packet.body.replace(row,row*(MAX_TRANSACTIONS+1),1)
        with self.assertRaisesRegex(ValueError,"row bound"):
            self.review(captures={**self.captures,"account_packet":changed(packet,raw)})

    def test_missing_withheld_or_access_bearing_whole_body_is_partial_before_child_or_xml_parsing(self):
        for key,capture in self.captures.items():
            withheld=replace(capture,body=None,retention="WITHHELD_SENSITIVE_BODY")
            with patch(MODULE+".review_sec_person_role_claims",side_effect=AssertionError("No child parsing")):
                report=self.review(captures={**self.captures,key:withheld})
            self.assertFalse(report["source_temporal_assessment_supported"])
            self.assertNotIn("transaction",report)
        packet=self.captures["account_packet"]
        raw=packet.body+b' api_key="synthetic-test-value-never-real-credential"'
        report=self.review(captures={**self.captures,"account_packet":changed(packet,raw)})
        self.assertEqual("WITHHELD_SENSITIVE_BODY",report["captures"]["account_packet"]["retention"])
        self.assertNotIn("synthetic-test-value",json.dumps(report))

    def test_hash_size_duplicate_ids_and_source_account_drift_cannot_supply_temporal_evidence(self):
        packet=self.captures["account_packet"]
        for capture in (replace(packet,sha256="0"*64),replace(packet,size_bytes=packet.size_bytes+1),
            replace(packet,record_id=self.captures["primary"].record_id),
            changed(packet,packet.body.replace(b"Nadella Satya",b"Satya Nadella",1))):
            with self.assertRaises(ValueError):self.review(captures={**self.captures,"account_packet":capture})

    def test_role_support_from_conflicting_proxy_source_clock_is_withheld(self):
        index=self.captures["index"]
        raw=index.body.replace(b"2025-10-21 16:16:13",b"2025-10-22 16:16:13")
        report=self.review(captures={**self.captures,"index":changed(index,raw)})
        self.assertFalse(report["source_temporal_assessment_supported"])
        self.assertIn("DATED_SOURCE_ROLE_ASSERTION_UNAVAILABLE",report["gaps"])
        self.assertNotIn("selected_role_temporal_assertion",report)

    def test_new_temporal_interpretation_cannot_be_backdated_to_capture_or_event_day(self):
        cutoff=self.actual["account_packet"].observed_at
        report=self.actual_review(knowledge_at=cutoff)
        self.assertEqual(cutoff.isoformat(),report["requested_knowledge_at"])
        self.assertFalse(report["source_temporal_assessment_supported"])
        self.assertNotIn("transaction",report)
        self.assertGreater(datetime.fromisoformat(report["known_at"]),cutoff)

    def test_completion_after_explicit_cutoff_clears_all_interpreted_transaction_and_role_facts(self):
        ended=self.physical+timedelta(seconds=2)
        report=self.review(knowledge_at=self.physical,outer_clocks=[self.physical,ended])
        self.assertEqual(ended.isoformat(),report["known_at"])
        self.assertEqual(report["known_at"],report["review_available_at"])
        self.assertFalse(report["source_temporal_assessment_supported"])
        self.assertEqual(["INTERPRETATION_COMPLETED_AFTER_REQUESTED_KNOWLEDGE_CUTOFF"],report["gaps"])
        for key in ("transaction","selected_role_temporal_assertion","source_account","source_document","source_role_review"):
            self.assertNotIn(key,report)

    def test_default_current_interpretation_has_physical_completion_and_nested_source_known_within_it(self):
        ended=self.physical+timedelta(seconds=2)
        report=self.review(outer_clocks=[self.physical,ended])
        self.assertIsNone(report["requested_knowledge_at"])
        self.assertTrue(report["source_temporal_assessment_supported"])
        self.assertEqual(ended.isoformat(),report["known_at"])
        self.assertLessEqual(datetime.fromisoformat(report["source_role_review"]["review_available_at"]),ended)

    def test_future_cutoff_inputs_parent_rollback_and_child_clock_scope_are_rejected(self):
        with self.assertRaises(ValueError):self.review(knowledge_at=self.physical+timedelta(seconds=1))
        with self.assertRaises(ValueError):self.review(outer_clocks=[self.physical,self.physical-timedelta(seconds=1)])
        with self.assertRaises(ValueError):self.review(source_clocks=[self.physical-timedelta(seconds=1)]*2)
        with self.assertRaises(ValueError):self.review(source_clocks=[self.physical,self.physical+timedelta(seconds=3)])
        primary=replace(self.captures["primary"],known_at=self.physical+timedelta(seconds=1))
        report=self.review(captures={**self.captures,"primary":primary})
        self.assertEqual(["ORIGINAL_INPUT_NOT_YET_KNOWN"],report["gaps"])
        self.assertFalse(report["source_temporal_assessment_supported"])

    def test_temporal_compatibility_never_promotes_legal_identity_interval_or_acceptance(self):
        self.fixture_day("2015-06-15")
        report=self.review()
        self.assertTrue(report["selected_role_temporal_assertion"]["source_assertion_temporally_compatible"])
        self.assertEqual("UNVERIFIED",report["role_at_transaction_day_status"])
        for key in ("natural_person_account_binding_verified","approved_name_alias","exact_role_interval_verified",
            "continuous_role_verified","instrument_identity_verified","cusip_class_validity_verified",
            "historical_machine_availability_inferred","execution_verified","issuer_attribution_allowed",
            "registry_modified","scoring_applied","trading_allowed","full_specialist_acceptance","quota_or_lease_modified"):
            self.assertIs(report[key],False,key)
        self.assertEqual(0,report["network_requests_performed"])

    def test_pure_temporal_review_performs_no_network_database_or_file_io(self):
        with patch("socket.socket.connect",side_effect=AssertionError("No HTTP")),\
             patch("sqlite3.connect",side_effect=AssertionError("No DB")),\
             patch("pathlib.Path.open",side_effect=AssertionError("No file IO in review")):
            report=self.review()
        self.assertTrue(report["requested_transaction_binding_supported"])


if __name__=="__main__":
    unittest.main()
