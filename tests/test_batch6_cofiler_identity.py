"""Explicit same-context co-filer pairing; no names/dimensions become CIK guesses."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.sec_corporate_action_bridge import issuer_names
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from tests.test_batch5_identity_bridge import named_cover
from tests.test_sec13f_identity_bulk import OBSERVED, PUBLISHED, cover

ROOT_CIK, CHILD_CIK = "0000320193", "0000123456"


def fixture():
    raw = f'''<html xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:issuer="https://issuer.example/2026">
      <xbrli:context id="parent"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{ROOT_CIK}</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="child"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{ROOT_CIK}</xbrli:identifier>
      <xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:SubsidiaryMember</xbrldi:explicitMember></xbrli:segment>
      </xbrli:entity><xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
      <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="parent">Apple Inc</ix:nonNumeric>
      <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="parent">{ROOT_CIK}</ix:nonNumeric>
      <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="child">Subsidiary Corporation</ix:nonNumeric>
      <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="child">{CHILD_CIK}</ix:nonNumeric>
      </html>'''.encode()
    return SourceSnapshot("ticker_filing", cover().url, OBSERVED, raw, PUBLISHED)


def revised(source, before, after):
    return SourceSnapshot(source.kind, source.url, source.observed_at,
                          source.payload.replace(before.encode(), after.encode()), source.published_at)


def child_cik_fact(value=CHILD_CIK):
    return f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="child">{value}</ix:nonNumeric>'


class CoFilerIssuerIdentityTests(unittest.TestCase):
    def test_explicit_child_cik_overrides_parent_context_only_with_legal_entity_scope(self):
        source = fixture(); before = source.payload
        facts = issuer_names(source)
        self.assertEqual({("parent", ROOT_CIK, "Apple Inc"), ("child", CHILD_CIK, "Subsidiary Corporation")},
                         {(fact["context_ref"], fact["issuer_cik"], fact["issuer_name"]) for fact in facts})
        self.assertEqual(before, source.payload)
        self.assertTrue(all(set(fact) == {"issuer_name", "issuer_cik", "anchor_date", "context_ref"} for fact in facts))

    def test_single_registrant_without_explicit_dei_cik_preserves_legacy_contract(self):
        source = named_cover(cover(), "Apple Inc")
        self.assertEqual([{"issuer_name": "Apple Inc", "issuer_cik": ROOT_CIK,
                           "anchor_date": "2026-06-30", "context_ref": "Security"}], issuer_names(source))

    def test_a_dimensional_child_without_explicit_cik_cannot_fall_back_to_parent(self):
        source = revised(fixture(), child_cik_fact(), "")
        self.assertEqual(["parent"], [fact["context_ref"] for fact in issuer_names(source)])

    def test_two_unbound_names_are_not_a_single_registrant_fallback(self):
        source = fixture()
        source = revised(source, child_cik_fact(), "")
        source = revised(source, f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="parent">{ROOT_CIK}</ix:nonNumeric>', "")
        self.assertEqual([], issuer_names(source))

    def test_one_legacy_registrant_in_several_contexts_preserves_each_dated_fact(self):
        source = fixture()
        source = revised(source, child_cik_fact(), "")
        source = revised(source, f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="parent">{ROOT_CIK}</ix:nonNumeric>', "")
        source = revised(source, 'Subsidiary Corporation', 'Apple Inc')
        source = revised(source, '<xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:SubsidiaryMember</xbrldi:explicitMember></xbrli:segment>', "")
        self.assertEqual(['parent','child'],[fact['context_ref'] for fact in issuer_names(source)])
        self.assertTrue(all(fact['issuer_cik']==ROOT_CIK for fact in issuer_names(source)))

    def test_an_explicit_cik_without_a_name_in_another_context_is_not_a_fallback_bridge(self):
        source = fixture()
        source = revised(source, f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="parent">{ROOT_CIK}</ix:nonNumeric>', "")
        source = revised(source, '<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="child">Subsidiary Corporation</ix:nonNumeric>', "")
        self.assertEqual([],issuer_names(source))

    def test_cik_only_document_does_not_supply_an_issuer_name(self):
        source=fixture()
        source=revised(source,'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="parent">Apple Inc</ix:nonNumeric>',"")
        source=revised(source,'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="child">Subsidiary Corporation</ix:nonNumeric>',"")
        self.assertEqual([],issuer_names(source))

    def test_explicit_cik_disagreement_without_a_legal_entity_dimension_is_rejected(self):
        source = revised(fixture(), '<xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:SubsidiaryMember</xbrldi:explicitMember></xbrli:segment>', "")
        self.assertEqual(["parent"], [fact["context_ref"] for fact in issuer_names(source)])

    def test_explicit_same_context_child_identifier_is_supported_without_parent_inference(self):
        source = fixture()
        old = f'<xbrli:context id="child"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{ROOT_CIK}'
        new = old.replace(ROOT_CIK, CHILD_CIK)
        source = revised(source, old, new)
        source = revised(source, '<xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:SubsidiaryMember</xbrldi:explicitMember></xbrli:segment>', "")
        self.assertEqual(CHILD_CIK, next(fact["issuer_cik"] for fact in issuer_names(source) if fact["context_ref"] == "child"))

    def test_malformed_duplicate_and_zero_explicit_ciks_never_trigger_context_fallback(self):
        for replacement in (child_cik_fact("0000000000"), child_cik_fact("123XYZ"), child_cik_fact()+child_cik_fact()):
            with self.subTest(replacement=replacement):
                self.assertEqual(["parent"], [fact["context_ref"] for fact in issuer_names(revised(fixture(), child_cik_fact(), replacement))])

    def test_nilled_continued_transformed_and_excluded_identity_facts_fail_closed(self):
        for attribute in ('xsi:nil="true"', 'continuedAt="other"', 'format="ixt:zerodash"'):
            source = revised(fixture(), 'name="dei:EntityCentralIndexKey" contextRef="child"',
                             'name="dei:EntityCentralIndexKey" contextRef="child" '+attribute)
            with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                issuer_names(source)
        source = revised(fixture(), CHILD_CIK+'</ix:nonNumeric>', CHILD_CIK+'<ix:exclude>ignored</ix:exclude></ix:nonNumeric>')
        with self.assertRaises(ValueError):
            issuer_names(source)

    def test_typed_multiple_foreign_empty_and_undefined_dimensions_do_not_bind_a_child(self):
        original = '<xbrli:segment><xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:SubsidiaryMember</xbrldi:explicitMember></xbrli:segment>'
        replacements = ('<xbrli:segment><xbrldi:typedMember dimension="dei:LegalEntityAxis"><issuer:id>child</issuer:id></xbrldi:typedMember></xbrli:segment>',
                        original.replace('</xbrli:segment>', '<xbrldi:explicitMember dimension="dei:LegalEntityAxis">issuer:OtherMember</xbrldi:explicitMember></xbrli:segment>'),
                        original.replace('dei:LegalEntityAxis', 'issuer:BusinessSegmentAxis'), '<xbrli:segment></xbrli:segment>',
                        original.replace('issuer:SubsidiaryMember', 'undefined:SubsidiaryMember'))
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                self.assertEqual(["parent"], [fact["context_ref"] for fact in issuer_names(revised(fixture(), original, replacement))])

    def test_contradictory_same_cik_date_names_do_not_assign_a_child_to_the_parent(self):
        source = revised(fixture(), child_cik_fact(), child_cik_fact(ROOT_CIK))
        self.assertEqual([], issuer_names(source))

    def test_ambiguous_duplicate_name_context_is_rejected_without_choosing_a_matching_name(self):
        source = revised(fixture(), '</html>', '<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="child">Other Child</ix:nonNumeric></html>')
        self.assertEqual(["parent"], [fact["context_ref"] for fact in issuer_names(source)])

    def test_missing_mixed_reversed_invalid_and_future_dates_are_not_identity_anchors(self):
        replacements = (('', '<xbrli:endDate>2026-06-30</xbrli:endDate>'),
                        ('<xbrli:instant>2026-06-30</xbrli:instant>', '<xbrli:endDate>2026-06-30</xbrli:endDate>'),
                        ('2026-07-01', '2026-01-01'), ('2026-02-30', '2026-06-30'), ('2026-07-21', '2026-06-30'))
        for replacement, before in replacements:
            with self.subTest(before=before,replacement=replacement):
                self.assertEqual([], issuer_names(revised(fixture(), before, replacement)))

    def test_an_undeclared_context_and_legacy_archive_cik_mismatch_cannot_bind_names(self):
        source = named_cover(cover(), "Apple Inc")
        self.assertEqual([], issuer_names(revised(source, 'contextRef="Security"', 'contextRef="missing"')))
        self.assertEqual([], issuer_names(revised(source, ROOT_CIK, CHILD_CIK)))

    def test_identity_namespace_rebinding_and_nested_member_values_fail_closed(self):
        source = revised(fixture(), 'contextRef="child">'+CHILD_CIK, 'contextRef="child" xmlns:dei="urn:foreign">'+CHILD_CIK)
        with self.assertRaises(ValueError):
            issuer_names(source)
        source = revised(fixture(), 'issuer:SubsidiaryMember', '<issuer:part>issuer:SubsidiaryMember</issuer:part>')
        with self.assertRaises(ValueError):
            issuer_names(source)

    def test_original_withheld_metadata_is_rejected_before_any_body_read(self):
        from evidence.batch6_cofiler_20261014.replay_pairs import DOC, original_sources
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/Path(DOC).with_suffix('.metadata.json')).write_text(json.dumps({'status':'WITHHELD_SENSITIVE_BODY','public_raw_available':False}))
            with self.assertRaisesRegex(ValueError,"WITHHELD"):
                original_sources(directory)

    def test_actual_dow_bytes_correct_the_original_join_and_preserve_unknown_publication_timezone(self):
        from evidence.batch6_cofiler_20261014.replay_pairs import HERE, build_report
        report = build_report()
        self.assertEqual(report,json.loads((HERE/'paired_identity_review.json').read_bytes()))
        self.assertEqual(2,len(report['after_fix']['facts']))
        old_child=next(fact for fact in report['before_fix']['facts'] if fact['context_ref']=='c-1')
        new_child=next(fact for fact in report['after_fix']['facts'] if fact['context_ref']=='c-1')
        self.assertEqual('0001751788',old_child['issuer_cik'])
        self.assertEqual('0000029915',new_child['issuer_cik'])
        self.assertEqual('UNKNOWN',report['publisher_clock']['accepted_timezone'])
        self.assertIsNone(report['publisher_clock']['published_at_utc'])
        self.assertFalse(report['knowledge_backdated_to_report_day'])
        self.assertEqual(4_000_000,report['bulk_max_source_bytes_unchanged'])
        self.assertEqual(0,report['new_http_requests'])
        self.assertEqual(0,report['verified_identities_added'])
        self.assertTrue(report['original_bytes_unchanged'])
        claimed=report.pop('review_sha256')
        self.assertEqual(claimed,hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest())

    def test_actual_xom_cofiler_correction_keeps_the_frozen_report_and_all_three_blockers(self):
        from evidence.batch6_cofiler_20261014.replay_pairs import HERE, ROOT, qualify_xom_review
        report=qualify_xom_review()
        self.assertEqual(report,json.loads((HERE/'xom_review_qualification.json').read_bytes()))
        historical=report['historical_report']
        self.assertEqual(historical['sha256'],hashlib.sha256((ROOT/historical['path']).read_bytes()).hexdigest())
        self.assertEqual(2,len(report['historical_from_issuer_names']))
        self.assertEqual([('Exxon Mobil Corporation','0000034088')],
                         [(fact['issuer_name'],fact['issuer_cik']) for fact in report['current_from_issuer_names']])
        self.assertEqual(3,len(report['unchanged_blockers']))
        self.assertEqual(0,report['new_http_requests'])
        self.assertFalse(report['registry_changed'])
        self.assertTrue(report['same_context_date_is_not_corporate_action_effective_date'])


if __name__=='__main__':
    unittest.main()
