"""Meaningful exact-model negative/audit and shared-runtime quota regressions."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import Message
import importlib.util
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.nhtsa_negative_review_service import (
    NhtsaNegativeReviewService, NhtsaResponseCapture, NhtsaRawRecallClient,
    documented_model_scope, review_capture, recall_url,
)
from market_checker_app.services.nhtsa_recall_scout_service import NhtsaRecallScoutService
from market_checker_app.services.public_source_budget import PublicSourceBudget
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'evidence/batch5_regulatory_20261014'
SPEC = importlib.util.spec_from_file_location('batch5_scope_inputs', FOLDER / 'capture_live.py')
INPUTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INPUTS)
NOW = datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
EMPTY = b'{"Count":0,"Message":"Results returned successfully","results":[]}'


def capture(scope, *, status=200, body=EMPTY, observed=NOW, origin='SYNTHETIC_TEST', mime='application/json'):
    url = recall_url(scope.make, scope.model, scope.model_year)
    return NhtsaResponseCapture(url, url, status, mime, body, observed, observed, origin=origin)


class Captures:
    def __init__(self, status=200, callback=None):
        self.status, self.callback, self.calls = status, callback, []

    def capture(self, scope, *, timeout_seconds):
        self.calls.append(scope.key)
        if self.callback:
            self.callback()
        return capture(scope, status=self.status)


class Recalls:
    def __init__(self, *, callback=None):
        self.calls, self.callback = [], callback

    def recalls(self, make, model, model_year):
        self.calls.append((make, model, model_year))
        if self.callback:
            self.callback()
        return {'Count': 1, 'Message': 'Results returned successfully', 'results': [{
            'Make': make, 'Model': model, 'ModelYear': str(model_year), 'Manufacturer': 'Ford Motor Company',
            'NHTSACampaignNumber': '26V104000', 'ReportReceivedDate': '20/02/2026'}]}


def service(store, folder, *, client=None, scopes=None, **kwargs):
    return NhtsaNegativeReviewService(store, client=client or Captures(),
        scopes=scopes or INPUTS.documented_scopes()[:2], archive_root=Path(folder)/'raw', **kwargs)


def registered_models():
    return json.loads((ROOT/'market_checker_app/data/verified_nhtsa_models.json').read_text())


class Batch5NhtsaScopeReviewTests(unittest.TestCase):
    def test_documented_catalog_rejects_wrong_year_make_and_changed_bytes(self):
        path = ROOT/'evidence/batch4_regulatory_20261013/raw/nhtsa_models_cadillac_2026.json'
        metadata = json.loads(path.with_name(path.stem+'.metadata.json').read_text())
        for make, model, year, body in [('FORD', 'CT5', 2026, path.read_bytes()),
                                      ('CADILLAC', 'CT5', 2025, path.read_bytes()),
                                      ('CADILLAC', 'NOT A MODEL', 2026, path.read_bytes()),
                                      ('CADILLAC', 'CT5', 2026, path.read_bytes()+b' ' )]:
            with self.subTest(make=make,model=model,year=year):
                with self.assertRaises(ValueError):
                    documented_model_scope(make=make, model=model, model_year=year,
                        catalog_body=body, catalog_metadata=metadata)
        self.assertEqual(8, len(INPUTS.documented_scopes()))
        self.assertEqual(3, len({scope.make for scope in INPUTS.documented_scopes()}))

    def test_complete_empty_contract_is_model_only_and_synthetic_is_not_live(self):
        scope = INPUTS.documented_scopes()[0]
        row = review_capture(scope, capture(scope), reviewed_at=NOW)
        self.assertEqual('COMPLETE_NEGATIVE_MODEL_YEAR', row['status'])
        self.assertTrue(row['complete_negative'])
        self.assertFalse(row['live_complete_negative_verified'])
        self.assertFalse(row['issuer_wide_absence_verified'])
        self.assertFalse(row['issuer_attribution_verified'])
        self.assertFalse(row['manufacturer_ownership_inferred'])
        self.assertFalse(row['scoring_applied'])
        self.assertFalse(row['prediction_input'])

    def test_http400_success_empty_never_becomes_complete_negative(self):
        scope = INPUTS.documented_scopes()[0]
        for status in (400, 404, 500, 206):
            with self.subTest(status=status):
                row = review_capture(scope, capture(scope,status=status),reviewed_at=NOW)
                self.assertEqual('PARTIAL',row['status'])
                self.assertFalse(row['complete_negative'])
                self.assertIn('IS_NOT_COMPLETE_NEGATIVE',row['reason'])

    def test_missing_error_or_duplicate_envelope_does_not_credit_empty(self):
        scope=INPUTS.documented_scopes()[0]
        for body in (b'{"Count":0,"results":[]}',
                     b'{"Count":0,"Message":"Results returned successfully","Error":"unavailable","results":[]}',
                     b'{"Count":0,"Count":0,"Message":"Results returned successfully","results":[]}'):
            with self.subTest(body=body):
                row=review_capture(scope,capture(scope,body=body),reviewed_at=NOW)
                self.assertEqual(('PARTIAL',False),(row['status'],row['complete_negative']))

    def test_capture_query_model_year_and_knowledge_clock_are_exact(self):
        scope=INPUTS.documented_scopes()[0]
        wrong = [replace(capture(scope), final_url=recall_url(scope.make,scope.model,2025)),
                 replace(capture(scope), observed_at=NOW+timedelta(days=1)),
                 replace(capture(scope), started_at=NOW+timedelta(seconds=1)),
                 replace(capture(scope), observed_at=NOW.replace(tzinfo=None))]
        for response in wrong:
            with self.subTest(response=response.final_url):
                with self.assertRaises(ValueError):
                    review_capture(scope,response,reviewed_at=NOW)

    def test_secret_cue_or_html_is_whole_body_withheld_not_encoded(self):
        scope=INPUTS.documented_scopes()[0]
        for body,mime,reason in ((b'{"azure_map_key":null}', 'application/json',
                                  'WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE'),
                                 (b'<html>error</html>','text/html','WITHHELD_UNEXPECTED_CONTENT_TYPE')):
            with self.subTest(reason=reason), TemporaryDirectory() as directory:
                store=ScoutStore(Path(directory)/'test.db')
                lane=service(store,directory,scopes=[scope])
                row=lane.ingest_capture(scope,capture(scope,body=body,mime=mime),as_of=NOW)
                self.assertEqual(reason,row['publication_status'])
                self.assertFalse(row['public_raw_available'])
                self.assertIsNone(row['archive_relative_path'])
                self.assertFalse(row['complete_negative'])
                self.assertEqual([],list((Path(directory)/'raw').rglob('response.json')))
                self.assertEqual(1,len(list((Path(directory)/'raw').rglob('review.json'))))

    def test_immutable_replay_preserves_bytes_and_does_not_backdate_availability(self):
        scope=INPUTS.documented_scopes()[0]
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db');lane=service(store,directory,scopes=[scope])
            response=capture(scope,origin='ARCHIVED_RESPONSE_REPLAY')
            imported=NOW+timedelta(days=1)
            first=lane.ingest_capture(scope,response,as_of=imported)
            second=lane.ingest_capture(scope,response,as_of=imported)
            self.assertEqual(first,second)
            self.assertEqual(EMPTY,(Path(directory)/'raw'/first['archive_relative_path']).read_bytes())
            self.assertEqual(0,lane.report(as_of=NOW)['complete_negative_model_years'])
            self.assertEqual(1,lane.report(as_of=imported)['complete_negative_model_years'])
            self.assertEqual(0,lane.report(as_of=imported)['live_complete_negative_model_years'])
            self.assertEqual(0,store.public_daily_usage('nhtsa',as_of=imported)['daily_requests'])
            with self.assertRaisesRegex(ValueError,'reserved runtime'):
                lane.ingest_capture(scope,replace(response,origin='LIVE_PUBLIC_HTTP'),as_of=imported)

    def test_eight_requests_are_shared_and_restart_cannot_reset_quota(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'test.db';store=ScoutStore(path);client=Captures()
            lane=service(store,directory,client=client,scopes=INPUTS.documented_scopes())
            first=lane.run(as_of=NOW)
            self.assertEqual((8,3),(first['daily_requests'],first['daily_issuers']))
            self.assertEqual(8,len(client.calls))
            again=service(ScoutStore(path),directory,client=client,scopes=INPUTS.documented_scopes()).run(as_of=NOW)
            self.assertEqual('WAIT_QUOTA',again['status'])
            self.assertEqual(0,again['attempted_requests'])
            self.assertEqual(8,len(client.calls))
            regular_client=Recalls()
            regular=NhtsaRecallScoutService(ScoutStore(path),client=regular_client,models=registered_models())
            blocked=regular.run(as_of=NOW)
            self.assertEqual('WAIT_QUOTA',blocked['status'])
            self.assertEqual([],regular_client.calls)
            self.assertEqual(8,blocked['daily_requests'])
            # Correcting scope cannot reset the provider's day quota.
            corrected=dict(registered_models()[0],known_at=NOW.isoformat())
            self.assertEqual('WAIT_QUOTA',NhtsaRecallScoutService(ScoutStore(path),client=regular_client,
                models=[corrected]).run(as_of=NOW)['status'])
            self.assertEqual([],regular_client.calls)

    def test_failed_http_attempts_stay_charged_and_saved(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db');client=Captures(status=400)
            lane=service(store,directory,client=client,scopes=INPUTS.documented_scopes())
            summary=lane.run(as_of=NOW)
            self.assertEqual(('PARTIAL',8,0),(summary['status'],summary['daily_requests'],
                                           summary['complete_negative_model_years']))
            with store._connect() as connection:
                outcomes=[r[0] for r in connection.execute("SELECT outcome FROM scout_public_requests WHERE source='nhtsa'")]
            self.assertEqual(['FAILED']*8,outcomes)
            self.assertEqual(8,len(lane.report(as_of=NOW+timedelta(minutes=1))['reviews']))
            self.assertTrue(all((Path(directory)/'raw'/r['archive_relative_path']).read_bytes()==EMPTY
                                for r in summary['reviews']))

    def test_lease_loss_keeps_already_persisted_case_and_stops_next_io(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db')
            def expire():
                with store._connect() as connection:
                    connection.execute("UPDATE scout_provider_leases SET lease_until=? WHERE source='nhtsa'",
                                       ((NOW-timedelta(seconds=1)).isoformat(),))
            client=Captures(callback=expire);lane=service(store,directory,client=client)
            summary=lane.run(as_of=NOW)
            self.assertEqual('LEASE_LOST',summary['status'])
            self.assertEqual(1,len(client.calls))
            self.assertEqual(1,len(lane.report(as_of=NOW+timedelta(minutes=1))['reviews']))
            self.assertEqual(1,summary['daily_requests'])

    def test_regular_runtime_lease_loss_keeps_findings_and_complete_check(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db')
            def expire():
                with store._connect() as connection:
                    connection.execute("UPDATE scout_provider_leases SET lease_until=? WHERE source='nhtsa'",
                                       ((NOW-timedelta(seconds=1)).isoformat(),))
            client=Recalls(callback=expire)
            ford=registered_models()[1];second=dict(ford,model='MUSTANG')
            summary=NhtsaRecallScoutService(store,client=client,models=[ford,second]).run(as_of=NOW)
            self.assertEqual(('LEASE_LOST',1,1),(summary['status'],summary['checked_models'],summary['new_findings']))
            self.assertEqual(1,len(client.calls))
            with store._connect() as connection:
                self.assertEqual(1,connection.execute("SELECT COUNT(*) FROM scout_findings WHERE source='nhtsa'").fetchone()[0])
                self.assertEqual((1,0),tuple(connection.execute(
                    "SELECT candidate_count,truncated FROM scout_specialist_checks WHERE source='nhtsa'").fetchone()))

    def test_provider_busy_and_future_identity_make_no_request(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db');client=Captures()
            token=store.claim_provider('nhtsa',as_of=NOW,seconds=180)
            self.assertEqual('BUSY',service(store,directory,client=client).run(as_of=NOW)['status'])
            store.release_provider('nhtsa',token)
            future=replace(INPUTS.documented_scopes()[0],known_at=NOW+timedelta(days=1))
            self.assertEqual('WAIT_IDENTITY',service(store,directory,client=client,scopes=[future]).run(as_of=NOW)['status'])
            self.assertEqual([],client.calls)
            self.assertEqual(0,store.public_daily_usage('nhtsa',as_of=NOW)['daily_requests'])

    def test_429_cooldown_is_shared_and_regular_collector_cannot_fetch(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db');client=Captures(status=429)
            summary=service(store,directory,client=client).run(as_of=NOW)
            self.assertEqual(('RATE_LIMITED',1),(summary['status'],summary['daily_requests']))
            regular=Recalls()
            blocked=NhtsaRecallScoutService(store,client=regular,models=registered_models()).run(as_of=NOW)
            self.assertEqual('RATE_LIMITED',blocked['status'])
            self.assertEqual([],regular.calls)
            self.assertEqual(1,len(client.calls))

    def test_live_clock_cannot_create_past_day_requests(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db');client=Captures();client.is_live=True
            lane=service(store,directory,client=client)
            with self.assertRaisesRegex(ValueError,'backdated'):
                lane.run(as_of=datetime(2000,1,1,tzinfo=timezone.utc))
            regular=Recalls();regular.is_live=True
            with self.assertRaisesRegex(ValueError,'actual UTC day'):
                NhtsaRecallScoutService(store,client=regular,models=registered_models()).run(
                    as_of=datetime(2000,1,1,tzinfo=timezone.utc))
            self.assertEqual([],client.calls);self.assertEqual([],regular.calls)

    def test_shared_registration_missing_fails_closed(self):
        with TemporaryDirectory() as directory:
            store=ScoutStore(Path(directory)/'test.db');client=Captures()
            with patch('market_checker_app.services.nhtsa_negative_review_service.PUBLIC_DAILY_LIMITS',{}):
                self.assertEqual('WAIT_SHARED_BUDGET',service(store,directory,client=client).run(as_of=NOW)['status'])
            self.assertEqual([],client.calls)

    def test_captured_actual_eight_have_exact_hashes_and_no_fabricated_negative(self):
        output=json.loads((FOLDER/'live_run.json').read_text())
        self.assertEqual((0,8),(output['daily_before']['daily_requests'],output['daily_after']['daily_requests']))
        self.assertEqual(3,output['daily_after']['daily_issuers'])
        self.assertFalse(output['fresh_or_reset_database'])
        self.assertFalse(output['prior_research_quota_history_recovered'])
        rows=output['result']['reviews'];self.assertEqual(8,len(rows))
        self.assertEqual((4,4),(sum(r['http_status']==200 for r in rows),sum(r['http_status']==400 for r in rows)))
        import hashlib
        for row in rows:
            body=(FOLDER/'captures'/row['archive_relative_path']).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(),row['raw_response_sha256'])
            self.assertEqual(len(body),row['raw_response_bytes'])
            self.assertEqual('LIVE_PUBLIC_HTTP',row['capture_origin'])
            self.assertLessEqual(datetime.fromisoformat(row['started_at']),datetime.fromisoformat(row['source_observed_at']))
            self.assertLessEqual(datetime.fromisoformat(row['source_observed_at']),datetime.fromisoformat(row['known_at']))
            self.assertFalse(row['complete_negative'])
            self.assertFalse(row['issuer_wide_absence_verified'])
            if row['http_status']==400:
                self.assertEqual({'Count':0,'Message':'Results returned successfully','results':[]},json.loads(body))
                self.assertEqual('PARTIAL',row['status'])
        self.assertEqual(17,sum(row['matched_rows'] for row in rows))
        self.assertEqual(0,output['persisted_report']['live_complete_negative_model_years'])


if __name__=='__main__':
    unittest.main()
