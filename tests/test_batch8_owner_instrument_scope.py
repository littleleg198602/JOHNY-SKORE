"""Current filed-account facts remain separate from legal identity/validity."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path,PurePosixPath,PureWindowsPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import URLError

from evidence.batch8_sec_20261015.build_review import (
    ROOT as EVIDENCE,SOURCE_PINS,build_review_artifact,build_review_plan,load_inputs,
)
from evidence.batch8_sec_20261015 import capture_primary as research
from market_checker_app.services.sec_owner_instrument_scope_review import (
    MAX_BYTES,OwnerInstrumentCapture,OwnerScopeReviewError,review_sec_owner_instrument_scope,
)


def changed(capture,body,**fields):
    return replace(capture,body=body,sha256=hashlib.sha256(body).hexdigest(),size_bytes=len(body),**fields)


def packet_body(xml,*,name="Nadella Satya",cik="0001513142",accepted="20260901180827"):
    header=f'''<SEC-DOCUMENT>0000789019-26-000157.txt : 20260901
<SEC-HEADER>
<ACCEPTANCE-DATETIME>{accepted}
ACCESSION NUMBER: 0000789019-26-000157
CONFORMED SUBMISSION TYPE: 4
FILED AS OF DATE: 20260901
REPORTING-OWNER:
 COMPANY DATA:
  COMPANY CONFORMED NAME: {name}
  CENTRAL INDEX KEY: {cik}
ISSUER:
 COMPANY DATA:
  COMPANY CONFORMED NAME: MICROSOFT CORP
  CENTRAL INDEX KEY: 0000789019
</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<SEQUENCE>1
<FILENAME>form4.xml
<TEXT>
<XML>'''.encode()
    return header+b"\n"+xml+b"</XML>\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>\n"


class OwnerInstrumentScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_prior,cls.actual_packet,cls.actual_xml,cls.actual_index,cls.actual_selection,cls.receipts=load_inputs()
        cls.golden=json.loads((EVIDENCE/"owner_instrument_scope_review.json").read_bytes())
        cls.physical=datetime.fromisoformat(cls.golden["review"]["review_ingested_at"])

    def setUp(self):
        self.prior=self.actual_prior; self.packet=self.actual_packet; self.xml=self.actual_xml; self.index=self.actual_index
        self.selection=deepcopy(self.actual_selection)

    def review(self,*,prior=None,packet=None,xml=None,index=None,selection=None,knowledge_at=None,physical=None):
        with patch("market_checker_app.services.sec_owner_instrument_scope_review._physical_clock",return_value=physical or self.physical):
            return review_sec_owner_instrument_scope(prior_account_capture=self.prior if prior is None else prior,
                packet_capture=self.packet if packet is None else packet,xml_capture=self.xml if xml is None else xml,
                index_capture=self.index if index is None else index,selection=self.selection if selection is None else selection,
                knowledge_at=knowledge_at)

    def complete_fixture(self,xml=None,*,name="Nadella Satya",accepted="20260901180827"):
        xml=self.xml if xml is None else xml
        raw=packet_body(xml.body,name=name,accepted=accepted)
        packet=replace(self.packet,record_id="synthetic-complete-header",body=raw,sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),retention="ORIGINAL_BYTES")
        index_raw=self.index.body.replace(b">9665<",(">"+str(len(raw))+"<").encode())
        index_raw=index_raw.replace(b">8214<",(">"+str(xml.size_bytes)+"<").encode())
        return packet,changed(self.index,index_raw)

    def test_actual_whole_originals_and_full_review_replay_are_hash_pinned(self):
        clocks=[self.golden["review"][key] for key in ("review_ingested_at","review_available_at")]
        self.assertEqual(self.golden,build_review_artifact(replay_clocks=clocks))
        for path,(size,digest) in SOURCE_PINS.items():
            raw=(EVIDENCE/path).read_bytes()
            self.assertEqual(size,len(raw));self.assertEqual(digest,hashlib.sha256(raw).hexdigest())
        self.assertEqual((4,3,1),(self.golden["actual_new_primary_research_transports"],self.golden["successful_complete_http200_originals"],self.golden["failed_original_transports"]))
        self.assertEqual(2,self.golden["remaining_unused_transport_slots"])

    def test_actual_current_account_role_signature_and_all_seven_rows_are_source_only(self):
        report=self.review()
        self.assertEqual("PARTIAL",report["status"])
        self.assertTrue(report["source_only_xml_scope_supported"])
        self.assertTrue(report["dated_reporting_account_match_supported"])
        self.assertFalse(report["current_header_corroborated"])
        self.assertFalse(report["complete_primary_packet_scope_supported"])
        self.assertEqual("0001513142",report["source_account"]["owner_cik"])
        self.assertEqual("Nadella Satya",report["source_account"]["account_name_as_filed"])
        self.assertEqual("2014-02-04",report["source_account"]["prior_report_day"])
        self.assertEqual("2026-08-31",report["source_account"]["current_report_day"])
        self.assertEqual(self.selection["role"],report["as_filed_role"]["raw_fields"])
        self.assertEqual([self.selection["signature"]],report["as_filed_signatures"])
        self.assertEqual(7,report["source_row_count"])
        self.assertEqual((2,5),(sum(r["kind"]=="TRANSACTION" for r in report["instrument_rows"]),sum(r["kind"]=="HOLDING" for r in report["instrument_rows"])))
        self.assertTrue(all(row["security_title"]=="Common Stock" for row in report["instrument_rows"]))
        self.assertTrue(all(not row["derivative"] for row in report["instrument_rows"]))

    def test_original_failed_packet_has_no_invented_complete_hash_bytes_or_header(self):
        report=self.review();capture=report["captures"]["packet"]
        self.assertEqual("NOT_CAPTURED_COMPLETE_BODY",capture["retention"])
        self.assertIsNone(capture["sha256"]);self.assertIsNone(capture["size_bytes"])
        self.assertIn("CURRENT_ORIGINAL_HEADER_PACKET_UNAVAILABLE",report["gaps"])
        self.assertEqual(0,self.receipts[1]["received_prefix_bytes"])
        self.assertNotIn("sha256",self.receipts[1])
        self.assertFalse(report["current_header_corroborated"])

    def test_distinct_disclosure_and_unqualified_index_clocks_do_not_create_continuity(self):
        report=self.review();scope=report["index_scope"]
        self.assertEqual("0000789019-26-000157",scope["accession"])
        self.assertNotEqual("0000789019-26-000161",scope["accession"])
        self.assertEqual("2026-09-01",scope["filing_day"])
        self.assertEqual("2026-09-01 18:08:27",scope["acceptance_datetime_unqualified_raw"])
        self.assertFalse(scope["acceptance_timezone_inferred"])
        self.assertFalse(scope["xml_size_conflict"])
        self.assertEqual((8214,8214,9665),(scope["index_declared_xml_bytes"],scope["captured_xml_bytes"],scope["index_declared_packet_bytes"]))
        for key in ("account_continuity_verified","role_continuity_verified","is_latest_owner_filing_proven"):
            self.assertFalse(report[key])

    def test_synthetic_complete_original_packet_confirms_fields_without_legal_or_instrument_adoption(self):
        packet,index=self.complete_fixture();report=self.review(packet=packet,index=index)
        self.assertTrue(report["current_header_corroborated"])
        self.assertTrue(report["complete_primary_packet_scope_supported"])
        for key in ("natural_person_identity_verified","person_cik_binding_verified","approved_name_alias","instrument_identity_verified",
                    "cusip_class_validity_verified","execution_verified","issuer_attribution_allowed","scoring_applied","trading_allowed"):
            self.assertFalse(report[key])
        self.assertFalse(report["signature_name_is_approved_owner_alias"])

    def test_raw_name_order_case_cik_issuer_accession_and_title_drift_are_rejected(self):
        for field,value in (("owner_name","Satya Nadella"),("owner_name","Nadella SATYA"),("owner_cik","0000789019"),
                            ("issuer_cik","0001513142"),("issuer_name","Microsoft Corporation"),
                            ("accession","0000789019-26-000161"),("instrument_title","Common stock")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):self.review(selection={**self.selection,field:value})

    def test_original_prior_header_name_case_drift_is_not_approved_after_rehash(self):
        prior=changed(self.prior,self.prior.body.replace(b"Nadella Satya",b"Nadella SATYA",1))
        with self.assertRaisesRegex(OwnerScopeReviewError,"raw names or CIKs differ"):self.review(prior=prior)

    def test_current_header_xml_name_conflict_and_unqualified_clock_conflict_are_distinct(self):
        packet,index=self.complete_fixture(name="Satya Nadella")
        with self.assertRaises(ValueError):self.review(packet=packet,index=index)
        packet,index=self.complete_fixture(accepted="20260901180828")
        report=self.review(packet=packet,index=index)
        self.assertTrue(report["source_only_xml_scope_supported"])
        self.assertFalse(report["complete_primary_packet_scope_supported"])
        self.assertIn("CURRENT_HEADER_INDEX_CLOCK_OR_SIZE_CONFLICT",report["gaps"])

    def test_current_packet_must_contain_the_exact_separately_pinned_raw_xml(self):
        packet,index=self.complete_fixture()
        packet=changed(packet,packet.body.replace(b"<issuerTradingSymbol>MSFT",b"<issuerTradingSymbol>MSFT ",1))
        with self.assertRaisesRegex(OwnerScopeReviewError,"exact separately pinned original XML"):
            self.review(packet=packet,index=index)

    def test_missing_role_title_signature_or_security_title_is_partial_without_fabrication(self):
        removals=[(b"<officerTitle>Chief Executive Officer</officerTitle>","MISSING_EXPLICIT_ROLE_FIELDS"),
            (b"<signatureName>Julia Stark, Attorney-in-Fact for Satya Nadella</signatureName>","MISSING_COMPLETE_EXACT_SIGNATURE"),
            (b"<value>Common Stock</value>","MISSING_ORIGINAL_INSTRUMENT_ROW_FIELDS")]
        for raw,gap in removals:
            self.assertIn(raw,self.xml.body)
            xml=changed(self.xml,self.xml.body.replace(raw,b"",1))
            report=self.review(xml=xml)
            self.assertFalse(report["source_only_xml_scope_supported"])
            self.assertIn(gap,report["gaps"])
            self.assertFalse(report["complete_primary_packet_scope_supported"])
            self.assertFalse(report["instrument_identity_verified"])

    def test_role_and_attorney_signature_expectations_cannot_be_relabelled(self):
        for selection in ({**self.selection,"role":{**self.selection["role"],"officerTitle":"Chairman and CEO"}},
                          {**self.selection,"signature":{**self.selection["signature"],"name":"Nadella Satya"}}):
            with self.assertRaises(OwnerScopeReviewError):self.review(selection=selection)

    def test_footnote_text_and_missing_reference_remain_explicit(self):
        report=self.review()
        self.assertEqual({"F1"},set(report["footnotes"]))
        self.assertEqual(["F1"],report["instrument_rows"][0]["footnote_ids"])
        xml=changed(self.xml,self.xml.body.replace(b'id="F1"',b'id="F2"',1))
        report=self.review(xml=xml)
        self.assertFalse(report["source_only_xml_scope_supported"])
        self.assertTrue(any("referencedFootnote" in row["missing_fields"] for row in report["instrument_rows"]))

    def test_index_basename_accession_must_equal_original_directory(self):
        url=self.index.source_url.replace("0000789019-26-000157-index.html","0000789019-26-000158-index.html")
        with self.assertRaisesRegex(OwnerScopeReviewError,"basename"):self.review(index=replace(self.index,source_url=url))

    def test_future_index_filing_day_cannot_become_earlier_by_later_known_clock(self):
        index=changed(self.index,self.index.body.replace(b"2026-09-01",b"2099-09-01"))
        future=self.physical.replace(year=2100)
        with self.assertRaisesRegex(OwnerScopeReviewError,"original observation scope"):
            self.review(index=replace(index,known_at=future),xml=replace(self.xml,known_at=future),
                        packet=replace(self.packet,known_at=future),prior=replace(self.prior,known_at=future),physical=future)

    def test_future_transaction_signature_and_report_day_are_rejected(self):
        for before,after in ((b"<value>2026-08-31</value>",b"<value>2099-08-31</value>"),
                             (b"<signatureDate>2026-09-01</signatureDate>",b"<signatureDate>2099-09-01</signatureDate>"),
                             (b"<periodOfReport>2026-08-31</periodOfReport>",b"<periodOfReport>2099-08-31</periodOfReport>")):
            self.assertIn(before,self.xml.body)
            with self.assertRaises(ValueError):self.review(xml=changed(self.xml,self.xml.body.replace(before,after,1)))

    def test_index_company_role_or_exact_owner_name_cannot_be_swapped_or_inferred(self):
        for before,after in ((b'Nadella Satya',b'Satya Nadella'),(b'>Reporting</a>',b'>Issuer</a>'),
                             (b'action=getowner',b'action=getissuer')):
            with self.assertRaises(OwnerScopeReviewError):self.review(index=changed(self.index,self.index.body.replace(before,after,1)))

    def test_joint_or_duplicated_accounts_do_not_allocate_rows_to_a_selected_person(self):
        owner=self.xml.body.split(b"<reportingOwner>",1)[1].split(b"</reportingOwner>",1)[0]
        raw=self.xml.body.replace(b"</reportingOwner>",b"</reportingOwner><reportingOwner>"+owner+b"</reportingOwner>",1)
        with self.assertRaisesRegex(OwnerScopeReviewError,"jointly unallocated"):self.review(xml=changed(self.xml,raw))

    def test_explicit_past_asof_does_not_backdate_a_new_review_even_when_inputs_were_available(self):
        cutoff=self.physical-timedelta(microseconds=1)
        with patch("market_checker_app.services.sec_owner_instrument_scope_review._xml",side_effect=AssertionError("no parser")):
            report=self.review(knowledge_at=cutoff)
        self.assertFalse(report["source_only_xml_scope_supported"])
        self.assertEqual(["AFTER_PHYSICAL_REVIEW_KNOWLEDGE_CUTOFF"],report["gaps"])
        self.assertEqual(self.physical.isoformat(),report["review_available_at"])
        self.assertNotIn("instrument_rows",report)

    def test_default_none_means_current_physical_ingestion_and_future_explicit_is_unavailable(self):
        report=self.review()
        self.assertIsNone(report["requested_knowledge_at"])
        self.assertTrue(report["source_only_xml_scope_supported"])
        self.assertEqual(self.physical.isoformat(),report["review_ingested_at"])
        with self.assertRaisesRegex(OwnerScopeReviewError,"Future"):self.review(knowledge_at=self.physical+timedelta(microseconds=1))

    def test_future_input_knowledge_is_withheld_before_original_parsing(self):
        with patch("market_checker_app.services.sec_owner_instrument_scope_review._packet",side_effect=AssertionError("no parser")):
            report=self.review(xml=replace(self.xml,known_at=self.physical+timedelta(seconds=1)))
        self.assertEqual(["ORIGINAL_INPUT_NOT_YET_KNOWN"],report["gaps"])
        self.assertNotIn("instrument_rows",report)

    def test_physical_clock_rollback_during_review_never_returns_an_acceptance(self):
        with patch("market_checker_app.services.sec_owner_instrument_scope_review._physical_clock",
                   side_effect=[self.physical,self.physical-timedelta(microseconds=1)]):
            with self.assertRaisesRegex(OwnerScopeReviewError,"rolled back"):
                review_sec_owner_instrument_scope(prior_account_capture=self.prior,packet_capture=self.packet,
                    xml_capture=self.xml,index_capture=self.index,selection=self.selection)

    def test_sensitive_xml_or_prior_body_is_wholly_withheld_before_any_parser(self):
        body=b'<html>clientSecret="SYNTHETIC_NO_ACCESS_VALUE_12345"</html>'
        for key,capture in (("xml",self.xml),("prior",self.prior),("index",self.index)):
            with patch("market_checker_app.services.sec_owner_instrument_scope_review._packet",side_effect=AssertionError("no parser")):
                report=self.review(**{key:changed(capture,body)})
            self.assertFalse(report["source_only_xml_scope_supported"])
            self.assertNotIn("SYNTHETIC_NO_ACCESS",json.dumps(report));self.assertNotIn("instrument_rows",report)

    def test_withheld_packet_is_metadata_only_without_restoring_or_losing_independent_xml_facts(self):
        packet=replace(self.packet,sha256="a"*64,size_bytes=100,retention="WITHHELD_SENSITIVE_BODY")
        report=self.review(packet=packet)
        self.assertTrue(report["source_only_xml_scope_supported"])
        self.assertFalse(report["current_header_corroborated"])
        self.assertEqual("WITHHELD_SENSITIVE_BODY",report["captures"]["packet"]["retention"])
        with self.assertRaisesRegex(OwnerScopeReviewError,"replacement bytes"):
            self.review(packet=replace(packet,body=b"<html>cleaned</html>"))

    def test_unknown_incomplete_packet_cannot_claim_a_full_hash_or_substitute_bytes(self):
        for packet in (replace(self.packet,sha256="0"*64),replace(self.packet,size_bytes=0),replace(self.packet,body=b"")):
            with self.assertRaisesRegex(OwnerScopeReviewError,"uncaptured whole body"):self.review(packet=packet)

    def test_whole_hash_size_url_bounds_clock_and_xml_entities_are_strict(self):
        for xml in (replace(self.xml,body=self.xml.body+b"\n"),replace(self.xml,sha256="a"*64),
                    replace(self.xml,size_bytes=MAX_BYTES+1),replace(self.xml,size_bytes=True),
                    replace(self.xml,known_at=self.xml.observed_at-timedelta(seconds=1)),
                    replace(self.xml,source_url=self.xml.source_url+"?token=SYNTHETIC_TEST")):
            with self.assertRaises(ValueError):self.review(xml=xml)
        for body in (b'<!DOCTYPE ownershipDocument [<!ENTITY example "x">]>'+self.xml.body,b"\x00"+self.xml.body):
            with self.assertRaises(ValueError):self.review(xml=changed(self.xml,body))

    def test_bounded_selection_and_duplicate_tables_are_not_relaxed(self):
        with self.assertRaises(OwnerScopeReviewError):self.review(selection={**self.selection,"extra":"x"*16001})
        with self.assertRaises(OwnerScopeReviewError):self.review(selection={**self.selection,"extra":True})
        raw=self.xml.body.replace(b"</nonDerivativeTable>",b"</nonDerivativeTable><nonDerivativeTable/>",1)
        with self.assertRaisesRegex(OwnerScopeReviewError,"table is duplicated"):self.review(xml=changed(self.xml,raw))

    def test_service_has_no_network_database_registry_or_input_mutation(self):
        before=deepcopy(self.selection)
        with patch("socket.socket.connect",side_effect=AssertionError("no network")),patch("sqlite3.connect",side_effect=AssertionError("no SQLite")):
            report=self.review()
        self.assertEqual(before,self.selection)
        self.assertEqual(0,report["network_requests_performed"])
        for key in ("quota_or_lease_modified","registry_modified","scoring_applied","trading_allowed","historical_machine_availability_inferred"):
            self.assertFalse(report[key])

    def test_actual_central_plan_retains_missing_body_without_a_fake_path_or_hash(self):
        plan=build_review_plan();self.assertEqual(json.loads((EVIDENCE/"actual_review_plan.json").read_bytes()),plan)
        self.assertEqual("SEC_OWNER_INSTRUMENT_SCOPE",plan["kind"])
        self.assertEqual({"prior_account","packet","xml","index"},set(plan["captures"]))
        missing=plan["captures"]["packet"]
        self.assertFalse(missing["body_available"]);self.assertIsNone(missing["body_ref"])
        self.assertIsNone(missing["metadata"]["sha256"]);self.assertIsNone(missing["metadata"]["size_bytes"])
        repo=EVIDENCE.parents[1]
        for capture in plan["captures"].values():
            if not capture["body_available"]:continue
            ref=capture["body_ref"]
            self.assertEqual(PurePosixPath(ref["path"]).as_posix(),PureWindowsPath(ref["path"]).as_posix())
            raw=repo.joinpath(*PurePosixPath(ref["path"]).parts).read_bytes()
            self.assertEqual(ref["bytes"],len(raw));self.assertEqual(ref["sha256"],hashlib.sha256(raw).hexdigest())


class ReservedResearchTransportTests(unittest.TestCase):
    def plan(self,root):
        raw=(EVIDENCE/"research_plan.json").read_bytes();(root/"research_plan.json").write_bytes(raw)
        return raw

    def signal(self):
        return SimpleNamespace(SIGALRM=1,ITIMER_REAL=2,signal=lambda *args:None,setitimer=lambda *args:None)

    def response(self,body,status=200,declared=None):
        class Response:
            code=status
            headers={"Content-Length":str(len(body) if declared is None else declared),"Content-Type":"text/xml"}
            offset=0
            def read1(self,count):
                value=body[self.offset:self.offset+count];self.offset+=len(value);return value
            read=read1
            def __enter__(self):return self
            def __exit__(self,*args):return False
        return Response()

    def test_reservation_is_durable_before_transport_and_no_redirect_is_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.plan(root)
            def opened(*args,**kwargs):
                reserve=json.loads((root/"attempt-01-reserved.json").read_bytes())
                self.assertEqual(hashlib.sha256(plan).hexdigest(),reserve["research_plan_sha256"])
                self.assertEqual((25,4000000),(reserve["absolute_timeout_seconds"],reserve["maximum_original_body_bytes"]))
                return self.response(b"<html>redirect</html>",302)
            opener=SimpleNamespace(open=opened)
            with patch.object(research,"ROOT",root),patch.object(research,"signal",self.signal()),patch.object(research.time,"sleep"),patch.object(research,"build_opener",return_value=opener):
                research.capture("https://www.sec.gov/Archives/edgar/data/789019/000078901926000157/form4.xml","synthetic.xml","CURRENT_PRIMARY_XML")
            result=json.loads((root/"attempt-01-result.json").read_bytes())
            self.assertEqual(302,result["http_status"])
            self.assertIsNone(research.NoRedirect().redirect_request(None,None,302,"",{},"https://example.test"))

    def test_six_reserved_failed_or_interrupted_attempts_cannot_be_reset_or_refunded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.plan(root)
            for index in range(1,7):(root/f"attempt-{index:02d}-reserved.json").write_text("{}")
            with patch.object(research,"ROOT",root),patch.object(research,"signal",self.signal()),patch.object(research,"build_opener",side_effect=AssertionError("no seventh transport")):
                with self.assertRaisesRegex(ValueError,"six-transport"):research.capture("https://www.sec.gov/Archives/edgar/data/789019/000078901926000157/form4.xml","synthetic.xml","CURRENT_PRIMARY_XML")

    def test_transient_failure_consumes_attempt_without_full_body_hash_or_prefix_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.plan(root)
            opener=SimpleNamespace(open=lambda *args,**kwargs:(_ for _ in ()).throw(URLError("SYNTHETIC_TRANSPORT_FAILURE")))
            with patch.object(research,"ROOT",root),patch.object(research,"signal",self.signal()),patch.object(research.time,"sleep"),patch.object(research,"build_opener",return_value=opener):
                research.capture("https://www.sec.gov/Archives/edgar/data/789019/000078901926000157/form4.xml","synthetic.xml","CURRENT_PRIMARY_XML")
            result=json.loads((root/"attempt-01-result.json").read_bytes())
            self.assertFalse(result["complete_original_body_received"]);self.assertNotIn("sha256",result)
            self.assertFalse((root/"primary").exists());self.assertEqual("URLError",result["failure_type"])

    def test_sensitive_whole_body_is_hash_metadata_only_and_oversized_body_is_not_read(self):
        for sensitive in (True,False):
            with self.subTest(sensitive=sensitive),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);self.plan(root)
                body=b'<html>clientSecret="SYNTHETIC_NO_ACCESS_VALUE_12345"</html>'
                response=self.response(body,declared=None if sensitive else 4000001)
                opener=SimpleNamespace(open=lambda *args,**kwargs:response)
                with patch.object(research,"ROOT",root),patch.object(research,"signal",self.signal()),patch.object(research.time,"sleep"),patch.object(research,"build_opener",return_value=opener):
                    research.capture("https://www.sec.gov/Archives/edgar/data/789019/000078901926000157/form4.xml","synthetic.xml","CURRENT_PRIMARY_XML")
                result=json.loads((root/"attempt-01-result.json").read_bytes())
                self.assertFalse((root/"primary").exists())
                if sensitive:self.assertEqual("WITHHELD_SENSITIVE_BODY",result["retention"])
                else:self.assertEqual(0,response.offset);self.assertFalse(result["complete_original_body_received"])


if __name__=="__main__":unittest.main()
