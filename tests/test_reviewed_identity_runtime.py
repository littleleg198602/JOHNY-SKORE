from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.agents.entity_registry_agent import EntityRegistryAgent
from market_checker_app.agents.contracts import AgentContext
from market_checker_app.agents.orchestrator import OrchestratorAgent
from market_checker_app.services.reviewed_identity_service import ReviewedIdentityRegistry, DEFAULT_EVENTS
from market_checker_app.storage.sqlite_store import SQLiteStore


class ReviewedIdentityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.registry = ReviewedIdentityRegistry.load()
        self.rows = self.registry.records
        self.now = max(datetime.fromisoformat(r['known_at']) for r in self.rows) + timedelta(seconds=1)

    def run_agent(self, ticker, records=None, when=None):
        return EntityRegistryAgent(records, reviewed_registry=self.registry).run(
            AgentContext('test', (ticker,), when or self.now))

    def test_successor_replaces_only_expected_issuer_and_drops_old_identifiers(self):
        raw={'XOM':{'cik':'0000034088','isin':'US0378331005','name':'old issuer'}}
        row=self.run_agent('XOM',raw).entities[0]
        self.assertEqual('0002115436',row.cik)
        self.assertEqual('cik:0002115436',row.issuer_id)
        self.assertIsNone(row.isin)
        self.assertIsNone(row.lei)
        self.assertEqual('ticker:XOM',row.entity_id)
        before=datetime.fromisoformat(self.rows[0]['known_at'])-timedelta(microseconds=1)
        old=self.run_agent('XOM',raw,when=before).entities[0]
        self.assertEqual('0000034088',old.cik)
        self.assertEqual('US0378331005',old.isin)

    def test_wrong_cik_is_quarantined_instead_of_silently_replaced(self):
        result=self.run_agent('XOM',{'XOM':{'cik':'0000320193'}})
        self.assertEqual('0000320193',result.entities[0].cik)
        self.assertEqual('QUARANTINED',result.entities[0].metadata['identity_resolution'])
        self.assertEqual(1,len(result.identity_conflicts))

    def test_merger_keeps_child_cik_and_does_not_inherit_parent_financial_identity(self):
        row=self.run_agent('LEG').entities[0]
        self.assertEqual('0000058492',row.cik)
        self.assertEqual('cik:0000058492',row.issuer_id)
        self.assertEqual('cik:0001206264',row.parent_entity_id)
        self.assertEqual('INACTIVE',row.metadata['instrument_status'])

    def test_observed_symbol_does_not_backdate_the_unknown_change_interval(self):
        event=next(r for r in self.rows if r['ticker']=='PSTG')
        before=datetime.fromisoformat(event['known_at'])-timedelta(microseconds=1)
        self.assertEqual('PSTG',self.registry.provider_symbol('PSTG',as_of=before))
        self.assertEqual('P',self.registry.provider_symbol('PSTG',as_of=self.now))
        self.assertEqual('PSTG',self.registry.provider_symbol('PSTG',as_of=self.now+timedelta(days=8)))
        class ReusedSymbol:
            def lookup(self,*args,**kwargs):
                return {'cik':'0000000001'}
        with patch('market_checker_app.services.issuer_catalog_service.IssuerCatalog.load',return_value=ReusedSymbol()):
            self.assertEqual('PSTG',self.registry.provider_symbol('PSTG',as_of=self.now))
        row=self.run_agent('PSTG').entities[0]
        self.assertEqual(('PSTG','P','0001474432'),(row.ticker,row.yahoo_ticker,row.cik))
        self.assertEqual([],row.metadata.get('dated_ticker_aliases',[]))

    def test_expired_future_and_not_yet_known_manual_identities_are_not_used(self):
        for fields in [dict(valid_from=(self.now+timedelta(days=1)).isoformat()),
                       dict(valid_to=self.now.isoformat()),
                       dict(metadata={'known_at':(self.now+timedelta(days=1)).isoformat()})]:
            row=self.run_agent('AAPL',{'AAPL':{'cik':'0000320193',**fields}}).entities[0]
            self.assertIsNone(row.cik)
            self.assertIsNone(row.legal_entity_id)
            self.assertEqual(0,row.confidence)

    def test_persistence_closes_prior_interval_without_rewriting_old_knowledge(self):
        old_clock=datetime(2026,6,1,tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            store=SQLiteStore(Path(tmp)/'history.db');store.ensure_schema()
            old=OrchestratorAgent(shadow_mode=True)
            old.register(EntityRegistryAgent({'XOM':{'cik':'0000034088','name':'old issuer'}},reviewed_registry=self.registry))
            with patch('market_checker_app.agents.orchestrator.utc_now',return_value=old_clock):
                first=old.run(watchlist=['XOM'])
            store.save_orchestration_report(first,pipeline_run_id=None)
            updated=OrchestratorAgent(shadow_mode=True);updated.register(EntityRegistryAgent(reviewed_registry=self.registry))
            with patch('market_checker_app.agents.orchestrator.utc_now',return_value=self.now):
                latest=updated.run(watchlist=['XOM'])
            store.save_orchestration_report(latest,pipeline_run_id=None)
            known_old=store.resolve_ticker_identity_as_of('XOM',known_at=old_clock+timedelta(seconds=1),effective_at=old_clock)
            self.assertEqual(['0000034088'],list(known_old['cik']))
            past=store.resolve_ticker_identity_as_of('XOM',known_at=self.now,effective_at=old_clock)
            self.assertEqual(['0000034088'],list(past['cik']))
            current=store.resolve_ticker_identity_as_of('XOM',known_at=self.now,effective_at=self.now)
            self.assertEqual(['0002115436'],list(current['cik']))
            version_count=len(store.read_entity_identity_versions())
            again=OrchestratorAgent(shadow_mode=True);again.register(EntityRegistryAgent(reviewed_registry=self.registry))
            with patch('market_checker_app.agents.orchestrator.utc_now',return_value=self.now+timedelta(seconds=1)):
                replay=again.run(watchlist=['XOM'])
            store.save_orchestration_report(replay,pipeline_run_id=None)
            self.assertEqual(['0002115436'],list(store.resolve_ticker_identity_as_of('XOM',known_at=self.now+timedelta(seconds=2),effective_at=self.now)['cik']))
            self.assertEqual(version_count,len(store.read_entity_identity_versions()))

    def test_inactive_instrument_is_rejected_by_quality_gate(self):
        from market_checker_app.agents.quality_gate_agent import QualityGateAgent
        from market_checker_app.agents import PredictionV21AdapterAgent
        import pandas as pd
        orchestrator=OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent(reviewed_registry=self.registry))
        orchestrator.register(PredictionV21AdapterAgent());orchestrator.register(QualityGateAgent())
        with patch('market_checker_app.agents.orchestrator.utc_now',return_value=self.now):
            report=orchestrator.run(watchlist=['LEG'],state={'signals':pd.DataFrame([{'ticker':'LEG','action':'NO_TRADE','forecast':'FLAT'}])})
        codes=[issue['code'] for check in report.quality_checks for issue in check.metadata['rejects']]
        self.assertIn('inactive_instrument',codes)

    def test_bogus_primary_source_and_duplicate_events_are_rejected(self):
        wrong=deepcopy(self.rows);wrong[0]['primary_url']='https://www.sec.gov/Archives/edgar/data/320193/a.htm'
        with self.assertRaises(ValueError):ReviewedIdentityRegistry(wrong)
        with self.assertRaises(ValueError):ReviewedIdentityRegistry(self.rows+[self.rows[0]])

    def test_bulk_provider_binding_preserves_canonical_result_keys(self):
        import pandas as pd
        from market_checker_app.collectors.yahoo_client import YahooClient
        frame=pd.DataFrame({'Open':[10.0],'High':[11.0],'Low':[9.0],'Close':[10.5],'Volume':[100]},
                           index=pd.DatetimeIndex(['2026-10-13']))
        with patch('market_checker_app.collectors.yahoo_client.datetime') as current_clock, patch('market_checker_app.collectors.yahoo_client.yf.download',return_value=frame) as download:
            current_clock.now.return_value=self.now
            frames,warnings=YahooClient().fetch_ohlc_batch(['PSTG'])
        self.assertEqual(['P'],download.call_args.kwargs['tickers'])
        self.assertEqual({'PSTG'},set(frames))
        self.assertEqual({},warnings)
