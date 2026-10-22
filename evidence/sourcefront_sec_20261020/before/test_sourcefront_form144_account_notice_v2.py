"""Original replay plus labelled synthetic adversarial cases; never LIVE acceptance."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

from evidence.sourcefront_sec_20261020.build_review import (
    ACCOUNT_SHA, NOTICE_SHA, ROOT, build_discovery, build_plan, load_inputs,
)
from market_checker_app.services.sec_form4_person_bridge import (
    Form4PersonBridgeError, review_form144_account_notice,
)

NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
CLOCK = "market_checker_app.services.sec_form4_person_bridge._physical_clock"


def modified(capture, old, new, **kwargs):
    """Synthetic derivative only: rehashing is never primary source acceptance."""
    if old not in capture.body:
        raise AssertionError("Synthetic trigger must hit actual retained bytes")
    body = capture.body.replace(old, new)
    return replace(capture, body=body, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), **kwargs)


class SourcefrontForm144AccountNoticeTests(unittest.TestCase):
    def setUp(self):
        self.selection, self.notice, self.account, self.plan = load_inputs()

    def review(self, *, notice=None, account=None, selection=None, knowledge_at=None, clock=NOW):
        with patch(CLOCK, return_value=clock):
            return review_form144_account_notice(notice_capture=self.notice if notice is None else notice,
                account_packet_capture=self.account if account is None else account,
                selection=self.selection if selection is None else selection, knowledge_at=knowledge_at)

    def test_actual_two_whole_originals_are_exact_and_separate_disclosures(self):
        self.assertEqual((4770, NOTICE_SHA), (len(self.notice.body), hashlib.sha256(self.notice.body).hexdigest()))
        self.assertEqual((9665, ACCOUNT_SHA), (len(self.account.body), hashlib.sha256(self.account.body).hexdigest()))
        report = self.review()
        self.assertTrue(report["notice"]["whole_original_consumed"])
        self.assertTrue(report["account_packet"]["whole_original_consumed"])
        self.assertEqual(2, report["independent_disclosure_count"])
        self.assertNotEqual(report["notice"]["accession"], report["account_packet"]["accession"])

    def test_actual_new_notice_corrobates_exact_account_and_issuer_not_natural_person(self):
        report = self.review(); notice = report["notice"]
        self.assertEqual({"cik": "0001513142", "name": "Nadella Satya"}, notice["reporting_account_as_filed"])
        self.assertEqual({"cik": "0000789019", "name": "MICROSOFT CORP"}, notice["subject_company_as_filed"])
        self.assertTrue(report["source_account_corroborated"])
        self.assertTrue(report["source_issuer_cik_corroborated"])
        self.assertEqual("SATYA NADELLA", notice["seller_name_as_filed"])
        self.assertEqual("MICROSOFT CORPORATION", notice["issuer_name_xml_raw"])
        for key in ("natural_person_identity_verified", "person_cik_binding_verified", "approved_name_alias",
                    "issuer_xml_name_variant_is_approved_alias", "seller_name_order_is_approved_account_alias"):
            self.assertFalse(report[key])

    def test_actual_aug31_overlap_does_not_hide_quantity_and_literal_title_conflicts(self):
        report = self.review(); notice = report["notice"]; rows = report["transaction_comparisons"]
        self.assertTrue(report["acquisition_day_overlap"])
        self.assertEqual("2026-08-31", notice["acquired_day"])
        self.assertEqual("86525", notice["acquired_units_raw"])
        self.assertEqual("Restricted Stock Units", notice["acquisition_nature_raw"])
        self.assertEqual(["178622", "70465.844"], [x["quantity_raw"] for x in rows])
        self.assertEqual(["A", "F"], [x["transaction_code_raw"] for x in rows])
        self.assertEqual(["Common Stock", "Common Stock"], [x["security_title_raw"] for x in rows])
        self.assertEqual("Common", notice["acquired_security_title_raw"])
        for row in rows:
            self.assertTrue(row["notice_acquired_day_equal"])
            self.assertFalse(row["notice_acquired_quantity_equal"])
            self.assertFalse(row["notice_security_title_literal_equal"])
            self.assertFalse(row["same_transaction_supported"])
        self.assertIn("NOTICE_AND_FORM4_TRANSACTION_QUANTITIES_DIFFER", report["gaps"])
        self.assertIn("NOTICE_AND_FORM4_SECURITY_TITLES_DIFFER", report["gaps"])
        self.assertFalse(report["same_transaction_supported"])

    def test_actual_proposed_sale_notice_and_plan_dates_are_distinct_from_execution(self):
        report = self.review(); notice = report["notice"]
        self.assertEqual("2026-09-01", notice["approx_sale_day"])
        self.assertEqual("2026-09-01", notice["notice_day"])
        self.assertEqual([{"literal": "03/08/2026", "day": "2026-03-08"}], notice["plan_adoption_days"])
        self.assertEqual(["Director", "Officer"], notice["relationships_as_filed"])
        self.assertEqual("/s/ Satya Nadella", notice["signature_raw"])
        self.assertTrue(notice["approx_sale_day_is_proposed_not_execution"])
        self.assertFalse(report["sale_notice_is_trade_execution"])
        self.assertFalse(report["execution_verified"])
        self.assertFalse(report["legal_role_at_transaction_verified"])
        self.assertFalse(report["role_continuity_verified"])

    def test_actual_original_leaf_quotes_and_xml_span_are_byte_exact(self):
        notice = self.review()["notice"]
        span = notice["xml_original_span"]
        self.assertEqual(span["sha256"], hashlib.sha256(self.notice.body[span["byte_start"]:span["byte_end"]]).hexdigest())
        for proof in notice["original_field_proofs"].values():
            raw = self.notice.body[proof["byte_start"]:proof["byte_end"]]
            self.assertEqual(proof["quote"], raw.decode())
            self.assertEqual(proof["bytes"], len(raw))
            self.assertEqual(proof["sha256"], hashlib.sha256(raw).hexdigest())

    def test_actual_placeholder_is_only_classified_never_exported(self):
        report = self.review()
        self.assertEqual("PUBLISHED_PLACEHOLDER_ONLY_VALUE_NOT_EXPORTED", report["notice"]["filer_ccc_classification"])
        self.assertNotIn("XXXXXXXX", json.dumps(report))
        self.assertFalse(report["live_test_flag_is_collector_live_acceptance"])

    def test_actual_raw_acceptance_clocks_have_unknown_zone_and_distinct_capture_knowledge(self):
        report = self.review(); notice = report["notice"]
        self.assertEqual("20260901163648", notice["acceptance_datetime_unqualified_raw"])
        self.assertEqual("UNKNOWN", notice["acceptance_timezone"])
        self.assertIsNone(notice["published_at"])
        self.assertFalse(notice["acceptance_timezone_inferred"])
        self.assertEqual("20260901180827", report["account_packet"]["header"]["acceptance_datetime_unqualified_raw"])
        self.assertEqual(self.notice.available_at.isoformat(), report["source_inputs_available_at"])
        self.assertEqual(NOW.isoformat(), report["review_ingested_at"])
        self.assertEqual(NOW.isoformat(), report["review_available_at"])
        self.assertFalse(report["historical_machine_availability_inferred"])

    def test_actual_plan_has_pinned_whole_bytes_and_preserved_physical_capture_clocks(self):
        plan = build_plan()
        self.assertEqual("SEC_FORM144_ACCOUNT_NOTICE", plan["kind"])
        self.assertNotIn("as_of", plan)
        self.assertEqual("FORM144_NOTICE_PACKET", plan["captures"]["notice"]["metadata"]["purpose"])
        for row in plan["captures"].values():
            raw = (ROOT / row["body_ref"]["path"]).read_bytes()
            self.assertEqual(row["body_ref"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(row["body_ref"]["bytes"], len(raw))
            self.assertEqual(row["metadata"]["observed_at"], row["metadata"]["known_at"])

    def test_actual_discovery_tokens_come_from_whole_profile_arrays(self):
        discovery = build_discovery()
        for row in discovery["retained_referring_originals"]:
            raw = (ROOT / row["whole_original"]["path"]).read_bytes()
            for token in row["tokens"]:
                original = raw[token["byte_start"]:token["byte_end"]]
                self.assertEqual(token["sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(token["value"], json.loads(original))
            self.assertEqual("0001950047-26-008927", row["tokens"][0]["value"])
        self.assertEqual(0, discovery["worker_http_requests"])
        self.assertEqual(1, discovery["coordinator_new_primary_http_requests"])

    def test_capture_hash_mismatch_is_rejected_before_semantic_review(self):
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=replace(self.notice, body=self.notice.body + b"\n"))

    def test_missing_original_has_metadata_only_and_no_excerpt_substitution(self):
        report = self.review(notice=replace(self.notice, body=None, retention="MISSING_BODY"))
        self.assertEqual(["REQUIRED_WHOLE_ORIGINAL_WITHHELD_OR_MISSING_NO_SUBSTITUTE"], report["gaps"])
        self.assertNotIn("notice", report)
        self.assertFalse(report["source_account_corroborated"])
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=replace(self.notice, retention="MISSING_BODY"))

    def test_synthetic_nonplaceholder_ccc_withholds_whole_body_without_replacement_or_value_export(self):
        changed = modified(self.notice, b"<ccc>XXXXXXXX</ccc>", b"<ccc>SYNTHETIC_SECRET_REJECTED</ccc>")
        report = self.review(notice=changed)
        self.assertEqual("WITHHELD_SENSITIVE_BODY", report["captures"]["notice"]["retention"])
        self.assertNotIn("notice", report)
        self.assertNotIn("SYNTHETIC_SECRET_REJECTED", json.dumps(report))
        self.assertFalse(report["source_account_corroborated"])

    def test_synthetic_general_sensitive_assignment_withholds_whole_original(self):
        changed = modified(self.notice, b"<XML>\n", b"<XML>\n<!-- api_key=synthetic_secret_for_rejection -->\n")
        report = self.review(notice=changed)
        self.assertEqual("WITHHELD_SENSITIVE_BODY", report["captures"]["notice"]["retention"])
        self.assertNotIn("notice", report)
        self.assertFalse(report["source_account_corroborated"])

    def test_rehashed_xml_excerpt_is_not_a_whole_filing_packet(self):
        start, end = self.notice.body.index(b"<?xml"), self.notice.body.index(b"</XML>")
        body = self.notice.body[start:end]
        changed = replace(self.notice, body=body, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=changed)

    def test_extra_trailing_data_and_duplicate_document_are_rejected(self):
        for body in (self.notice.body + b"not-consumed", self.notice.body.replace(b"</SEC-DOCUMENT>", b"<DOCUMENT>\n</DOCUMENT>\n</SEC-DOCUMENT>")):
            changed = replace(self.notice, body=body, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
            with self.subTest(synthetic_bytes=len(body)), self.assertRaises(Form4PersonBridgeError):
                self.review(notice=changed)

    def test_wrong_url_basename_accession_and_directory_are_rejected(self):
        for url in (self.notice.source_url.replace("008927.txt", "008928.txt"),
                    self.notice.source_url.replace("/789019/", "/1513142/"),
                    self.notice.source_url.replace("000195004726008927", "000195004726008928")):
            with self.subTest(url=url), self.assertRaises(Form4PersonBridgeError):
                self.review(notice=replace(self.notice, source_url=url))

    def test_rehashed_header_document_name_and_count_drift_are_rejected(self):
        for old, new in ((b"<FILENAME>primary_doc.xml", b"<FILENAME>other.xml"),
                         (b"PUBLIC DOCUMENT COUNT:\t\t1", b"PUBLIC DOCUMENT COUNT:\t\t2"),
                         (b"CONFORMED SUBMISSION TYPE:\t144", b"CONFORMED SUBMISSION TYPE:\t4")):
            with self.subTest(trigger=old), self.assertRaises(Form4PersonBridgeError):
                self.review(notice=modified(self.notice, old, new))

    def test_header_xml_filer_or_issuer_cik_conflict_rejects_rehashed_notice(self):
        for old, new in ((b"<cik>0001513142</cik>", b"<cik>0001513143</cik>"),
                         (b"<issuerCik>0000789019</issuerCik>", b"<issuerCik>0000789020</issuerCik>")):
            with self.subTest(trigger=old), self.assertRaises(Form4PersonBridgeError):
                self.review(notice=modified(self.notice, old, new))

    def test_header_name_order_change_is_not_approved_by_changing_selection(self):
        selection = {**self.selection, "owner_header_name": "Satya Nadella"}
        changed = modified(self.notice, b"Nadella Satya", b"Satya Nadella")
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=changed, selection=selection)

    def test_duplicate_xml_scalar_and_foreign_namespace_are_rejected(self):
        for old, new in ((b"<issuerCik>0000789019</issuerCik>", b"<issuerCik>0000789019</issuerCik><issuerCik>0000789019</issuerCik>"),
                         (b'xmlns="http://www.sec.gov/edgar/ownership"', b'xmlns="https://example.invalid/ownership"')):
            with self.subTest(trigger=old), self.assertRaises(Form4PersonBridgeError):
                self.review(notice=modified(self.notice, old, new))

    def test_entity_declaration_is_rejected_before_xml_expansion(self):
        changed = modified(self.notice, b"<XML>\n", b"<XML>\n<!DOCTYPE edgarSubmission [<!ENTITY x 'bad'>]>\n")
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=changed)

    def test_same_accession_or_duplicate_capture_identity_cannot_count_as_two_sources(self):
        with self.assertRaises(Form4PersonBridgeError):
            self.review(selection={**self.selection, "notice_accession": self.selection["account_accession"]})
        with self.assertRaises(Form4PersonBridgeError):
            self.review(account=replace(self.account, record_id=self.notice.record_id))

    def test_rehashed_form4_owner_conflict_cannot_be_hidden_by_original_notice(self):
        changed = modified(self.account, b"<rptOwnerCik>0001513142</rptOwnerCik>", b"<rptOwnerCik>0001513143</rptOwnerCik>")
        with self.assertRaises(Form4PersonBridgeError):
            self.review(account=changed)

    def test_missing_requested_transaction_day_does_not_use_period_or_nearest_row(self):
        with self.assertRaises(Form4PersonBridgeError):
            self.review(selection={**self.selection, "transaction_day": "2026-08-30"})

    def test_source_date_beyond_original_capture_is_rejected_even_when_rehashed(self):
        changed = modified(self.notice, b"<acquiredDate>08/31/2026</acquiredDate>", b"<acquiredDate>08/31/2099</acquiredDate>")
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=changed)

    def test_internal_notice_clock_conflict_withholds_account_comparison_support(self):
        changed = modified(self.notice, b"<noticeDate>09/01/2026</noticeDate>", b"<noticeDate>09/02/2026</noticeDate>")
        report = self.review(notice=changed)
        self.assertIn("NOTICE_DAY_AFTER_FILED_DAY", report["gaps"])
        self.assertFalse(report["source_account_corroborated"])
        self.assertFalse(report["acquisition_day_overlap"])

    def test_approx_sale_future_literal_is_prospective_not_execution_or_capture_fact(self):
        changed = modified(self.notice, b"<approxSaleDate>09/01/2026</approxSaleDate>", b"<approxSaleDate>10/21/2026</approxSaleDate>")
        report = self.review(notice=changed)
        self.assertTrue(report["notice"]["approx_sale_day_after_capture"])
        self.assertTrue(report["notice"]["approx_sale_day_is_proposed_not_execution"])
        self.assertFalse(report["execution_verified"])

    def test_old_cutoff_cannot_backdate_new_capture_interpretation(self):
        report = self.review(knowledge_at=NOW-timedelta(seconds=1))
        self.assertFalse(report["source_account_corroborated"])
        self.assertNotIn("notice", report)
        self.assertEqual(NOW.isoformat(), report["review_available_at"])

    def test_explicit_cutoff_before_physical_completion_removes_interpreted_facts(self):
        with patch(CLOCK, side_effect=(NOW, NOW+timedelta(seconds=2))):
            report = review_form144_account_notice(notice_capture=self.notice, account_packet_capture=self.account,
                selection=self.selection, knowledge_at=NOW)
        self.assertFalse(report["source_account_corroborated"])
        self.assertFalse(report["source_issuer_cik_corroborated"])
        self.assertFalse(report["acquisition_day_overlap"])
        self.assertNotIn("notice", report)
        self.assertNotIn("transaction_comparisons", report)
        self.assertEqual(["AFTER_PHYSICAL_REVIEW_COMPLETION_KNOWLEDGE_CUTOFF"], report["gaps"])

    def test_input_knowledge_after_physical_start_is_not_visible(self):
        report = self.review(notice=replace(self.notice, known_at=NOW+timedelta(seconds=1)))
        self.assertEqual(["REQUIRED_ORIGINAL_NOT_YET_KNOWN"], report["gaps"])
        self.assertNotIn("notice", report)
        self.assertFalse(report["source_account_corroborated"])

    def test_future_cutoff_or_clock_rollback_is_rejected(self):
        with self.assertRaises(Form4PersonBridgeError):
            self.review(knowledge_at=NOW+timedelta(seconds=1))
        with patch(CLOCK, side_effect=(NOW, NOW-timedelta(seconds=1))), self.assertRaises(Form4PersonBridgeError):
            review_form144_account_notice(notice_capture=self.notice, account_packet_capture=self.account, selection=self.selection)

    def test_exact_quantity_and_title_agreement_still_does_not_prove_transaction_or_alias(self):
        changed = modified(self.notice, b"<amountOfSecuritiesAcquired>86525</amountOfSecuritiesAcquired>", b"<amountOfSecuritiesAcquired>178622</amountOfSecuritiesAcquired>")
        changed = modified(changed, b"<securitiesClassTitle>Common</securitiesClassTitle>", b"<securitiesClassTitle>Common Stock</securitiesClassTitle>")
        report = self.review(notice=changed); row = report["transaction_comparisons"][0]
        self.assertTrue(row["notice_acquired_quantity_equal"])
        self.assertTrue(row["notice_security_title_literal_equal"])
        self.assertFalse(row["same_transaction_supported"])
        self.assertFalse(report["same_transaction_supported"])
        self.assertFalse(report["instrument_identity_verified"])
        self.assertFalse(report["approved_name_alias"])

    def test_named_seller_organization_is_not_promoted_to_natural_person(self):
        changed = modified(self.notice, b"SATYA NADELLA", b"SYNTHETIC SELLER LLC")
        report = self.review(notice=changed)
        self.assertEqual("SYNTHETIC SELLER LLC", report["notice"]["seller_name_as_filed"])
        self.assertFalse(report["natural_person_identity_verified"])
        self.assertFalse(report["person_cik_binding_verified"])

    def test_bounded_review_is_read_only_without_network_sql_quota_or_input_mutation(self):
        before = deepcopy(self.selection)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("no HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("no SQL")):
            report = self.review()
        self.assertEqual(before, self.selection)
        self.assertEqual(0, report["network_requests_performed"])
        for flag in ("quota_or_lease_modified", "registry_modified", "scoring_applied", "trading_allowed", "issuer_attribution_allowed"):
            self.assertFalse(report[flag])

    def test_selection_extra_keys_invalid_precision_and_new_purpose_in_old_api_are_rejected(self):
        for selection in ({**self.selection, "alias_approved": True},
                          {**self.selection, "transaction_day": "20260831"},
                          {**self.selection, "owner_cik": self.selection["issuer_cik"]}):
            with self.subTest(selection=selection), self.assertRaises(Form4PersonBridgeError):
                self.review(selection=selection)
        with self.assertRaises(Form4PersonBridgeError):
            self.review(notice=replace(self.notice, purpose="OWNER_PROFILE"))


if __name__ == "__main__":
    unittest.main()
