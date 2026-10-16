from copy import deepcopy
from datetime import datetime,timedelta,timezone
import unittest
from market_checker_app.agents import AgentContext,EntityRegistryAgent
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.services.specialist_identity_config import configured_counterparty_identities


class PrimaryCounterpartyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry=DatedCounterpartyIdentityRegistry.load();self.records=self.registry.records()
        self.when=max(clock(r['known_at']) for r in self.records)+timedelta(seconds=1)

    def test_real_primary_registry_is_loaded_and_keeps_683_issuers_and_two_children_separate(self):
        self.assertEqual('LOADED',configured_counterparty_identities({})[1])
        self.assertEqual(683,sum(r['relationship_role']=='DIRECT_ISSUER' for r in self.records))
        self.assertEqual(2,sum(r['relationship_role']=='SUBSIDIARY_OF_ISSUER' for r in self.records))
        report=EntityRegistryAgent().run(AgentContext('test',('DUK','SO'),self.when))
        for entity in report.entities:
            child=entity.metadata['documented_subsidiaries'][0]
            self.assertNotEqual(entity.cik,child['entity_identifier']['value'])
            self.assertFalse(child['financial_attribution_allowed'])
            self.assertEqual('AS_REPORTED_ONLY',child['scope'])

    def test_subsidiary_snapshot_is_not_extended_to_current_ownership_or_parent_financials(self):
        child=next(r for r in self.records if r['relationship_role']=='SUBSIDIARY_OF_ISSUER')
        args=dict(disclosed_name=child['disclosed_name'],as_of=self.when,
            published_at=datetime(2025,12,31,tzinfo=timezone.utc),
            exposure_source_url='https://www.sec.gov/Archives/edgar/data/1/independent.htm')
        hit=self.registry.resolve(**args)
        self.assertEqual('IDENTIFIED',hit['counterparty_identity_status'])
        self.assertFalse(hit['counterparty_financials_attribution_allowed'])
        self.assertEqual('OUTSIDE_EFFECTIVE_PERIOD',self.registry.resolve(**{**args,'published_at':self.when})['counterparty_identity_resolution'])
        before=clock(child['known_at'])-timedelta(microseconds=1)
        self.assertEqual('NOT_YET_KNOWN',self.registry.resolve(**{**args,'as_of':before})['counterparty_identity_resolution'])

    def test_joint_venture_bad_parent_and_missing_legal_fact_do_not_pass(self):
        child=next(r for r in self.records if r['relationship_role']=='SUBSIDIARY_OF_ISSUER')
        for mutation in ['parent','joint','cik']:
            row=deepcopy(child)
            if mutation=='cik':row['evidence'][0]['fact_projection']['cik']='0000000001'
            else:
                proof=next(e for e in row['evidence'] if e['purpose']=='ENTITY_TO_ISSUER')
                if mutation=='parent':proof['exhibit_row']['parent_cik']='0000000001'
                else:proof['quote']+=' and joint ventures'
            with self.assertRaises(ValueError):DatedCounterpartyIdentityRegistry([row])
