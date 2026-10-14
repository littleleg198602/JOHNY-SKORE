from copy import deepcopy
from datetime import timedelta
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from market_checker_app.agents import AgentContext, EntityRegistryAgent
from market_checker_app.services.issuer_catalog_service import IssuerCatalog
from market_checker_app.services.primary_listing_service import PrimaryListingRegistry, DEFAULT_BINDINGS
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.utils.ticker_universe import load_canonical_tickers


class PrimaryListingRegistryTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(DEFAULT_BINDINGS.read_text())
        self.registry=PrimaryListingRegistry(self.data)
        self.when=max(clock(r['known_at']) for r in self.data['records'])+timedelta(seconds=1)
        self.catalog=IssuerCatalog.load(as_of=self.when)

    def test_685_exact_primary_classes_preserve_canonical_keys_and_inactive_instrument(self):
        tickers=load_canonical_tickers()
        result=EntityRegistryAgent().run(AgentContext('test',tuple(tickers),self.when))
        self.assertEqual(tickers,[r.ticker for r in result.entities])
        verified=[r for r in result.entities if r.metadata.get('instrument_identity_verified')]
        self.assertEqual(685,len(verified))
        self.assertTrue(all(r.share_class and r.metadata['class_source_sha256'] and not r.isin and not r.lei for r in verified))
        rows=result.state_updates['entities_by_ticker']
        self.assertEqual('P',rows['PSTG'].yahoo_ticker)
        self.assertEqual('0002115436',rows['XOM'].cik)
        self.assertEqual('INACTIVE',rows['LEG'].metadata['instrument_status'])
        self.assertEqual('ISSUER_ONLY',rows['OZK'].metadata['identity_resolution'])

    def test_future_stale_wrong_issuer_reused_symbol_and_other_class_remain_unverified(self):
        r=self.registry.records['AAPL']
        args=dict(canonical_ticker='AAPL',symbol='AAPL',cik=r['issuer_cik'],as_of=self.when,catalog=self.catalog)
        self.assertIsNotNone(self.registry.lookup(**args))
        for changes in [dict(as_of=clock(r['known_at'])-timedelta(microseconds=1)),
                        dict(as_of=clock(r['known_at'])+timedelta(days=7)),dict(cik='0000000001'),dict(symbol='AAPL-P')]:
            self.assertIsNone(self.registry.lookup(**{**args,**changes}))
        class Reused:
            def lookup(self,*args,**kwargs):return {'cik':'0000000001'}
        self.assertIsNone(self.registry.lookup(**{**args,'catalog':Reused()}))

    def test_corrupt_source_wrong_issuer_and_duplicate_registry_rows_fail(self):
        for change in ['sha256','issuer_cik','known_at']:
            data=deepcopy(self.data);row=data['records'][0]
            row[change]={'sha256':'0'*64,'issuer_cik':'0000000001','known_at':'2000-01-01T00:00:00+00:00'}[change]
            with self.assertRaises(ValueError):PrimaryListingRegistry(data)
        data=deepcopy(self.data);data['records'].append(data['records'][0])
        with self.assertRaises(ValueError):PrimaryListingRegistry(data)

    def test_explicit_user_instrument_mapping_is_preserved(self):
        result=EntityRegistryAgent({'AAPL':{'cik':'0000320193','share_class':'explicit class','instrument_id':'explicit-id'}}).run(AgentContext('test',('AAPL',),self.when))
        self.assertEqual(('explicit class','explicit-id'),(result.entities[0].share_class,result.entities[0].instrument_id))

    def test_enriched_successor_persists_without_losing_effective_history(self):
        from market_checker_app.agents.orchestrator import OrchestratorAgent
        from market_checker_app.storage.sqlite_store import SQLiteStore
        from datetime import datetime,timezone
        old_clock=datetime(2026,6,30,tzinfo=timezone.utc)
        with TemporaryDirectory() as temp:
            store=SQLiteStore(Path(temp)/'history.db')
            def save(agent,when):
                runner=OrchestratorAgent(shadow_mode=True);runner.register(agent)
                with patch('market_checker_app.agents.orchestrator.utc_now',return_value=when):report=runner.run(watchlist=['XOM'])
                store.save_orchestration_report(report,pipeline_run_id=None)
                return report
            save(EntityRegistryAgent({'XOM':{'cik':'0000034088'}}),old_clock)
            report=save(EntityRegistryAgent(),self.when)
            self.assertTrue(report.entities[0].metadata['instrument_identity_verified'])
            self.assertEqual(['0000034088'],list(store.resolve_ticker_identity_as_of('XOM',known_at=self.when,effective_at=old_clock)['cik']))
            successor=store.resolve_ticker_identity_as_of('XOM',known_at=self.when,effective_at=datetime(2026,8,1,tzinfo=timezone.utc))
            self.assertEqual(['0002115436'],list(successor['cik']))
            uncertain=store.resolve_ticker_identity_as_of('XOM',known_at=self.when,
                effective_at=datetime(2026,7,1,12,tzinfo=timezone.utc))
            self.assertTrue(uncertain.empty)

    def test_sec_fundamentals_preserves_verified_distinct_share_class_instruments(self):
        from market_checker_app.agents import OrchestratorAgent,SecFundamentalsAgent
        from market_checker_app.config import FundamentalIngestionConfig
        from tests.test_stage2_fundamental_ingestion import _SameIssuerShareClassClient
        runner=OrchestratorAgent(shadow_mode=True);runner.register(EntityRegistryAgent())
        runner.register(SecFundamentalsAgent(FundamentalIngestionConfig(enabled=True,
            user_agent='JohnySkoreTests tests@example.com'),client=_SameIssuerShareClassClient()))
        with patch('market_checker_app.agents.orchestrator.utc_now',return_value=self.when):
            report=runner.run(watchlist=['GOOG','GOOGL'])
        primary=report.executions[0].result.entities
        expected={e.instrument_id for e in primary}
        self.assertEqual(2,len(expected))
        self.assertTrue(all(e.metadata['instrument_identity_verified'] for e in primary))
        self.assertEqual(expected,{doc.instrument_id for doc in report.documents})
        documents={doc.document_id:doc for doc in report.documents}
        self.assertEqual(expected,{documents[fact.document_id].instrument_id for fact in report.fundamental_facts})
        self.assertTrue(all(documents[fact.document_id].ticker==fact.ticker for fact in report.fundamental_facts))
