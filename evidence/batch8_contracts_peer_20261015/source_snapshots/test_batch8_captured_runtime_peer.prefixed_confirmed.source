"""Independent captured-runtime boundary fixtures; no real forecasts or HTTP.

Every source and database below is temporary synthetic mechanism data. The
tests exercise the actual persistence boundary, not a duplicate validation rule.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services import captured_market_data_provider as provider_module
from market_checker_app.services.captured_market_data_provider import (
    BENCHMARK_TICKERS, MANIFEST_SCHEMA_VERSION, build_captured_market_data_entry,
)
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.services.us_equity_calendar_service import last_completed_session, sessions_between
from market_checker_app.services.yahoo_price_provenance_service import capture_provider_frame, persist_observation
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.weekly_shadow_runner import RuntimeConfigurationError, finalize_analysis_run, run_weekly_shadow


BEFORE_CLOSE = datetime(2026, 10, 15, 19, 59, tzinfo=timezone.utc)
AFTER_CLOSE = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)
DIAGNOSTICS = []


class CapturedRuntimePeerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = BEFORE_CLOSE
        self.manifest_path = self.root / 'market_manifest.json'
        latest = last_completed_session(BEFORE_CLOSE)
        sessions = sessions_between(latest-pd.Timedelta(days=180), latest)[-66:]
        values = [100.0+index/10 for index in range(len(sessions))]
        frame = pd.DataFrame({'Open': [v-.2 for v in values], 'High': [v+.3 for v in values],
            'Low': [v-.4 for v in values], 'Close': values, 'Volume': 1_000_000}, index=pd.DatetimeIndex(sessions))
        self.entries = []
        for ticker in ('AAPL', *BENCHMARK_TICKERS):
            observed = BEFORE_CLOSE-timedelta(minutes=1)
            record = capture_provider_frame(ticker, frame, started_at=observed-timedelta(seconds=1), observed_at=observed)
            record['evidence_origin'] = 'DETERMINISTIC_TEST_FIXTURE'
            record['capture_id'] = canonical_hash({key:value for key,value in record.items() if key!='capture_id'})
            path = persist_observation(record, self.root/'fixture_sources')
            self.entries.append(build_captured_market_data_entry(archive_root=self.root,
                source_archive_path=path.relative_to(self.root).as_posix()))
        self.manifest_path.write_text(json.dumps({'schema_version':MANIFEST_SCHEMA_VERSION,'instruments':self.entries}), encoding='utf-8')
        self.config = AppConfig(output_dir=self.root/'output', sqlite_path=self.root/'unit_only.db', export_excel=False,
            agent_stage1_enabled=False, captured_market_manifest=self.manifest_path, captured_market_archive_root=self.root)
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('No peer-test HTTP')).start()
        patch('market_checker_app.services.pipeline_service.YahooClient.__init__', side_effect=AssertionError('No live provider')).start()
        patch('market_checker_app.services.pipeline_service.YahooCacheStore.__init__', side_effect=AssertionError('No metadata cache')).start()
        patch('market_checker_app.services.pipeline_service.YahooOhlcCacheStore.__init__', side_effect=AssertionError('No OHLC cache')).start()
        patch('market_checker_app.collectors.mt5_client.MT5Client.load_open_positions', side_effect=AssertionError('No MT5 positions')).start()
        patch.object(provider_module, 'physical_clock', side_effect=lambda:self.clock).start()
        original_provider = provider_module.CapturedMarketDataProvider
        patch.object(provider_module, 'CapturedMarketDataProvider', side_effect=lambda **kwargs:
            original_provider(**kwargs, observation_clock=lambda:self.clock)).start()
        patch('market_checker_app.services.pipeline_service.utc_now', side_effect=lambda:self.clock).start()
        outer = self
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return outer.clock.astimezone(tz) if tz is not None else outer.clock.replace(tzinfo=None)
        patch('market_checker_app.services.pipeline_service.datetime', Clock).start()
        patch('market_checker_app.weekly_shadow_runner.datetime', Clock).start()
        self.pipeline = PipelineService(self.config)
        self.store = SQLiteStore(self.config.sqlite_path)

    def normal_fixture_run(self):
        result = self.pipeline.run(['AAPL'], [], self.store, rss_enabled=True, mt5_enabled=True,
            yahoo_metadata_enabled=True)
        for row in result['point_in_time_inputs']:
            row['provenance']['evidence_origin'] = 'DETERMINISTIC_TEST_FIXTURE'
        return result

    def finalize(self, result):
        try:
            summary = finalize_analysis_run(result=result, config=self.config, tickers=['AAPL'],
                store=self.store, pipeline=self.pipeline)
            return {'returned':True,'summary':summary}
        except RuntimeConfigurationError as error:
            self.assertIn('WAIT_DATA', str(error))
            return {'returned':False,'error_type':type(error).__name__,'wait_data':True}

    def test_initial_wait_data_stops_before_weekly_store_initialization(self):
        self.clock = AFTER_CLOSE
        with patch('market_checker_app.weekly_shadow_runner.SQLiteStore', side_effect=AssertionError('DB must stay unopened')):
            with self.assertRaisesRegex(RuntimeConfigurationError, 'WAIT_DATA'):
                run_weekly_shadow(config=self.config, tickers=['AAPL'], rss_sources=[],
                    rss_enabled=True, mt5_enabled=True, yahoo_metadata_enabled=True)
        self.assertFalse(self.config.sqlite_path.exists())

    def test_normal_captured_run_keeps_missing_metadata_and_exact_source_pins_without_live_cache(self):
        result = self.normal_fixture_run()
        snapshot, warning = self.pipeline.yahoo_client.fetch_metadata('AAPL')
        self.assertEqual({}, snapshot.data)
        self.assertIn('CAPTURED_METADATA_NOT_SUPPLIED', warning)
        provenance = result['point_in_time_inputs'][0]['provenance']['captured_market_inputs']
        entries = {entry['ticker']:entry for entry in self.entries}
        for role in ('asset','benchmark'):
            pin = provenance[role]
            self.assertEqual(entries[pin['ticker']]['source'],pin['source'])
            self.assertEqual('UNKNOWN',pin['publisher_revision_timing_status'])
            self.assertFalse(pin['source_clocks_rebased'])
            self.assertFalse(pin['strict_pit_eligible'])
        self.assertFalse(provenance['strict_pit_eligible'])
        self.assertTrue(self.store.read_prediction_snapshots().empty)

    def _baseline_write_boundary(self, change):
        result = self.normal_fixture_run()
        saved_macro = self.store.save_macro_observations
        def intervening_work(rows):
            value = saved_macro(rows)
            change()
            return value
        with patch.object(self.store,'save_macro_observations',side_effect=intervening_work):
            outcome = self.finalize(result)
        count = len(self.store.read_prediction_snapshots())
        current = self.pipeline.yahoo_client.readiness(['AAPL'])
        DIAGNOSTICS.append({'case':self._testMethodName,'fixture_only':True,
            'provider_current_status':current['status'],'provider_current_reasons':current['reason_counts'],
            'actual_baseline_snapshots_persisted':count,'finalizer_returned':outcome['returned'],
            'summary_snapshot_status':outcome.get('summary',{}).get('point_in_time_snapshot_status'),
            'summary_snapshot_error':outcome.get('summary',{}).get('point_in_time_snapshot_error'),
            'expected_baseline_snapshots_persisted':0,'http_attempts':0,'production_db_writes':0})
        self.assertEqual('WAIT_DATA', current['status'])
        self.assertEqual(0,count,'Captured inputs became WAIT_DATA after entry validation; no new forecast may cross the actual write boundary')

    def test_manifest_mutation_after_entry_check_stops_before_baseline_snapshot_write(self):
        self._baseline_write_boundary(lambda:self.manifest_path.write_bytes(self.manifest_path.read_bytes()+b' \n'))

    def test_crossing_next_session_close_after_entry_check_stops_before_baseline_snapshot_write(self):
        self._baseline_write_boundary(lambda:setattr(self,'clock',AFTER_CLOSE))

    def test_source_change_during_candidate_preparation_preserves_baseline_and_stops_candidate_write(self):
        result = self.normal_fixture_run()
        baseline = {}
        def candidate(*args,**kwargs):
            rows = self.store.read_prediction_snapshots()
            self.assertEqual(1,len(rows))
            baseline['snapshot_hash'] = rows.iloc[0]['snapshot_hash']
            self.manifest_path.write_bytes(self.manifest_path.read_bytes()+b' \n')
            identifier = str(rows.iloc[0]['snapshot_id'])
            return {'status':'FIXTURE_ONLY','analysis_only':True,'ranking_modified':False,
                'artifact':{'artifact_id':canonical_hash({'synthetic_boundary_fixture':True}),
                    'model_id':'SYNTHETIC_FIXTURE_ONLY','trained_as_of':BEFORE_CLOSE.isoformat(),'training_sample_count':0},
                'predictions':[{'snapshot_id':identifier,'ticker':'AAPL','as_of':BEFORE_CLOSE.isoformat(),
                    'candidate_probability_up':.5,'momentum_probability_up':.5}]}
        with patch('market_checker_app.weekly_shadow_runner.build_candidate_model_report',side_effect=candidate):
            outcome = self.finalize(result)
        snapshots = self.store.read_prediction_snapshots()
        candidates = self.store.read_candidate_model_predictions()
        DIAGNOSTICS.append({'case':self._testMethodName,'fixture_only':True,
            'actual_baseline_snapshots_persisted':len(snapshots),'actual_candidate_predictions_persisted':len(candidates),
            'expected_baseline_snapshots_persisted':1,'expected_candidate_predictions_persisted':0,
            'baseline_hash_preserved':len(snapshots)==1 and snapshots.iloc[0]['snapshot_hash']==baseline['snapshot_hash'],
            'finalizer_returned':outcome['returned'],'http_attempts':0,'production_db_writes':0})
        self.assertEqual(1,len(snapshots))
        self.assertEqual(baseline['snapshot_hash'],snapshots.iloc[0]['snapshot_hash'])
        self.assertEqual(0,len(candidates),'Already stored baseline stays immutable; changed captured input cannot acquire a new candidate output')


if __name__ == '__main__':
    unittest.main()
