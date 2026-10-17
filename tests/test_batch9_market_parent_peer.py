"""Independent synthetic raw-parent/provider/pipeline persistence boundary.

No genuine data, source HTTP, initialized DB, forecast registration or labels.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.config import AppConfig
from market_checker_app.services import captured_market_data_provider as provider_module
from market_checker_app.services import pipeline_service as pipeline_module
from market_checker_app.services.captured_market_data_provider import CapturedMarketDataProvider
from market_checker_app.services.captured_market_input_acquisition import (
    ALLOWED_TICKERS, CapturedMarketInputAcquisition,
)
from market_checker_app.services.us_equity_calendar_service import _nyse_calendar


NOW=datetime(2026,10,15,22,tzinfo=timezone.utc)
OBSERVATIONS=[]


class MemoryPersistenceSpy:
    """Only an in-memory boundary spy; it never initializes SQLite."""
    def __init__(self):self.saved=[];self.count_updates=[]
    def save_run(self,*args,**kwargs):self.saved.append((args,kwargs));return 17
    def update_run_counts(self,*args):self.count_updates.append(args)


def synthetic_chart(ticker,sessions):
    schedule=_nyse_calendar().schedule(start_date=sessions[0],end_date=sessions[-1])
    prices=[80+index/4 for index in range(len(sessions))]
    result={'meta':{'symbol':ticker,'currency':'USD','exchangeTimezoneName':'America/New_York',
        'exchangeName':'NMS' if ticker=='AAPL' else 'PCX',
        'instrumentType':'EQUITY' if ticker=='AAPL' else 'ETF','dataGranularity':'1d'},
        'timestamp':[int(value.timestamp()) for value in schedule.market_open],
        'indicators':{'quote':[{'open':[price-.25 for price in prices],
            'high':[price+.5 for price in prices],'low':[price-.5 for price in prices],
            'close':prices,'volume':[1_000_000+index for index in range(len(sessions))]}]}}
    return json.dumps({'chart':{'result':[result],'error':None}},separators=(',',':')).encode()


class MarketParentPipelinePeerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.sequence=0;self.symlinks=set();self.guard_hits=[]
        self.guards={}
        for target in ('socket.socket.connect','sqlite3.connect',
            'market_checker_app.services.pipeline_service.YahooClient.__init__',
            'market_checker_app.services.pipeline_service.YahooCacheStore',
            'market_checker_app.services.pipeline_service.YahooOhlcCacheStore',
            'market_checker_app.services.pipeline_service.SQLiteStore',
            'market_checker_app.services.pipeline_service.GleifClient',
            'market_checker_app.services.pipeline_service.RSSClient.collect',
            'market_checker_app.services.pipeline_service.MT5Client.fetch_ohlcv_batch',
            'market_checker_app.services.captured_market_input_acquisition._http_get'):
            guard=patch(target,side_effect=AssertionError('Forbidden remote/cache/DB operation'))
            self.guards[target]=guard.start();self.addCleanup(guard.stop)
        native_is_symlink=Path.is_symlink
        def is_symlink(path):
            if path in self.symlinks:self.guard_hits.append(path);return True
            return native_is_symlink(path)
        guard=patch.object(Path,'is_symlink',is_symlink);guard.start();self.addCleanup(guard.stop)
        clock=patch.object(pipeline_module,'utc_now',return_value=NOW);clock.start();self.addCleanup(clock.stop)

    def tearDown(self):
        for target,mock in self.guards.items():
            self.assertEqual(0,mock.call_count,target)
        self.assertFalse(list(self.root.rglob('*.db')))

    def fixture(self):
        root=self.root/('fixture_'+str(self.sequence));self.sequence+=1
        plan=None
        def source(url,byte_budget):
            self.assertEqual(2_000_000,byte_budget)
            ticker=url.split('/chart/')[1].split('?')[0]
            return {'status':200,'response_url':url,'body':synthetic_chart(ticker,plan['expected_sessions']),
                'content_encoding':'identity','content_type':'application/json',
                'body_complete_within_byte_budget':True}
        collector=CapturedMarketInputAcquisition(archive_root=root,enabled=True,
            observation_clock=lambda:NOW,transport=source)
        plan=collector.plan();report=collector.acquire(plan,execute=True)
        self.assertEqual(('READY',13),(report['status'],report['usable_count']))
        self.assertTrue(report['fixture_only'])
        manifest=root/report['manifest_path']
        provider=CapturedMarketDataProvider(manifest_path=manifest,archive_root=root,observation_clock=lambda:NOW)
        pins={row['ticker']:row for row in report['rows']}
        observations={row['observation_path']:hashlib.sha256((root/row['observation_path']).read_bytes()).hexdigest() for row in report['rows']}
        return {'root':root,'manifest':manifest,'manifest_bytes':manifest.read_bytes(),
            'provider':provider,'rows':pins,'observations':observations}

    def pipeline(self,fixture):
        config=AppConfig(output_dir=fixture['root']/'outputs',sqlite_path=fixture['root']/'history.db',
            save_history=True,agent_stage1_enabled=False,captured_market_manifest=fixture['manifest'],
            captured_market_archive_root=fixture['root'],large_universe_threshold=100)
        with patch.object(provider_module,'CapturedMarketDataProvider',return_value=fixture['provider']):
            pipeline=pipeline_module.PipelineService(config)
        self.assertIs(pipeline.yahoo_client,fixture['provider'])
        self.assertIsNone(pipeline.yahoo_cache);self.assertIsNone(pipeline.yahoo_ohlc_cache)
        return pipeline

    def invalidate(self,fixture,ticker,mode):
        raw=fixture['root']/fixture['rows'][ticker]['raw_path']
        if mode=='tamper':
            before=raw.read_bytes();changed=before.replace(b'"USD"',b'"EUR"',1)
            self.assertNotEqual(before,changed);self.assertEqual(len(before),len(changed));raw.write_bytes(changed)
        elif mode=='missing':raw.unlink()
        elif mode=='symlink':self.symlinks.add(raw)
        else:raise AssertionError('Unknown synthetic invalidation')
        return raw

    def assert_outer_pins_unchanged(self,fixture):
        self.assertEqual(fixture['manifest_bytes'],fixture['manifest'].read_bytes())
        self.assertEqual(fixture['observations'],{path:hashlib.sha256((fixture['root']/path).read_bytes()).hexdigest() for path in fixture['observations']})

    def record_case(self,scenario,ticker,mode,store,error):
        OBSERVATIONS.append({'scenario':scenario,'ticker':ticker,'mode':mode,
            'synthetic_in_memory_save_calls':len(store.saved),'raised_WAIT_DATA':error is not None and 'WAIT_DATA' in str(error),
            'source_http_requests':0,'actual_database_initializations':0,'genuine_forecasts':0,'fixture_only':True})

    def test_valid_marked_parents_reach_actual_pipeline_save_boundary_only_in_memory(self):
        fixture=self.fixture();pipeline=self.pipeline(fixture);store=MemoryPersistenceSpy()
        result=pipeline.run(['AAPL'],['https://example.test/never-fetched'],store,
            rss_enabled=True,mt5_enabled=True,yahoo_metadata_enabled=True)
        self.assertEqual(1,len(store.saved));self.assertEqual(17,result['run_id'])
        self.assertEqual('READY',result['captured_market_data']['pre_persistence']['status'])
        provenance=result['point_in_time_inputs'][0]['provenance']
        self.assertEqual('DETERMINISTIC_TEST_FIXTURE',provenance['evidence_origin']);self.assertTrue(provenance['fixture_only'])
        for name in ('asset','benchmark'):
            proof=provenance['captured_market_inputs'][name]
            self.assertEqual('VERIFIED_CONTENT_REPLAY',proof['original_chart_parent']['status'])
            self.assertEqual('UNKNOWN',proof['publisher_revision_timing_status']);self.assertFalse(proof['strict_pit_eligible'])
        self.assert_outer_pins_unchanged(fixture);self.record_case('positive_control','AAPL','none',store,None)

    def test_actual_provider_rereads_original_parent_after_successful_frame_read(self):
        fixture=self.fixture();provider=fixture['provider']
        frame,warning=provider.fetch_ohlc_only('AAPL');self.assertIsNone(warning);self.assertEqual(66,len(frame))
        self.invalidate(fixture,'AAPL','tamper')
        rejected,warning=provider.fetch_ohlc_only('AAPL')
        self.assertIsNone(rejected);self.assertIn('ORIGINAL_CHART_PARENT_HASH_OR_BYTE_MISMATCH',warning)
        self.assertIsNone(provider.provenance_for('AAPL')['provenance']);self.assert_outer_pins_unchanged(fixture)

    def preflight_case(self,mode):
        for ticker in ('AAPL','XLY'):
            with self.subTest(ticker=ticker,mode=mode):
                fixture=self.fixture();pipeline=self.pipeline(fixture);store=MemoryPersistenceSpy()
                raw=self.invalidate(fixture,ticker,mode);error=None
                try:pipeline.run(['AAPL'],[],store,rss_enabled=True,mt5_enabled=True)
                except RuntimeError as caught:error=caught
                self.record_case('preflight',ticker,mode,store,error)
                self.assertEqual(0,len(store.saved));self.assertIsNotNone(error);self.assertIn('WAIT_DATA',str(error))
                readiness=fixture['provider'].readiness(['AAPL'])
                self.assertEqual(('WAIT_DATA',12),(readiness['status'],readiness['usable_count']))
                row=next(row for row in readiness['rows'] if row['ticker']==ticker)
                self.assertIn('SYMLINK_REFUSED' if mode=='symlink' else 'PARENT_HASH_OR_BYTE_MISMATCH',row['reason'])
                if mode=='symlink':self.assertIn(raw,self.guard_hits)
                self.assert_outer_pins_unchanged(fixture)

    def test_raw_body_tamper_blocks_asset_or_unused_benchmark_at_preflight(self):self.preflight_case('tamper')
    def test_missing_raw_body_blocks_asset_or_unused_benchmark_at_preflight(self):self.preflight_case('missing')
    def test_raw_body_symlink_blocks_asset_or_unused_benchmark_and_hits_guard(self):self.preflight_case('symlink')

    def mutation_during_pipeline(self,*,mode,ticker,late):
        fixture=self.fixture();pipeline=self.pipeline(fixture);store=MemoryPersistenceSpy();changed=[];error=None
        def progress(state):
            if not changed and (state.current_step=='save_history' if late else bool(state.completed_rows)):
                changed.append(self.invalidate(fixture,ticker,mode))
        try:pipeline.run(['AAPL'],[],store,progress_callback=progress,rss_enabled=True,mt5_enabled=True)
        except RuntimeError as caught:error=caught
        self.record_case('save_progress_callback' if late else 'after_completed_row',ticker,mode,store,error)
        self.assertTrue(changed,'Mutation must happen on the real pipeline progress path')
        self.assertEqual(0,len(store.saved),'Invalid raw parent reached actual PipelineService save_run boundary')
        self.assertIsNotNone(error);self.assertIn('WAIT_DATA',str(error));self.assert_outer_pins_unchanged(fixture)
        if mode=='symlink':self.assertIn(changed[0],self.guard_hits)

    def test_parent_changed_after_input_consumption_blocks_final_persistence(self):
        for ticker in ('AAPL','XLY'):
            for mode in ('tamper','missing','symlink'):
                with self.subTest(ticker=ticker,mode=mode):self.mutation_during_pipeline(mode=mode,ticker=ticker,late=False)

    def test_parent_changed_in_save_history_callback_cannot_cross_persistence_boundary(self):
        for ticker in ('AAPL','XLY'):
            for mode in ('tamper','missing','symlink'):
                with self.subTest(ticker=ticker,mode=mode):self.mutation_during_pipeline(mode=mode,ticker=ticker,late=True)


if __name__=='__main__':unittest.main()
