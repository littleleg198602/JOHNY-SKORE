from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.cms_owner_capture_service import capture_cms_owners
from market_checker_app.services.public_source_budget import PublicSourceBudget
from market_checker_app.storage.scout_store import ScoutStore

NOW = datetime(2026, 10, 13, 21, tzinfo=timezone.utc)


def row(identifier):
    return {"ENROLLMENT ID": identifier, "ORGANIZATION NAME - OWNER": "OWNER",
            "TYPE - OWNER": "O", "ROLE CODE - OWNER": "35"}


class Client:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def page(self, name, *, size, cursor):
        self.calls.append(cursor)
        if cursor == self.fail and self.fail is not None:
            raise OSError("interrupted")
        index = int(cursor or 0)
        return ([row(str(index))], str(index + 1)) if index < 2 else ([], None)


class CmsOwnerCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "capture.json"
        self.store = ScoutStore(Path(self.tmp.name) / "history.db")

    def capture(self, client, **changes):
        args = dict(subject_id="HCA", query_name="OWNER", checkpoint_path=self.path,
                    page_size=1, max_pages=5, as_of=NOW, client=client)
        args.update(changes)
        return capture_cms_owners(self.store, **args)

    def test_failure_keeps_page_and_restart_resumes_without_first_page(self):
        with self.assertRaises(OSError):
            self.capture(Client(fail="1"))
        checkpoint = json.loads(self.path.read_text())
        self.assertEqual("1", checkpoint["next_cursor"])
        self.assertEqual("0", checkpoint["pages"][0]["rows"][0]["ENROLLMENT ID"])
        same_day = Client()
        self.assertFalse(self.capture(same_day)["complete"])
        self.assertEqual([], same_day.calls)
        resumed = Client()
        summary = self.capture(resumed, as_of=NOW + timedelta(days=1))
        self.assertEqual(["1", "2"], resumed.calls)
        self.assertTrue(summary["complete"])
        self.assertEqual(2, summary["captured_rows"])
        self.assertFalse(summary["issuer_attribution_allowed"])
        self.assertFalse(summary["pagination_snapshot_guaranteed"])
        self.assertEqual(2, self.store.public_daily_usage("cms", as_of=NOW)["daily_requests"])
        self.assertEqual(2, self.store.public_daily_usage("cms", as_of=NOW + timedelta(days=1))["daily_requests"])

    def test_complete_capture_has_no_more_requests_on_replay(self):
        self.capture(Client())
        client = Client()
        self.assertTrue(self.capture(client)["complete"])
        self.assertEqual([], client.calls)

    def test_changed_query_and_tampering_are_rejected_before_network(self):
        self.capture(Client(), max_pages=1)
        client = Client()
        with self.assertRaises(ValueError):
            self.capture(client, query_name="OTHER")
        state = json.loads(self.path.read_text())
        state["next_cursor"] = "9"
        self.path.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            self.capture(client)
        self.assertEqual([], client.calls)

    def test_shared_quota_survives_another_capture_invocation(self):
        budget = PublicSourceBudget(self.store, "cms", as_of=NOW, max_run_seconds=120)
        self.assertTrue(budget.acquire())
        try:
            for _ in range(20):
                budget.call("HCA", "seed", lambda: [])
        finally:
            budget.release()
        client = Client()
        result = self.capture(client)
        self.assertFalse(result["complete"])
        self.assertEqual([], client.calls)
        self.assertEqual(20, result["daily_requests"])
        self.assertFalse(self.path.exists())

    def test_bad_page_cannot_replace_saved_cursor(self):
        self.capture(Client(), max_pages=1)
        before = self.path.read_bytes()
        class BadClient:
            def page(self, *args, **kwargs):
                return [dict(row("1"), **{"ORGANIZATION NAME - OWNER": "OTHER"})], "2"
        with self.assertRaises(ValueError):
            self.capture(BadClient(), as_of=NOW + timedelta(days=1))
        self.assertEqual(before, self.path.read_bytes())

    def test_changed_checkpoint_between_read_and_lease_is_not_overwritten(self):
        self.capture(Client(), max_pages=1)
        original = self.store.claim_provider
        def claim(*args, **kwargs):
            state = json.loads(self.path.read_text())
            state["pages"][0]["observed_at"] = "2026-10-13T21:00:01+00:00"
            self.path.write_text(json.dumps(state))
            return original(*args, **kwargs)
        client = Client()
        with patch.object(self.store, "claim_provider", side_effect=claim):
            with self.assertRaisesRegex(ValueError, "changed during lease"):
                self.capture(client, as_of=NOW + timedelta(days=1))
        self.assertEqual([], client.calls)

    def test_database_hardlink_cannot_be_used_as_checkpoint(self):
        self.path.hardlink_to(self.store.db_path)
        before = self.store.db_path.read_bytes()
        with self.assertRaises(ValueError):
            self.capture(Client())
        self.assertEqual(before, self.store.db_path.read_bytes())
