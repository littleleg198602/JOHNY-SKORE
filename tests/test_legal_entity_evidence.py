from datetime import timedelta
import unittest
from tests.test_sec13f_identity_bulk import cover
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from market_checker_app.services.legal_entity_evidence_service import parse_legal_entity


class LegalEntityEvidenceTests(unittest.TestCase):
    def source(self,extra):
        snap=cover(extra=extra)
        return snap

    def test_name_cik_and_context_cannot_be_borrowed_from_a_different_registrant(self):
        own='<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Apple Inc.</ix:nonNumeric>'
        self.assertEqual('0000320193',parse_legal_entity(self.source(own))['cik'])
        wrong='<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="Security">0000789019</ix:nonNumeric>'
        with self.assertRaises(ValueError):parse_legal_entity(self.source(own+wrong))
        with self.assertRaises(ValueError):parse_legal_entity(self.source(own+own.replace('Apple Inc.','Different Corp.')))

    def test_joint_registrant_requires_explicit_cik_in_same_name_context(self):
        extra='<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Child Inc.</ix:nonNumeric><ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="Security">0000789019</ix:nonNumeric>'
        snap=self.source(extra);url=snap.url.replace('/320193/','/789019/')
        child=SourceSnapshot(snap.kind,url,snap.observed_at,snap.payload,snap.published_at)
        self.assertEqual(('0000789019','0000320193'),tuple(parse_legal_entity(child)[k] for k in ['cik','reporting_context_cik']))
        snap=self.source(extra.replace('contextRef="Security">0000789019','contextRef="Other">0000789019'))
        with self.assertRaises(ValueError):parse_legal_entity(SourceSnapshot(snap.kind,url,snap.observed_at,snap.payload,snap.published_at))

    def test_nested_equivalent_facts_work_but_nil_or_continued_names_do_not(self):
        fact='<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="Security">Apple Inc.</ix:nonNumeric>'
        nested=fact.replace('Apple Inc.',fact)
        self.assertEqual('Apple Inc.',parse_legal_entity(self.source(nested))['legal_name'])
        for attr in [' xsi:nil="true"',' continuedAt="later"']:
            with self.assertRaises(ValueError):parse_legal_entity(self.source(fact.replace('contextRef="Security"','contextRef="Security"'+attr)))
