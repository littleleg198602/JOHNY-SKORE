"""Pure in-memory controls and a genuine retained incomplete report; no SQL/HTTP."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import usaspending_prime_scope_review as service
from market_checker_app.services.usaspending_prime_archive_recovery import CAPTURE_SCHEMA
from market_checker_app.services.usaspending_scout_service import API_URL,FIELDS


NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
CAPTURED=datetime(2026,10,1,12,tzinfo=timezone.utc)
RECIPIENT={'uei':'ABCDEFGHIJKL','recipient_name':'SYNTHETIC RECIPIENT'}
QUERY={'filters':{'award_type_codes':['A','B','C','D'],'recipient_search_text':[RECIPIENT['uei']],
    'time_period':[{'start_date':'2026-01-01','end_date':'2026-10-01'}]},'fields':FIELDS,'limit':2,
    'sort':'Last Modified Date','order':'desc'}


def encoded(value):return json.dumps(value,allow_nan=False,sort_keys=True).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()


def row(identifier='TEST1'):
    return {'Award ID':identifier,'Recipient Name':RECIPIENT['recipient_name'],'Recipient UEI':RECIPIENT['uei'],
        'generated_internal_id':'CONT_AWD_'+identifier+'_9700_PARENT_9700','Start Date':'2026-09-01',
        'Award Amount':100.0,'Total Outlays':None,'Last Modified Date':'2026-09-30 12:03:00',
        'Awarding Agency':'Synthetic agency','Contract Award Type':'DELIVERY ORDER'}


def inputs(pages=None,mode='FIXTURE'):
    pages=pages if pages is not None else [([row()],False,1)]
    request={'query':deepcopy(QUERY),'recipient':dict(RECIPIENT),'selected_award_ids':[row()['generated_internal_id']],
             'pages':[],'legacy_payloads':[]};captures={}
    for number,(rows,has_next,total) in enumerate(pages,1):
        payload={'spending_level':'awards','limit':2,'results':rows,'page_metadata':{'page':number,'hasNext':has_next,
            'next':number+1 if has_next else None,'previous':number-1 if number>1 else None,'hasPrevious':number>1}}
        if total is not None:payload['page_metadata']['total']=total
        body=encoded(payload);started=CAPTURED+timedelta(seconds=3*(number-1))
        manifest={'schema_version':CAPTURE_SCHEMA,'capture_mode':mode,'source_url':API_URL,'source_url_final':API_URL,
            'method':'POST','http_status':200,'complete_response':True,'request':{**deepcopy(QUERY),'page':number},
            'request_started_at':started.isoformat(),'response_completed_at':(started+timedelta(seconds=1)).isoformat(),
            'response_sha256':sha(body),'response_bytes':len(body),'body_file':f'body-{sha(body)}.response',
            'response_headers':{'Content-Length':str(len(body))}}
        raw=encoded(manifest);m,b=f'manifest_{number}',f'body_{number}'
        request['pages'].append({'page':number,'manifest_capture':m,'manifest_sha256':sha(raw),'body_capture':b,'body_sha256':sha(body)})
        captures[m]=({},raw);captures[b]=({},body)
    return request,captures


def mutate(data,page=1,manifest=None,payload=None):
    request,captures=deepcopy(data);item=request['pages'][page-1]
    meta=json.loads(captures[item['manifest_capture']][1]);body=captures[item['body_capture']][1]
    if payload:
        value=json.loads(body);payload(value);body=encoded(value)
        meta.update(response_sha256=sha(body),response_bytes=len(body),body_file=f'body-{sha(body)}.response',response_headers={'Content-Length':str(len(body))})
        captures[item['body_capture']]=({},body);item['body_sha256']=sha(body)
    if manifest:manifest(meta)
    raw=encoded(meta);captures[item['manifest_capture']]=({},raw);item['manifest_sha256']=sha(raw)
    return request,captures


class PrimeScopeTests(unittest.TestCase):
    def setUp(self):
        clock=patch.object(service,'_physical_clock',return_value=NOW);clock.start();self.addCleanup(clock.stop)

    def review(self,data=None,**kwargs):
        request,captures=data or inputs()
        return service.review_usaspending_prime_scope(request,captures,**kwargs)

    def rejected(self,data,reason=None):
        result=self.review(data);self.assertEqual('REJECTED',result['status'],result)
        self.assertFalse(result['query_complete']);self.assertFalse(result['observed_page_chain_complete'])
        if reason:self.assertEqual(reason,result['reason'])

    def test_complete_synthetic_page_chain_is_only_a_mechanism_control(self):
        result=self.review(inputs([([row()],True,2),([row('TEST2')],False,2)]))
        self.assertEqual('SYNTHETIC_COMPLETE_CONTROL',result['status'],result)
        self.assertTrue(result['synthetic_page_chain_complete']);self.assertFalse(result['observed_page_chain_complete'])
        self.assertFalse(result['query_complete']);self.assertFalse(result['genuine_negative_verified'])
        self.assertEqual(2,result['reported_unique_awards']);self.assertTrue(result['reported_totals_stable'])

    def test_live_shaped_terminal_control_stays_distinct_from_snapshot_query_completeness(self):
        result=self.review(inputs(mode='LIVE'))
        self.assertEqual('TRAVERSAL_REVIEWED',result['status'])
        self.assertTrue(result['observed_page_chain_complete']);self.assertFalse(result['query_complete'])
        for key in ('publisher_revision_semantics_verified','pagination_snapshot_guaranteed','total_count_semantics_verified','source_sort_order_verified'):
            self.assertFalse(result[key])

    def test_terminal_empty_fixture_cannot_supply_a_genuine_negative_or_selected_award_absence(self):
        result=self.review(inputs([([],False,0)]))
        self.assertEqual('SYNTHETIC_COMPLETE_CONTROL',result['status'])
        self.assertFalse(result['genuine_negative_verified']);self.assertFalse(result['selected_award_absence_verified'])
        self.assertFalse(result['selected_award_presence'][0]['absence_verified'])

    def test_missing_next_page_has_exact_expected_request_but_no_invented_body_pin(self):
        result=self.review(inputs([([row()],True,2)]))
        self.assertEqual('WAIT',result['status']);missing=result['missing_evidence'][-1]
        self.assertEqual(2,missing['page']);self.assertEqual('WHOLE_NEXT_PAGE_REQUIRED_BY_HAS_NEXT',missing['reason'])
        self.assertIsNone(missing['expected_sha256']);self.assertFalse(missing['hash_pin_known'])
        self.assertEqual(service._digest({**QUERY,'page':2}),missing['expected_request_sha256'])

    def test_partial_tail_requires_whole_prior_pages_not_only_terminal_flag(self):
        request,captures=inputs([([row()],True,2),([row('TEST2')],False,2)])
        first=request['pages'].pop(0);captures.pop(first['manifest_capture']);captures.pop(first['body_capture'])
        result=self.review((request,captures));self.assertEqual('WAIT',result['status'])
        self.assertTrue(any(x.get('page')==1 and x['reason']=='WHOLE_PRIOR_PAGE_LINEAGE_MISSING' for x in result['missing_evidence']))
        self.assertFalse(result['observed_page_chain_complete'])

    def test_missing_manifest_preserves_exact_expected_hash_and_never_promotes_response_only(self):
        request,captures=inputs();item=request['pages'][0];captures.pop(item['manifest_capture'])
        result=self.review((request,captures));self.assertEqual('WAIT',result['status'])
        missing=result['missing_evidence'][0];self.assertEqual(item['manifest_sha256'],missing['expected_sha256'])
        self.assertTrue(missing['hash_pin_known']);self.assertIsNone(result['reported_unique_awards'])

    def test_body_and_manifest_each_require_their_own_whole_byte_pin(self):
        for kind in ('manifest','body'):
            request,captures=inputs();request['pages'][0][kind+'_sha256']='0'*64
            with self.subTest(kind=kind):self.rejected((request,captures),'ORIGINAL_PRIME_ARTIFACT_HASH_OR_BOUND_MISMATCH')
        request,captures=inputs();request['pages'][0]['body_sha256']=None
        self.rejected((request,captures),'AVAILABLE_PRIME_ARTIFACT_REQUIRES_EXACT_HASH_PIN')

    def test_hash_valid_response_cannot_inherit_a_different_manifest_length_or_wire_encoding(self):
        for changes in ({'response_bytes':1},{'body_file':'different.response'},{'content_encoding':'gzip'},
                        {'response_headers':{'Content-Length':'1'}},{'response_headers':{'Content-Encoding':'gzip'}}):
            with self.subTest(changes=changes):self.rejected(mutate(inputs(),manifest=lambda m:m.update(changes)))

    def test_transaction_store_replay_and_non_original_modes_cannot_be_prime_pages(self):
        for changes in ({'schema_version':'award_transaction_capture_v1'},{'capture_mode':'ARCHIVE_REPLAY'},
                        {'capture_mode':'PROJECTED_STORE_ROWS'},{'source_url':'https://api.usaspending.gov/api/v2/transactions/'}):
            with self.subTest(changes=changes):self.rejected(mutate(inputs(),manifest=lambda m:m.update(changes)))

    def test_http400_empty_is_an_error_not_a_negative(self):
        self.rejected(mutate(inputs([([],False,0)]),manifest=lambda m:m.update(http_status=400)))

    def test_same_query_fields_filters_limit_sort_and_page_are_required_on_every_original(self):
        for change in (lambda m:m['request'].update(page=2),lambda m:m['request'].update(limit=1),
                       lambda m:m['request'].update(order='asc'),lambda m:m['request']['filters'].update(recipient_search_text=['ZZZZZZZZZZZZ'])):
            with self.subTest(change=change):self.rejected(mutate(inputs(),manifest=change))

    def test_named_recipient_and_source_award_ids_must_match_not_only_search_text(self):
        for field,value in (('Recipient UEI','ZZZZZZZZZZZZ'),('Recipient Name','OTHER RECIPIENT'),('generated_internal_id','CONT_TX_TEST1')):
            with self.subTest(field=field):self.rejected(mutate(inputs(),payload=lambda p:p['results'][0].update({field:value})))

    def test_changed_or_duplicate_award_across_offset_pages_blocks_chain(self):
        source=row();second={**source,'Award Amount':999.0}
        self.rejected(inputs([([source],True,2),([second],False,2)]),'DUPLICATE_OR_CHANGED_AWARD_ACROSS_OFFSET_PAGES')

    def test_fixture_and_live_record_origins_cannot_be_mixed_into_original_chain(self):
        data=mutate(inputs([([row()],True,2),([row('TEST2')],False,2)]),page=2,manifest=lambda m:m.update(capture_mode='LIVE'))
        self.rejected(data,'FIXTURE_AND_ORIGINAL_PRIME_ORIGINS_CANNOT_BE_MIXED')

    def test_invalid_next_previous_and_non_boolean_hasnext_are_rejected(self):
        for field,value in (('hasNext',1),('next',2),('previous',False),('hasPrevious',0),('total',True)):
            with self.subTest(field=field):self.rejected(mutate(inputs(),payload=lambda p:p['page_metadata'].update({field:value})))

    def test_original_total_changes_or_disagrees_with_unique_enumerated_awards(self):
        self.rejected(inputs([([row()],True,2),([row('TEST2')],False,3)]),'REPORTED_TOTAL_CHANGED_DURING_OFFSET_TRAVERSAL')
        self.rejected(inputs([([row()],False,2)]),'REPORTED_TOTAL_DOES_NOT_MATCH_ENUMERATED_UNIQUE_AWARDS')

    def test_original_last_record_cursor_cannot_refer_to_a_different_row(self):
        def valid(payload):
            payload['results'][0]['internal_id']=15
            payload['page_metadata']['last_record_unique_id']=15
        data=mutate(inputs(),payload=valid)
        self.assertEqual('SYNTHETIC_COMPLETE_CONTROL',self.review(data)['status'])
        self.rejected(mutate(data,payload=lambda p:p['page_metadata'].update(last_record_unique_id=16)),
                      'PRIME_LAST_RECORD_CURSOR_CONFLICT')

    def test_missing_total_is_wait_even_with_an_original_terminal_page(self):
        result=self.review(inputs([([row()],False,None)],mode='LIVE'))
        self.assertEqual('WAIT',result['status']);self.assertFalse(result['reported_totals_stable'])
        self.assertTrue(any(x['kind']=='STABLE_ORIGINAL_TOTAL' for x in result['missing_evidence']))

    def test_pages_after_terminal_response_cannot_extend_a_different_collection(self):
        self.rejected(inputs([([row()],False,2),([row('TEST2')],False,2)]),'EXTRA_PAGES_AFTER_ORIGINAL_TERMINAL_RESPONSE')

    def test_overlapping_future_or_reversed_capture_and_knowledge_clocks_fail_closed(self):
        two=inputs([([row()],True,2),([row('TEST2')],False,2)])
        self.rejected(mutate(two,page=2,manifest=lambda m:m.update(request_started_at=CAPTURED.isoformat())),'OVERLAPPING_OR_REORDERED_PRIME_CAPTURE_CLOCKS')
        for changes in ({'request_started_at':(CAPTURED+timedelta(days=1)).isoformat()},
                        {'known_at':(NOW+timedelta(seconds=1)).isoformat()},
                        {'response_completed_at':(NOW+timedelta(seconds=1)).isoformat()}):
            with self.subTest(changes=changes):self.rejected(mutate(inputs(),manifest=lambda m:m.update(changes)))

    def test_last_modified_clock_is_preserved_but_never_proves_revision_or_publication(self):
        for literal,status in ((None,'UNKNOWN'),('2026-09-30','DATE_ONLY'),('2026-09-30 12:03:00','TIMEZONE_UNKNOWN'),('2026-09-30T12:03:00Z','SOURCE_TIMESTAMP')):
            data=mutate(inputs(),payload=lambda p:p['results'][0].update({'Last Modified Date':literal}))
            result=self.review(data);clock=result['award_observations'][0]['modified_clock']
            self.assertEqual(literal,clock['raw']);self.assertEqual(status,clock['status'])
            self.assertFalse(clock['publisher_revision_time_verified']);self.assertFalse(result['source_revision_history_complete'])
        self.rejected(mutate(inputs(),payload=lambda p:p['results'][0].update({'Last Modified Date':'2099-01-01'})))

    def test_later_retained_artifact_availability_is_distinct_from_original_response_clock(self):
        request,captures=inputs();key=request['pages'][0]['body_capture'];known=NOW-timedelta(days=1)
        captures[key]=({'known_at':known.isoformat()},captures[key][1])
        result=self.review((request,captures));page=result['pages'][0]
        self.assertEqual(known.isoformat(),page['retained_artifact_known_at'])
        self.assertEqual(known.isoformat(),result['source_inputs_known_at'])
        self.assertEqual((CAPTURED+timedelta(seconds=1)).isoformat(),page['original_response_known_at'])

    def test_future_retained_artifact_ingestion_or_availability_cannot_hide_behind_old_source_capture(self):
        for field in ('known_at','available_at','ingested_at','first_ingested_at'):
            request,captures=inputs();key=request['pages'][0]['body_capture']
            captures[key]=({field:(NOW+timedelta(seconds=1)).isoformat()},captures[key][1])
            with self.subTest(field=field):self.rejected((request,captures),'RETAINED_ARTIFACT_METADATA_AFTER_REVIEW')

    def test_outer_retained_known_clock_cannot_precede_its_observation(self):
        request,captures=inputs();key=request['pages'][0]['body_capture']
        captures[key]=({'observed_at':NOW.isoformat(),'known_at':(NOW-timedelta(days=1)).isoformat()},captures[key][1])
        self.rejected((request,captures),'RETAINED_ARTIFACT_METADATA_CLOCK_REVERSED')

    def test_extra_snapshot_label_is_not_a_publisher_semantics_proof(self):
        data=mutate(inputs(mode='LIVE'),payload=lambda p:p.update(snapshot_id='synthetic-test-snapshot'))
        result=self.review(data);self.assertEqual('TRAVERSAL_REVIEWED',result['status'])
        self.assertFalse(result['query_complete']);self.assertFalse(result['pagination_snapshot_guaranteed'])
        self.assertFalse(result['publisher_revision_semantics_verified'])

    def test_unrecognized_scope_flags_cannot_request_completeness_or_earlier_identity(self):
        request,captures=inputs();request['query_complete']=True
        self.rejected((request,captures),'INVALID_BOUNDED_PRIME_SCOPE_REQUEST')

    def test_withheld_whole_body_is_not_restored_or_substituted(self):
        request,captures=inputs();key=request['pages'][0]['body_capture'];captures[key]=({'body_withheld':True},None)
        result=self.review((request,captures));self.assertEqual('WAIT',result['status'])
        request,captures=inputs();key=request['pages'][0]['body_capture'];captures[key]=({'body_withheld':True},captures[key][1])
        self.rejected((request,captures),'WITHHELD_WHOLE_BODY_CANNOT_HAVE_A_SUBSTITUTE')

    def test_unsafe_whole_input_is_rejected_without_echo_or_sanitized_projection(self):
        data=mutate(inputs(),payload=lambda p:p.update(access_token='synthetic-only-access-value'))
        result=self.review(data);self.assertEqual('REJECTED',result['status']);self.assertNotIn('synthetic-only-access-value',json.dumps(result))

    def test_duplicate_json_key_does_not_select_a_convenient_last_value(self):
        request,captures=inputs();item=request['pages'][0];raw=captures[item['manifest_capture']][1]
        raw=raw[:-1]+b',"capture_mode":"LIVE"}'
        captures[item['manifest_capture']]=({},raw);item['manifest_sha256']=sha(raw)
        self.rejected((request,captures),'DUPLICATE_JSON_KEY')

    def test_whole_body_and_manifest_bytes_have_independent_caps_before_decode(self):
        request,captures=inputs();key=request['pages'][0]['body_capture']
        captures[key]=({},b' '*4_000_001)
        self.rejected((request,captures),'PRIME_WHOLE_BODY_BOUND')
        request,captures=inputs();item=request['pages'][0];raw=b' '*64_001
        captures[item['manifest_capture']]=({},raw);item['manifest_sha256']=sha(raw)
        self.rejected((request,captures),'ORIGINAL_PRIME_ARTIFACT_HASH_OR_BOUND_MISMATCH')

    def test_page_budget_and_unselected_capture_cannot_expand_explicit_scope(self):
        request,captures=inputs();item=request['pages'][0]
        request['pages']=[{**item,'page':number,'manifest_capture':f'm_{number}','body_capture':f'b_{number}'} for number in range(1,7)]
        self.rejected((request,{}),'PRIME_SCOPE_PAGE_OR_CAPTURE_BOUND')
        request,captures=inputs();captures['outside_scope']=({},b'{}')
        self.rejected((request,captures),'UNSELECTED_CAPTURE_CANNOT_SUPPLY_PRIME_SCOPE')

    def test_completion_after_explicit_cutoff_clears_every_positive_source_observation(self):
        with patch.object(service,'_physical_clock',side_effect=[NOW,NOW+timedelta(seconds=1)]):result=self.review(inputs(mode='LIVE'),as_of=NOW)
        self.assertEqual('WAIT',result['status']);self.assertEqual('AFTER_PHYSICAL_REVIEW_CUTOFF',result['reason'])
        for key in ('award_observations','pages','legacy_observations','selected_award_presence'):self.assertEqual([],result[key])
        self.assertIsNone(result['reported_unique_awards']);self.assertIsNone(result['source_inputs_known_at'])
        self.assertFalse(result['reported_totals_stable']);self.assertFalse(result['observed_page_chain_complete'])
        self.assertEqual((NOW+timedelta(seconds=1)).isoformat(),result['knowledge_at'])

    def test_clock_rollback_has_no_backdated_knowledge_and_past_or_future_cutoff_does_not_parse(self):
        with patch.object(service,'_physical_clock',side_effect=[NOW,NOW-timedelta(seconds=1)]):result=self.review()
        self.assertEqual('PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS',result['reason']);self.assertIsNone(result['knowledge_at'])
        for cutoff,status in ((NOW-timedelta(seconds=1),'WAIT'),(NOW+timedelta(seconds=1),'REJECTED')):
            with patch.object(service,'_arguments',side_effect=AssertionError('No old/future cutoff parse')):
                self.assertEqual(status,self.review(as_of=cutoff)['status'])

    def test_no_file_network_sql_quota_registry_or_input_mutation_occurs(self):
        data=inputs();original=deepcopy(data)
        with patch('pathlib.Path.open',side_effect=AssertionError('No IO')),patch('socket.socket.connect',side_effect=AssertionError('No HTTP')),patch('sqlite3.connect',side_effect=AssertionError('No SQL')):
            result=self.review(data)
        self.assertEqual(original,data);self.assertEqual('SYNTHETIC_COMPLETE_CONTROL',result['status'])
        for key in ('source_http_requests','collector_requests','db_operations','quota_operations','registry_adoptions'):self.assertEqual(0,result[key])
        for key in ('earlier_uei_relationship_inferred','recipient_legal_identity_verified','issuer_coverage_complete','historical_availability_reconstructed','first_or_last_revision_verified','scoring_applied','trading_allowed','revenue_or_cumulative_difference_inferred'):self.assertFalse(result[key])

    def test_retained_actual_lmt_report_remains_unknown_original_scope_and_waits_for_exact_pins(self):
        from evidence.batch11_contracts_20261018.replay_retained import load_inputs
        result=self.review(load_inputs())
        self.assertEqual('WAIT',result['status'],result);self.assertIsNone(result['query_scope'])
        legacy=result['legacy_observations'][0];self.assertEqual(100,legacy['reported_rows']);self.assertTrue(legacy['reported_has_next'])
        self.assertFalse(legacy['original_http_body']);self.assertFalse(legacy['whole_original_request_present'])
        self.assertIsNone(result['reported_unique_awards']);self.assertFalse(result['genuine_negative_verified'])
        self.assertTrue(any(x['kind']=='WHOLE_ORIGINAL_REQUEST' for x in result['missing_evidence']))
        self.assertEqual({1,2},{x['page'] for x in result['missing_evidence'] if x.get('page')})
        self.assertTrue(all(not x['hash_pin_known'] for x in result['missing_evidence']))
        self.assertEqual('UNKNOWN_NO_ORIGINAL_PRIME_PAGES',result['selected_award_presence'][0]['observation'])


if __name__=='__main__':unittest.main()
