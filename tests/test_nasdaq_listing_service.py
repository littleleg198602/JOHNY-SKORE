import base64
from datetime import datetime,timezone,timedelta
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.agents import AgentContext,EntityRegistryAgent
from market_checker_app.nasdaq_listing_capture import capture
from market_checker_app.services.issuer_catalog_service import IssuerCatalog
from market_checker_app.services.primary_listing_service import PrimaryListingRegistry
from market_checker_app.services.nasdaq_listing_service import NasdaqListingRegistry,SOURCE_URL,HEADER


class NasdaqListingTests(unittest.TestCase):
    def setUp(self):
        primary=PrimaryListingRegistry.load()
        from market_checker_app.services.reviewed_identity_service import clock
        self.when=max(clock(r['known_at']) for r in primary.records.values())+timedelta(seconds=1)
        self.catalog=IssuerCatalog.load(as_of=self.when)
        self.line='OZK|Bank OZK - Common Stock|Q|N|N|100|N|N'

    def payload(self,lines=None):
        return ('|'.join(HEADER)+'\n'+(lines or self.line)+'\nFile Creation Time: '+self.when.strftime('%m%d%Y%H:%M')+'|||||||\n').encode()

    def registry(self,lines=None):
        body=self.payload(lines)
        data={'schema_version':1,'source_url':SOURCE_URL,'observed_at':self.when.isoformat(),
            'sha256':hashlib.sha256(body).hexdigest(),'payload_base64':base64.b64encode(body).decode()}
        return NasdaqListingRegistry(data)

    def lookup(self,registry,**changes):
        return registry.lookup('OZK',**{'cik':'0001569650','as_of':self.when,'catalog':self.catalog,**changes})

    def test_exact_common_stock_and_independent_current_sec_issuer_are_required(self):
        proof=self.lookup(self.registry())
        self.assertEqual('AS_REPORTED_EXCHANGE_LISTED_CLASS',proof['scope'])
        self.assertEqual(SOURCE_URL,proof['source_url'])
        self.assertIsNone(self.lookup(self.registry(),cik='0000000001'))
        self.assertIsNone(self.lookup(self.registry(),as_of=self.when-timedelta(seconds=1)))
        self.assertIsNone(self.lookup(self.registry(),as_of=self.when+timedelta(days=7)))

    def test_ambiguous_preferred_test_and_etf_rows_grant_no_common_equity_credit(self):
        for line in [self.line+'\n'+self.line,self.line.replace('Common Stock','Preferred Stock'),
                     self.line.replace('|Q|N|','|Q|Y|'),self.line[:-3]+'Y|N']:
            self.assertIsNone(self.lookup(self.registry(line)))

    def test_checksum_wrong_endpoint_and_missing_footer_are_rejected(self):
        body=self.payload()
        data={'schema_version':1,'source_url':SOURCE_URL,'observed_at':self.when.isoformat(),
            'sha256':'0'*64,'payload_base64':base64.b64encode(body).decode()}
        with self.assertRaises(ValueError):NasdaqListingRegistry(data)
        data['sha256']=hashlib.sha256(body).hexdigest();data['source_url']='https://example.com/list'
        with self.assertRaises(ValueError):NasdaqListingRegistry(data)

    def test_bank_runtime_uses_exchange_class_without_claiming_a_sec_cover(self):
        with patch('market_checker_app.agents.entity_registry_agent.NasdaqListingRegistry.load_optional',return_value=self.registry()):
            result=EntityRegistryAgent().run(AgentContext('test',('OZK',),self.when))
        entity=result.entities[0]
        self.assertTrue(entity.metadata['instrument_identity_verified'])
        self.assertEqual('nasdaq_primary_listing',entity.source)
        self.assertIn('AS_REPORTED_EXCHANGE_LISTED_CLASS',entity.metadata['instrument_identifier_scope'])
        self.assertIsNone(entity.isin)

    def test_capture_failure_does_not_replace_prior_bytes(self):
        now=datetime.now(timezone.utc)
        body=('|'.join(HEADER)+'\n'+self.line+'\nFile Creation Time: '+now.strftime('%m%d%Y%H:%M')+'|||||||\n').encode()
        with TemporaryDirectory() as temp:
            output=Path(temp)/'capture.json'
            capture(output=output,transport=lambda url:body)
            original=output.read_bytes()
            with self.assertRaises(ValueError):capture(output=output,transport=lambda url:b'bad')
            self.assertEqual(original,output.read_bytes())
