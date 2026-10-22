"""Actual urllib redirect processing against an offline HTTPS transport only."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

from market_checker_app.services import nhtsa_recall_scout_service as nhtsa
from market_checker_app.services.public_source_budget import PublicSourceBudget
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parents[1]
MODELS = json.loads((ROOT / "market_checker_app/data/verified_nhtsa_models.json").read_text())
FORD = next(model for model in MODELS if model["ticker"] == "F")
CHEVROLET = next(model for model in MODELS if model["ticker"] == "GM")
NOW = datetime(2026, 10, 14, 7, tzinfo=timezone.utc)
EMPTY = b'{"Count":0,"Message":"Results returned successfully","results":[]}'


class OfflineHTTPS(HTTPSHandler):
    """Replace only socket I/O; use the real urllib redirect/error handlers."""
    def __init__(self, response_for):
        super().__init__()
        self.response_for, self.requests = response_for, []

    def https_open(self, request):
        self.requests.append(request.full_url)
        status, location, body = self.response_for(request.full_url, len(self.requests))
        headers = Message()
        headers["Content-Type"] = "application/json"
        if location is not None:
            headers["Location"] = location
        response = addinfourl(BytesIO(body), headers, request.full_url, code=status)
        response.msg = "Synthetic offline response"
        return response


@contextmanager
def offline_transport(response_for):
    transport = OfflineHTTPS(response_for)

    def offline_opener(*handlers):
        return build_opener(transport, *handlers)

    with patch.object(nhtsa, "build_opener", side_effect=offline_opener), \
         patch("urllib.request.urlopen", side_effect=AssertionError("Default urlopen must not run")), \
         patch("socket.create_connection", side_effect=AssertionError("Actual network is forbidden")), \
         patch.object(nhtsa.NhtsaRecallClient, "is_live", False):
        yield transport


def saved_rows(store):
    with store._connect() as connection:
        findings = [tuple(row) for row in connection.execute("""SELECT subject_id, source_object_id,
            content_hash, available_at, details_json FROM scout_findings
            WHERE source='nhtsa' ORDER BY source_object_id""")]
        checks = [tuple(row) for row in connection.execute("""SELECT subject_id, identity_key,
            candidate_count, truncated FROM scout_specialist_checks WHERE source='nhtsa'
            ORDER BY subject_id, identity_key""")]
        outcomes = [row[0] for row in connection.execute("""SELECT outcome FROM scout_public_requests
            WHERE source='nhtsa' ORDER BY attempted_at, request_id""")]
    return findings, checks, outcomes


def positive_body(model):
    return json.dumps({"Count": 1, "Message": nhtsa.SUCCESS_MESSAGE, "results": [{
        "Make": model["make"], "Model": model["model"], "ModelYear": str(model["model_year"]),
        "Manufacturer": model["manufacturer"], "NHTSACampaignNumber": "26V104000",
        "ReportReceivedDate": "20/02/2026"}]}).encode()


class Batch5NhtsaRedirectGuardTests(unittest.TestCase):
    def test_actual_urllib_302_and_307_cannot_start_second_transport_operation(self):
        url = nhtsa.recall_url(FORD["make"], FORD["model"], FORD["model_year"])
        for status in (302, 307):
            for target in (url, url.replace("api.nhtsa.gov", "example.org")):
                # If redirects were followed, the next operation would succeed.
                def response_for(requested, number):
                    return (status, target, EMPTY) if number == 1 else (200, None, EMPTY)

                with self.subTest(status=status, target=target), offline_transport(response_for) as transport:
                    with self.assertRaises(HTTPError) as error:
                        nhtsa.NhtsaRecallClient().recalls(FORD["make"], FORD["model"], FORD["model_year"])
                    self.assertEqual(status, error.exception.code)
                    self.assertEqual([url], transport.requests)
                    error.exception.close()

    def test_redirect_is_one_failed_reservation_without_complete_check_or_same_day_retry(self):
        for status in (302, 307):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "test.db")
                with offline_transport(lambda url, number: (status, url, EMPTY)) as transport:
                    service = nhtsa.NhtsaRecallScoutService(store, client=nhtsa.NhtsaRecallClient(), models=[FORD])
                    first = service.run(as_of=NOW)
                    self.assertEqual(("PARTIAL", 0, 1, 0),
                        (first["status"], first["checked_models"], first["failed_models"], first["new_findings"]))
                    self.assertEqual((1, 1, 1),
                        (first["attempted_requests"], first["daily_requests"], first["daily_issuers"]))
                    self.assertEqual(([], [], ["FAILED"]), saved_rows(store))
                    restarted = nhtsa.NhtsaRecallScoutService(ScoutStore(store.db_path),
                        client=nhtsa.NhtsaRecallClient(), models=[FORD]).run(as_of=NOW)
                    self.assertEqual(("WAIT_QUOTA", 0, 1),
                        (restarted["status"], restarted["attempted_requests"], restarted["daily_requests"]))
                    self.assertEqual(1, len(transport.requests))
                    self.assertEqual(([], [], ["FAILED"]), saved_rows(store))

    def test_later_redirect_preserves_prior_finding_check_and_charged_attempts(self):
        for status in (302, 307):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "test.db")
                ford_url = nhtsa.recall_url(FORD["make"], FORD["model"], FORD["model_year"])
                chevrolet_url = nhtsa.recall_url(CHEVROLET["make"], CHEVROLET["model"], CHEVROLET["model_year"])

                def response_for(url, number):
                    return (200, None, positive_body(FORD)) if url == ford_url else (status, url, EMPTY)

                with offline_transport(response_for) as transport:
                    result = nhtsa.NhtsaRecallScoutService(store, client=nhtsa.NhtsaRecallClient(),
                        models=[FORD, CHEVROLET]).run(as_of=NOW)
                    self.assertEqual(("PARTIAL", 1, 1, 1, 2, 2),
                        (result["status"], result["checked_models"], result["failed_models"],
                         result["new_findings"], result["attempted_requests"], result["daily_requests"]))
                    self.assertEqual([ford_url, chevrolet_url], transport.requests)
                    preserved = saved_rows(store)
                    self.assertEqual((1, 1, ["RETURNED", "FAILED"]),
                        (len(preserved[0]), len(preserved[1]), preserved[2]))
                    self.assertEqual((FORD["ticker"], 1, 0),
                        (preserved[1][0][0], preserved[1][0][2], preserved[1][0][3]))
                    self.assertFalse(json.loads(preserved[0][0][4])["scoring_applied"])
                    retry = nhtsa.NhtsaRecallScoutService(ScoutStore(store.db_path),
                        client=nhtsa.NhtsaRecallClient(), models=[FORD, CHEVROLET]).run(as_of=NOW)
                    self.assertEqual(("WAIT_QUOTA", 0, 2),
                        (retry["status"], retry["attempted_requests"], retry["daily_requests"]))
                    self.assertEqual(preserved, saved_rows(store))
                    self.assertEqual(2, len(transport.requests))

    def test_exhausted_shared_daily_quota_blocks_transport_for_unattempted_make(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            budget = PublicSourceBudget(store, "nhtsa", as_of=NOW, max_run_seconds=180)
            self.assertTrue(budget.acquire())
            try:
                for number in range(8):
                    # Synthetic reservation setup only; never actual source acceptance.
                    budget.call("MAKE:FORD", f"synthetic-budget-{number}", lambda: None)
            finally:
                budget.release()
            before = saved_rows(store)
            with offline_transport(lambda url, number: (307, url, EMPTY)) as transport:
                result = nhtsa.NhtsaRecallScoutService(ScoutStore(store.db_path),
                    client=nhtsa.NhtsaRecallClient(), models=[CHEVROLET]).run(as_of=NOW)
            self.assertEqual(("WAIT_QUOTA", 0, 8),
                (result["status"], result["attempted_requests"], result["daily_requests"]))
            self.assertEqual([], transport.requests)
            self.assertEqual(before, saved_rows(store))


if __name__ == "__main__":
    unittest.main()
