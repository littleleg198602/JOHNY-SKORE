"""Offline synthetic mechanisms and exact retained replay; no LIVE/OOS claim."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import sec_supplier_concentration_review as service


NOW = datetime(2026,10,17,12,tzinfo=timezone.utc)
CAPTURED = NOW-timedelta(days=1)
URL = 'https://www.sec.gov/Archives/edgar/data/4127/000000412725000085/swks-20251003.htm'
INDEX = URL.rsplit('/',1)[0]+'/0000004127-25-000085-index.htm'
PERIODS = [(2025,'2024-09-28','2025-10-03','67','f-977','c-229'),
           (2024,'2023-09-30','2024-09-27','69','f-978','c-230'),
           (2023,'2022-10-01','2023-09-29','66','f-979','c-231')]
CHANNELS = 'through sales to multiple distributors, contract manufacturers, and direct sales for multiple applications including smartphones, tablets, desktop, and notebook computers, watches and other devices'
SCOPE = 'All Skyworks subsidiaries are included in the Company’s consolidated financial statements and all intercompany balances are eliminated in consolidation.'
CALENDAR = 'The Company’s fiscal year ends on the Friday closest to September 30. The fiscal year ended on October 3, 2025 (“fiscal 2025”) consisted of 53 weeks. The fiscal year ended on September 27, 2024 (“fiscal 2024”) and the fiscal year ended on September 29, 2023 (“fiscal 2023”) each consisted of 52 weeks.'


def pin(body,literal):
    encoded=literal.encode();start=body.index(encoded)
    return {'byte_start':start,'byte_end':start+len(encoded),'sha256':hashlib.sha256(encoded).hexdigest()}


def capture(body,url):
    return {'requested_url':url,'final_url':url,'started_at':(CAPTURED-timedelta(seconds=1)).isoformat(),
        'observed_at':CAPTURED.isoformat(),'status':'CAPTURED','origin':'LIVE_PRIMARY_RESEARCH_HTTP',
        'public_raw_available':True,'http_status':200,'raw_response_bytes':len(body),'raw_response_sha256':hashlib.sha256(body).hexdigest()}


def fixture(label='Apple',anonymous=False):
    dei={'EntityRegistrantName':'Skyworks Solutions, Inc.','EntityCentralIndexKey':'0000004127',
        'DocumentFiscalYearFocus':'2025','DocumentPeriodEndDate':'October 3, 2025','DocumentType':'10-K'}
    facts=''.join(f'<ix:nonNumeric name="dei:{key}" contextRef="c-1">{value}</ix:nonNumeric>' for key,value in dei.items())
    contexts=''
    for year,start,end,value,fact,context in PERIODS:
        contexts+=(f'<xbrli:context id="{context}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000004127</xbrli:identifier>'
            '<xbrli:segment><xbrldi:explicitMember dimension="srt:MajorCustomersAxis">swks:CompanyMember</xbrldi:explicitMember>'
            '<xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</xbrldi:explicitMember>'
            '<xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:SalesRevenueNetMember</xbrldi:explicitMember></xbrli:segment>'
            f'</xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>')
    numbers=[f'<ix:nonFraction unitRef="number" contextRef="{context}" decimals="2" name="us-gaap:ConcentrationRiskPercentage1" scale="-2" id="{fact}">{value}</ix:nonFraction>'
             for year,start,end,value,fact,context in PERIODS]
    paragraph=(f'<div>During fiscal 2025, fiscal 2024, and fiscal 2023, {label}, {CHANNELS}, in the aggregate accounted for '
        f'{numbers[0]}%, {numbers[1]}%, and {numbers[2]}% of the Company’s net revenue, respectively.</div>')
    scope=f'<div>{SCOPE}</div>';calendar=f'<div>{CALENDAR}</div>'
    section='<div>14. SEGMENT INFORMATION AND CONCENTRATIONS</div>'
    alias='<div>Our key customers include Amazon, Apple Inc. (“Apple”), and Delta Corp. Our competitors include Delta Rival.</div>'
    body=('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
        'xmlns:srt="http://fasb.org/srt/2025" xmlns:swks="http://www.skyworksinc.com/20251003"><body>'
        '<xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000004127</xbrli:identifier></xbrli:entity>'
        '<xbrli:period><xbrli:startDate>2024-09-28</xbrli:startDate><xbrli:endDate>2025-10-03</xbrli:endDate></xbrli:period></xbrli:context>'
        f'<xbrli:unit id="number"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>{contexts}{facts}{alias}{scope}{calendar}{section}{paragraph}</body></html>').encode()
    index=('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">'
        '<html><body><span class="companyName">Skyworks <a href="/cgi-bin/browse-edgar?CIK=0000004127">CIK</a></span>'
        '<div class="infoHead">Filing Date</div><div class="info">2025-11-07</div>'
        '<div class="infoHead">Period of Report</div><div class="info">2025-10-03</div>'
        '<div class="infoHead">Accepted</div><div class="info">2025-11-07 16:08:12</div>'
        f'<table><tr><td>1</td><td>10-K</td><td><a href="{URL}">swks-20251003.htm</a></td><td>10-K</td><td>{len(body)}</td></tr></table></body></html>').encode()
    selected={'issuer_ticker':'SWKS','issuer_context_id':'c-1','issuer_name':'Skyworks Solutions, Inc.','issuer_cik':'0000004127','fiscal_period_end':'2025-10-03',
        'claim_role':'CUSTOMER_REVENUE_CONCENTRATION','reporting_scope':'CONSOLIDATED_REPORTING_GROUP',
        'counterparty_label':label,'counterparty_kind':'ANONYMOUS' if anonymous else 'NAMED',
        'denominator_literal':'the Company’s net revenue','channel_qualifier_literal':CHANNELS,'aggregation_qualifier_literal':'in the aggregate',
        'scope_passage':pin(body,scope),'fiscal_calendar_passage':pin(body,calendar),'section_heading':pin(body,section),'concentration_passage':pin(body,paragraph),
        'periods':[dict(zip(('fiscal_year','period_start','period_end','value_literal','fact_id','context_id'),period)) for period in PERIODS]}
    if not anonymous:selected.update(counterparty_legal_name_literal='Apple Inc.',alias_passage=pin(body,alias))
    return {'selection':selected},{'issuer':(capture(body,URL),body),'index':(capture(index,INDEX),index)}


def changed(original,before,after,key='issuer'):
    request,captures=deepcopy(original);metadata,body=captures[key];modified=body.replace(before,after)
    captures[key]=(capture(modified,metadata['requested_url']),modified)
    if key=='issuer':
        for field in ('scope_passage','fiscal_calendar_passage','section_heading','concentration_passage','alias_passage'):
            if field in request['selection']:
                old=request['selection'][field];literal=body[old['byte_start']:old['byte_end']].replace(before,after).decode()
                request['selection'][field]=pin(modified,literal)
    return request,captures


def legal_fixture():
    rows=[('document_type','Document Type','10-K'),('legal_name','Entity Registrant Name','Apple Inc.'),
        ('cik','Entity Central Index Key','0000320193'),('period_end','Document Period End Date','Sep. 27, 2025'),('fiscal_year','Document Fiscal Year Focus','2025')]
    body=('<html><body><table><tr><th rowspan="2">Cover</th><th>12 Months Ended</th></tr><tr><th>Sep. 27, 2025</th></tr>'
          +''.join(f'<tr><td>{label}</td><td>{value}</td></tr>' for _,label,value in rows)+'</table></body></html>').encode()
    url='https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/R1.htm'
    pins={key:pin(body,f'<tr><td>{label}</td><td>{value}</td></tr>') for key,label,value in rows}
    pins['header_period']=pin(body,'<tr><th>Sep. 27, 2025</th></tr>')
    selected={'mode':'SEC_R1_TABLE','table_index':0,'value_column':1,'legal_name':'Apple Inc.','cik':'0000320193',
              'report_period_end':'2025-09-27','field_pins':pins}
    return selected,(capture(body,url),body)


class SupplierConcentrationTests(unittest.TestCase):
    def setUp(self):
        self.clock=patch.object(service,'_physical_clock',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)

    def review(self,inputs=None,**kwargs):
        request,captures=inputs or fixture()
        return service.review_sec_supplier_concentration(request,captures,**kwargs)

    def test_exact_named_percentages_keep_denominator_period_channels_and_generic_dimensions(self):
        result=self.review();self.assertEqual('CANDIDATE_REVIEWED',result['status'],result)
        c=result['claim'];self.assertEqual(['67','69','66'],[r['value_literal'] for r in c['quantities']])
        self.assertEqual([2025,2024,2023],[r['fiscal_year'] for r in c['quantities']])
        self.assertEqual('Skyworks Solutions, Inc.',c['denominator']['owner_name'])
        self.assertFalse(c['denominator']['counterparty_own_revenue']);self.assertIsNone(c['denominator']['absolute_amount'])
        self.assertEqual(CHANNELS,c['channel_qualifier_literal']);self.assertEqual('in the aggregate',c['aggregation_qualifier_literal'])
        self.assertEqual('Apple Inc.',c['counterparty']['reported_legal_name']);self.assertTrue(c['counterparty']['source_disclosed_alias_supported'])
        for row in c['quantities']:
            self.assertIsNone(row['currency']);self.assertFalse(row['currency_applicable'])
            self.assertFalse(row['generic_customer_member_renamed']);self.assertFalse(row['counterparty_identity_from_dimension_verified'])
            self.assertEqual('swks:CompanyMember',row['source_dimensions'][0]['member'])

    def test_independent_exact_legal_snapshot_is_separate_from_each_full_period_continuity(self):
        request,captures=fixture();selected,capture_value=legal_fixture();request['counterparty_selection']=selected;captures['counterparty']=capture_value
        result=self.review((request,captures));legal=result['claim']['independent_counterparty_legal_snapshot']
        self.assertTrue(legal['independent_source_snapshot_supported']);self.assertEqual('0000320193',legal['cik'])
        self.assertEqual('2025-09-27',legal['report_period_end'])
        self.assertFalse(legal['relationship_date_identity_verified']);self.assertFalse(result['continuous_legal_identity_verified'])
        self.assertFalse(result['counterparty_identity_at_each_period_verified']);self.assertFalse(result['counterparty_financials_attribution_allowed'])

    def test_anonymous_percentages_are_useful_without_any_named_attribution(self):
        result=self.review(fixture('Customer A',True));self.assertEqual('CANDIDATE_REVIEWED',result['status'],result)
        self.assertEqual('ANONYMOUS',result['claim']['counterparty']['kind'])
        self.assertIsNone(result['claim']['counterparty']['reported_legal_name'])
        self.assertEqual('ANONYMOUS_ONLY',result['claim']['independent_counterparty_legal_snapshot']['status'])
        self.assertIn('ANONYMOUS_LABEL_ATTRIBUTION_BLOCKED',result['claim']['missing_facts'])

    def test_anonymous_customer_cannot_be_renamed_nvda_or_adopt_an_independent_legal_identity(self):
        for label in ('Customer A','CustomerA','one customer','a customer'):
            request,captures=fixture(label,True);request['selection']['counterparty_kind']='NAMED'
            with self.subTest(label=label):self.assertEqual('ANONYMOUS_LABEL_CANNOT_BE_RECLASSIFIED_AS_NAMED',self.review((request,captures))['reason'])
        request,captures=fixture('Customer A',True);request['selection'].update(counterparty_label='NVIDIA',counterparty_kind='NAMED')
        self.assertEqual('COUNTERPARTY_PERCENTAGES_NOT_THE_SAME_COMPLETE_SCOPED_SENTENCE',self.review((request,captures))['reason'])
        request,captures=fixture('Customer A',True);selected,value=legal_fixture();request['counterparty_selection']=selected;captures['counterparty']=value
        self.assertEqual('ANONYMOUS_CUSTOMER_CANNOT_ACQUIRE_A_NAMED_LEGAL_IDENTITY',self.review((request,captures))['reason'])

    def test_other_paragraph_name_does_not_attach_to_an_anonymous_numeric_sentence(self):
        request,captures=fixture('Customer A',True)
        request['selection'].update(counterparty_kind='NAMED',counterparty_label='Apple',counterparty_legal_name_literal='Apple Inc.')
        body=captures['issuer'][1];request['selection']['alias_passage']=pin(body,'<div>Our key customers include Amazon, Apple Inc. (“Apple”), and Delta Corp. Our competitors include Delta Rival.</div>')
        self.assertEqual('COUNTERPARTY_PERCENTAGES_NOT_THE_SAME_COMPLETE_SCOPED_SENTENCE',self.review((request,captures))['reason'])

    def test_legal_name_alias_must_be_literal_customer_scope_not_competitor_or_fuzzy_name(self):
        f=fixture();f[0]['selection']['counterparty_legal_name_literal']='APPLE INC.'
        self.assertEqual('LEGAL_NAME_ALIAS_NOT_EXPLICIT_IN_CUSTOMER_SCOPE',self.review(f)['reason'])
        f=changed(fixture(),b'Our key customers include Amazon, Apple Inc.',b'Our competitors include Amazon, Apple Inc.')
        self.assertEqual('LEGAL_NAME_ALIAS_NOT_EXPLICIT_IN_CUSTOMER_SCOPE',self.review(f)['reason'])

    def test_fraction_scale_unit_values_and_concept_are_not_interchangeable(self):
        for before,after in ((b'scale="-2"',b'scale="0"'),(b'xbrli:pure',b'xbrli:shares'),
            (b'us-gaap:ConcentrationRiskPercentage1',b'us-gaap:OtherCommitment'),(b'>67</ix:nonFraction>',b'>68</ix:nonFraction>')):
            with self.subTest(after=after):self.assertEqual('REJECTED',self.review(changed(fixture(),before,after))['status'])
        f=fixture();f[0]['selection']['periods'][0]['value_literal']='101'
        self.assertEqual('INVALID_CONCENTRATION_PERIOD_OR_PERCENTAGE',self.review(f)['reason'])

    def test_receivable_denominator_cannot_be_relabelled_as_net_revenue(self):
        f=changed(fixture(),b'SalesRevenueNetMember',b'AccountsReceivableNetCurrentMember')
        self.assertEqual('CONCENTRATION_CUSTOMER_NET_REVENUE_DIMENSIONS_REQUIRED',self.review(f)['reason'])
        f=fixture();f[0]['selection']['denominator_literal']='Apple’s net revenue'
        self.assertEqual('CUSTOMER_CHANNEL_AGGREGATION_OR_NET_REVENUE_DENOMINATOR_MISMATCH',self.review(f)['reason'])

    def test_generic_customer_member_is_never_substituted_by_a_named_company(self):
        f=changed(fixture(),b'swks:CompanyMember',b'swks:NVIDIAMember')
        self.assertEqual('CONCENTRATION_CUSTOMER_NET_REVENUE_DIMENSIONS_REQUIRED',self.review(f)['reason'])

    def test_percentages_periods_or_contexts_cannot_be_swapped_between_years(self):
        f=fixture();f[0]['selection']['periods'].reverse();self.assertEqual('REJECTED',self.review(f)['status'])
        f=fixture();f[0]['selection']['periods'][0]['context_id']='c-230';self.assertEqual('REJECTED',self.review(f)['status'])
        f=changed(fixture(),b'<xbrli:endDate>2024-09-27</xbrli:endDate>',b'<xbrli:endDate>2024-09-28</xbrli:endDate>')
        self.assertEqual('CONCENTRATION_PERIOD_ENTITY_OR_REPORTING_SCOPE_MISMATCH',self.review(f)['reason'])

    def test_source_fiscal_calendar_dates_and_53_week_period_are_preserved(self):
        result=self.review();self.assertIn('53 weeks',result['claim']['evidence']['fiscal_calendar_passage']['literal_visible_passage'])
        self.assertEqual('2024-09-28',result['claim']['quantities'][0]['period_start'])
        f=changed(fixture(),'October 3, 2025 (“fiscal 2025”)'.encode(),'October 4, 2025 (“fiscal 2025”)'.encode())
        self.assertEqual('CONCENTRATION_PERIOD_ENTITY_OR_REPORTING_SCOPE_MISMATCH',self.review(f)['reason'])

    def test_clause_qualifiers_and_respectively_order_cannot_be_trimmed_or_extended(self):
        for before,after in ((b'in the aggregate accounted',b'not in the aggregate accounted'),
            (b'respectively.',b'and another customer, respectively.'),(b'through sales to multiple distributors,',b'through direct sales,')):
            with self.subTest(after=after):self.assertEqual('REJECTED',self.review(changed(fixture(),before,after))['status'])

    def test_whole_visible_paragraph_and_original_hash_offsets_are_mandatory(self):
        f=fixture();p=f[0]['selection']['concentration_passage'];p['byte_end']-=6;p['sha256']=hashlib.sha256(f[1]['issuer'][1][p['byte_start']:p['byte_end']]).hexdigest()
        self.assertEqual('SELECTION_NOT_COMPLETE_ORIGINAL_BLOCK',self.review(f)['reason'])
        f=fixture();f[0]['selection']['concentration_passage']['sha256']='0'*64
        self.assertEqual('WHOLE_BLOCK_HASH_OFFSET_OR_BOUND_MISMATCH',self.review(f)['reason'])
        f=changed(fixture(),b'<div>During fiscal',b'<div hidden="hidden">During fiscal')
        self.assertEqual('NONVISIBLE_OR_NONLEAF_SELECTED_BLOCK',self.review(f)['reason'])

    def test_source_issuer_cik_name_and_consolidated_scope_are_not_legal_entity_allocations(self):
        for key,value in (('issuer_name','Skyworks'),('issuer_cik','0000320193'),('issuer_context_id','c-229'),('reporting_scope','SINGLE_ISSUER')):
            f=fixture();f[0]['selection'][key]=value
            with self.subTest(key=key):self.assertEqual('REJECTED',self.review(f)['status'])
        f=changed(fixture(),b'are included in the Company',b'are not included in the Company')
        self.assertEqual('CONSOLIDATED_NOTE_AND_CONCENTRATION_SCOPE_NOT_EXPLICIT',self.review(f)['reason'])

    def test_external_footnotes_and_a_table_cell_scope_cannot_be_silently_dropped(self):
        f=changed(fixture(),b'respectively.</div>',b'respectively.<sup>(1)</sup></div>')
        self.assertEqual('UNRESOLVED_EXTERNAL_FOOTNOTE_OR_REFERENCE',self.review(f)['reason'])
        f=changed(fixture(),f'<div>{SCOPE}</div>'.encode(),f'<table><tr><td><div>{SCOPE}</div></td></tr></table>'.encode())
        f[0]['selection']['scope_passage']=pin(f[1]['issuer'][1],f'<div>{SCOPE}</div>')
        self.assertEqual('UNSUPPORTED_TABLE_COLUMN_SCOPE',self.review(f)['reason'])

    def test_namespace_override_or_duplicate_facts_cannot_change_unit_or_customer_scope(self):
        f=changed(fixture(),b'<xbrli:measure>',b'<xbrli:measure xmlns:xbrli="http://example.com">')
        self.assertEqual('CONCENTRATION_PERCENTAGE_NEEDS_DIMENSIONLESS_UNIT',self.review(f)['reason'])
        f=changed(fixture(),b'<xbrldi:explicitMember dimension="srt:',b'<xbrldi:explicitMember xmlns:srt="http://example.com" dimension="srt:')
        self.assertEqual('CONCENTRATION_CUSTOMER_NET_REVENUE_DIMENSIONS_REQUIRED',self.review(f)['reason'])

    def test_exact_index_scope_filer_report_dates_and_visibility_are_required(self):
        for before,after in ((b'CIK=0000004127',b'CIK=0000320193'),(b'<td>10-K</td>',b'<td>20-F</td>'),
            (b'2025-11-07',b'2027-11-07'),(b'<tr>',b'<tr hidden="hidden">')):
            with self.subTest(after=after):self.assertEqual('REJECTED',self.review(changed(fixture(),before,after,'index'))['status'])

    def test_served_index_size_conflict_stays_visible_without_replacing_body(self):
        f=fixture();body=f[1]['issuer'][1];index=f[1]['index'][1].replace(f'<td>{len(body)}</td>'.encode(),f'<td>{len(body)-109}</td>'.encode())
        f[1]['index']=(capture(index,INDEX),index)
        result=self.review(f);self.assertEqual('CANDIDATE_REVIEWED',result['status'])
        self.assertTrue(result['claim']['filing']['publisher_size_conflict']);self.assertFalse(result['claim']['filing']['exact_historical_as_filed_version_verified'])
        self.assertEqual(body,f[1]['issuer'][1])

    def test_old_legal_snapshot_cannot_supply_period_continuity_and_bad_metadata_is_not_retained_as_identity(self):
        request,captures=fixture();selected,value=legal_fixture();request['counterparty_selection']=selected;captures['counterparty']=value
        captures['counterparty'][0]['known_at']=(NOW+timedelta(seconds=1)).isoformat()
        result=self.review((request,captures));legal=result['claim']['independent_counterparty_legal_snapshot']
        self.assertFalse(legal['independent_source_snapshot_supported']);self.assertNotIn('cik',legal)
        self.assertFalse(result['counterparty_identity_at_each_period_verified'])

    def test_wrong_legal_name_column_or_same_source_cannot_supply_independent_identity(self):
        for change in ({'legal_name':'APPLE INC.'},{'value_column':2},{'cik':'0001045810'}):
            request,captures=fixture();selected,value=legal_fixture();selected.update(change);request['counterparty_selection']=selected;captures['counterparty']=value
            with self.subTest(change=change):self.assertFalse(self.review((request,captures))['claim']['independent_counterparty_legal_snapshot']['independent_source_snapshot_supported'])

    def test_missing_or_withheld_whole_issuer_fails_without_any_substitute(self):
        f=fixture();metadata,body=f[1]['issuer'];metadata['body_withheld']=True;f[1]['issuer']=(metadata,None)
        result=self.review(f);self.assertEqual('REJECTED',result['status']);self.assertIsNone(result['claim'])
        f=fixture();f[1]['extra']=(capture(b'{}',URL),b'{}');self.assertEqual('EXPLICIT_CONCENTRATION_CAPTURE_SCOPE_REQUIRED',self.review(f)['reason'])

    def test_unsafe_whole_inputs_never_echo_or_project_access_values(self):
        f=changed(fixture(),b'</body>',b'<p>access_token=synthetic-only-value</p></body>')
        result=self.review(f);self.assertEqual('REJECTED',result['status']);self.assertNotIn('synthetic-only-value',json.dumps(result))
        f=fixture();f[0]['selection']['access_token']='synthetic-only-value';result=self.review(f)
        self.assertEqual('REJECTED',result['status']);self.assertNotIn('synthetic-only-value',json.dumps(result))

    def test_source_capture_and_current_ingestion_clocks_are_separate_and_past_cutoff_rejects(self):
        result=self.review();self.assertEqual(NOW.isoformat(),result['knowledge_at'])
        self.assertEqual(CAPTURED.isoformat(),result['claim']['source_inputs_known_at'])
        self.assertIsNone(result['claim']['primary_source']['publisher_publication_at'])
        self.assertFalse(result['claim']['filing']['acceptance_timezone_inferred'])
        self.assertEqual('AFTER_PHYSICAL_KNOWLEDGE_CUTOFF',self.review(as_of=NOW-timedelta(microseconds=1))['reason'])
        with patch.object(service,'_physical_clock',side_effect=[NOW,NOW-timedelta(microseconds=1)]):
            self.assertEqual('PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS',self.review()['reason'])

    def test_future_source_clock_or_wrong_whole_body_hash_fails_closed(self):
        f=fixture();f[1]['issuer'][0]['observed_at']=(NOW+timedelta(seconds=1)).isoformat();self.assertEqual('REJECTED',self.review(f)['status'])
        f=fixture();f[1]['issuer'][0]['raw_response_sha256']='0'*64;self.assertEqual('REJECTED',self.review(f)['status'])

    def test_no_amount_purchase_share_registry_model_or_trading_claim_is_activated(self):
        result=self.review()
        for field in ('scoring_applied','model_modified','trading_allowed','activation_allowed','counterparty_financials_attribution_allowed',
            'direct_customer_share_verified','supplier_purchase_share_verified','absolute_revenue_amount_inferred',
            'current_instrument_identity_verified','current_q3_identity_verified','historical_availability_reconstructed','out_of_sample_evaluation_verified'):
            self.assertIs(result[field],False)
        self.assertEqual(0,result['db_writes']);self.assertEqual(0,result['registry_writes'])

    def test_pure_replay_has_no_http_database_or_file_writes(self):
        with patch('socket.socket.connect',side_effect=AssertionError('No HTTP')),patch('sqlite3.connect',side_effect=AssertionError('No DB')),patch.object(Path,'write_bytes',side_effect=AssertionError('No file writes')):
            first=self.review();second=self.review()
        self.assertEqual(first,second);self.assertEqual('CANDIDATE_REVIEWED',first['status'])

    def test_actual_complete_primary_and_retained_legal_source_replay_exact_original_pins(self):
        root=Path(__file__).resolve().parents[1];packet=json.loads((root/'evidence/batch9_contracts_20261016/selection.json').read_bytes())
        def read(key):
            spec=packet[key];raw=(root/spec['repository_relative_path']).read_bytes();self.assertEqual(spec['sha256'],hashlib.sha256(raw).hexdigest());return raw
        captures={}
        for key in ('issuer','index','counterparty'):
            metadata=json.loads(read(key+'_capture'))
            for part in packet[key+'_capture'].get('json_pointer','').split('/')[1:]:metadata=metadata[int(part)] if isinstance(metadata,list) else metadata[part]
            captures[key]=(metadata,read(key+'_body'))
        result=service.review_sec_supplier_concentration(packet['request'],captures)
        self.assertEqual('CANDIDATE_REVIEWED',result['status'],result)
        self.assertEqual(['67','69','66'],[r['value_literal'] for r in result['claim']['quantities']])
        self.assertEqual('0000004127-25-000085',result['claim']['primary_source']['accession_number'])
        self.assertTrue(result['claim']['independent_counterparty_legal_snapshot']['independent_source_snapshot_supported'])
        self.assertTrue(result['claim']['filing']['publisher_size_conflict']);self.assertFalse(result['continuous_legal_identity_verified'])


if __name__=='__main__':unittest.main()
