"""New denominator mechanisms and actual original replay; all transport offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.collectors import sec_supplier_revenue_denominator_review as service
from market_checker_app.services import sec_supplier_concentration_review as concentration
from tests.test_batch9_supplier_concentration import capture, fixture as concentration_fixture, pin


NOW = datetime(2026,10,19,12,tzinfo=timezone.utc)
POLICY = ('Revenue Recognition. We recognize revenue in accordance with the Financial Accounting Standards Board’s '
          'Accounting Standards Codification (“ASC”) 606 Revenue from Contracts with Customers net of estimated reserves. '
          'Our revenue reserves require estimates for returns and price protection.')
PERIODS = [(2025,'2024-09-28','2025-10-03','4,086.9','den-25','c-1',1),
           (2024,'2023-09-30','2024-09-27','4,178.0','den-24','c-7',2),
           (2023,'2022-10-01','2023-09-29','4,772.4','den-23','c-8',3)]


def fixture():
    base,captures = concentration_fixture()
    body = captures['issuer'][1]
    contexts = ''.join(f'<xbrli:context id="{context}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000004127</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>'
                       for _,start,end,_,_,context,_ in PERIODS[1:])
    header = '<tr><td></td><td colspan="3">Fiscal Years Ended</td></tr>'
    dates = '<tr><td></td><td>October 3, 2025</td><td>September 27, 2024</td><td>September 29, 2023</td></tr>'
    revenue = '<tr><td>Net revenue</td>'+''.join(
        f'<td><ix:nonFraction unitRef="usd" contextRef="{context}" decimals="-5" name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" format="ixt:num-dot-decimal" scale="6" id="{fact}">{value}</ix:nonFraction></td>'
        for _,_,_,value,fact,context,_ in PERIODS)+'</tr>'
    table = f'<table>{header}{dates}{revenue}</table>'
    titles = ['<div>SKYWORKS SOLUTIONS, INC.</div>','<div>CONSOLIDATED STATEMENTS OF OPERATIONS</div>',
              '<div>(In millions, except per share amounts)</div>',f'<div>{POLICY}</div>']
    added = contexts+'<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'+''.join(titles[:3])+table+titles[3]
    modified = body.replace(b'<body>',b'<body xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">').replace(b'</body>',added.encode()+b'</body>')
    for field in ('scope_passage','fiscal_calendar_passage','section_heading','concentration_passage','alias_passage'):
        old = base['selection'][field]
        base['selection'][field] = pin(modified,body[old['byte_start']:old['byte_end']].decode())
    captures['issuer'] = (capture(modified,captures['issuer'][0]['requested_url']),modified)
    selected = {'measure':'NET_REVENUE','reporting_scope':'CONSOLIDATED_REPORTING_GROUP',
        **{key:pin(modified,literal) for key,literal in zip(service._BLOCKS,titles)},
        'statement_table':pin(modified,table),'period_header_row':pin(modified,header),
        'column_header_row':pin(modified,dates),'revenue_row':pin(modified,revenue),
        'periods':[dict(zip(('fiscal_year','period_start','period_end','value_literal','fact_id','context_id','column'),row)) for row in PERIODS]}
    return {'selection':selected,'concentration_request':base},captures


def changed(inputs,before,after):
    request,captures = deepcopy(inputs)
    metadata,body = captures['issuer'];modified = body.replace(before,after)
    if modified == body: raise AssertionError('Synthetic fixture mutation did not apply')
    for selected in (request['selection'],request['concentration_request']['selection']):
        for key,old in tuple(selected.items()):
            if isinstance(old,dict) and set(old) == {'byte_start','byte_end','sha256'}:
                literal = body[old['byte_start']:old['byte_end']].replace(before,after).decode()
                selected[key] = pin(modified,literal)
    captures['issuer'] = (capture(modified,metadata['requested_url']),modified)
    return request,captures


class SupplierRevenueDenominatorTests(unittest.TestCase):
    def setUp(self):
        for module in (service,concentration):
            clock = patch.object(module,'_physical_clock',return_value=NOW)
            clock.start();self.addCleanup(clock.stop)

    def review(self,inputs=None,**kwargs):
        request,captures = inputs or fixture()
        return service.review_sec_supplier_revenue_denominator(request,captures,**kwargs)

    def assertRejected(self,inputs,reason=None):
        result = self.review(inputs)
        self.assertEqual('REJECTED',result['status'],result)
        self.assertIsNone(result['claim'])
        if reason: self.assertEqual(reason,result['reason'])

    def test_disclosed_three_supplier_totals_have_exact_units_periods_and_no_customer_estimate(self):
        report = self.review();self.assertEqual('CANDIDATE_REVIEWED',report['status'],report)
        rows = report['claim']['quantities']
        self.assertEqual(['4086900000','4178000000','4772400000'],[row['scaled_total_net_revenue_usd'] for row in rows])
        self.assertEqual([2025,2024,2023],[row['fiscal_year'] for row in rows])
        self.assertEqual(['c-1','c-7','c-8'],[row['context_id'] for row in rows])
        self.assertEqual([False,True,True],[row['comparative_in_current_filing'] for row in rows])
        for row in rows:
            self.assertEqual('USD',row['currency']);self.assertEqual('millions',row['display_unit'])
            self.assertEqual(6,row['scale']);self.assertEqual(-5,row['decimals'])
            self.assertIsNone(row['source_sign']);self.assertEqual([],row['context_dimensions'])
            self.assertTrue(row['same_original_issuer_and_period_supported'])
            self.assertFalse(row['denominator_basis_identity_verified'])
            self.assertFalse(row['same_original_period_and_denominator_basis_supported'])
            self.assertFalse(row['customer_amount_calculated']);self.assertIsNone(row['customer_amount'])
        self.assertEqual('Skyworks Solutions, Inc.',report['claim']['denominator_owner']['owner_name'])

    def test_join_records_source_fact_ids_without_reinterpreting_generic_customer_member(self):
        result = self.review()['claim']
        self.assertEqual(['f-977','f-978','f-979'],[row['concentration_fact_id'] for row in result['quantities']])
        self.assertEqual(['c-229','c-230','c-231'],[row['concentration_context_id'] for row in result['quantities']])
        self.assertFalse(result['generic_customer_member_renamed'])
        self.assertFalse(result['customer_percentage_times_amount_performed'])
        self.assertEqual('WITHHELD_DENOMINATOR_BASIS_UNVERIFIED',result['concentration_join_status'])
        self.assertTrue(result['same_original_issuer_and_period_supported'])
        self.assertFalse(result['denominator_basis_identity_verified'])

    def test_comparatives_remain_current_filing_versions_with_prior_restated_definition_unknown(self):
        result = self.review()
        self.assertEqual('COMPARATIVES_AS_REPORTED_IN_THIS_CURRENT_FILING',result['claim']['comparative_version_scope'])
        self.assertEqual('UNKNOWN_NOT_RECONSTRUCTED',result['claim']['definition_comparability_across_prior_filings'])
        self.assertFalse(result['historical_as_filed_comparison_verified'])
        self.assertEqual(POLICY,result['claim']['definition_policy_literal'])

    def test_reclassification_qualifier_is_preserved_and_automatic_basis_join_is_withheld(self):
        f = changed(fixture(),b'Our revenue reserves',b'Prior-period amounts were reclassified. Our revenue reserves')
        result = self.review(f);self.assertEqual('CANDIDATE_REVIEWED',result['status'],result)
        self.assertEqual(['reclassified'],result['claim']['definition_change_terms_in_selected_policy'])
        self.assertEqual('WITHHELD_DENOMINATOR_BASIS_UNVERIFIED',result['claim']['concentration_join_status'])
        self.assertTrue(all(not row['same_original_period_and_denominator_basis_supported'] for row in result['claim']['quantities']))
        self.assertIn('reclassified',result['claim']['definition_policy_literal'])

    def test_matching_contexts_and_a_policy_without_change_words_do_not_prove_basis_identity(self):
        result = self.review();claim = result['claim']
        self.assertEqual('CANDIDATE_REVIEWED',result['status'])
        self.assertEqual([],claim['definition_change_terms_in_selected_policy'])
        self.assertFalse(claim['definition_change_scan_is_exhaustive'])
        self.assertTrue(claim['same_original_issuer_and_period_supported'])
        self.assertFalse(result['denominator_basis_identity_verified'])
        self.assertFalse(claim['denominator_basis_identity_verified'])
        self.assertEqual('WITHHELD_DENOMINATOR_BASIS_UNVERIFIED',claim['concentration_join_status'])
        self.assertTrue(all(row['same_original_issuer_and_period_supported'] for row in claim['quantities']))
        self.assertTrue(all(not row['same_original_period_and_denominator_basis_supported'] for row in claim['quantities']))

    def test_unrecognized_basis_qualification_is_kept_whole_without_reenabling_a_join(self):
        suffix = ' The totals omit acquired-subsidiary sales while the concentration calculation includes them.'
        f = changed(fixture(),POLICY.encode(),(POLICY+suffix).encode())
        report = self.review(f);claim = report['claim']
        self.assertEqual('CANDIDATE_REVIEWED',report['status'])
        self.assertEqual(POLICY+suffix,claim['definition_policy_literal'])
        self.assertEqual([],claim['definition_change_terms_in_selected_policy'])
        self.assertFalse(claim['denominator_basis_identity_verified'])
        self.assertTrue(all(not row['same_original_period_and_denominator_basis_supported'] for row in claim['quantities']))
        self.assertEqual(['4086900000','4178000000','4772400000'],[row['scaled_total_net_revenue_usd'] for row in claim['quantities']])

    def test_revenue_total_cannot_be_replaced_by_percent_receivables_or_geographic_segment(self):
        for before,after in ((b'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',b'us-gaap:AccountsReceivableNetCurrent'),
                (b'>Net revenue</td>',b'>Customer revenue</td>'),(b'(In millions, except per share amounts)',b'(In percentages)')):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after))
        f = fixture();f[0]['selection']['measure'] = 'APPLE_PURCHASES'
        self.assertRejected(f,'UNSUPPORTED_DENOMINATOR_MEASURE_OR_SCOPE')

    def test_currency_is_exact_unit_namespace_not_a_dollar_symbol_or_unit_id(self):
        for before,after in ((b'iso4217:USD',b'iso4217:CAD'),(b'http://www.xbrl.org/2003/iso4217',b'http://example.invalid/currencies')):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after),'DENOMINATOR_EXACT_USD_UNIT_REQUIRED')

    def test_scale_sign_precision_and_transform_are_not_silently_normalized(self):
        for before,after in ((b'scale="6"',b'scale="3"'),(b'decimals="-5"',b'decimals="-6"'),
                (b'format="ixt:num-dot-decimal"',b'format="ixt:num-comma-decimal"'),
                (b'scale="6"',b'sign="-" scale="6"'),(b'2020-02-12',b'2015-02-26')):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after),'DENOMINATOR_FACT_CONCEPT_SCALE_SIGN_FORMAT_OR_VALUE_MISMATCH')

    def test_literal_and_footnote_qualifiers_cannot_disappear_in_a_cell(self):
        for before,after in ((b'>4,086.9</ix:nonFraction>',b'>4,086.9</ix:nonFraction><sup>(1)</sup>'),
                (b'id="den-25"',b'footnoteRefs="note1" id="den-25"')):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after),'DENOMINATOR_NONVISIBLE_OR_EXTERNAL_QUALIFIER')
        self.assertRejected(changed(fixture(),b'>4,086.9</ix:nonFraction>',b'>4,086.9</ix:nonFraction> excluding tax'))

    def test_wrong_comparative_duration_is_not_the_customer_percentage_period(self):
        f = changed(fixture(),b'<xbrli:startDate>2023-09-30</xbrli:startDate><xbrli:endDate>2024-09-27</xbrli:endDate></xbrli:period></xbrli:context><xbrli:context id="c-8"',
                    b'<xbrli:startDate>2023-10-01</xbrli:startDate><xbrli:endDate>2024-09-27</xbrli:endDate></xbrli:period></xbrli:context><xbrli:context id="c-8"')
        self.assertRejected(f,'DENOMINATOR_DURATION_ENTITY_OR_CONSOLIDATED_CONTEXT_MISMATCH')
        f = fixture();f[0]['selection']['periods'][1]['period_start'] = '2023-10-01'
        self.assertRejected(f,'DENOMINATOR_PERIOD_OR_LITERAL_DOES_NOT_MATCH_CONCENTRATION')

    def test_foreign_issuer_or_dimensioned_context_cannot_supply_a_consolidated_total(self):
        before = b'<xbrli:context id="c-7"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000004127</xbrli:identifier>'
        for after in (before.replace(b'0000004127',b'0000320193'),before+b'<xbrli:segment/>'):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after),'DENOMINATOR_DURATION_ENTITY_OR_CONSOLIDATED_CONTEXT_MISMATCH')

    def test_instant_duplicate_or_unknown_context_structure_does_not_become_duration_revenue(self):
        before = b'<xbrli:context id="c-7"><xbrli:entity>'
        f = changed(fixture(),before,b'<xbrli:context id="c-7"><xbrli:scenario/><xbrli:entity>')
        self.assertRejected(f,'DENOMINATOR_DURATION_ENTITY_OR_CONSOLIDATED_CONTEXT_MISMATCH')
        f = changed(fixture(),b'id="den-24"',b'id="den-25"')
        self.assertRejected(f,'DENOMINATOR_ROW_FACTS_NOT_THE_EXACT_ORDERED_SELECTION')

    def test_visible_column_years_must_align_with_fact_contexts_not_just_fiscal_year_numbers(self):
        f = changed(fixture(),b'<td>September 27, 2024</td>',b'<td>September 29, 2023</td>')
        self.assertRejected(f,'DENOMINATOR_VISIBLE_COLUMN_DATE_FACT_OR_VALUE_MISMATCH')
        f = fixture();f[0]['selection']['periods'][1]['column'] = 3
        self.assertRejected(f,'DENOMINATOR_VISIBLE_COLUMN_DATE_FACT_OR_VALUE_MISMATCH')
        f = fixture();f[0]['selection']['periods'].reverse()
        self.assertRejected(f,'DENOMINATOR_ROW_FACTS_NOT_THE_EXACT_ORDERED_SELECTION')

    def test_full_statement_group_and_units_are_required_next_to_the_same_table(self):
        for before,after in ((b'<div>SKYWORKS SOLUTIONS, INC.</div>',b'<div>APPLE INC.</div>'),
                (b'CONSOLIDATED STATEMENTS OF OPERATIONS',b'STANDALONE STATEMENTS OF OPERATIONS'),
                (b'(In millions, except per share amounts)',b'(In thousands, except per share amounts)')):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after),'DENOMINATOR_STATEMENT_GROUP_OR_DISPLAY_UNIT_NOT_EXPLICIT')

    def test_full_policy_is_required_and_never_truncated_to_hide_reserve_qualifiers(self):
        f = fixture();p = f[0]['selection']['basis_passage'];p['byte_end'] -= 6
        p['sha256'] = hashlib.sha256(f[1]['issuer'][1][p['byte_start']:p['byte_end']]).hexdigest()
        self.assertRejected(f,'SELECTION_NOT_COMPLETE_ORIGINAL_BLOCK')
        self.assertRejected(changed(fixture(),b'net of estimated reserves.',b'before estimated reserves.'),'DENOMINATOR_NET_REVENUE_POLICY_BASIS_NOT_EXPLICIT')

    def test_complete_statement_and_revenue_row_pins_cannot_point_to_independent_fragments(self):
        f = fixture();f[0]['selection']['statement_table']['byte_end'] -= 8
        p = f[0]['selection']['statement_table'];p['sha256'] = hashlib.sha256(f[1]['issuer'][1][p['byte_start']:p['byte_end']]).hexdigest()
        self.assertRejected(f,'DENOMINATOR_TABLE_NOT_THE_COMPLETE_PINNED_ORIGINAL')
        f = fixture();f[0]['selection']['revenue_row'] = f[0]['selection']['column_header_row']
        self.assertRejected(f,'DENOMINATOR_STATEMENT_ROW_OR_HEADER_SCOPE_MISMATCH')

    def test_table_cannot_be_borrowed_from_hidden_ancestor_or_nested_object(self):
        f = fixture();p = f[0]['selection']['statement_table'];table = f[1]['issuer'][1][p['byte_start']:p['byte_end']]
        f = changed(f,table,b'<section hidden="hidden">'+table+b'</section>')
        f[0]['selection']['statement_table'] = pin(f[1]['issuer'][1],table.decode())
        self.assertRejected(f,'DENOMINATOR_SELECTED_TABLE_NOT_VISIBLE_OR_TOP_LEVEL')
        self.assertRejected(changed(fixture(),b'<tr><td>Net revenue',b'<tr hidden="hidden"><td>Net revenue'),'DENOMINATOR_NONVISIBLE_OR_EXTERNAL_QUALIFIER')

    def test_nested_tables_rowspans_and_ambiguous_column_width_fail_closed(self):
        for before,after in ((b'<td>Net revenue</td>',b'<td rowspan="2">Net revenue</td>'),
                (b'colspan="3"',b'colspan="99"'),(b'<td>Net revenue</td>',b'<td><table><tr><td>Net revenue</td></tr></table></td>')):
            with self.subTest(after=after):self.assertRejected(changed(fixture(),before,after))

    def test_wrong_scale_cannot_be_fixed_by_changing_only_a_selected_literal(self):
        f = fixture();f[0]['selection']['periods'][0]['value_literal'] = '4.1'
        self.assertRejected(f,'DENOMINATOR_VISIBLE_COLUMN_DATE_FACT_OR_VALUE_MISMATCH')
        f = fixture();f[0]['selection']['periods'][0]['value_literal'] = '-4,086.9'
        self.assertRejected(f,'DENOMINATOR_PERIOD_OR_LITERAL_DOES_NOT_MATCH_CONCENTRATION')

    def test_overlong_numeric_literal_is_rejected_before_decimal_precision_can_round_it(self):
        f = fixture();f[0]['selection']['periods'][0]['value_literal'] = '999,'*15+'999.9'
        self.assertRejected(f,'DENOMINATOR_PERIOD_OR_LITERAL_DOES_NOT_MATCH_CONCENTRATION')

    def test_duplicate_currency_unit_or_selected_fact_does_not_take_the_first_match(self):
        unit = b'<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
        self.assertRejected(changed(fixture(),unit,unit+unit),'DENOMINATOR_EXACT_USD_UNIT_REQUIRED')
        f = fixture();body = f[1]['issuer'][1]
        start = body.index(b'<ix:nonFraction unitRef="usd"');end = body.index(b'</ix:nonFraction>',start)+len(b'</ix:nonFraction>')
        duplicate = body[start:end]
        f = changed(f,b'</body>',b'<div>'+duplicate+b'</div></body>')
        self.assertRejected(f,'DENOMINATOR_DUPLICATE_OR_MISSING_FACT_CONTEXT')

    def test_a_different_source_or_unsupported_concentration_cannot_be_joined(self):
        f = fixture();f[0]['concentration_request']['selection']['issuer_cik'] = '0000320193'
        self.assertRejected(f,'ORIGINAL_CONCENTRATION_REVIEW_NOT_SUPPORTED')
        f = fixture();f[1]['issuer'][0]['raw_response_sha256'] = '0'*64
        self.assertRejected(f,'ORIGINAL_CONCENTRATION_REVIEW_NOT_SUPPORTED')

    def test_unexpected_cross_scope_fields_and_unsafe_payloads_do_not_echo_values(self):
        f = fixture();f[0]['selection']['customer_amount'] = 'estimated'
        self.assertRejected(f,'INVALID_OR_UNSAFE_DENOMINATOR_REQUEST')
        f = fixture();f[0]['selection']['access_token'] = 'fixture-only-value'
        report = self.review(f);self.assertEqual('REJECTED',report['status']);self.assertNotIn('fixture-only-value',json.dumps(report))

    def test_default_physical_now_is_separate_from_source_and_explicit_past_cannot_backdate(self):
        report = self.review();self.assertEqual(NOW.isoformat(),report['knowledge_at'])
        self.assertLess(report['claim']['source_inputs_known_at'],report['knowledge_at'])
        self.assertIsNone(report['claim']['primary_source']['publisher_publication_at'])
        self.assertEqual('AFTER_PHYSICAL_KNOWLEDGE_CUTOFF',self.review(as_of=NOW-timedelta(seconds=1))['reason'])
        with patch.object(service,'_physical_clock',side_effect=[NOW,NOW-timedelta(microseconds=1)]):
            self.assertEqual('PHYSICAL_REVIEW_CLOCK_MOVED_BACKWARDS',self.review()['reason'])

    def test_late_physical_completion_respects_explicit_cutoff(self):
        with patch.object(service,'_physical_clock',side_effect=[NOW,NOW+timedelta(seconds=2)]):
            report = self.review(as_of=NOW+timedelta(seconds=1))
        self.assertEqual('AFTER_PHYSICAL_KNOWLEDGE_CUTOFF',report['reason'])

    def test_producer_is_pure_and_does_not_change_original_inputs_or_production_scope(self):
        inputs = fixture();before = deepcopy(inputs)
        with patch('socket.socket.connect',side_effect=AssertionError('offline only')),patch('sqlite3.connect',side_effect=AssertionError('no DB')),patch.object(Path,'write_bytes',side_effect=AssertionError('no source writes')):
            report = self.review(inputs)
        self.assertEqual(before,inputs);self.assertEqual('CANDIDATE_REVIEWED',report['status'],report)
        for key in ('source_http_requests','collector_requests','usaspending_requests','db_writes','registry_adoptions'):
            self.assertEqual(0,report[key])
        for key in ('customer_amount_calculated','counterparty_purchases_verified','direct_purchase_share_verified',
                    'physical_quantity_denominator_verified','counterparty_identity_continuity_verified','scoring_applied',
                    'model_modified','fitter_modified','trading_allowed','activation_allowed','out_of_sample_verified'):
            self.assertIs(report[key],False)

    def test_actual_whole_original_financial_statement_denominators_replay_with_exact_pins(self):
        from evidence.batch10_contracts_20261018.replay_retained import load_inputs
        report = self.review(load_inputs());self.assertEqual('CANDIDATE_REVIEWED',report['status'],report)
        claim = report['claim'];self.assertEqual('0000004127-25-000085',claim['primary_source']['accession_number'])
        self.assertEqual(['4086900000','4178000000','4772400000'],[r['scaled_total_net_revenue_usd'] for r in claim['quantities']])
        self.assertEqual(['f-61','f-62','f-63'],[r['fact_id'] for r in claim['quantities']])
        self.assertEqual([4,10,16],[r['column'] for r in claim['quantities']])
        self.assertEqual('Apple',claim['reported_customer']['reported_label'])
        self.assertTrue(claim['filing']['publisher_size_conflict']);self.assertFalse(claim['filing']['exact_historical_as_filed_version_verified'])
        self.assertIn('price protection',claim['definition_policy_literal'])
        self.assertFalse(claim['broader_award_uei_or_durable_history_verified'])


if __name__ == '__main__': unittest.main()
