from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from market_checker_app.services.public_source_budget import (
    PUBLIC_DAILY_LIMITS, PublicBudgetExhausted, PublicSourceBudget,
)
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 14, 0, 10, tzinfo=timezone.utc)


class Batch5SharedBudgetTests(unittest.TestCase):
    def test_nhtsa_and_ir_reservations_share_the_existing_durable_ledger(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "existing.db")
            prior = store.claim_provider("fda", as_of=NOW, seconds=3600)
            reserved = store.reserve_public_request(
                "fda", subject_id="SYNTHETIC_OLD", identity_key="old-scope",
                as_of=NOW, daily_issuer_limit=40, daily_request_limit=320, lease_token=prior)
            store.finish_public_request(reserved["request_id"], outcome="RETURNED")
            store.release_provider("fda", prior)
            for source, limit in (("nhtsa", 8), ("rss", 6)):
                with self.subTest(source=source):
                    budget = PublicSourceBudget(store, source, as_of=NOW, max_run_seconds=30)
                    self.assertTrue(budget.acquire())
                    for index in range(limit):
                        if index == 0:
                            def failed():
                                raise OSError("synthetic transport failure")
                            with self.assertRaises(OSError):
                                budget.call("SYNTHETIC_SUBJECT", "first-scope", failed)
                        else:
                            self.assertEqual(index, budget.call("SYNTHETIC_SUBJECT", str(index), lambda: index))
                    with self.assertRaises(PublicBudgetExhausted):
                        budget.call("OTHER_SUBJECT", "changed-scope", lambda: self.fail("quota allowed I/O"))
                    budget.release()
                    reopened = ScoutStore(store.db_path)
                    self.assertEqual(limit, reopened.public_daily_usage(source, as_of=NOW)["daily_requests"])
                    with reopened._connect() as conn:
                        self.assertEqual(1, conn.execute(
                            "SELECT COUNT(*) FROM scout_public_requests WHERE source=? AND outcome='FAILED'", (source,)
                        ).fetchone()[0])
            self.assertEqual({"daily_requests": 1, "daily_issuers": 1}, store.public_daily_usage("fda", as_of=NOW))
            self.assertEqual((40, 320), PUBLIC_DAILY_LIMITS["fda"])

    def test_restart_and_changed_scope_cannot_retry_the_same_subject_on_the_same_day(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "existing.db"
            for source in ("nhtsa", "rss"):
                with self.subTest(source=source):
                    first = PublicSourceBudget(ScoutStore(path), source, as_of=NOW, max_run_seconds=30)
                    self.assertTrue(first.acquire())
                    first.call("SYNTHETIC_SUBJECT", "scope-a", lambda: None)
                    first.release()
                    second = PublicSourceBudget(ScoutStore(path), source, as_of=NOW, max_run_seconds=30)
                    self.assertTrue(second.acquire())
                    try:
                        with self.assertRaises(PublicBudgetExhausted):
                            second.call("SYNTHETIC_SUBJECT", "scope-b", lambda: self.fail("restart allowed I/O"))
                        self.assertEqual("WAIT_QUOTA", second.stop_status)
                    finally:
                        second.release()
                    next_day = PublicSourceBudget(ScoutStore(path), source, as_of=NOW + timedelta(days=1), max_run_seconds=30)
                    self.assertTrue(next_day.acquire())
                    try:
                        next_day.call("SYNTHETIC_SUBJECT", "scope-b", lambda: None)
                        self.assertEqual(1, next_day.store.public_daily_usage(source, as_of=NOW)["daily_requests"])
                        self.assertEqual(1, next_day.store.public_daily_usage(source, as_of=NOW + timedelta(days=1))["daily_requests"])
                    finally:
                        next_day.release()

    def test_lease_loss_prevents_io_and_http429_cooldown_is_shared_after_reopen(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "existing.db"
            for source in ("nhtsa", "rss"):
                with self.subTest(source=source):
                    store = ScoutStore(path)
                    budget = PublicSourceBudget(store, source, as_of=NOW, max_run_seconds=30)
                    self.assertTrue(budget.acquire())
                    store.release_provider(source, budget.token)
                    with self.assertRaises(PublicBudgetExhausted):
                        budget.call("SYNTHETIC_SUBJECT", "scope", lambda: self.fail("lost lease allowed I/O"))
                    self.assertEqual("LEASE_LOST", budget.stop_status)
                    self.assertEqual(0, store.public_daily_usage(source, as_of=NOW)["daily_requests"])
                    budget.release()
                    active = PublicSourceBudget(store, source, as_of=NOW, max_run_seconds=30)
                    self.assertTrue(active.acquire())
                    try:
                        def limited():
                            raise HTTPError("https://example.invalid/synthetic", 429, "synthetic", {"Retry-After": "7200"}, None)
                        with self.assertRaises(HTTPError):
                            active.call("SYNTHETIC_SUBJECT", "scope", limited)
                    finally:
                        active.release()
                    reopened = PublicSourceBudget(ScoutStore(path), source, as_of=NOW + timedelta(minutes=1), max_run_seconds=30)
                    self.assertFalse(reopened.acquire())
                    self.assertEqual("RATE_LIMITED", reopened.blocked_summary()["status"])
                    self.assertEqual(1, reopened.store.public_daily_usage(source, as_of=NOW)["daily_requests"])


if __name__ == "__main__":
    unittest.main()
