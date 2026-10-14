from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from tests.test_sec13f_identity_bulk import cover
from market_checker_app.primary_listing_capture import capture_one
from market_checker_app.services.sec13f_identity_bulk import load_snapshots
from market_checker_app.collectors.sec_edgar_client import SecAccessBlockedError


class PrimaryListingCaptureTests(unittest.TestCase):
    def test_capture_preserves_primary_bytes_and_derives_exact_class(self):
        source=cover()
        class Catalog:
            def lookup(self,*args,**kwargs):return {'cik':'0000320193'}
        class Reviewed:
            def decision(self,*args,**kwargs):return None
        class Client:
            def _request_json(self,url):return {'filings':{'recent':{'accessionNumber':['one'],'acceptanceDateTime':[source.published_at.isoformat()]}}}
            def _parse_filings(self,*args,**kwargs):return [SimpleNamespace(accession_number='one',filing_url=source.url)]
            def _request_bytes(self,url):return source.payload
        with TemporaryDirectory() as temp:
            row=capture_one('AAPL',catalog=Catalog(),reviewed=Reviewed(),client=Client(),workspace=Path(temp))
            self.assertEqual('VALIDATED',row['status'])
            self.assertEqual(source.sha256,row['sha256'])
            snapshots=load_snapshots(Path(temp))
            self.assertEqual([source.payload],[s.payload for s in snapshots])
            self.assertNotIn('user_agent',row)
            class Blocked(Client):
                def _request_bytes(self,url):raise SecAccessBlockedError('blocked')
            with self.assertRaises(SecAccessBlockedError):
                capture_one('AAPL',catalog=Catalog(),reviewed=Reviewed(),client=Blocked(),workspace=Path(temp))

    def test_inactive_instrument_does_not_collect_parent_or_form_a_new_binding(self):
        class Reviewed:
            def decision(self,*args,**kwargs):return {'instrument_status':'INACTIVE','id':'ended'}
        with TemporaryDirectory() as temp:
            row=capture_one('LEG',catalog=None,reviewed=Reviewed(),client=None,workspace=Path(temp))
        self.assertEqual('INACTIVE',row['status'])
