import base64
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.agents import AgentContext, EntityRegistryAgent
from market_checker_app.services.issuer_catalog_service import IssuerCatalog
from market_checker_app.services.reviewed_identity_service import ReviewedIdentityRegistry
from market_checker_app.utils.ticker_universe import load_canonical_tickers


class IssuerCatalogTests(unittest.TestCase):
    def test_current_binding_has_knowledge_and_staleness_bounds(self):
        catalog=IssuerCatalog.load()
        self.assertIsNone(catalog.lookup('AAPL',as_of=catalog.known_at-timedelta(microseconds=1)))
        binding=catalog.lookup('AAPL',as_of=catalog.known_at)
        self.assertEqual('0000320193',binding['cik'])
        self.assertEqual('CURRENT_ISSUER_ONLY_NOT_SHARE_CLASS',binding['scope'])
        self.assertIsNone(catalog.lookup('AAPL',as_of=catalog.known_at+timedelta(days=7)))

    def test_reused_symbol_is_not_resolved_and_checksum_changes_are_rejected(self):
        current=IssuerCatalog.load()
        raw=json.dumps({'0':{'ticker':'AAPL','cik_str':320193,'title':'First'},
                        '1':{'ticker':'AAPL','cik_str':1,'title':'Second'}}).encode()
        data=dict(schema_version=1,source_url='https://www.sec.gov/files/company_tickers.json',
            observed_at=current.known_at.isoformat(),sha256=hashlib.sha256(raw).hexdigest(),
            payload_base64=base64.b64encode(raw).decode())
        self.assertIsNone(IssuerCatalog(data).lookup('AAPL',as_of=current.known_at))
        data['payload_base64']=base64.b64encode(raw+b' ').decode()
        with self.assertRaises(ValueError):IssuerCatalog(data)

    def test_canonical_687_are_preserved_and_all_current_issuers_have_explicit_scope(self):
        catalog=IssuerCatalog.load();registry=ReviewedIdentityRegistry.load()
        when=max(catalog.known_at,max(__import__('datetime').datetime.fromisoformat(x['known_at']) for x in registry.records))+timedelta(seconds=1)
        tickers=load_canonical_tickers()
        result=EntityRegistryAgent(reviewed_registry=registry).run(AgentContext('test',tuple(tickers),when))
        self.assertEqual(tickers,[e.ticker for e in result.entities])
        self.assertEqual(687,sum(e.cik is not None for e in result.entities))
        self.assertEqual(684,sum(e.metadata['identity_resolution']=='ISSUER_ONLY' for e in result.entities))
        self.assertTrue(all(not e.metadata['instrument_identity_verified'] for e in result.entities if e.metadata['identity_resolution']=='ISSUER_ONLY'))
        self.assertEqual('INACTIVE',result.state_updates['entities_by_ticker']['LEG'].metadata['instrument_status'])

    def test_user_registry_is_not_silently_replaced_by_catalog(self):
        catalog=IssuerCatalog.load()
        result=EntityRegistryAgent({'AAPL':{'cik':'0000000001'}}).run(AgentContext('test',('AAPL',),catalog.known_at+timedelta(seconds=1)))
        self.assertEqual('0000000001',result.entities[0].cik)

    def test_refresh_keeps_immutable_copies_and_does_not_store_contact_headers(self):
        from market_checker_app.issuer_catalog_capture import capture
        raw=json.dumps({'0':{'ticker':'AAPL','cik_str':320193,'title':'Apple'}}).encode()
        class Client:
            def _request_bytes(self,url):
                self.url=url
                return raw
        with TemporaryDirectory() as temp:
            out=Path(temp)/'current.json';client=Client()
            capture(output=out,client=client)
            first=out.read_bytes()
            capture(output=out,client=client)
            archives=list((out.parent/'issuer_catalog_archive').glob('*.json'))
            self.assertTrue(any(p.read_bytes()==first for p in archives))
            self.assertGreaterEqual(len(archives),3)
            self.assertNotIn('user_agent',json.loads(out.read_text()))
            self.assertEqual('https://www.sec.gov/files/company_tickers.json',client.url)
