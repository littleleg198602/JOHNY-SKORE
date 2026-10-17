"""Whole original packet tests; mutated/crafted bytes are synthetic controls."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence.batch9_sec_20261016 import capture_primary as research
from evidence.batch9_sec_20261016.build_review import (
    ROOT as EVIDENCE, REPO, INDEX_PIN, XML_PIN, PACKET_PIN, OLD_FAILURE_SHA256,
    build_review_artifact, build_review_plan, load_inputs,
)
from market_checker_app.services.sec_complete_packet_review import (
    CompletePacketCapture, CompletePacketReviewError, MAX_BYTES,
    parse_complete_sec_packet, review_sec_complete_packet,
)

ACCESSION = "0000789019-26-000157"


def changed(capture, body, **fields):
    return replace(capture, body=body, sha256=hashlib.sha256(body).hexdigest(),
                   size_bytes=len(body), **fields)


class CompletePacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_packet, cls.actual_xml, cls.actual_index, cls.actual_selection, cls.receipt, cls.old = load_inputs()
        cls.golden = json.loads((EVIDENCE / "complete_packet_review_v1.json").read_bytes())
        cls.physical = datetime.fromisoformat(cls.golden["review"]["review_ingested_at"])

    def setUp(self):
        self.packet, self.xml, self.index = self.actual_packet, self.actual_xml, self.actual_index
        self.selection = deepcopy(self.actual_selection)

    def review(self, *, packet=None, xml=None, index=None, selection=None, knowledge_at=None, clock=None):
        with patch("market_checker_app.services.sec_complete_packet_review._physical_clock", return_value=clock or self.physical):
            return review_sec_complete_packet(packet_capture=packet or self.packet, xml_capture=xml or self.xml,
                index_capture=index or self.index, selection=self.selection if selection is None else selection,
                knowledge_at=knowledge_at)

    def synthetic_packet(self, raw, *, xml=None, index_raw=None):
        packet = changed(self.packet, raw, record_id="synthetic-mutated-complete-packet")
        original = self.index.body if index_raw is None else index_raw
        original = original.replace(b">9665<", (">" + str(len(raw)) + "<").encode())
        if xml is not None:
            original = original.replace(b">8214<", (">" + str(xml.size_bytes) + "<").encode())
        return packet, changed(self.index, original, record_id="synthetic-mutated-index")

    def parse(self, raw=None):
        return parse_complete_sec_packet(self.packet.body if raw is None else raw, expected_accession=ACCESSION)

    def test_genuine_current_whole_body_pins_and_deterministic_review_replay(self):
        clocks = [self.golden["review"][key] for key in ("review_ingested_at", "review_available_at")]
        self.assertEqual(self.golden, build_review_artifact(replay_clocks=clocks))
        for capture, expected in ((self.packet, PACKET_PIN), (self.xml, XML_PIN), (self.index, INDEX_PIN)):
            self.assertEqual(expected, (len(capture.body), hashlib.sha256(capture.body).hexdigest()))
        self.assertEqual((1, 1, 0), (self.golden["actual_new_primary_research_transports"],
            self.golden["successful_complete_http200_originals"], self.golden["reused_prior_xml_index_new_http_count"]))

    def test_real_complete_packet_closes_only_packet_header_component(self):
        report = self.review()
        self.assertEqual("PARTIAL", report["status"])
        for key in ("original_complete_packet_supported", "original_header_scope_supported",
                    "original_primary_xml_exact_binding_supported", "standalone_xml_index_scope_supported"):
            self.assertTrue(report[key], key)
        self.assertEqual((2, 5), (report["transaction_row_count"], report["holding_row_count"]))
        self.assertEqual(["Common Stock"], report["security_titles_as_filed"])
        self.assertEqual([{"cik": "0001513142", "name": "Nadella Satya"}], report["reporting_accounts_as_filed"])
        self.assertEqual("Chief Executive Officer", report["roles_as_filed"][0]["officerTitle"])
        self.assertEqual("Julia Stark, Attorney-in-Fact for Satya Nadella", report["signatures_as_filed"][0]["name"])

    def test_real_exact_document_span_binds_original_xml_without_serialization(self):
        parsed = self.parse()
        self.assertTrue(parsed["whole_original_consumed"])
        self.assertEqual((9665, 1), (parsed["packet_bytes"], len(parsed["documents"])))
        document = parsed["documents"][0]
        self.assertEqual(("4", "1", "form4.xml"), (document["document_type"], document["sequence_raw"], document["filename"]))
        span = document["ownership_xml_span"]
        self.assertEqual((8214, XML_PIN[1]), (span["bytes"], span["sha256"]))
        self.assertEqual(self.xml.body, self.packet.body[span["byte_start"]:span["byte_end"]])
        self.assertNotEqual(self.xml.sha256, document["text_span"]["sha256"])

    def test_real_header_roles_ciks_and_raw_acceptance_have_no_inferred_timezone(self):
        header = self.parse()["header"]
        self.assertEqual("20260901180827", header["acceptance_datetime_unqualified_raw"])
        self.assertEqual("2026-09-01 18:08:27", header["acceptance_datetime_unqualified_display"])
        self.assertFalse(header["acceptance_timezone_inferred"])
        self.assertEqual("2026-08-31", header["period_day"])
        self.assertEqual("2026-09-01", header["filed_day"])
        self.assertEqual([{"role": "REPORTING-OWNER", "name": "Nadella Satya", "cik": "0001513142"},
                          {"role": "ISSUER", "name": "MICROSOFT CORP", "cik": "0000789019"}], header["account_roles"])

    def test_real_primary_capture_clocks_do_not_rewrite_old_incomplete_transport(self):
        raw = (REPO / "evidence/batch8_sec_20261015/attempt-02-result.json").read_bytes()
        self.assertEqual(OLD_FAILURE_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertEqual("URLError", self.old["failure_type"])
        self.assertNotIn("sha256", self.old)
        self.assertNotIn("size_bytes", self.old)
        self.assertGreater(self.packet.observed_at, datetime.fromisoformat(self.old["observed_at"]))
        self.assertEqual("Fri, 16 Oct 2026 12:18:22 GMT", self.receipt["http_date"])
        self.assertEqual("Tue, 01 Sep 2026 22:08:45 GMT", self.receipt["last_modified"])

    def test_no_person_alias_cusip_continuity_execution_or_acceptance_promotion(self):
        report = self.review()
        for key in ("natural_person_identity_verified", "person_cik_binding_verified", "approved_name_alias",
                    "instrument_identity_verified", "cusip_class_validity_verified", "role_continuity_verified",
                    "account_continuity_verified", "execution_verified", "issuer_attribution_allowed",
                    "historical_machine_availability_inferred", "scoring_applied", "registry_modified",
                    "trading_allowed", "quota_or_lease_modified", "signature_or_poa_is_approved_person_alias"):
            self.assertIs(report[key], False, key)
        self.assertEqual(0, report["network_requests_performed"])
        self.assertTrue(report["one_accession_documents_are_one_disclosure"])
        self.assertTrue(report["gaps"])

    def test_actual_root_relative_plan_contains_only_exact_whole_body_references(self):
        plan = build_review_plan()
        self.assertEqual(plan, json.loads((EVIDENCE / "actual_review_plan.json").read_bytes()))
        self.assertEqual("SEC_COMPLETE_PACKET", plan["kind"])
        self.assertEqual({"packet", "xml", "index"}, set(plan["captures"]))
        self.assertEqual({"selection": self.selection}, plan["request"])
        for row in plan["captures"].values():
            ref = row["body_ref"]
            self.assertNotIn("\\", ref["path"])
            self.assertFalse(Path(ref["path"]).is_absolute())
            raw = (REPO / ref["path"]).read_bytes()
            self.assertEqual((ref["bytes"], ref["sha256"]), (len(raw), hashlib.sha256(raw).hexdigest()))

    def test_truncated_envelope_header_text_document_or_footer_is_rejected(self):
        for marker in (b"</SEC-HEADER>", b"</XML>", b"</TEXT>", b"</DOCUMENT>", b"</SEC-DOCUMENT>"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                self.parse(self.packet.body.replace(marker, b"", 1))

    def test_trailing_non_whitespace_or_prefix_excerpt_is_not_a_complete_packet(self):
        for raw in (b"excerpt\n" + self.packet.body, self.packet.body + b"extra", self.xml.body,
                    self.packet.body.split(b"<DOCUMENT>", 1)[1]):
            with self.subTest(prefix=raw[:16]), self.assertRaises(ValueError): self.parse(raw)

    def test_accidental_or_claimed_other_accession_cannot_bind_header(self):
        for raw in (self.packet.body.replace(b"0000789019-26-000157.txt", b"0000789019-26-000158.txt", 1),
                    self.packet.body.replace(b"0000789019-26-000157.hdr.sgml", b"0000789019-26-000158.hdr.sgml", 1),
                    self.packet.body.replace(b"ACCESSION NUMBER:\t\t0000789019-26-000157", b"ACCESSION NUMBER:\t\t0000789019-26-000158", 1)):
            self.assertNotEqual(raw, self.packet.body)
            with self.assertRaises(ValueError): self.parse(raw)

    def test_header_required_fields_cannot_be_duplicated_or_hidden_in_role_scope(self):
        for raw in (self.packet.body.replace(b"</SEC-HEADER>", b"ACCESSION NUMBER: 0000789019-26-000157\n</SEC-HEADER>"),
                    self.packet.body.replace(b"REPORTING-OWNER:", b"REPORTING-OWNER:\nPUBLIC DOCUMENT COUNT: 1"),
                    self.packet.body.replace(b"<ACCEPTANCE-DATETIME>20260901180827", b"<ACCEPTANCE-DATETIME>20260901180827\n<ACCEPTANCE-DATETIME>20260901180827"),
                    self.packet.body.replace(b"REPORTING-OWNER:", b"REPORTING-OWNER:\n<ACCEPTANCE-DATETIME>20260901180827")):
            with self.assertRaises(ValueError): self.parse(raw)

    def test_header_role_name_or_cik_drift_does_not_inherit_xml_account(self):
        for old, new in ((b"Nadella Satya", b"Satya Nadella"), (b"0001513142", b"0001513143"),
                         (b"MICROSOFT CORP", b"Microsoft Corporation"), (b"0000789019", b"0000789020")):
            before, after = self.packet.body.split(b"</SEC-HEADER>", 1)
            packet, index = self.synthetic_packet(before.replace(old, new) + b"</SEC-HEADER>" + after)
            with self.subTest(new=new), self.assertRaises(ValueError): self.review(packet=packet, index=index)

    def test_duplicate_issuer_or_reporting_owner_header_roles_are_rejected(self):
        section = b"REPORTING-OWNER:\nCOMPANY CONFORMED NAME: Nadella Satya\nCENTRAL INDEX KEY: 0001513142\nFORM TYPE: 4\n"
        with self.assertRaises(ValueError): self.parse(self.packet.body.replace(b"</SEC-HEADER>", section+b"</SEC-HEADER>"))

    def test_header_reporting_form_type_cannot_disagree_with_submission(self):
        raw = self.packet.body.replace(b"FORM TYPE:\t\t4", b"FORM TYPE:\t\t5", 1)
        self.assertNotEqual(raw, self.packet.body)
        with self.assertRaises(ValueError): self.parse(raw)

    def test_header_count_cannot_silently_drop_or_invent_document_blocks(self):
        raw = self.packet.body.replace(b"PUBLIC DOCUMENT COUNT:\t\t1", b"PUBLIC DOCUMENT COUNT:\t\t2", 1)
        self.assertNotEqual(raw, self.packet.body)
        with self.assertRaises(ValueError): self.parse(raw)

    def test_document_identity_requires_canonical_metadata_order_and_unique_fields(self):
        for raw in (self.packet.body.replace(b"<TYPE>4\n", b"<TYPE>4\n<TYPE>4\n", 1),
                    self.packet.body.replace(b"<TYPE>4\n<SEQUENCE>1\n", b"<SEQUENCE>1\n<TYPE>4\n", 1),
                    self.packet.body.replace(b"<FILENAME>form4.xml\n", b"<FILENAME>../form4.xml\n", 1),
                    self.packet.body.replace(b"<SEQUENCE>1\n", b"<SEQUENCE>2\n", 1)):
            with self.assertRaises(ValueError): self.parse(raw)

    def test_matching_xml_under_another_document_type_or_filename_is_not_primary_binding(self):
        for old, new in ((b"<TYPE>4\n", b"<TYPE>EX-24\n"), (b"<FILENAME>form4.xml\n", b"<FILENAME>other.xml\n")):
            packet, index = self.synthetic_packet(self.packet.body.replace(old, new, 1))
            with self.subTest(new=new), self.assertRaises(ValueError): self.review(packet=packet, index=index)

    def test_nested_or_unmatched_controls_in_text_cannot_be_skipped(self):
        for control in (b"<DOCUMENT>", b"</DOCUMENT>", b"<SEC-HEADER>", b"</SEC-DOCUMENT>", b"<TEXT>"):
            with self.subTest(control=control), self.assertRaises(ValueError):
                self.parse(self.packet.body.replace(b"<XML>\n", b"<XML>\n"+control+b"\n", 1))

    def test_duplicate_or_noncanonical_xml_wrappers_are_rejected(self):
        for raw in (self.packet.body.replace(b"<XML>\n", b"<XML>\n<XML>\n", 1),
                    self.packet.body.replace(b"</XML>\n", b"</XML>\n</XML>\n", 1),
                    self.packet.body.replace(b"<XML>\n", b"<XML>", 1)):
            with self.assertRaises(ValueError): self.parse(raw)

    def test_rehashed_semantically_equal_xml_still_fails_exact_original_binding(self):
        raw = self.packet.body.replace(b"<issuerTradingSymbol>MSFT", b"<issuerTradingSymbol> MSFT", 1)
        packet, index = self.synthetic_packet(raw)
        report = self.review(packet=packet, index=index)
        self.assertTrue(report["original_header_scope_supported"])
        self.assertFalse(report["original_complete_packet_supported"])
        self.assertFalse(report["original_primary_xml_exact_binding_supported"])
        self.assertIn("ORIGINAL_PACKET_PRIMARY_XML_BYTE_BINDING_CONFLICT", report["gaps"])

    def test_line_ending_conversion_cannot_pass_original_byte_binding(self):
        packet, index = self.synthetic_packet(self.packet.body.replace(b"\n", b"\r\n"))
        report = self.review(packet=packet, index=index)
        self.assertFalse(report["original_primary_xml_exact_binding_supported"])
        self.assertFalse(report["original_complete_packet_supported"])

    def test_synthetic_multiple_documents_are_consumed_with_opaque_poa_not_identity(self):
        second = b"<DOCUMENT>\n<TYPE>EX-24\n<SEQUENCE>2\n<FILENAME>poa.txt\n<TEXT>\nSynthetic POA is not a natural-person alias.\n</TEXT>\n</DOCUMENT>\n"
        raw = self.packet.body.replace(b"PUBLIC DOCUMENT COUNT:\t\t1", b"PUBLIC DOCUMENT COUNT:\t\t2", 1)
        raw = raw.replace(b"</SEC-DOCUMENT>", second+b"</SEC-DOCUMENT>")
        parsed = self.parse(raw)
        self.assertEqual(2, len(parsed["documents"]))
        self.assertEqual("OPAQUE_OTHER_DOCUMENT_NOT_IDENTITY_EVIDENCE", parsed["documents"][1]["text_envelope"])
        packet, index = self.synthetic_packet(raw)
        with self.assertRaisesRegex(ValueError, "document count conflicts"): self.review(packet=packet, index=index)

    def test_duplicate_document_filenames_and_sequence_reset_are_rejected(self):
        doc = self.packet.body[self.packet.body.index(b"<DOCUMENT>"):self.packet.body.index(b"</SEC-DOCUMENT>")]
        raw = self.packet.body.replace(b"PUBLIC DOCUMENT COUNT:\t\t1", b"PUBLIC DOCUMENT COUNT:\t\t2", 1)
        for second in (doc, doc.replace(b"<SEQUENCE>1", b"<SEQUENCE>2")):
            with self.assertRaises(ValueError): self.parse(raw.replace(b"</SEC-DOCUMENT>", second+b"</SEC-DOCUMENT>"))

    def test_index_primary_sequence_count_size_and_exact_filename_guards(self):
        for old, new in ((b">9665<", b">9666<"), (b">8214<", b">8215<")):
            report = self.review(index=changed(self.index, self.index.body.replace(old,new)))
            self.assertFalse(report["original_complete_packet_supported"])
        raw = self.index.body.replace(b'<div class="info">1</div>', b'<div class="info">2</div>', 1)
        self.assertNotEqual(raw, self.index.body)
        with self.assertRaises(ValueError): self.review(index=changed(self.index, raw))

    def test_index_primary_sequence_drift_is_rejected_even_after_rehash(self):
        raw = self.index.body.replace(b'<td scope="row">1</td>', b'<td scope="row">2</td>')
        self.assertNotEqual(raw, self.index.body)
        with self.assertRaisesRegex(ValueError, "sequence conflicts"): self.review(index=changed(self.index, raw))

    def test_index_url_basename_folder_and_selected_primary_filename_must_match(self):
        for index in (replace(self.index, source_url=self.index.source_url.replace("000157-index", "000158-index")),
                      replace(self.index, source_url=self.index.source_url.replace("/000078901926000157/", "/000078901926000158/"))):
            with self.assertRaises(ValueError): self.review(index=index)
        with self.assertRaises(ValueError): self.review(selection={**self.selection,"primary_filename":"other.xml"})

    def test_header_index_unqualified_clock_conflict_remains_explicit(self):
        packet, index = self.synthetic_packet(self.packet.body.replace(b"20260901180827", b"20260901180828", 1))
        report = self.review(packet=packet, index=index)
        self.assertFalse(report["original_complete_packet_supported"])
        self.assertIn("HEADER_INDEX_UNQUALIFIED_ACCEPTANCE_CONFLICT", report["gaps"])
        self.assertFalse(report["complete_original_packet"]["header"]["acceptance_timezone_inferred"])

    def test_acceptance_day_conflict_is_not_a_timezone_reconstruction(self):
        packet, index = self.synthetic_packet(self.packet.body.replace(b"20260901180827", b"20260902180827", 1))
        report = self.review(packet=packet, index=index)
        self.assertFalse(report["original_header_scope_supported"])
        self.assertIn("ACCEPTANCE_DATE_DIFFERS_FROM_FILED_DAY", report["gaps"])
        self.assertNotIn("acceptance_at_utc", report["complete_original_packet"]["header"])

    def test_original_source_observation_upper_bounds_header_change_day(self):
        packet, index = self.synthetic_packet(self.packet.body.replace(b"DATE AS OF CHANGE:\t\t20260901", b"DATE AS OF CHANGE:\t\t20990901", 1))
        with self.assertRaisesRegex(ValueError, "physical source observation"): self.review(packet=packet,index=index,clock=datetime(2100,1,1,tzinfo=timezone.utc))

    def test_invalid_calendar_or_missing_unqualified_acceptance_is_rejected(self):
        for raw in (self.packet.body.replace(b"20260901180827",b"20261301180827",1),
                    self.packet.body.replace(b"<ACCEPTANCE-DATETIME>",b"<ACCEPTANCE-UTC>",1)):
            with self.assertRaises(ValueError): self.parse(raw)

    def test_requested_old_cutoff_does_not_backdate_a_new_review(self):
        cutoff = self.packet.observed_at
        report = self.review(knowledge_at=cutoff)
        self.assertEqual(["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"],report["gaps"])
        self.assertFalse(report["original_complete_packet_supported"])
        self.assertNotIn("captures", report)
        self.assertEqual(cutoff.isoformat(),report["requested_knowledge_at"])
        self.assertGreater(datetime.fromisoformat(report["review_available_at"]),cutoff)

    def test_default_none_uses_current_physical_ingestion_and_original_input_clocks(self):
        report = self.review()
        self.assertIsNone(report["requested_knowledge_at"])
        self.assertEqual(self.physical.isoformat(),report["review_available_at"])
        self.assertEqual(self.packet.available_at.isoformat(),report["source_inputs_available_at"])

    def test_future_cutoff_and_physical_clock_rollback_are_rejected(self):
        with self.assertRaises(ValueError): self.review(knowledge_at=self.physical+timedelta(seconds=1))
        with patch("market_checker_app.services.sec_complete_packet_review._physical_clock",side_effect=[self.physical,self.physical-timedelta(seconds=1)]):
            with self.assertRaisesRegex(ValueError,"rolled back"):
                review_sec_complete_packet(packet_capture=self.packet,xml_capture=self.xml,index_capture=self.index,selection=self.selection)

    def test_future_input_availability_cannot_supply_current_facts(self):
        report=self.review(packet=replace(self.packet,known_at=self.physical+timedelta(seconds=1)))
        self.assertEqual(["ORIGINAL_INPUT_NOT_YET_KNOWN"],report["gaps"])
        self.assertFalse(report["original_complete_packet_supported"])
        self.assertNotIn("complete_original_packet",report)

    def test_missing_whole_packet_has_no_excerpt_or_invented_hash_substitute(self):
        missing=replace(self.packet,body=None,sha256=None,size_bytes=None,retention="NOT_CAPTURED_COMPLETE_BODY")
        report=self.review(packet=missing)
        self.assertTrue(report["standalone_xml_index_scope_supported"])
        self.assertFalse(report["original_complete_packet_supported"])
        self.assertIn("ORIGINAL_COMPLETE_PACKET_UNAVAILABLE_NO_HEADER_SUBSTITUTE",report["gaps"])
        with self.assertRaises(ValueError): self.review(packet=replace(missing,body=b"excerpt"))
        with self.assertRaises(ValueError): self.review(packet=replace(missing,sha256=self.packet.sha256))

    def test_withheld_packet_metadata_is_partial_before_opening_or_parsing_body(self):
        withheld=replace(self.packet,body=None,retention="WITHHELD_SENSITIVE_BODY")
        with patch("market_checker_app.services.sec_complete_packet_review.parse_complete_sec_packet",side_effect=AssertionError("Must not parse withheld body")):
            report=self.review(packet=withheld)
        self.assertFalse(report["original_complete_packet_supported"])
        self.assertEqual(self.packet.sha256,report["captures"]["packet"]["sha256"])
        self.assertNotIn("complete_original_packet",report)

    def test_new_synthetic_access_bearing_whole_packet_is_withheld_not_cleaned(self):
        secret=b"synthetic-test-value-never-real-credential"
        raw=self.packet.body.replace(b"</SEC-HEADER>",b'api_key="'+secret+b'"\n</SEC-HEADER>')
        packet,index=self.synthetic_packet(raw)
        report=self.review(packet=packet,index=index)
        self.assertEqual("WITHHELD_SENSITIVE_BODY",report["captures"]["packet"]["retention"])
        self.assertNotIn(secret.decode(),json.dumps(report))
        with self.assertRaises(ValueError):self.parse(raw)

    def test_missing_or_withheld_xml_or_index_is_partial_without_parsing(self):
        for key,capture in (("xml",self.xml),("index",self.index)):
            withheld=replace(capture,body=None,retention="WITHHELD_SENSITIVE_BODY")
            with self.subTest(key=key),patch("market_checker_app.services.sec_complete_packet_review._xml",side_effect=AssertionError("No XML parsing")):
                report=self.review(**{key:withheld})
            self.assertEqual(["RETAINED_ORIGINAL_XML_OR_INDEX_UNAVAILABLE"],report["gaps"])

    def test_empty_cleaned_or_rehashed_excerpt_is_not_original_packet(self):
        for body in (b"",self.xml.body,b"<SEC-DOCUMENT>\n</SEC-DOCUMENT>\n"):
            with self.subTest(body=body[:20]),self.assertRaises(ValueError):self.review(packet=changed(self.packet,body))

    def test_hash_size_duplicate_record_ids_and_noncanonical_source_urls_rejected(self):
        for capture in (replace(self.packet,sha256="0"*64),replace(self.packet,size_bytes=9664),
                        replace(self.packet,record_id=self.xml.record_id),
                        replace(self.packet,source_url=self.packet.source_url+"?token=synthetic")):
            with self.assertRaises(ValueError):self.review(packet=capture)

    def test_selection_exact_name_order_fields_and_iso_days_cannot_be_relabelled(self):
        selections=[{**self.selection,"owner_name":"Satya Nadella"},{**self.selection,"issuer_cik":"0001513142"},
            {**self.selection,"form":"4/A"},{**self.selection,"period_day":"20260831"},
            {**self.selection,"period_day":"2026-02-30"},{**self.selection,"extra":"unsupported"}]
        for selection in selections:
            with self.subTest(selection=selection),self.assertRaises(ValueError):self.review(selection=selection)

    def test_bounds_reject_unbounded_packet_header_line_and_document_count(self):
        for raw in (b"x"*(MAX_BYTES+1), b"\x00"+self.packet.body,
                    self.packet.body.replace(b"</SEC-HEADER>",b"x"*200_001+b"\n</SEC-HEADER>"),
                    self.packet.body.replace(b"PUBLIC DOCUMENT COUNT:\t\t1",b"PUBLIC DOCUMENT COUNT:\t\t33"),
                    self.packet.body+b"\n"*100_001):
            with self.assertRaises(ValueError):self.parse(raw)

    def test_dtd_entities_wrong_xml_root_or_encoded_xml_cannot_bind(self):
        for xml_raw in (self.xml.body.replace(b"<ownershipDocument>",b'<!DOCTYPE ownershipDocument [<!ENTITY x "x">]><ownershipDocument>',1),
                        self.xml.body.replace(b"ownershipDocument",b"otherDocument"),
                        self.xml.body.decode().encode("utf-16")):
            xml=changed(self.xml,xml_raw)
            packet,index=self.synthetic_packet(self.packet.body.replace(self.xml.body,xml_raw),xml=xml)
            with self.assertRaises(ValueError):self.review(packet=packet,xml=xml,index=index)

    def test_missing_or_future_signature_and_transaction_dates_cannot_be_invented(self):
        for old,new in ((b"<signatureDate>2026-09-01</signatureDate>",b""),
                        (b"<signatureDate>2026-09-01</signatureDate>",b"<signatureDate>2099-09-01</signatureDate>"),
                        (b"<value>2026-08-31</value>",b"<value>2099-08-31</value>"),
                        (b"<signatureName>Julia Stark, Attorney-in-Fact for Satya Nadella</signatureName>",b"")):
            self.assertIn(old,self.xml.body)
            xml=changed(self.xml,self.xml.body.replace(old,new,1))
            packet,index=self.synthetic_packet(self.packet.body.replace(self.xml.body,xml.body),xml=xml)
            with self.assertRaises(ValueError):self.review(packet=packet,xml=xml,index=index)

    def test_research_helper_never_retries_the_consumed_actual_diagnostic(self):
        with patch.object(research.signal,"setitimer",create=True),patch.object(research,"build_opener",side_effect=AssertionError("No second HTTP")):
            with self.assertRaisesRegex(ValueError,"already consumed"):
                research.capture(self.packet.source_url,"another.txt","EXPLICIT_NEW_CURRENT_PACKET_DIAGNOSTIC")

    def test_research_helper_allows_only_exact_preplanned_url_and_no_redirect(self):
        for url in (self.packet.source_url+"?x=1",self.packet.source_url.replace("www.sec.gov","example.com"),
                    self.packet.source_url.replace("000157.txt","000158.txt")):
            with self.assertRaises(ValueError):research._url(url)
        self.assertIsNone(research.NoRedirect().redirect_request(None,None,302,"redirect",{},"https://www.sec.gov/other"))

    def test_offline_report_performs_no_network_database_or_file_writes(self):
        with patch("socket.socket.connect",side_effect=AssertionError("No HTTP")),patch("sqlite3.connect",side_effect=AssertionError("No DB")),\
             patch("pathlib.Path.open",side_effect=AssertionError("No file writes or reads in pure review")):
            report=self.review()
        self.assertTrue(report["original_complete_packet_supported"])

    def test_research_artifact_writer_refuses_overwriting_an_existing_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"receipt.json"
            research._write(path,{"first":True})
            old=path.read_bytes()
            with self.assertRaises(FileExistsError):research._write(path,{"overwrite":True})
            self.assertEqual(old,path.read_bytes())


if __name__=="__main__":
    unittest.main()
