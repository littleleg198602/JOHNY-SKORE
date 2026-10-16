"""Mechanism fixtures; generated bodies never count as live source evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from market_checker_app.services import sec_supply_chain_scope_review as service
from evidence.batch7_contracts_20261015 import capture_primary


PHYSICAL = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
CAPTURED = PHYSICAL - timedelta(hours=1)
NAME = "Taiwan Semiconductor Manufacturing Company Limited"
CLAIM = f"We utilize foundries, such as {NAME}, or TSMC, and Samsung Electronics Co., Ltd., or Samsung, to produce our semiconductor wafers."
QUANTITY = "TSMC supplied 70% of our purchases of semiconductor wafers in fiscal 2026."


def _pin(body, start, end):
    return {"byte_start": start, "byte_end": end, "sha256": hashlib.sha256(body[start:end]).hexdigest()}


def _plain(body, text):
    encoded = text.encode();start = body.index(encoded)
    return _pin(body, start, start + len(encoded))


def _capture(body, url):
    return {"requested_url": url, "final_url": url, "started_at": (CAPTURED - timedelta(seconds=1)).isoformat(),
            "observed_at": CAPTURED.isoformat(), "status": "CAPTURED", "origin": "LIVE_PRIMARY_RESEARCH_HTTP",
            "public_raw_available": True, "http_status": 200, "raw_response_bytes": len(body),
            "raw_response_sha256": hashlib.sha256(body).hexdigest()}


def _issuer(claim=CLAIM, quantity=QUANTITY):
    fields = {"EntityRegistrantName": "NVIDIA CORP", "EntityCentralIndexKey": "0001045810",
              "DocumentFiscalYearFocus": "2026", "DocumentPeriodEndDate": "January 25, 2026", "DocumentType": "10-K"}
    facts = ''.join(f'<ix:nonNumeric name="dei:{key}" contextRef="c-1">{value}</ix:nonNumeric>' for key, value in fields.items())
    body = (f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
            'xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:xbrli="http://www.xbrl.org/2003/instance"><body>'
            '<xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier>'
            '</xbrli:entity><xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate>'
            f'</xbrli:period></xbrli:context>{facts}<p>{claim}</p><p>{quantity}</p></body></html>').encode()
    meta = _capture(body, 'https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm')
    selection = {"issuer_ticker": "NVDA", "issuer_context_id": "c-1", "issuer_name": "NVIDIA CORP", "issuer_cik": "0001045810",
                 "fiscal_period_end": "2026-01-25", "claim_role": "FOUNDRY_SUPPLIER", "counterparty_name": NAME,
                 "counterparty_alias": "TSMC", "product_or_input": "semiconductor wafers", "claim_passage": _plain(body, claim)}
    return body, meta, selection


def _legal(*, name=NAME, cik="0001046179", header="Dec. 31, 2025 shares"):
    fields = [("legal_name", "Entity Registrant Name", name), ("cik", "Entity Central Index Key", cik),
              ("period_end", "Document Period End Date", "Dec. 31, 2025"), ("fiscal_year", "Document Fiscal Year Focus", "2025"),
              ("document_type", "Document Type", "20-F")]
    body = ('<html><body><table><tr><th rowspan="2">Cover</th><th>12 Months Ended</th></tr>'
            f'<tr><th>{header}</th></tr>' + ''.join(f'<tr><td>{label}</td><td>{value}</td></tr>' for _, label, value in fields)
            + '</table></body></html>').encode()
    meta = _capture(body, 'https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/R1.htm')
    pins = {}
    for key, label, value in fields:
        text = f'<tr><td>{label}</td><td>{value}</td></tr>';pins[key] = _plain(body, text)
    pins['header_period'] = _plain(body, f'<tr><th>{header}</th></tr>')
    selection = {"mode": "SEC_R1_TABLE", "table_index": 0, "value_column": 1, "legal_name": name, "cik": cik,
                 "report_period_end": "2025-12-31", "field_pins": pins}
    return body, meta, selection


class SupplyScopeTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(service, '_physical_clock', return_value=PHYSICAL);self.clock.start();self.addCleanup(self.clock.stop)

    def _review(self, issuer=None, legal=None, **changes):
        body, meta, selection = issuer or _issuer()
        kwargs = {'issuer_capture': meta, 'selection': selection, **changes}
        if legal:
            kwargs.update(counterparty_body=legal[0], counterparty_capture=legal[1], counterparty_selection=legal[2])
        return service.review_sec_supply_chain_scope(body, **kwargs)

    def test_named_foundry_object_is_separate_from_quantity_and_legal_identity(self):
        result = self._review();self.assertEqual('CANDIDATE_REVIEWED', result['status'])
        claim = result['claim'];self.assertEqual('FOUNDRY_SUPPLIER', claim['claim_role'])
        self.assertEqual('semiconductor wafers', claim['product_or_input'])
        self.assertEqual('NAMED_ONLY', claim['counterparty_legal_identity']['status'])
        self.assertFalse(claim['quantified_exposure']['supplier_specific_quantity_reported'])
        self.assertIsNone(claim['quantified_exposure']['value'])
        self.assertIsNone(claim['relationship_exact_event_day'])
        self.assertFalse(result['sole_source_inferred']);self.assertFalse(result['counterparty_identity_verified'])
        self.assertFalse(result['counterparty_financials_attribution_allowed']);self.assertFalse(result['scoring_applied'])

    def test_exact_independent_legal_name_cik_date_column_is_a_snapshot_only(self):
        result = self._review(legal=_legal());identity = result['claim']['counterparty_legal_identity']
        self.assertTrue(identity['independent_source_snapshot_supported'])
        self.assertEqual('0001046179', identity['cik']);self.assertEqual('2025-12-31', identity['report_period_end'])
        self.assertEqual(NAME, identity['reported_legal_name']);self.assertEqual(1, identity['value_column'])
        self.assertFalse(identity['relationship_date_identity_verified']);self.assertFalse(identity['continuous_legal_validity_verified'])
        self.assertFalse(identity['registry_identity_adopted']);self.assertFalse(identity['financial_attribution_allowed'])
        self.assertFalse(result['counterparty_identity_verified'])

    def test_names_cannot_be_fuzzily_expanded_from_a_profile_abbreviation(self):
        result = self._review(legal=_legal(name='TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD'))
        self.assertFalse(result['claim']['counterparty_legal_identity']['independent_source_snapshot_supported'])
        self.assertEqual('INDEPENDENT_LEGAL_NAME_CIK_OR_PERIOD_MISMATCH', result['claim']['counterparty_legal_identity']['reason'])

    def test_literal_purchase_share_requires_supplier_object_unit_denominator_and_year(self):
        body, meta, selected = _issuer();selected['quantity'] = {'value_literal': '70', 'unit_literal': '%',
            'denominator_literal': 'our purchases of semiconductor wafers', 'passage': _plain(body, QUANTITY)}
        result = self._review((body, meta, selected));quantity = result['claim']['quantified_exposure']
        self.assertTrue(quantity['supplier_specific_quantity_reported']);self.assertEqual('70', quantity['value'])
        self.assertFalse(quantity['revenue_share_reported']);self.assertFalse(result['quantity_or_revenue_share_inferred'])

    def test_two_named_foundries_do_not_manufacture_equal_share_or_revenue_denominator(self):
        body, meta, selected = _issuer()
        for changed in ({'value_literal': '50'}, {'denominator_literal': 'our revenue'}, {'unit_literal': 'USD'}, {'value_literal': '101'}):
            quantity = {'value_literal': '70', 'unit_literal': '%', 'denominator_literal': 'our purchases of semiconductor wafers',
                        'passage': _plain(body, QUANTITY), **changed}
            self.assertEqual('REJECTED', self._review((body, meta, {**selected, 'quantity': quantity}))['status'])

    def test_wrong_fiscal_year_or_other_counterparty_quantity_is_excluded(self):
        for quantity_text in (QUANTITY.replace('2026', '2025'), QUANTITY.replace('TSMC', 'Samsung')):
            body, meta, selected = _issuer(quantity=quantity_text)
            selected['quantity'] = {'value_literal': '70', 'unit_literal': '%', 'denominator_literal': 'our purchases of semiconductor wafers',
                                    'passage': _plain(body, quantity_text)}
            self.assertEqual('SUPPLIER_QUANTITY_NOT_LITERAL_FOR_OBJECT_AND_PERIOD', self._review((body, meta, selected))['reason'])

    def test_wrong_claim_role_product_name_and_invented_alias_fail_closed(self):
        fixture = _issuer()
        for changes in ({'claim_role': 'CUSTOMER'}, {'product_or_input': 'HBM memory'}, {'counterparty_name': 'Other Corporation'},
                        {'counterparty_alias': 'TSM'}, {'issuer_name': 'NVIDIA Corporation'}, {'issuer_cik': '0001046179'}):
            self.assertEqual('REJECTED', self._review((fixture[0], fixture[1], {**fixture[2], **changes}))['status'])

    def test_negated_and_forward_supplier_statement_is_not_current_relationship(self):
        for text in (CLAIM.replace('We utilize', 'We will utilize'), CLAIM.replace('We utilize', 'We no longer utilize')):
            self.assertEqual('NEGATED_OR_FORWARD_RELATIONSHIP', self._review(_issuer(claim=text))['reason'])

    def test_original_passage_hash_offsets_and_hidden_content_are_strict(self):
        body, meta, selected = _issuer()
        selected['claim_passage']['sha256'] = '0' * 64
        self.assertEqual('PASSAGE_HASH_MISMATCH', self._review((body, meta, selected))['reason'])
        body, meta, selected = _issuer();body = body.replace(f'<p>{CLAIM}'.encode(), f'<p hidden="hidden">{CLAIM}'.encode())
        meta = _capture(body, meta['requested_url']);selected['claim_passage'] = _plain(body, CLAIM)
        self.assertEqual('NONVISIBLE_SELECTED_PASSAGE', self._review((body, meta, selected))['reason'])

    def test_renderer_same_column_and_field_pins_reject_mixed_identity(self):
        legal = _legal();selected = deepcopy(legal[2]);selected['value_column'] = 2
        self.assertFalse(self._review(legal=(legal[0], legal[1], selected))['claim']['counterparty_legal_identity']['independent_source_snapshot_supported'])
        selected = deepcopy(legal[2]);selected['field_pins']['cik']['sha256'] = '0' * 64
        self.assertFalse(self._review(legal=(legal[0], legal[1], selected))['claim']['counterparty_legal_identity']['independent_source_snapshot_supported'])
        self.assertEqual('IDENTITY_DATE_COLUMN_HEADER_MISMATCH', self._review(legal=_legal(header='Dec. 31, 2024 shares'))['claim']['counterparty_legal_identity']['reason'])

    def test_wrong_legal_cik_hidden_table_and_same_accession_cannot_verify_supplier(self):
        identity = self._review(legal=_legal(cik='0001045810'))['claim']['counterparty_legal_identity']
        self.assertFalse(identity['independent_source_snapshot_supported'])
        body, meta, selected = _legal();body = body.replace(b'<table>', b'<table hidden="hidden">')
        meta = _capture(body, meta['requested_url'])
        selected['field_pins'] = _legal()[2]['field_pins']
        identity = self._review(legal=(body, meta, selected))['claim']['counterparty_legal_identity']
        self.assertEqual('NONVISIBLE_IDENTITY_TABLE_CONTENT', identity['reason'])
        body, meta, selected = _legal();meta['requested_url'] = meta['requested_url'].replace('000162828026025362', '000104581026000021')
        meta['final_url'] = meta['requested_url']
        identity = self._review(legal=(body, meta, selected))['claim']['counterparty_legal_identity']
        self.assertEqual('SAME_DISCLOSURE_NOT_INDEPENDENT', identity['reason'])

    def test_hidden_nested_taxonomy_tables_do_not_contribute_identity_facts(self):
        body, meta, selected = _legal()
        popup = '<table style="display:none"><tr><td>definition</td><td><table><tr><td>Other Company</td></tr></table></td></tr></table>'
        body = body.replace(b'</body>', popup.encode() + b'</body>')
        meta = _capture(body, meta['requested_url'])
        identity = self._review(legal=(body, meta, selected))['claim']['counterparty_legal_identity']
        self.assertTrue(identity['independent_source_snapshot_supported'])
        selected = {**selected, 'table_index': 1}
        identity = self._review(legal=(body, meta, selected))['claim']['counterparty_legal_identity']
        self.assertEqual('NONVISIBLE_IDENTITY_TABLE_CONTENT', identity['reason'])

    def test_withheld_old_capture_metadata_is_not_promoted_by_format_adapter(self):
        body, meta, selected = _issuer()
        legacy = {'capture_kind': 'RAW_HTTP_RESPONSE_BODY', 'url': meta['requested_url'], 'resolved_url': meta['requested_url'],
                  'retrieval_started_at': meta['started_at'], 'observed_at': meta['observed_at'], 'sha256': meta['raw_response_sha256'],
                  'size_bytes': len(body), 'http_status': 200, 'public_raw_available': False}
        self.assertEqual('WITHHELD_OR_UNAVAILABLE_WHOLE_BODY', self._review((body, legacy, selected))['reason'])

    def test_exact_original_legacy_capture_preserves_capture_clock_without_new_collection(self):
        body, meta, selected = _issuer()
        legacy = {'capture_kind': 'RAW_HTTP_RESPONSE_BODY', 'url': meta['requested_url'], 'resolved_url': meta['requested_url'],
                  'retrieval_started_at': meta['started_at'], 'observed_at': meta['observed_at'], 'sha256': meta['raw_response_sha256'],
                  'size_bytes': len(body), 'http_status': 200}
        result = self._review((body, legacy, selected));self.assertEqual('CANDIDATE_REVIEWED', result['status'])
        self.assertEqual(CAPTURED.isoformat(), result['claim']['source']['source_capture_completed_at'])
        self.assertEqual(PHYSICAL.isoformat(), result['ingested_at'])
        self.assertEqual(0, result['http_attempts']);self.assertEqual(0, result['usaspending_requests'])

    def test_cutoff_and_capture_clocks_cannot_backdate_physical_knowledge(self):
        self.assertEqual('AFTER_PHYSICAL_KNOWLEDGE_CUTOFF', self._review(as_of=CAPTURED)['reason'])
        body, meta, selected = _issuer();meta['observed_at'] = (PHYSICAL + timedelta(days=1)).isoformat()
        self.assertEqual('REJECTED', self._review((body, meta, selected))['status'])
        self.assertEqual('REJECTED', self._review(as_of=PHYSICAL.replace(tzinfo=None))['status'])

    def test_whole_unsafe_source_is_not_exported_or_replaced(self):
        body, meta, selected = _issuer();body = body.replace(b'</body>', b'<script>access_token="short";</script></body>')
        meta = _capture(body, meta['requested_url']);selected['claim_passage'] = _plain(body, CLAIM)
        result = self._review((body, meta, selected));self.assertEqual('UNSAFE_WHOLE_SOURCE_OR_METADATA', result['reason'])
        self.assertNotIn('short', str(result));self.assertIsNone(result['claim'])

    def test_body_bounds_hashes_bad_xml_and_noncanonical_hint_fail_closed(self):
        body, meta, selected = _issuer()
        self.assertEqual('REJECTED', self._review((body + b'x', meta, selected))['status'])
        self.assertEqual('REJECTED', self._review((body, meta, {**selected, 'issuer_ticker': 'NOTREALX'}))['status'])
        with patch.object(service, 'MAX_BODY_BYTES', len(body) - 1):self.assertEqual('REJECTED', self._review()['status'])
        broken = body[:-10];self.assertEqual('REJECTED', self._review((broken, _capture(broken, meta['requested_url']), selected))['status'])

    def test_replay_does_not_mutate_inputs_write_stores_collect_or_approve_identity(self):
        issuer, legal = _issuer(), _legal();before = deepcopy((issuer, legal))
        with patch('urllib.request.urlopen', side_effect=AssertionError('HTTP')), patch('sqlite3.connect', side_effect=AssertionError('DB')):
            first = self._review(issuer, legal);second = self._review(issuer, legal)
        self.assertEqual(first, second);self.assertEqual(before, (issuer, legal))
        self.assertFalse(first['counterparty_identity_verified']);self.assertEqual(0, first['registry_writes'])

    def test_offline_redirect_attempt_is_reserved_and_never_followed(self):
        with TemporaryDirectory() as directory, patch.object(capture_primary, 'DIRECTORY', Path(directory)), patch('builtins.print'):
            opener = MagicMock();opener.open.side_effect = HTTPError('https://www.sec.gov/Archives/edgar/data/1/000000000126000001/a.htm',302,'test',{},None)
            with patch.object(capture_primary, 'build_opener', return_value=opener):
                result = capture_primary.capture('https://www.sec.gov/Archives/edgar/data/1/000000000126000001/a.htm')
            self.assertEqual('REDIRECT_BLOCKED', result['status']);self.assertEqual(1, opener.open.call_count)
            self.assertEqual(1, len(list((Path(directory)/'requests').glob('*.reserved.json'))))
            self.assertEqual([], list((Path(directory)/'raw').glob('*')))


if __name__ == '__main__':unittest.main()
