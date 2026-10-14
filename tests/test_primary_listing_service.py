from datetime import datetime, timezone
import unittest
from tests.test_sec13f_identity_bulk import cover, OBSERVED
from market_checker_app.services.primary_listing_service import parse_requested_listing
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot


class PrimaryListingTests(unittest.TestCase):
    def changed(self,source,body):
        return SourceSnapshot(source.kind,source.url,source.observed_at,body.encode(),source.published_at)

    def test_exact_class_uses_its_own_cik_and_context(self):
        source=cover()
        result=parse_requested_listing(source,'AAPL')
        self.assertEqual(('AAPL','0000320193','Security'),(result['ticker'],result['issuer_cik'],result['context_ref']))
        self.assertIn('Common Stock',result['class_title'])
        with self.assertRaises(ValueError):parse_requested_listing(source,'MSFT')

    def test_unrelated_continued_debt_cannot_grant_or_remove_equity_credit(self):
        source=cover(extra='<ix:nonNumeric name="dei:Security12bTitle" contextRef="Debt" continuedAt="next">Debt</ix:nonNumeric><ix:nonNumeric name="dei:TradingSymbol" contextRef="Debt">NOTE1</ix:nonNumeric>')
        self.assertEqual('AAPL',parse_requested_listing(source,'AAPL')['ticker'])
        with self.assertRaises(ValueError):parse_requested_listing(source,'NOTE1')

    def test_nested_equivalent_facts_validate_each_independent_context(self):
        source=cover();body=source.payload.decode()
        context=body[body.index('<xbrli:context'):body.index('</xbrli:context>')+len('</xbrli:context>')]
        body=body.replace('</xbrli:context>','</xbrli:context>'+context.replace('id="Security"','id="Other"'),1)
        body=body.replace('>AAPL</ix:nonNumeric>','><ix:nonNumeric name="dei:TradingSymbol" contextRef="Other">AAPL</ix:nonNumeric></ix:nonNumeric>')
        title='Common Stock, $0.01 par value per share'
        body=body.replace('>'+title+'</ix:nonNumeric>','><ix:nonNumeric name="dei:Security12bTitle" contextRef="Other">'+title+'</ix:nonNumeric></ix:nonNumeric>')
        result=parse_requested_listing(self.changed(source,body),'AAPL')
        self.assertEqual(title,result['class_title'])

    def test_different_issuer_duplicate_context_future_anchor_and_mixed_classes_fail(self):
        source=cover();body=source.payload.decode()
        cases=[body.replace('>0000320193<','>0000789019<'),
               body.replace('>2026-06-30<','>2027-06-30<'),
               body.replace('</html>','<ix:nonNumeric name="dei:Security12bTitle" contextRef="Security">Preferred Stock</ix:nonNumeric></html>'),
               body.replace('</html>','<xbrli:context id="Security"></xbrli:context></html>')]
        for case in cases:
            with self.assertRaises(ValueError):parse_requested_listing(self.changed(source,case),'AAPL')

    def test_nil_or_continued_requested_class_is_not_verified(self):
        for extra in [' continuedAt="later"',' xsi:nil="true"']:
            source=cover();body=source.payload.decode().replace('name="dei:Security12bTitle"','name="dei:Security12bTitle"'+extra)
            with self.assertRaises(ValueError):parse_requested_listing(self.changed(source,body),'AAPL')

    def test_explicit_venue_annotation_and_unrelated_same_symbol_debt(self):
        source=cover();body=source.payload.decode().replace('>AAPL<','>AAPL (NYSE)<')
        context=body[body.index('<xbrli:context'):body.index('</xbrli:context>')+len('</xbrli:context>')]
        body=body.replace('</html>',context.replace('id="Security"','id="Debt"')+
            '<ix:nonNumeric name="dei:TradingSymbol" contextRef="Debt">AAPL</ix:nonNumeric>'+
            '<ix:nonNumeric name="dei:Security12bTitle" contextRef="Debt">Senior Notes due 2028</ix:nonNumeric></html>')
        proof=parse_requested_listing(self.changed(source,body),'AAPL')
        self.assertEqual('AAPL (NYSE)',proof['as_filed_ticker'])
        with self.assertRaises(ValueError):parse_requested_listing(self.changed(source,body.replace('(NYSE)','(unverified venue)')),'AAPL')

    def test_linked_same_accession_exhibit_can_use_cover_contexts(self):
        source=cover();body=source.payload.decode()
        context=body[body.index('<xbrli:context'):body.index('</xbrli:context>')+len('</xbrli:context>')]
        parent=self.changed(source,body.replace('</html>','<a href="ex99.htm">See Exhibit 99.3</a></html>'))
        exhibit=SourceSnapshot('ticker_filing',source.url.rsplit('/',1)[0]+'/ex99.htm',source.observed_at,
            body.replace(context,'').encode(),source.published_at)
        proof=parse_requested_listing(exhibit,'AAPL',context_snapshot=parent)
        self.assertEqual(parent.sha256,proof['context_source']['sha256'])
        with self.assertRaises(ValueError):parse_requested_listing(exhibit,'AAPL')
        with self.assertRaises(ValueError):parse_requested_listing(exhibit,'AAPL',context_snapshot=source)
