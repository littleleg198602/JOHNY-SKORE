from datetime import datetime, timezone
from http.client import IncompleteRead
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.request import Request

from market_checker_app.services.usaspending_request_budget import _OfficialRedirectHandler, post_json
from market_checker_app.services.usaspending_scout_service import UsaSpendingScoutService
from market_checker_app.services.usaspending_recipient_discovery import UsaSpendingRecipientDiscovery
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import UsaSpendingCheckpointStore

from tests.test_usaspending_resumable_collection import Awards, CLOCK, identity, page


class Response(io.BytesIO):
    def __init__(self, body, url="https://api.usaspending.gov/api/v2/recipient/"):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


class UsaSpendingHttpBudgetTests(unittest.TestCase):
    def test_official_json_post_is_bounded_and_preserves_request_body(self):
        with patch("market_checker_app.services.usaspending_request_budget.urlopen",
                   return_value=Response(b'{"results": []}')) as urlopen:
            self.assertEqual({"results": []}, post_json("https://api.usaspending.gov/api/v2/recipient/", {"page": 1}, timeout=3))
        args, kwargs = urlopen.call_args
        self.assertEqual(3, kwargs["timeout"])
        self.assertEqual("POST", args[0].method)
        self.assertEqual({"page": 1}, json.loads(args[0].data))

    def test_body_byte_budget_and_json_object_are_required(self):
        for body, message in ((b'{"long": "' + b"x" * 30 + b'"}', "byte budget"),
                              (b"[]", "must be an object")):
            with self.subTest(body=body):
                with patch("market_checker_app.services.usaspending_request_budget.MAX_RESPONSE_BYTES", 20), \
                     patch("market_checker_app.services.usaspending_request_budget.urlopen", return_value=Response(body)):
                    with self.assertRaisesRegex(ValueError, message):
                        post_json("https://api.usaspending.gov/api/v2/recipient/", {})

    def test_redirects_to_non_official_https_authorities_are_rejected(self):
        for url in ("http://api.usaspending.gov/", "https://api.usaspending.gov.example.com/",
                    "https://api.usaspending.gov:444/", "https://user@api.usaspending.gov/"):
            with self.subTest(url=url):
                with patch("market_checker_app.services.usaspending_request_budget.urlopen", return_value=Response(b"{}", url)):
                    with self.assertRaisesRegex(ValueError, "official host"):
                        post_json("https://api.usaspending.gov/api/v2/recipient/", {})

    def test_non_official_initial_url_is_rejected_before_network_io(self):
        with patch("market_checker_app.services.usaspending_request_budget.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "official host"):
                post_json("https://example.com/api/", {})
            urlopen.assert_not_called()

    def test_redirect_is_rejected_before_following_external_authority(self):
        request = Request("https://api.usaspending.gov/api/v2/recipient/")
        with self.assertRaisesRegex(ValueError, "official host"):
            _OfficialRedirectHandler().redirect_request(request, None, 302, "Found", {}, "https://example.com/")

    def test_incomplete_declared_response_cannot_return_a_valid_json_negative(self):
        response = Response(b'{"results": [], "page_metadata": {"hasNext": false}}')
        response.length = 100
        with patch("market_checker_app.services.usaspending_request_budget.urlopen", return_value=response):
            with self.assertRaises(IncompleteRead):
                post_json("https://api.usaspending.gov/api/v2/recipient/", {})

    def test_non_finite_json_values_are_rejected_as_malformed_responses(self):
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(token=token), \
                 patch("market_checker_app.services.usaspending_request_budget.urlopen",
                       return_value=Response(b'{"amount":' + token + b'}')):
                with self.assertRaisesRegex(ValueError, "Non-finite"):
                    post_json("https://api.usaspending.gov/api/v2/recipient/", {})

    def test_streamed_response_cannot_run_past_its_deadline(self):
        with patch("market_checker_app.services.usaspending_request_budget.urlopen", return_value=Response(b"{}")), \
             patch("market_checker_app.services.usaspending_request_budget.time.monotonic", side_effect=[0, 0, 10]):
            with self.assertRaises(TimeoutError):
                post_json("https://api.usaspending.gov/api/v2/recipient/", {}, timeout=3)

    def test_collector_configuration_is_bounded_and_rejects_booleans(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scouts.db")
            client = Awards(lambda uei, number: page())
            for settings in ({"max_pages": True}, {"max_pages": 21}, {"max_uei": 101},
                             {"max_requests": 0}, {"max_run_seconds": float("inf")},
                             {"daily_request_limit": 121}, {"refresh_days": 0}):
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    UsaSpendingScoutService(store, client=client, identities=[identity()], **settings)
            with self.assertRaises(ValueError):
                UsaSpendingRecipientDiscovery(store, client=object(), max_subjects=False)

    def test_live_provider_lease_blocks_duplicate_collection_without_network(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scouts.db")
            token = store.claim_provider("usaspending", as_of=CLOCK)
            self.assertIsNotNone(token)
            client = Awards(lambda uei, number: page())
            result = UsaSpendingScoutService(store, client=client, identities=[identity()]).run(as_of=CLOCK)
            self.assertEqual("BUSY", result["status"])
            self.assertEqual(0, result["attempted_requests"])
            self.assertEqual([], client.calls)
            store.release_provider("usaspending", token)

    def test_expired_run_budget_never_calls_the_client(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scouts.db")
            client = Awards(lambda uei, number: page())
            with patch("market_checker_app.services.usaspending_request_budget.UsaSpendingRunBudget.remaining", return_value=0):
                result = UsaSpendingScoutService(store, client=client, identities=[identity()]).run(as_of=CLOCK)
            self.assertEqual([], client.calls)
            self.assertEqual(0, result["attempted_requests"])
            self.assertEqual("RUN_BUDGET", result["stop_reason"])
            self.assertEqual(1, result["coverage_unchecked_uei"])

    def test_deadline_expires_while_reserving_request_without_starting_network_io(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scouts.db")
            client = Awards(lambda uei, number: page())
            original = UsaSpendingCheckpointStore.reserve
            expired = False

            def reserve(checkpoints, *args, **kwargs):
                nonlocal expired
                reservation = original(checkpoints, *args, **kwargs)
                expired = True
                return reservation

            with patch.object(UsaSpendingCheckpointStore, "reserve", new=reserve), \
                 patch("market_checker_app.services.usaspending_request_budget.UsaSpendingRunBudget.remaining",
                       side_effect=lambda: 0 if expired else 60):
                result = UsaSpendingScoutService(store, client=client, identities=[identity()]).run(as_of=CLOCK)
            self.assertEqual([], client.calls)
            self.assertEqual("RUN_BUDGET", result["stop_reason"])
            self.assertEqual(1, result["attempted_requests"])
            self.assertEqual(1, result["daily_requests_used"])
            self.assertEqual(0, result["completed_uei"])


if __name__ == "__main__":
    unittest.main()
