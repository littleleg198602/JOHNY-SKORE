"""Offline mechanisms; generated inputs are fixtures, never live acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import sec_purchase_obligation_scope_review as service


PHYSICAL = datetime(2026, 10, 15, 19, tzinfo=timezone.utc)
CAPTURED = PHYSICAL - timedelta(hours=1)
URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"
INDEX = URL.rsplit("/", 1)[0] + "/0001045810-26-000021-index.htm"
GROUP = "NVIDIA Corporation and Subsidiaries"
CATEGORY = service._CATEGORY
MEMBER = service._MEMBER


def _pin(body, literal):
    start = body.index(literal.encode())
    return {"byte_start": start, "byte_end": start + len(literal.encode()),
            "sha256": hashlib.sha256(literal.encode()).hexdigest()}


def _capture(body, url):
    return {"requested_url": url, "final_url": url, "started_at": (CAPTURED - timedelta(seconds=1)).isoformat(),
            "observed_at": CAPTURED.isoformat(), "status": "CAPTURED", "origin": "LIVE_PRIMARY_RESEARCH_HTTP",
            "public_raw_available": True, "http_status": 200, "raw_response_bytes": len(body),
            "raw_response_sha256": hashlib.sha256(body).hexdigest()}


def _fixture(*, scale="9", unit="USD", amount="95.2", context_cik="0001045810", instant="2026-01-25",
             member=MEMBER, axis="us-gaap:OtherCommitmentsAxis", fact_extra="", paragraph_extra="",
             paragraph_hidden=False, second_fact=False, literal_date="January 25, 2026"):
    fields = {"EntityRegistrantName": "NVIDIA CORP", "EntityCentralIndexKey": "0001045810",
              "DocumentFiscalYearFocus": "2026", "DocumentPeriodEndDate": "January 25, 2026", "DocumentType": "10-K"}
    facts = ''.join(f'<ix:nonNumeric name="dei:{key}" contextRef="c-1">{value}</ix:nonNumeric>' for key, value in fields.items())
    group = f'<div>{GROUP}</div>'
    notes = '<div>Notes to the Consolidated Financial Statements (Continued)</div>'
    section = '<div>Note 12 - Commitments and Contingencies</div>'
    number = (f'<ix:nonFraction unitRef="usd" contextRef="c-177" decimals="-8" '
              f'name="us-gaap:OtherCommitment" scale="{scale}" id="f-894"{fact_extra}>{amount}</ix:nonFraction>')
    paragraph = (f'<div{chr(32) + "hidden=\"hidden\"" if paragraph_hidden else ""}>'
        f'{CATEGORY} reflect datacenter-scale production and longer future ordering horizons across current and future product architectures. '
        'We enter into agreements with our supply vendors that allow them to procure inventory based upon our defined criteria, '
        'and in certain instances, these agreements are cancellable, able to be rescheduled, or adjustable for our business needs prior to placing firm orders. '
        'Changes to these agreements may result in additional costs. '
        f'As of {literal_date}, these commitments were ${number} billion, of which substantially all will be paid through fiscal year 2027.'
        f'{paragraph_extra}' + (number.replace('f-894', 'f-895') if second_fact else '') + '</div>')
    body = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
        'xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:us-gaap="http://fasb.org/us-gaap/2025" '
        'xmlns:nvda="http://www.nvidia.com/20260125" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"><body>'
        '<xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001045810</xbrli:identifier>'
        '</xbrli:entity><xbrli:period><xbrli:startDate>2025-01-27</xbrli:startDate><xbrli:endDate>2026-01-25</xbrli:endDate>'
        '</xbrli:period></xbrli:context>'
        f'<xbrli:context id="c-177"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{context_cik}</xbrli:identifier>'
        f'<xbrli:segment><xbrldi:explicitMember dimension="{axis}">nvda:{member}</xbrldi:explicitMember></xbrli:segment>'
        f'</xbrli:entity><xbrli:period><xbrli:instant>{instant}</xbrli:instant></xbrli:period></xbrli:context>'
        f'<xbrli:unit id="usd"><xbrli:measure>iso4217:{unit}</xbrli:measure></xbrli:unit>{facts}'
        '<p>We utilize foundries, such as TSMC and Samsung, to produce semiconductor wafers.</p>'
        f'{group}{notes}{section}{paragraph}</body></html>').encode()
    index = ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">'
        '<html><body><span class="companyName">NVIDIA CORP <a href="/cgi-bin/browse-edgar?CIK=0001045810">CIK</a></span>'
        '<div class="infoHead">Filing Date</div><div class="info">2026-02-25</div>'
        '<div class="infoHead">Period of Report</div><div class="info">2026-01-25</div>'
        '<div class="infoHead">Accepted</div><div class="info">2026-02-25 16:42:19</div>'
        f'<table><tr><td>1</td><td>10-K</td><td><a href="/ix?doc=/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm">nvda-20260125.htm</a></td>'
        f'<td>10-K</td><td>{len(body)}</td></tr></table></body></html>').encode()
    selected = {"issuer_ticker": "NVDA", "issuer_context_id": "c-1", "issuer_name": "NVIDIA CORP",
        "issuer_cik": "0001045810", "fiscal_period_end": "2026-01-25",
        "claim_role": "AGGREGATE_PURCHASE_COMMITMENT", "statement_scope": "CONSOLIDATED_GROUP",
        "group_name_literal": GROUP, "group_heading": _pin(body, group), "notes_heading": _pin(body, notes),
        "section_heading": _pin(body, section), "paragraph": _pin(body, paragraph),
        "quantity": {"fact_id": "f-894", "context_id": "c-177", "unit_id": "usd", "value_literal": amount,
            "unit_literal": "billion", "currency": "USD", "as_of_date": "2026-01-25",
            "payment_horizon_fiscal_year": 2027, "payment_qualifier_literal": "substantially all"}}
    return body, _capture(body, URL), index, _capture(index, INDEX), selected


def _changed_body(fixture, before, after):
    body, capture, index, index_capture, selection = fixture
    changed = body.replace(before, after)
    selection = deepcopy(selection)
    for key in ('group_heading', 'notes_heading', 'section_heading', 'paragraph'):
        pin = selection[key]
        original = body[pin['byte_start']:pin['byte_end']]
        selection[key] = _pin(changed, original.replace(before, after).decode())
    return changed, _capture(changed, capture['requested_url']), index, index_capture, selection


class PurchaseObligationScopeTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(service, '_physical_clock', return_value=PHYSICAL)
        clock.start(); self.addCleanup(clock.stop)

    def review(self, fixture=None, **kwargs):
        body, capture, index, index_capture, selection = fixture or _fixture()
        return service.review_sec_purchase_obligation_scope(body, issuer_capture=capture,
            index_body=index, index_capture=index_capture, selection=selection, **kwargs)

    def test_aggregate_numeric_fact_currency_category_and_group_are_exact(self):
        result = self.review(); self.assertEqual('CANDIDATE_REVIEWED', result['status'], result)
        claim = result['claim']; q = claim['quantity']
        self.assertEqual(('95.2', 'billion', 'USD', '2026-01-25'),
            (q['value_literal'], q['unit_literal'], q['currency'], q['context_instant']))
        self.assertEqual(Decimal('95200000000'), Decimal(q['scaled_currency_amount']))
        self.assertEqual('COMMITMENT_CATEGORY_NOT_COUNTERPARTY', q['category_dimension']['scope_kind'])
        self.assertEqual(GROUP, claim['group_name_literal']); self.assertEqual(CATEGORY, claim['group_denominator']['category_literal'])
        self.assertIsNone(claim['group_denominator']['supplier_allocation_denominator'])
        self.assertIsNone(claim['supplier_name']); self.assertIsNone(claim['supplier_share'])
        self.assertFalse(claim['individual_issuer_amount_attribution_allowed'])
        self.assertFalse(claim['group_legal_entity_membership_verified'])

    def test_agreement_and_payment_qualifiers_are_preserved_not_an_exact_schedule(self):
        q = self.review()['claim']['quantity']
        self.assertEqual(2027, q['payment_horizon_fiscal_year'])
        self.assertEqual('substantially all', q['payment_horizon_qualifier_literal'])
        self.assertEqual(2, len(q['agreement_qualifiers_literal']))
        self.assertIn('cancellable', q['agreement_qualifiers_literal'][0])
        self.assertFalse(q['fully_non_cancelable_inferred']); self.assertFalse(q['table_row_column_applicable'])

    def test_other_paragraph_supplier_names_never_join_an_anonymous_total(self):
        f = _fixture()
        for changes in ({'requested_counterparty': 'TSMC'}, {'counterparty_name': 'Samsung'},
                        {'supplier_share': '50'}, {'statement_scope': 'SINGLE_ISSUER'}, {'claim_role': 'ACTUAL_SPEND'}):
            with self.subTest(changes=changes):
                updated = (*f[:4], {**f[4], **changes})
                self.assertEqual('UNSUPPORTED_OR_CROSS_SCOPE_SELECTION', self.review(updated)['reason'])
        result = self.review(f)
        self.assertFalse(result['claim']['supplier_names_from_other_passages_joined'])
        for key in ('supplier_attribution_allowed', 'supplier_specific_quantity_verified', 'supplier_legal_identity_verified',
                    'actual_spend_verified', 'purchase_volume_verified', 'revenue_share_verified', 'scoring_applied',
                    'model_modified', 'trading_allowed', 'out_of_sample_evaluation_verified'):
            self.assertIs(result[key], False)

    def test_changed_value_unit_currency_horizon_and_qualifier_are_rejected(self):
        for key, value in (('value_literal', '95.3'), ('currency', 'EUR'), ('unit_literal', 'million'),
                           ('as_of_date', '2025-01-26'), ('payment_horizon_fiscal_year', 2028),
                           ('payment_qualifier_literal', 'all')):
            f = _fixture(); f[4]['quantity'][key] = value
            with self.subTest(key=key): self.assertEqual('REJECTED', self.review(f)['status'])

    def test_fact_scale_currency_and_sign_are_not_reinterpreted(self):
        for kwargs in ({'scale': '6'}, {'unit': 'EUR'}, {'fact_extra': ' sign="-"'}, {'fact_extra': ' format="ixt:num-dot-decimal"'}):
            with self.subTest(kwargs=kwargs): self.assertEqual('REJECTED', self.review(_fixture(**kwargs))['status'])

    def test_fact_context_is_same_issuer_and_instant_with_category_not_supplier_dimension(self):
        for kwargs in ({'context_cik': '0001046179'}, {'instant': '2025-01-26'}, {'member': 'TSMCMember'},
                       {'axis': 'us-gaap:SupplierNameAxis'}):
            with self.subTest(kwargs=kwargs): self.assertEqual('REJECTED', self.review(_fixture(**kwargs))['status'])

    def test_nested_namespace_overrides_cannot_relabel_currency_or_category(self):
        f = _changed_body(_fixture(), b'<xbrli:measure>', b'<xbrli:measure xmlns:iso4217="http://example.com/2025">')
        self.assertEqual('CURRENCY_UNIT_IS_NOT_SIMPLE_USD', self.review(f)['reason'])
        f = _changed_body(_fixture(), b'<xbrldi:explicitMember ', b'<xbrldi:explicitMember xmlns:us-gaap="http://example.com/2025" ')
        self.assertEqual('SUPPLIER_OR_OTHER_CATEGORY_DIMENSION_REFUSED', self.review(f)['reason'])

    def test_literal_amount_asof_must_match_context_date(self):
        result = self.review(_fixture(literal_date='January 26, 2026'))
        self.assertEqual('COMMITMENT_VALUE_ASOF_OR_PAYMENT_SCOPE_NOT_LITERAL', result['reason'])

    def test_full_original_qualifiers_cannot_be_omitted_by_cutting_the_span(self):
        f = _fixture(); pin = f[4]['paragraph']; pin['byte_end'] -= len('</div>')
        pin['sha256'] = hashlib.sha256(f[0][pin['byte_start']:pin['byte_end']]).hexdigest()
        self.assertEqual('SELECTION_NOT_COMPLETE_ORIGINAL_BLOCK', self.review(f)['reason'])

    def test_cancellation_is_conditional_and_cannot_be_relabelled_universal(self):
        f = _changed_body(_fixture(), b'in certain instances, these agreements', b'these agreements')
        self.assertEqual('COMMITMENT_QUALIFIERS_NOT_PRESERVED', self.review(f)['reason'])

    def test_hidden_ancestor_or_hidden_numeric_body_is_rejected(self):
        self.assertEqual('NONVISIBLE_OR_NONLEAF_SELECTED_BLOCK', self.review(_fixture(paragraph_hidden=True))['reason'])
        self.assertEqual('NONVISIBLE_OR_NONLEAF_SELECTED_BLOCK', self.review(_fixture(fact_extra=' hidden="hidden"'))['reason'])
        self.assertEqual('NONVISIBLE_OR_NONLEAF_SELECTED_BLOCK', self.review(_fixture(fact_extra=' style="display: \nnone"'))['reason'])

    def test_a_table_cell_paragraph_cannot_discard_its_column_scope(self):
        f = _changed_body(_fixture(), f'<div>{GROUP}</div>'.encode(), f'<table><tr><td><div>{GROUP}</div></td></tr></table>'.encode())
        f[4]['group_heading'] = _pin(f[0], f'<div>{GROUP}</div>')
        self.assertEqual('UNSUPPORTED_TABLE_COLUMN_SCOPE', self.review(f)['reason'])

    def test_external_footnote_or_more_than_one_numeric_fact_is_not_silently_dropped(self):
        self.assertEqual('UNRESOLVED_EXTERNAL_FOOTNOTE_OR_REFERENCE', self.review(_fixture(paragraph_extra='<sup>(1)</sup>'))['reason'])
        self.assertEqual('QUANTITY_FACT_CONTEXT_UNIT_OR_PARAGRAPH_AMBIGUOUS', self.review(_fixture(second_fact=True))['reason'])

    def test_wrong_pin_hash_and_partial_plaintext_selection_fail_closed(self):
        f = _fixture(); f[4]['paragraph']['sha256'] = '0' * 64
        self.assertEqual('WHOLE_BLOCK_HASH_OFFSET_OR_BOUND_MISMATCH', self.review(f)['reason'])
        f = _fixture(); f[4]['paragraph'] = _pin(f[0], 'Changes to these agreements may result in additional costs.')
        self.assertEqual('MISSING_OR_INCOMPLETE_ORIGINAL_BLOCK', self.review(f)['reason'])

    def test_exact_dei_pair_and_consolidated_scope_cannot_be_changed(self):
        for change in ({'issuer_name': 'NVIDIA Corporation'}, {'issuer_cik': '0001046179'}, {'issuer_context_id': 'c-177'},
                       {'fiscal_period_end': '2025-01-26'}, {'group_name_literal': 'TSMC and Subsidiaries'}):
            f = _fixture(); f[4].update(change)
            with self.subTest(change=change): self.assertEqual('REJECTED', self.review(f)['status'])

    def test_a_cofiler_cannot_be_silently_combined_into_the_single_issuer_anchor(self):
        f = _changed_body(_fixture(), b'</body>', b'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="other">0001046179</ix:nonNumeric></body>')
        self.assertEqual('COFILER_SCOPE_REQUIRES_SEPARATE_REVIEW', self.review(f)['reason'])

    def test_duplicate_numeric_id_outside_the_visible_paragraph_is_ambiguous(self):
        extra = b'<div hidden="hidden"><ix:nonFraction name="us-gaap:OtherCommitment" contextRef="c-177" unitRef="usd" scale="9" decimals="-8" id="f-894">95.2</ix:nonFraction></div>'
        f = _changed_body(_fixture(), b'</body>', extra + b'</body>')
        self.assertEqual('QUANTITY_FACT_CONTEXT_UNIT_OR_PARAGRAPH_AMBIGUOUS', self.review(f)['reason'])

    def test_index_primary_accession_form_filer_and_report_day_are_bound(self):
        for before, after in ((b'CIK=0001045810', b'CIK=0001046179'), (b'<td>10-K</td>', b'<td>20-F</td>'),
                              (b'2026-02-25', b'2027-02-25'), (b'2026-01-25', b'2025-01-26')):
            f = list(_fixture()); f[2] = f[2].replace(before, after); f[3] = _capture(f[2], INDEX)
            with self.subTest(after=after): self.assertEqual('REJECTED', self.review(f)['status'])
        f = list(_fixture()); f[3] = _capture(f[2], INDEX.replace('000104581026000021', '000104581026000022'))
        self.assertEqual('INDEX_PRIMARY_SCOPE_ACCESSION_OR_DATE_CONFLICT', self.review(f)['reason'])

    def test_hidden_index_primary_row_or_filer_does_not_support_a_visible_proof(self):
        for before, after in ((b'<tr>', b'<tr hidden="hidden">'),
                              (b'<span class="companyName">', b'<span class="companyName" style="display:none">')):
            f = list(_fixture()); f[2] = f[2].replace(before, after); f[3] = _capture(f[2], INDEX)
            with self.subTest(after=after): self.assertEqual('INDEX_PROOF_USES_NONVISIBLE_CONTENT', self.review(f)['reason'])

    def test_index_declared_served_size_conflict_is_retained_without_fixing_bytes(self):
        f = list(_fixture()); raw = f[0]
        f[2] = f[2].replace(f'<td>{len(raw)}</td>'.encode(), f'<td>{len(raw)-111}</td>'.encode())
        f[3] = _capture(f[2], INDEX)
        result = self.review(f)
        self.assertEqual('CANDIDATE_REVIEWED', result['status'])
        self.assertTrue(result['claim']['filing']['publisher_size_conflict'])
        self.assertFalse(result['claim']['filing']['exact_historical_as_filed_version_verified'])
        self.assertEqual(raw, f[0]); self.assertIn('INDEX_SIZE_DIFFERS_FROM_RETAINED_SERVED_ORIGINAL', result['claim']['missing_facts'])

    def test_source_clocks_remain_old_and_new_knowledge_is_actual_ingestion(self):
        result = self.review()
        self.assertEqual(PHYSICAL.isoformat(), result['knowledge_at']); self.assertEqual(PHYSICAL.isoformat(), result['ingested_at'])
        self.assertEqual(CAPTURED.isoformat(), result['claim']['primary_source']['capture_completed_utc'])
        self.assertEqual(CAPTURED.isoformat(), result['claim']['source_inputs_known_at'])
        self.assertIsNone(result['claim']['primary_source']['publisher_publication_at'])
        self.assertFalse(result['claim']['primary_source']['publisher_publication_clock_verified'])
        self.assertFalse(result['independent_sec_api_acceptance_clock_crosscheck_performed'])
        self.assertFalse(result['source_clock_conflict_resolved'])
        self.assertFalse(result['claim']['filing']['acceptance_timezone_inferred'])
        self.assertEqual('AFTER_PHYSICAL_KNOWLEDGE_CUTOFF', self.review(as_of=PHYSICAL-timedelta(microseconds=1))['reason'])

    def test_source_capture_and_known_clocks_cannot_be_reversed_or_future(self):
        for target, changes in ((1, {'observed_at': (PHYSICAL+timedelta(seconds=1)).isoformat()}),
                                (3, {'known_at': (PHYSICAL+timedelta(seconds=1)).isoformat()}),
                                (1, {'known_at': (CAPTURED-timedelta(seconds=1)).isoformat()})):
            f = _fixture(); f[target].update(changes)
            with self.subTest(changes=changes): self.assertEqual('REJECTED', self.review(f)['status'])

    def test_physical_clock_rollback_during_review_is_rejected(self):
        with patch.object(service, '_physical_clock', side_effect=[PHYSICAL, PHYSICAL-timedelta(microseconds=1)]):
            self.assertEqual('PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS', self.review()['reason'])

    def test_withheld_and_access_bearing_whole_bodies_are_never_replaced(self):
        f = _fixture(); f[1]['body_withheld'] = True
        self.assertEqual('WITHHELD_OR_UNAVAILABLE_WHOLE_BODY', self.review(f)['reason'])
        f = list(_fixture()); f[0] = f[0].replace(b'</body>', b'<p>api_key=synthetic-only</p></body>'); f[1] = _capture(f[0], URL)
        result = self.review(f); self.assertEqual('UNSAFE_WHOLE_SOURCE_OR_METADATA', result['reason'])
        self.assertIsNone(result['claim']); self.assertNotIn('synthetic-only', json.dumps(result))

    def test_original_hash_bytes_bounds_redirect_and_dtd_checks(self):
        f = _fixture(); f[1]['raw_response_sha256'] = '0' * 64
        self.assertEqual('REJECTED', self.review(f)['status'])
        f = _fixture(); f[3]['final_url'] = URL
        self.assertEqual('NOT_COMPLETE_ORIGINAL_PUBLIC_SEC_INDEX', self.review(f)['reason'])
        f = list(_fixture()); f[0] = b'x' * (service.MAX_BODY_BYTES + 1); f[1] = _capture(f[0], URL)
        self.assertEqual('CAPTURE_BODY_OR_METADATA_BOUND_MISMATCH', self.review(f)['reason'])
        f = list(_fixture()); f[2] = b'<!DOCTYPE html [<!ENTITY bad "fixture">]>' + f[2]; f[3] = _capture(f[2], INDEX)
        self.assertEqual('UNSUPPORTED_INDEX_ENTITY_OR_INTERNAL_DTD', self.review(f)['reason'])

    def test_replay_is_pure_and_has_no_http_database_or_output_writes(self):
        with patch('socket.socket.connect', side_effect=AssertionError('Unexpected network')), \
             patch('sqlite3.connect', side_effect=AssertionError('Unexpected database')), \
             patch.object(Path, 'write_bytes', side_effect=AssertionError('Unexpected output write')):
            first = self.review(); second = self.review()
        self.assertEqual(first, second); self.assertEqual('CANDIDATE_REVIEWED', first['status'])
        self.assertEqual(0, first['source_http_attempts']); self.assertEqual(0, first['usaspending_requests'])

    def test_actual_retained_nvda_original_replays_exact_fact_without_supplier_join(self):
        root = Path(__file__).resolve().parents[1]
        packet = json.loads((root / 'evidence/batch8_contracts_20261015/selection.json').read_text())
        def body(key):
            spec = packet[key]; raw = (root / spec['repository_relative_path']).read_bytes()
            self.assertEqual(spec['sha256'], hashlib.sha256(raw).hexdigest()); return raw
        def metadata(key):
            record = json.loads(body(key))
            for part in packet[key].get('json_pointer', '').split('/')[1:]:
                record = record[int(part)] if isinstance(record, list) else record[part]
            return record
        result = service.review_sec_purchase_obligation_scope(body('issuer_body'), issuer_capture=metadata('issuer_capture'),
            index_body=body('index_body'), index_capture=metadata('index_capture'), selection=packet['selection'])
        self.assertEqual('CANDIDATE_REVIEWED', result['status'], result)
        self.assertEqual('f-894', result['claim']['quantity']['fact_id'])
        self.assertEqual('95.2', result['claim']['quantity']['value_literal'])
        self.assertTrue(result['claim']['filing']['publisher_size_conflict'])
        self.assertEqual('0001045810-26-000021', result['claim']['primary_source']['accession_number'])
        self.assertIsNone(result['claim']['supplier_name']); self.assertFalse(result['supplier_specific_quantity_verified'])


if __name__ == '__main__':
    unittest.main()
