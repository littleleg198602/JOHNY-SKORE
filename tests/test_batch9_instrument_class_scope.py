"""Original registered-row/Class/CUSIP boundaries; synthetic cases are not LIVE."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import sec_instrument_class_scope_review as module
from market_checker_app.services.sec_symbol_action_scope_review import symbol_action_passages

NOW=datetime(2026,10,16,18,tzinfo=timezone.utc)
OBSERVED=NOW-timedelta(days=1)
CIK="0001474432"
CLASS="Class A Common Stock, $0.0001 par value per share"


def capture(url,body):
    return ({"requested_url":url,"final_url":url,"status":"PUBLIC_ORIGINAL_BYTES_CAPTURED","http_status":200,
             "raw_sha256":hashlib.sha256(body).hexdigest(),"bytes":len(body),"started_at":(OBSERVED-timedelta(seconds=1)).isoformat(),"observed_at":OBSERVED.isoformat()},body)


def replace_body(captures,key,body):captures[key]=capture(captures[key][0]["requested_url"],body)


def registered_selection(captures,key="cover"):
    body=captures[key][1];table=module.registered_security_tables(body)[0]
    section=next(item for item in symbol_action_passages(body) if item["text"].startswith("Securities registered pursuant"))
    return {"source_key":key,"context_ref":"Security","issuer_name":"Everpure, Inc.","registration_section":section,
            "table_number":1,"header_row":1,"security_row":2,"header_pin":module._pin(table["rows"][0]),"security_pin":module._pin(table["rows"][1])}


def fixture(cusip_column=None):
    head='<th>CUSIP</th>' if cusip_column is not None else ''
    value='<td>'+cusip_column+'</td>' if cusip_column is not None else ''
    body=f'''<html xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt-sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31">
    <xbrli:context id="Security"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-08-26</xbrli:instant></xbrli:period></xbrli:context>
    <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="Security">{CIK}</ix:nonNumeric><ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Everpure, Inc.</ix:nonNumeric>
    <div>Securities registered pursuant to Section 12(b) of the Act:<table><tr><th>Title of each class</th><th>Trading symbol(s)</th><th>Name of each exchange on which registered</th>{head}</tr>
    <tr><td><ix:nonNumeric name="dei:Security12bTitle" contextRef="Security">{CLASS}</ix:nonNumeric></td><td><ix:nonNumeric name="dei:TradingSymbol" contextRef="Security">P</ix:nonNumeric></td><td><ix:nonNumeric name="dei:SecurityExchangeName" contextRef="Security" format="ixt-sec:exchnameen">New York Stock Exchange LLC</ix:nonNumeric></td>{value}</tr></table></div></html>'''.encode()
    schedule=f'''<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g"><headerData><submissionType>SCHEDULE 13G/A</submissionType></headerData><formData><coverPageHeader><securitiesClassTitle>{CLASS}</securitiesClassTitle><eventDateRequiresFilingThisStatement>08/26/2026</eventDateRequiresFilingThisStatement><issuerInfo><issuerCik>{CIK}</issuerCik><issuerName>Everpure Inc</issuerName><issuerCusip>74624M102</issuerCusip></issuerInfo></coverPageHeader></formData></edgarSubmission>'''.encode()
    source_url='https://www.sec.gov/Archives/edgar/data/102909/000010290926001203/primary_doc.xml'
    index=f'''<html><span class="companyName">VANGUARD GROUP INC (Filed by) CIK : <a href="/cgi-bin/browse-edgar?CIK=0000102909&amp;action=getcompany">0000102909</a> (see all company filings)</span>
    <span class="companyName">Everpure, Inc. (Subject) CIK : <a href="/cgi-bin/browse-edgar?CIK={CIK}&amp;action=getcompany">{CIK}</a> (see all company filings)</span>
    <div class="infoHead">Filing Date</div><div class="info">2026-09-01</div><div class="infoHead">Accepted</div><div class="info">2026-09-01 16:00:00</div>
    <table><tr><td>1</td><td></td><td><a href="{source_url}">primary_doc.xml</a></td><td>SCHEDULE 13G/A</td><td>{len(schedule)}</td></tr></table></html>'''.encode()
    submissions={"cik":"1474432","filings":{"recent":{"accessionNumber":["0001474432-26-000084"],"primaryDocument":["cover.htm"],"form":["8-K"],"filingDate":["2026-09-01"],"acceptanceDateTime":["2026-09-01T20:00:00Z"]}}}
    captures={"cover":capture('https://www.sec.gov/Archives/edgar/data/1474432/000147443226000084/cover.htm',body),
              "schedule":capture(source_url,schedule),"schedule_index":capture('https://www.sec.gov/Archives/edgar/data/1474432/000010290926001203/0000102909-26-001203-index.html',index),
              "submissions":capture('https://data.sec.gov/submissions/CIK0001474432.json',json.dumps(submissions).encode())}
    request={"scope":{"issuer_cik":CIK,"symbol":"P","class_title":CLASS,"cusip":"74624M102"},
             "filings":[{"source_key":"cover","proof_key":"submissions","proof_kind":"SUBMISSIONS","form":"8-K"},
                        {"source_key":"schedule","proof_key":"schedule_index","proof_kind":"SCHEDULE13_INDEX","form":"SCHEDULE 13G/A"}],
             "registered_claims":[registered_selection(captures)],"schedule_claims":[{"source_key":"schedule"}],"certificate_claims":[]}
    return request,captures


class InstrumentClassScopeTests(unittest.TestCase):
    def setUp(self):
        patcher=patch.object(module,"_physical_clock",return_value=NOW);patcher.start();self.addCleanup(patcher.stop)
        self.request,self.captures=fixture()

    def review(self,**kwargs):return module.review_sec_instrument_class_scope(self.request,self.captures,**kwargs)

    def change_cover(self,old,new):
        replace_body(self.captures,"cover",self.captures["cover"][1].replace(old,new))
        self.request["registered_claims"]=[registered_selection(self.captures)]

    def test_exact_independent_registered_and_schedule_facts_do_not_adopt_an_instrument(self):
        before=deepcopy((self.request,self.captures))
        with patch("socket.socket.connect",side_effect=AssertionError("No HTTP")),patch("sqlite3.connect",side_effect=AssertionError("No DB")),patch.object(Path,"glob",side_effect=AssertionError("No discovery")):
            report=self.review()
        fact=report["registered_security_facts"][0];schedule=report["schedule_subject_facts"][0]
        self.assertTrue(fact["registered_class_symbol_fact_supported"])
        self.assertIsNone(fact["same_row_cusip"])
        self.assertTrue(schedule["exact_class_cusip_source_fact_supported"])
        self.assertTrue(schedule["same_day_registered_symbol_bridge_supported"])
        self.assertEqual(before,(self.request,self.captures))
        self.assertEqual("PRIMARY_INSTRUMENT_SCOPE_PARTIAL",report["status"])
        self.assertIn("ISSUED_INSTRUMENT_VALIDITY_INTERVAL_UNPROVEN",report["blockers"])
        self.assertIn("SYMBOL_ACTION_EXACT_EFFECTIVE_DAY_NOT_ESTABLISHED_BY_REGISTERED_TABLE",report["blockers"])
        for key in ("production_registry_changed","automatic_alias_allowed","interval_extension_allowed","historical_machine_availability_inferred","score_changed","trading_allowed"):
            self.assertFalse(report[key])
        self.assertEqual(0,report["new_verified_identities"])
        self.assertEqual(0,report["current_q3_verified_instrument_coverage"])
        sha=report.pop("review_sha256");self.assertEqual(sha,hashlib.sha256(module._encoded(report)).hexdigest())

    def test_same_row_cusip_is_extracted_but_not_borrowed_from_adjacent_text(self):
        self.request,self.captures=fixture("74624M102")
        self.assertEqual("74624M102",self.review()["registered_security_facts"][0]["same_row_cusip"])
        self.request,self.captures=fixture()
        self.change_cover(b'</div>',b'</div><p>CUSIP 74624M102 belongs to another instrument.</p>')
        self.assertIsNone(self.review()["registered_security_facts"][0]["same_row_cusip"])

    def test_a_different_same_row_cusip_is_a_preserved_conflict(self):
        self.request,self.captures=fixture("037833100")
        report=self.review()
        self.assertEqual("037833100",report["registered_security_facts"][0]["same_row_cusip"])
        self.assertIn("REGISTERED_SAME_ROW_CUSIP_CONFLICT",report["blockers"])

    def test_generic_schedule_class_is_not_upgraded_by_a_later_registered_cover(self):
        replace_body(self.captures,"schedule",self.captures["schedule"][1].replace(CLASS.encode(),b"Common Stock"))
        # Preserve its exact new original index length as a separate declared fact.
        old_index=self.captures["schedule_index"][1]
        replace_body(self.captures,"schedule_index",old_index.replace(str(len(fixture()[1]['schedule'][1])).encode(),str(len(self.captures['schedule'][1])).encode()))
        report=self.review()
        self.assertIn("SCHEDULE_CLASS_OR_PAR_TERM_UNRESOLVED",report["blockers"])
        self.assertFalse(report["schedule_subject_facts"][0]["exact_class_cusip_source_fact_supported"])

    def test_exact_schedule_class_fact_and_same_day_symbol_bridge_are_separate(self):
        replace_body(self.captures,"schedule",self.captures["schedule"][1].replace(b"08/26/2026",b"05/27/2026"))
        report=self.review();fact=report["schedule_subject_facts"][0]
        self.assertTrue(fact["exact_class_cusip_source_fact_supported"])
        self.assertFalse(fact["same_day_registered_symbol_bridge_supported"])
        self.assertIn("INDEPENDENT_SAME_DAY_REGISTERED_SYMBOL_CLASS_BINDING_MISSING",report["blockers"])

    def test_registered_cells_cannot_mix_inline_contexts(self):
        self.change_cover(b'name="dei:SecurityExchangeName" contextRef="Security"',b'name="dei:SecurityExchangeName" contextRef="Other"')
        with self.assertRaisesRegex(ValueError,"share their original local inline context"):self.review()

    def test_class_par_and_name_cannot_be_inferred_from_the_cik(self):
        for field,value in (("class_title","Common Stock"),("class_title","Class A Common Stock"),("class_title","Class A Common Stock, $0.01 par value per share")):
            with self.subTest(value=value):
                request,captures=fixture();request['scope'][field]=value
                with self.assertRaises(ValueError):module.review_sec_instrument_class_scope(request,captures)
        self.request['registered_claims'][0]['issuer_name']='Different Company'
        with self.assertRaisesRegex(ValueError,"tuple conflicts"):self.review()

    def test_missing_section_or_whole_row_hash_cannot_be_replaced_by_a_text_quote(self):
        for key in ('header_pin','security_pin'):
            with self.subTest(key=key):
                request,captures=fixture();request['registered_claims'][0][key]['sha256']='0'*64
                with self.assertRaisesRegex(ValueError,"row pin"):module.review_sec_instrument_class_scope(request,captures)
        self.request['registered_claims'][0]['registration_section']['text']='Securities registered pursuant to Section 12(b) of the Act: invented'
        with self.assertRaisesRegex(ValueError,"whole original"):self.review()

    def test_hidden_registered_table_is_not_a_source_fact(self):
        body=self.captures['cover'][1].replace(b'<table>',b'<table style="display:none">')
        replace_body(self.captures,'cover',body)
        with self.assertRaisesRegex(ValueError,'whole original|hidden'):self.review()

    def test_registered_header_and_data_columns_must_have_the_same_physical_span(self):
        self.change_cover(b'<th>Trading symbol(s)</th>',b'<th colspan="2">Trading symbol(s)</th>')
        with self.assertRaisesRegex(ValueError,'misaligned'):self.review()

    def test_exchange_transform_is_exactly_namespace_bound_and_not_a_class_transform(self):
        self.assertEqual('ixt-sec:exchnameen',self.review()['registered_security_facts'][0]['exchange_inline_format'])
        for old,new in ((b'http://www.sec.gov/inlineXBRL/transformation/2015-08-31',b'https://example.test/transform'),
                        (b'format="ixt-sec:exchnameen"',b'format="ixt:invented"')):
            with self.subTest(new=new):
                request,captures=fixture();replace_body(captures,'cover',captures['cover'][1].replace(old,new))
                with self.assertRaisesRegex(ValueError,'Unsupported registered'):registered_selection(captures)
        request,captures=fixture();replace_body(captures,'cover',captures['cover'][1].replace(b'name="dei:Security12bTitle"',b'name="dei:Security12bTitle" format="ixt-sec:exchnameen"'))
        with self.assertRaisesRegex(ValueError,'Unsupported registered'):registered_selection(captures)

    def test_reporting_filer_route_is_not_reassigned_to_the_subject_cik(self):
        report=self.review();proof=report['filings']['schedule']
        self.assertEqual(['0000102909',CIK],[row['cik'] for row in proof['index_companies']])
        self.assertNotIn('CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE',report['blockers'])
        metadata,body=self.captures['schedule'];url=metadata['requested_url'].replace('/data/102909/','/data/1474432/')
        self.captures['schedule']=capture(url,body)
        self.assertIn('CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE',self.review()['blockers'])

    def test_wrong_index_subject_role_or_foreign_raw_link_is_refused(self):
        for old,new in ((b'(Subject)',b'(Filed by)'),(b'/data/102909/',b'/data/999999/'),(b'https://www.sec.gov/Archives',b'https://example.test/Archives')):
            with self.subTest(new=new):
                request,captures=fixture();replace_body(captures,'schedule_index',captures['schedule_index'][1].replace(old,new))
                with self.assertRaises(ValueError):module.review_sec_instrument_class_scope(request,captures)

    def test_duplicate_foreign_schedule_fields_and_future_event_days_are_refused(self):
        for old,new in ((b'<issuerCik>',b'<issuerCik xmlns="https://example.test">'),
                        (b'</issuerCik>',b'</issuerCik><issuerCik>0000029915</issuerCik>'),
                        (b'08/26/2026',b'10/01/2026')):
            with self.subTest(new=new):
                request,captures=fixture();replace_body(captures,'schedule',captures['schedule'][1].replace(old,new))
                with self.assertRaises(ValueError):module.review_sec_instrument_class_scope(request,captures)

    def test_reversed_future_and_explicit_past_review_clocks_are_refused(self):
        for fields in ({'observed_at':(NOW+timedelta(seconds=1)).isoformat()},{'known_at':(OBSERVED-timedelta(seconds=1)).isoformat()}):
            with self.subTest(fields=fields):
                request,captures=fixture();captures['cover'][0].update(fields)
                with self.assertRaises(ValueError):module.review_sec_instrument_class_scope(request,captures)
        with self.assertRaisesRegex(ValueError,'AFTER_PHYSICAL'):self.review(as_of=NOW-timedelta(microseconds=1))
        with self.assertRaisesRegex(ValueError,'Future review'):self.review(as_of=NOW+timedelta(microseconds=1))

    def test_withheld_whole_body_is_not_restored_or_substituted(self):
        metadata,body=self.captures['cover'];metadata['status']='WITHHELD_SENSITIVE_BODY'
        self.captures['cover']=(metadata,None)
        report=self.review();self.assertIn('REGISTERED_WHOLE_SOURCE_UNAVAILABLE',report['blockers'])
        self.captures['cover']=(metadata,body)
        with self.assertRaisesRegex(ValueError,'cannot be restored'):self.review()

    def test_any_access_bearing_whole_source_is_refused_before_projection(self):
        replace_body(self.captures,'cover',self.captures['cover'][1]+b'<p>access_token="synthetic-fixture-only"</p>')
        with self.assertRaisesRegex(ValueError,'Whole access-bearing'):self.review()

    def test_original_byte_and_capture_bounds_are_enforced(self):
        for fields in ({'bytes':4_000_001},{'raw_sha256':'0'*64},{'body_transformed':True}):
            with self.subTest(fields=fields):
                request,captures=fixture();captures['cover'][0].update(fields)
                with self.assertRaises(ValueError):module.review_sec_instrument_class_scope(request,captures)
        for i in range(13):self.captures['extra'+str(i)]=self.captures['cover']
        with self.assertRaisesRegex(ValueError,'sixteen'):self.review()

    def test_real_originals_add_two_registered_class_anchors_without_fabricating_cusip_or_images(self):
        from evidence.batch9_identity_20261016.replay_instrument_scope import build_inputs
        request,captures,plan=build_inputs();report=module.review_sec_instrument_class_scope(request,captures)
        self.assertEqual(7,len(captures));self.assertNotIn('as_of',plan)
        self.assertEqual(2,report['registered_source_anchor_count'])
        facts=report['registered_security_facts']
        self.assertEqual(['2026-05-27','2026-08-26'],[row['context_anchor_on'] for row in facts])
        self.assertTrue(all(row['registered_class_symbol_fact_supported'] for row in facts))
        self.assertTrue(all(row['symbol']=='P' and row['same_row_cusip'] is None for row in facts))
        schedule=report['schedule_subject_facts'][0]
        self.assertEqual((CIK,'74624M102','Common Stock','2026-03-13'),tuple(schedule[key] for key in ('issuer_cik','cusip','class_title','event_on')))
        self.assertFalse(schedule['exact_class_cusip_source_fact_supported'])
        cert=report['certificate_scopes'][0]
        self.assertEqual('IMAGE_WRAPPER_ONLY',cert['status']);self.assertEqual(2,len(cert['original_image_references']))
        self.assertFalse(cert['image_bodies_available']);self.assertFalse(cert['class_cusip_supported'])
        self.assertEqual(1252,report['filings']['certificate']['index_declared_bytes'])
        self.assertEqual(1361,captures['certificate'][0]['bytes'])
        self.assertTrue(report['filings']['certificate']['publisher_size_conflict'])
        self.assertEqual('2026-03-24 18:36:40',report['filings']['certificate']['acceptance_unqualified_raw'])
        self.assertTrue(report['filings']['certificate']['acceptance_calendar_day_conflict'])
        self.assertEqual('0000102909',report['filings']['schedule']['index_companies'][0]['cik'])
        self.assertEqual(CIK,report['filings']['schedule']['index_companies'][1]['cik'])
        self.assertIn('CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE',report['blockers'])
        self.assertEqual(0,report['current_q3_verified_instrument_coverage']);self.assertEqual(0,report['new_verified_identities'])
