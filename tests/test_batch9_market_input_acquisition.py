"""Deterministic synthetic chart bodies only; no live forecast/PIT acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pandas as pd

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services import captured_market_input_acquisition as module
from market_checker_app.services.captured_market_data_provider import CapturedMarketDataProvider
from market_checker_app.services.captured_market_input_acquisition import (
    ALLOWED_TICKERS, MAX_BODY_BYTES, CapturedMarketInputAcquisition,
    parse_original_chart, verify_original_chart_parent,
)
from market_checker_app.services.us_equity_calendar_service import _nyse_calendar
from market_checker_app.services.yahoo_price_provenance_service import load_observation


NOW=datetime(2026,10,15,22,tzinfo=timezone.utc)


def seal(record):
    record['capture_id']=canonical_hash({key:value for key,value in record.items() if key!='capture_id'})
    return record


def body_for(ticker,sessions,*,mutate=None):
    schedule=_nyse_calendar().schedule(start_date=sessions[0],end_date=sessions[-1])
    close=[100.125+index/8 for index in range(len(sessions))]
    quote={'open':[value-.25 for value in close],'high':[value+.5 for value in close],
        'low':[value-.5 for value in close],'close':close,
        'volume':[1_000_000+index for index in range(len(sessions))]}
    result={'meta':{'symbol':ticker,'currency':'USD','exchangeTimezoneName':'America/New_York',
        'exchangeName':'NMS' if ticker=='AAPL' else 'PCX','instrumentType':'EQUITY' if ticker=='AAPL' else 'ETF',
        'dataGranularity':'1d'},'timestamp':[int(value.timestamp()) for value in schedule.market_open],
        'indicators':{'quote':[quote],'adjclose':[{'adjclose':[value-.125 for value in close]}]}}
    if mutate:mutate(result)
    return json.dumps({'chart':{'result':[result],'error':None}},allow_nan=False,separators=(',',':')).encode()


def response(body,url,*,status=200,encoding='identity',complete=True):
    return {'status':status,'response_url':url,'body':body,'content_encoding':encoding,
        'content_type':'application/json','body_complete_within_byte_budget':complete}


class CapturedMarketInputAcquisitionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.calls=[]
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect',side_effect=AssertionError('No real source HTTP in fixtures')).start()
        patch('sqlite3.connect',side_effect=AssertionError('No DB')).start()

    def service(self,transport=None,clock=None):
        def source(url,limit):
            self.calls.append((url,limit))
            ticker=url.split('/chart/')[1].split('?')[0]
            return response(body_for(ticker,self.plan['expected_sessions']),url)
        return CapturedMarketInputAcquisition(archive_root=self.root/'archive',enabled=True,
            observation_clock=clock or (lambda:NOW),transport=transport or source)

    def run_capture(self,tickers=('AAPL',),transport=None):
        service=self.service(transport);self.plan=service.plan(tickers)
        return service,service.acquire(self.plan,execute=True)

    def record(self,report,index=0):
        return load_observation(self.root/'archive'/report['rows'][index]['observation_path'])

    def test_default_off_and_explicit_execute_are_both_required_without_files(self):
        def forbidden(*args):raise AssertionError('Disabled acquisition called transport')
        service=CapturedMarketInputAcquisition(archive_root=self.root/'disabled',transport=forbidden,
            observation_clock=lambda:NOW)
        plan=service.plan()
        self.assertEqual('DISABLED',service.acquire(plan,execute=True)['status'])
        service.enabled=True
        self.assertEqual('DISABLED',service.acquire(plan)['status'])
        self.assertFalse((self.root/'disabled').exists())

    def test_enable_and_execute_require_actual_booleans(self):
        with self.assertRaises(ValueError):CapturedMarketInputAcquisition(archive_root=self.root,enabled=1)
        service=self.service();plan=service.plan()
        with self.assertRaises(ValueError):service.acquire(plan,execute=1)
        self.assertFalse((self.root/'archive').exists())

    def test_plan_predeclares_exact_thirteen_urls_and_bounds_without_io(self):
        service=self.service();plan=service.plan()
        self.assertEqual(list(ALLOWED_TICKERS),plan['tickers'])
        self.assertEqual((13,2_000_000,26_000_000),(plan['request_budget'],plan['byte_budget_per_response'],plan['maximum_total_response_bytes']))
        self.assertEqual(66,len(plan['expected_sessions']))
        self.assertEqual('2026-10-15',plan['latest_completed_session'])
        self.assertEqual((0,0,0),(plan['redirects'],plan['retries'],plan['fallback_requests']))
        self.assertTrue(all(row['method']=='GET' and row['url'].startswith(module.ENDPOINT+row['ticker']+'?') for row in plan['requests']))
        self.assertFalse(self.calls);self.assertFalse((self.root/'archive').exists())

    def test_scope_is_exact_bounded_and_does_not_consume_an_unbounded_iterator(self):
        service=self.service()
        for tickers in (['NVDA'],['AAPL','AAPL'],list(ALLOWED_TICKERS)+['AAPL'],[]):
            with self.subTest(tickers=tickers),self.assertRaises(ValueError):service.plan(tickers)
        consumed=[]
        def endless():
            while True:consumed.append(1);yield 'AAPL'
        with self.assertRaises(ValueError):service.plan(endless())
        self.assertEqual(14,len(consumed));self.assertFalse(self.calls)

    def test_changed_plan_is_rejected_before_claim_or_network(self):
        service=self.service();plan=service.plan();plan['request_budget']=14
        with self.assertRaisesRegex(ValueError,'PLAN_CONTENT_MISMATCH'):service.acquire(plan,execute=True)
        self.assertFalse(self.calls);self.assertFalse((self.root/'archive').exists())

    def test_a_resealed_arbitrary_endpoint_is_not_a_valid_plan(self):
        service=self.service();plan=service.plan();plan['requests'][0]['url']='https://example.test/AAPL'
        plan['plan_id']=canonical_hash({k:v for k,v in plan.items() if k!='plan_id'})
        with self.assertRaisesRegex(ValueError,'STALE_OR_CHANGED'):service.acquire(plan,execute=True)
        self.assertFalse(self.calls)

    def test_plan_cannot_slide_to_a_new_completed_close_or_be_from_the_future(self):
        clock=[NOW];service=self.service(clock=lambda:clock[0]);plan=service.plan()
        clock[0]=NOW+timedelta(days=1)
        with self.assertRaisesRegex(ValueError,'STALE_OR_CHANGED'):service.acquire(plan,execute=True)
        service=self.service();plan=service.plan();plan['planned_at']=(NOW+timedelta(hours=1)).isoformat()
        plan['plan_id']=canonical_hash({k:v for k,v in plan.items() if k!='plan_id'})
        with self.assertRaisesRegex(ValueError,'STALE_OR_CHANGED'):service.acquire(plan,execute=True)
        self.assertFalse(self.calls)

    def test_naive_and_rolled_back_physical_clocks_fail_closed(self):
        with self.assertRaises(ValueError):self.service(clock=lambda:NOW.replace(tzinfo=None)).plan()
        clock=[NOW];service=self.service(clock=lambda:clock[0]);service.plan();clock[0]-=timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError,'CLOCK_ROLLBACK'):service.plan()
        self.assertFalse(self.calls)

    def test_full_thirteen_fixture_capture_is_ready_but_not_live_or_pit_evidence(self):
        service,report=self.run_capture(ALLOWED_TICKERS)
        self.assertEqual(('READY',13,13,13),(report['status'],report['source_http_requests'],report['raw_captures'],report['usable_count']))
        self.assertEqual(13,len(self.calls));self.assertTrue(report['fixture_only'])
        self.assertFalse(report['strict_pit_eligible']);self.assertFalse(report['prospective_audit_implicitly_enabled'])
        self.assertEqual((0,0,0),(report['db_writes'],report['forecasts_created'],report['labels_created']))
        for row in report['rows']:
            raw=self.root/'archive'/row['raw_path']
            self.assertEqual(body_for(row['ticker'],self.plan['expected_sessions']),raw.read_bytes())
            record=load_observation(self.root/'archive'/row['observation_path'])
            self.assertEqual('DETERMINISTIC_TEST_FIXTURE',record['evidence_origin'])
            self.assertEqual('UNKNOWN',record['publisher_revision_timing_status'])
            self.assertEqual('VERIFIED_CONTENT_REPLAY',verify_original_chart_parent(record,self.root/'archive')['status'])
        provider=CapturedMarketDataProvider(manifest_path=self.root/'archive'/report['manifest_path'],archive_root=self.root/'archive')
        self.assertEqual(0,provider.readiness(['AAPL'])['usable_count'])

    def test_parser_preserves_exact_ohlcv_and_timestamps_without_adjusting(self):
        service=self.service();plan=service.plan(['AAPL']);raw=body_for('AAPL',plan['expected_sessions'])
        frame,proof=parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)
        original=json.loads(raw)['chart']['result'][0]
        for lower,title in (('open','Open'),('high','High'),('low','Low'),('close','Close'),('volume','Volume')):
            self.assertEqual(original['indicators']['quote'][0][lower],frame[title].tolist())
        self.assertEqual(original['timestamp'],[int(value.timestamp()) for value in frame.index])
        self.assertNotEqual(frame.Close.tolist(),frame['Adj Close'].tolist())
        self.assertFalse(proof['frame_is_original_http_body']);self.assertFalse(proof['synthesized_price_or_volume_or_action_values'])

    def test_absent_action_fields_are_explicit_and_never_zero_filled(self):
        _,report=self.run_capture();record=self.record(report)
        self.assertEqual('MISSING_IN_RESPONSE',record['parser']['action_fields_status'])
        self.assertIsNone(record['parser']['original_action_fields'])
        self.assertNotIn('Stock Splits',json.loads(record['frame_json'])['columns'])
        self.assertNotIn('Dividends',json.loads(record['frame_json'])['columns'])
        self.assertFalse(record['parser']['action_columns_filled_with_zero'])

    def test_frame_serialization_preserves_subdecimal_prices_and_large_integer_volume(self):
        def source(url,limit):
            def mutate(result):
                quote=result['indicators']['quote'][0]
                for key,value in (('open',2e-18),('high',3e-18),('low',1e-18),('close',2.25e-18),('volume',2**53+1)):
                    quote[key][0]=value
                result['indicators']['adjclose'][0]['adjclose'][0]=1.75e-18
            return response(body_for('AAPL',self.plan['expected_sessions'],mutate=mutate),url)
        _,report=self.run_capture(transport=source);record=self.record(report)
        payload=json.loads(record['frame_json']);first=dict(zip(payload['columns'],payload['data'][0]))
        self.assertEqual((2e-18,2.25e-18,2**53+1),(first['Open'],first['Close'],first['Volume']))
        self.assertIs(type(first['Volume']),int)
        self.assertEqual('VERIFIED_CONTENT_REPLAY',verify_original_chart_parent(record,self.root/'archive')['status'])

    def test_sparse_action_event_map_is_preserved_as_returned_without_dense_values(self):
        service=self.service();plan=service.plan(['AAPL'])
        events={'dividends':{'1784073600':{'date':1784073600,'amount':.25}},'splits':{}}
        raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result.update(events=events))
        frame,proof=parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)
        self.assertEqual(events,proof['original_action_fields']);self.assertEqual('AS_RETURNED_EVENT_MAP',proof['action_fields_status'])
        self.assertNotIn('Dividends',frame.columns);self.assertNotIn('Stock Splits',frame.columns)

    def test_foreign_or_ambiguous_instrument_metadata_is_rejected(self):
        service=self.service();plan=service.plan(['AAPL'])
        for key,value in (('symbol','NVDA'),('currency','EUR'),('exchangeName','PCX'),('instrumentType','ETF'),('dataGranularity','1h'),('exchangeTimezoneName','UTC')):
            with self.subTest(key=key):
                raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result['meta'].update({key:value}))
                with self.assertRaisesRegex(ValueError,'AMBIGUOUS_CHART_INSTRUMENT'):parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)

    def test_missing_ohlcv_is_never_reconstructed_from_close_or_adjusted_close(self):
        service=self.service();plan=service.plan(['AAPL'])
        for key in ('open','high','low','close','volume'):
            raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result['indicators']['quote'][0].pop(key))
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'ORIGINAL_CHART_OHLCV'):
                parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)

    def test_nonfinite_null_nonnumeric_negative_and_short_arrays_are_rejected(self):
        service=self.service();plan=service.plan(['AAPL'])
        for value in (None,True,'100',0,-1):
            raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result['indicators']['quote'][0]['open'].__setitem__(2,value))
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'INCOMPLETE_OR_NONFINITE'):
                parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)
        raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result['indicators']['quote'][0]['volume'].pop())
        with self.assertRaises(ValueError):parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)

    def test_duplicate_shifted_and_missing_sessions_are_rejected_without_padding(self):
        service=self.service();plan=service.plan(['AAPL'])
        mutations=(lambda result:result['timestamp'].__setitem__(1,result['timestamp'][0]),
            lambda result:result['timestamp'].__setitem__(1,result['timestamp'][1]+60),lambda result:result['timestamp'].pop())
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw=body_for('AAPL',plan['expected_sessions'],mutate=mutate)
                with self.assertRaises(ValueError):parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)

    def test_older_window_and_future_session_knowledge_are_not_refreshed(self):
        service=self.service();plan=service.plan(['AAPL']);raw=body_for('AAPL',plan['expected_sessions'])
        with self.assertRaisesRegex(ValueError,'STALE_OR_SHIFTED'):
            parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW+timedelta(days=1))
        with self.assertRaisesRegex(ValueError,'STALE_OR_SHIFTED'):
            parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW-timedelta(hours=3))

    def test_duplicate_keys_nonfinite_constants_and_inconsistent_prices_are_rejected(self):
        service=self.service();plan=service.plan(['AAPL'])
        for raw in (b'{"chart":{},"chart":{}}',b'{"chart":NaN}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)
        raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result['indicators']['quote'][0]['high'].__setitem__(0,1))
        with self.assertRaisesRegex(ValueError,'INCONSISTENT'):parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)

    def test_unknown_or_unbounded_action_maps_are_not_adopted(self):
        service=self.service();plan=service.plan(['AAPL'])
        for events in ({'unknown':{}},{'splits':[]},{'dividends':{str(i):{} for i in range(67)}}):
            raw=body_for('AAPL',plan['expected_sessions'],mutate=lambda result:result.update(events=events))
            with self.subTest(events=events),self.assertRaises(ValueError):parse_original_chart(raw,ticker='AAPL',expected_sessions=plan['expected_sessions'],observed_at=NOW)

    def test_redirect_response_never_traverses_and_retains_only_original_safe_error(self):
        def source(url,limit):self.calls.append(url);return response(b'{"message":"redirect response"}','https://example.test/elsewhere')
        _,report=self.run_capture(transport=source)
        self.assertEqual((1,1,0),(len(self.calls),report['raw_captures'],report['usable_count']))
        self.assertIn('RESPONSE_URL_CONFLICT',report['rows'][0]['reason']);self.assertIsNone(report['manifest_path'])

    def test_http_failure_body_is_original_failed_evidence_not_an_empty_success(self):
        def source(url,limit):return response(b'{"chart":{"result":null,"error":{"code":"Too Many Requests"}}}',url,status=429)
        _,report=self.run_capture(transport=source)
        self.assertEqual(('WAIT_DATA',1,0),(report['status'],report['raw_captures'],report['usable_count']))
        self.assertEqual(429,report['rows'][0]['http_status']);self.assertEqual('COMPLETE_HTTP200_REQUIRED',report['rows'][0]['reason'])

    def test_unsafe_whole_bodies_are_withheld_without_substitute_or_error_echo(self):
        sentinel='SYNTHETIC_PRIVATE_SENTINEL_123456'
        for index,raw in enumerate((json.dumps({'api_key':sentinel}).encode(),b'{"\\u0061pi_key":"'+sentinel.encode()+b'"}',b'bad password='+sentinel.encode())):
            service=self.service(lambda url,limit:response(raw,url));service.archive_root=self.root/str(index)
            self.plan=service.plan(['AAPL']);report=service.acquire(self.plan,execute=True)
            self.assertEqual((0,0),(report['raw_captures'],report['usable_count']))
            self.assertEqual('WITHHELD_UNSAFE_WHOLE_CHART_BODY',report['rows'][0]['reason'])
            all_saved=b''.join(path.read_bytes() for path in service.archive_root.rglob('*') if path.is_file())
            self.assertNotIn(sentinel.encode(),all_saved);self.assertFalse(list(service.archive_root.rglob('*.raw')))

    def test_oversized_or_encoded_whole_response_is_not_archived_as_a_safe_prefix(self):
        for index,(raw,encoding,complete) in enumerate(((b'x'*(MAX_BODY_BYTES+1),'identity',False),(b'encoded-whole-bytes','gzip',True))):
            service=self.service(lambda url,limit:response(raw,url,encoding=encoding,complete=complete));service.archive_root=self.root/str(index)
            self.plan=service.plan(['AAPL']);report=service.acquire(self.plan,execute=True)
            self.assertEqual((0,0),(report['raw_captures'],report['usable_count']))
            self.assertFalse(list(service.archive_root.rglob('*.raw')))

    def test_transport_failure_stops_remaining_same_host_requests_without_retry(self):
        def source(url,limit):self.calls.append(url);raise URLError('synthetic fixture failure')
        _,report=self.run_capture(ALLOWED_TICKERS,transport=source)
        self.assertEqual((1,1,0),(len(self.calls),report['source_http_requests'],report['usable_count']))
        self.assertEqual('TRANSPORT_FAILURE',report['rows'][0]['reason'])
        self.assertTrue(all(row['reason']=='NOT_ATTEMPTED_AFTER_TRANSPORT_FAILURE' for row in report['rows'][1:]))

    def test_partial_capture_preserves_prior_and_later_successful_counts(self):
        def source(url,limit):
            ticker=url.split('/chart/')[1].split('?')[0]
            raw=body_for(ticker,self.plan['expected_sessions'],mutate=(lambda result:result['meta'].update(symbol='FOREIGN')) if ticker=='SPY' else None)
            return response(raw,url)
        _,report=self.run_capture(('AAPL','SPY','XLB'),transport=source)
        self.assertEqual(('PARTIAL',3,3,2),(report['status'],report['source_http_requests'],report['raw_captures'],report['usable_count']))
        self.assertEqual(['CAPTURED','WAIT_DATA','CAPTURED'],[row['status'] for row in report['rows']])

    def test_same_plan_replay_never_spends_the_request_budget_again(self):
        service,first=self.run_capture(ALLOWED_TICKERS)
        second=service.acquire(self.plan,execute=True)
        self.assertEqual(13,len(self.calls));self.assertEqual(('READY',0,13),(second['status'],second['source_http_requests'],second['original_source_http_requests']))
        self.assertEqual(first['manifest_sha256'],second['manifest_sha256']);self.assertTrue(second['replay'])
        fresh=self.service();third=fresh.acquire(self.plan,execute=True)
        self.assertEqual((0,13),(third['source_http_requests'],len(self.calls)))

    def test_interrupted_claim_cannot_be_retried_by_a_new_instance(self):
        def source(url,limit):self.calls.append(url);raise KeyboardInterrupt('synthetic interrupted execution')
        service=self.service(source);self.plan=service.plan(['AAPL'])
        with self.assertRaises(KeyboardInterrupt):service.acquire(self.plan,execute=True)
        report=self.service().acquire(self.plan,execute=True)
        self.assertEqual(('WAIT_DATA',0,1),(report['status'],report['source_http_requests'],len(self.calls)))
        self.assertEqual('PLAN_ALREADY_CLAIMED_NO_RETRY',report['reason'])

    def test_atomic_claim_also_blocks_a_concurrent_same_plan_execution(self):
        entered=threading.Event();release=threading.Event()
        def source(url,limit):
            self.calls.append(url);entered.set()
            if not release.wait(5):raise AssertionError('Test release missing')
            return response(body_for('AAPL',self.plan['expected_sessions']),url)
        first=self.service(source);self.plan=first.plan(['AAPL']);second=self.service()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(first.acquire,self.plan,execute=True)
            try:
                self.assertTrue(entered.wait(5));report=second.acquire(self.plan,execute=True)
                self.assertEqual(('WAIT_DATA',0),(report['status'],report['source_http_requests']))
            finally:release.set()
            self.assertEqual('READY',pending.result(timeout=10)['status'])
        self.assertEqual(1,len(self.calls))

    def test_raw_parent_tamper_or_missing_bytes_fail_read_only_replay(self):
        _,report=self.run_capture();record=self.record(report);raw=self.root/'archive'/report['rows'][0]['raw_path']
        before=raw.read_bytes();raw.write_bytes(before.replace(b'100.125',b'100.126',1))
        with self.assertRaisesRegex(ValueError,'HASH_OR_BYTE_MISMATCH'):verify_original_chart_parent(record,self.root/'archive')
        raw.unlink()
        with self.assertRaisesRegex(ValueError,'HASH_OR_BYTE_MISMATCH'):verify_original_chart_parent(record,self.root/'archive')

    def test_raw_parent_path_escape_and_symlink_guard_are_rejected(self):
        _,report=self.run_capture();record=self.record(report);raw=self.root/'archive'/report['rows'][0]['raw_path']
        for path in ('../escape.raw','/absolute.raw','one/../escape.raw'):
            changed=deepcopy(record);changed['raw_http_parent']['raw_path']=path;seal(changed)
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'OUTSIDE_ARCHIVE_ROOT'):verify_original_chart_parent(changed,self.root/'archive')
        original=Path.is_symlink;hits=[]
        def symlink(path):
            if path==raw:hits.append(path);return True
            return original(path)
        with patch.object(Path,'is_symlink',symlink),self.assertRaisesRegex(ValueError,'SYMLINK_REFUSED'):
            verify_original_chart_parent(record,self.root/'archive')
        self.assertTrue(hits)

    def test_resealed_frame_cannot_disagree_with_original_raw_quote_arrays(self):
        _,report=self.run_capture();record=self.record(report);body=json.loads(record['frame_json'])
        body['data'][0][0]+=.01;record['frame_json']=json.dumps(body,separators=(',',':'))
        record['frame_sha256']=hashlib.sha256(record['frame_json'].encode()).hexdigest();seal(record)
        with self.assertRaisesRegex(ValueError,'FRAME_REPLAY_MISMATCH'):verify_original_chart_parent(record,self.root/'archive')

    def test_resealed_source_hash_or_url_cannot_change_original_parent_binding(self):
        _,report=self.run_capture();record=self.record(report)
        for key,value in (('body_sha256','a'*64),('request_url','https://example.test/elsewhere'),('response_url','https://example.test/elsewhere')):
            changed=deepcopy(record);changed['raw_http_parent'][key]=value;seal(changed)
            with self.subTest(key=key),self.assertRaises(ValueError):verify_original_chart_parent(changed,self.root/'archive')
        changed=deepcopy(record);changed['upstream_response_sha256']='a'*64;seal(changed)
        with self.assertRaisesRegex(ValueError,'HASH_OR_BYTE_MISMATCH'):verify_original_chart_parent(changed,self.root/'archive')

    def test_parent_future_or_rolled_back_knowledge_clocks_cannot_be_rebased(self):
        _,report=self.run_capture();record=self.record(report)
        for key,value in (('attempted_at',(NOW+timedelta(seconds=1)).isoformat()),('available_at',(NOW-timedelta(seconds=1)).isoformat()),('archived_at',(NOW-timedelta(seconds=1)).isoformat())):
            changed=deepcopy(record);changed['raw_http_parent'][key]=value;seal(changed)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'CLOCK_CONFLICT'):verify_original_chart_parent(changed,self.root/'archive')
        changed=deepcopy(record);changed['captured_at']=(NOW+timedelta(days=3650)).isoformat();seal(changed)
        with self.assertRaisesRegex(ValueError,'CLOCK_CONFLICT'):verify_original_chart_parent(changed,self.root/'archive')

    def test_parent_parser_and_observation_method_are_pinned_and_not_replaced(self):
        _,report=self.run_capture();record=self.record(report)
        changed=deepcopy(record);changed['parser']['frame_is_original_http_body']=True;seal(changed)
        with self.assertRaisesRegex(ValueError,'FRAME_REPLAY_MISMATCH'):verify_original_chart_parent(changed,self.root/'archive')
        for key,value in (('auto_adjust_requested',True),('methodology','ADJUSTED_CLOSE_ONLY'),('synthesized_columns',['Open'])):
            changed=deepcopy(record);changed[key]=value;seal(changed)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'UNSUPPORTED_ORIGINAL'):verify_original_chart_parent(changed,self.root/'archive')

    def test_parent_verification_only_reads_exact_pins_without_discovery_or_writes(self):
        _,report=self.run_capture();record=self.record(report)
        original={path.relative_to(self.root):hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()}
        with patch.object(Path,'glob',side_effect=AssertionError('No discovery')),patch.object(Path,'rglob',side_effect=AssertionError('No discovery')):
            proof=verify_original_chart_parent(record,self.root/'archive')
        after={path.relative_to(self.root):hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(original,after);self.assertTrue(proof['original_clocks_preserved']);self.assertFalse(proof['strict_pit_eligible'])

    def test_native_transport_makes_one_exact_bounded_request_and_closes_response(self):
        class NativeResponse:
            code=200;headers={'Content-Encoding':'identity','Content-Type':'application/json'}
            def __enter__(self):return self
            def __exit__(self,*args):self.closed=True
            def geturl(self):return url
            def read(self,limit):self.limit=limit;return b'{"public":"original"}'
        class Opener:
            def open(self,request,timeout):self.request=request;self.timeout=timeout;return native
        url=module.ENDPOINT+'AAPL?exact=1';native=NativeResponse();opener=Opener()
        with patch.object(module,'build_opener',return_value=opener) as build:
            received=module._http_get(url,MAX_BODY_BYTES)
        self.assertEqual(url,opener.request.full_url);self.assertEqual('GET',opener.request.get_method())
        self.assertEqual(MAX_BODY_BYTES+1,native.limit);self.assertTrue(native.closed)
        self.assertEqual({},build.call_args.args[0].proxies);self.assertIsInstance(build.call_args.args[1],module._NoRedirect)
        self.assertIsNone(build.call_args.args[1].redirect_request(None,None,302,'',{},'https://example.test'))
        self.assertEqual(b'{"public":"original"}',received['body'])

    def test_native_http_error_retains_original_error_bytes_without_retry(self):
        url=module.ENDPOINT+'AAPL?exact=1';error=HTTPError(url,302,'redirect',{'Content-Encoding':'identity'},io.BytesIO(b'original safe redirect body'))
        class Opener:
            calls=0
            def open(self,*args,**kwargs):self.calls+=1;raise error
        opener=Opener()
        with patch.object(module,'build_opener',return_value=opener):received=module._http_get(url,MAX_BODY_BYTES)
        self.assertEqual(1,opener.calls);self.assertEqual(302,received['status']);self.assertEqual(b'original safe redirect body',received['body'])


if __name__=='__main__':unittest.main()
