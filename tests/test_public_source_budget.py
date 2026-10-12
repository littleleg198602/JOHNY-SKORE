from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.fda_recall_scout_service import FdaRecallScoutService
from market_checker_app.services.healthcare_identity_service import HealthcareIdentityRegistry
from market_checker_app.services.healthcare_scout_service import (
    CMS_URL, TRIALS_URL, HealthcareNameScoutService,
)
from market_checker_app.services.doj_scout_service import DojPressReleaseScoutService
from market_checker_app.services.epa_echo_scout_service import EpaEchoScoutService
from market_checker_app.services.public_source_budget import (
    PUBLIC_DAILY_LIMITS, PublicBudgetExhausted, PublicSourceBudget,
)
from market_checker_app.services.specialist_acceptance_service import classify_source_run
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records

NOW = datetime(2026, 10, 11, 10, tzinfo=timezone.utc)


class EmptyPublicClient:
    def __init__(self, source, error=None):
        self.source, self.error, self.calls = source, error, []
        self.source_url = CMS_URL if source == "cms" else TRIALS_URL

    def answer(self, name):
        self.calls.append(name)
        if self.error:
            raise self.error

    def recalls(self, product_type, name, *, limit, skip=0):
        self.answer(name)
        return {"results": [], "meta": {"results": {"total": 0}}}

    def complete_response_letters(self, name, *, limit, skip=0):
        return self.recalls("crl", name, limit=limit, skip=skip)

    def page(self, name, *, size, cursor=None, page=None):
        self.answer(name)
        return [], 0 if self.source == "doj" else None

    def facilities(self, name):
        self.answer(name)
        return [], 0


class PublicSourceBudgetTests(unittest.TestCase):
    def store(self, directory, tickers=("AAPL", "MSFT")):
        store = ScoutStore(Path(directory) / "test.db")
        for index, ticker in enumerate(tickers, 1):
            store.observe_sec_identity(subject_id=ticker, cik=f"{index:010d}",
                                       company_name=f"Synthetic {ticker} Inc.", as_of=NOW)
        return store

    def reserve(self, store, token, ticker="AAPL", clock=NOW, requests=4, issuers=2):
        return store.reserve_public_request(
            "fda", subject_id=ticker, identity_key=f"synthetic:{ticker}", as_of=clock,
            daily_issuer_limit=issuers, daily_request_limit=requests, lease_token=token)

    def test_atomic_request_limit_with_concurrent_connections(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            other = ScoutStore(store.db_path)
            token = store.claim_provider("fda", as_of=NOW)
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda i: self.reserve(store if i % 2 else other, token), range(12)))
            self.assertEqual(4, sum(r["status"] == "RESERVED" for r in results))
            self.assertEqual({"daily_requests": 4, "daily_issuers": 1}, store.public_daily_usage("fda", as_of=NOW))

    def test_distinct_issuer_limit_and_pages_share_one_subject(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            token = store.claim_provider("fda", as_of=NOW)
            self.assertEqual("RESERVED", self.reserve(store, token, issuers=1)["status"])
            self.assertEqual("RESERVED", self.reserve(store, token, issuers=1)["status"])
            self.assertEqual("WAIT_QUOTA", self.reserve(store, token, "MSFT", issuers=1)["status"])
            self.assertEqual({"daily_requests": 2, "daily_issuers": 1}, store.public_daily_usage("fda", as_of=NOW))

    def test_restart_running_request_and_identity_changes_do_not_reset_quota(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            token = store.claim_provider("fda", as_of=NOW, seconds=1)
            reservation = self.reserve(store, token, requests=1)
            reopened = ScoutStore(store.db_path)
            next_clock = NOW + timedelta(seconds=2)
            new_token = reopened.claim_provider("fda", as_of=next_clock)
            self.assertEqual("LEASE_LOST", self.reserve(reopened, token, "MSFT", next_clock)["status"])
            self.assertEqual("WAIT_QUOTA", reopened.reserve_public_request(
                "fda", subject_id="AAPL", identity_key="changed-name-and-cik", as_of=next_clock,
                daily_issuer_limit=2, daily_request_limit=4, lease_token=new_token)["status"])
            with reopened._connect() as conn:
                row = conn.execute("SELECT outcome FROM scout_public_requests WHERE request_id=?",
                                   (reservation["request_id"],)).fetchone()
            self.assertEqual("RUNNING", row[0])
            self.assertEqual(0, reopened.specialist_coverage("fda", as_of=next_clock)["ever_checked"])
            reopened.release_provider("fda", new_token)
            day = NOW + timedelta(days=1)
            next_token = reopened.claim_provider("fda", as_of=day)
            self.assertEqual("RESERVED", self.reserve(reopened, next_token, clock=day, requests=1)["status"])

    def test_every_fda_endpoint_page_is_charged_and_interrupted_issuer_stays_due(self):
        with TemporaryDirectory() as directory, patch.dict(PUBLIC_DAILY_LIMITS, {"fda": (3, 6)}):
            store = self.store(directory)
            client = EmptyPublicClient("fda")
            result = FdaRecallScoutService(store, client=client).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1, 6, 2),
                             (result["status"], result["checked_issuers"], result["attempted_requests"], result["attempted_issuers"]))
            self.assertEqual(6, len(client.calls))
            reopened = ScoutStore(store.db_path)
            versions = HealthcareIdentityRegistry.load().identity_versions(reopened, "fda", as_of=NOW)
            self.assertEqual(["MSFT"], [row["subject_id"] for row in reopened.specialist_due(
                "fda", as_of=NOW, limit=10, identity_versions=versions)])
            again = FdaRecallScoutService(reopened, client=client).run(as_of=NOW)
            self.assertEqual(("WAIT_QUOTA", 0), (again["status"], again["attempted_requests"]))
            self.assertEqual(6, len(client.calls))
            self.assertFalse(classify_source_run(again)["complete"])
            self.assertFalse(classify_source_run(again)["attempted"])
            next_day = FdaRecallScoutService(reopened, client=client).run(as_of=NOW + timedelta(days=1))
            self.assertEqual(("OK", 1, 4), (next_day["status"], next_day["checked_issuers"], next_day["daily_requests"]))

    def test_all_five_collectors_share_restart_persistent_limits(self):
        cases = (
            ("fda", ("AAPL", "MSFT"), FdaRecallScoutService, 4),
            ("cms", ("HCA", "UHS"), HealthcareNameScoutService, 1),
            ("clinicaltrials", ("LLY", "PFE"), HealthcareNameScoutService, 1),
            ("doj", ("AAPL", "MSFT"), DojPressReleaseScoutService, 1),
            ("epa", ("DD", "DOW"), EpaEchoScoutService, 1),
        )
        for source, tickers, service_class, per_issuer in cases:
            with self.subTest(source=source), TemporaryDirectory() as directory, patch.dict(
                    PUBLIC_DAILY_LIMITS, {source: (1, per_issuer)}):
                store = self.store(directory, tickers)
                client = EmptyPublicClient(source)
                result = service_class(store, client=client).run(as_of=NOW, universe=set(tickers))
                self.assertEqual(("PARTIAL", 1, per_issuer),
                                 (result["status"], result["checked_issuers"], result["daily_requests"]))
                reopened = ScoutStore(store.db_path)
                again = service_class(reopened, client=client).run(as_of=NOW, universe={tickers[1]})
                self.assertEqual(("WAIT_QUOTA", 0), (again["status"], again["attempted_requests"]))
                self.assertEqual(per_issuer, len(client.calls))
                tomorrow = service_class(reopened, client=client).run(as_of=NOW + timedelta(days=1))
                self.assertEqual(("OK", 1), (tomorrow["status"], tomorrow["checked_issuers"]))

    def test_provider_lease_prevents_overlapping_network_calls(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            other = ScoutStore(store.db_path)
            competing_client = EmptyPublicClient("fda")
            competitor = FdaRecallScoutService(other, client=competing_client)
            statuses = []
            class Overlapping(EmptyPublicClient):
                def answer(self, name):
                    statuses.append(competitor.run(as_of=NOW)["status"])
                    super().answer(name)
            first = FdaRecallScoutService(store, client=Overlapping("fda")).run(as_of=NOW)
            self.assertEqual("OK", first["status"])
            self.assertTrue(statuses and all(status == "BUSY" for status in statuses))
            self.assertEqual([], competing_client.calls)
            self.assertIsNotNone(store.claim_provider("fda", as_of=NOW))

    def test_rate_limit_and_retry_after_survive_restart_without_new_requests(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            client = EmptyPublicClient("fda", HTTPError("https://api.fda.gov", 429, "Limited",
                                                       {"Retry-After": "7200"}, None))
            first = FdaRecallScoutService(store, client=client).run(as_of=NOW)
            retry = datetime.fromisoformat(first["retry_at"])
            self.assertGreaterEqual(retry, NOW + timedelta(hours=2))
            reopened = ScoutStore(store.db_path)
            again = FdaRecallScoutService(reopened, client=client).run(as_of=NOW + timedelta(hours=1))
            self.assertEqual(("RATE_LIMITED", 0, 1),
                             (again["status"], again["attempted_requests"], again["daily_requests"]))
            self.assertEqual(1, len(client.calls))
            self.assertFalse(classify_source_run(again)["attempted"])
            self.assertEqual(0, reopened.specialist_coverage("fda", as_of=NOW)["ever_checked"])

    def test_failed_subjects_rotate_after_never_attempted_on_next_day(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory, ("AAPL", "MSFT", "NVDA"))
            client = EmptyPublicClient("fda", OSError("Synthetic outage"))
            first = FdaRecallScoutService(store, client=client, max_failures=1).run(as_of=NOW)
            again = FdaRecallScoutService(ScoutStore(store.db_path), client=client, max_failures=1).run(as_of=NOW)
            self.assertEqual(1, first["attempted_requests"])
            self.assertEqual(1, again["attempted_requests"])
            self.assertEqual(["Synthetic AAPL Inc.", "Synthetic MSFT Inc."], client.calls)
            third = FdaRecallScoutService(store, client=client, max_failures=1).run(as_of=NOW + timedelta(days=1))
            self.assertEqual(1, third["attempted_requests"])
            self.assertEqual("Synthetic NVDA Inc.", client.calls[-1])
            self.assertEqual(0, store.specialist_coverage("fda", as_of=NOW + timedelta(days=1))["ever_checked"])

    def test_requests_crossing_midnight_use_each_utc_day_and_providers_are_independent(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            before = NOW.replace(hour=23, minute=59, second=59)
            with patch("market_checker_app.services.public_source_budget.time.perf_counter", return_value=0) as perf:
                budget = PublicSourceBudget(store, "fda", as_of=before, max_run_seconds=60)
                self.assertTrue(budget.acquire())
                budget.call("AAPL", "synthetic:AAPL", lambda: None)
                perf.return_value = 2
                budget.call("AAPL", "synthetic:AAPL", lambda: None)
                budget.release()
            self.assertEqual({"daily_requests": 1, "daily_issuers": 1}, store.public_daily_usage("fda", as_of=before))
            self.assertEqual({"daily_requests": 1, "daily_issuers": 1}, store.public_daily_usage("fda", as_of=before + timedelta(seconds=2)))
            self.assertEqual({"daily_requests": 0, "daily_issuers": 0}, store.public_daily_usage("cms", as_of=before))
            local = before.astimezone(timezone(timedelta(hours=2)))
            self.assertEqual(1, store.public_daily_usage("fda", as_of=local)["daily_requests"])

    def test_lease_expiry_stops_request_and_exception_releases_provider(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            with patch("market_checker_app.services.public_source_budget.time.perf_counter", return_value=0) as perf:
                budget = PublicSourceBudget(store, "fda", as_of=NOW, max_run_seconds=1)
                self.assertTrue(budget.acquire())
                perf.return_value = 27
                with self.assertRaises(PublicBudgetExhausted):
                    budget.call("AAPL", "synthetic:AAPL", lambda: self.fail("I/O after lease expiry"))
                self.assertEqual("LEASE_LOST", budget.annotate({"status": "PARTIAL"})["status"])
                budget.release()
            class Interrupted(EmptyPublicClient):
                def answer(self, name):
                    raise KeyboardInterrupt("Synthetic interruption")
            with self.assertRaises(KeyboardInterrupt):
                FdaRecallScoutService(store, client=Interrupted("fda")).run(as_of=NOW)
            self.assertIsNotNone(store.claim_provider("fda", as_of=NOW))
            with store._connect() as conn:
                self.assertEqual("FAILED", conn.execute("SELECT outcome FROM scout_public_requests").fetchone()[0])

    def test_scope_before_budget_and_telemetry_survive_persistence(self):
        with TemporaryDirectory() as directory:
            store = self.store(directory)
            client = EmptyPublicClient("fda")
            result = FdaRecallScoutService(store, client=client).run(as_of=NOW, universe={"MSFT"})
            self.assertEqual(["Synthetic MSFT Inc."] * 4, client.calls)
            store.record_source_run("fda", as_of=NOW, summary=result)
            saved = ScoutStore(store.db_path).latest_source_runs()["fda"]
            for field in ("daily_requests", "daily_issuers", "attempted_requests", "attempted_issuers",
                          "daily_request_limit", "daily_issuer_limit", "quota_deferred_issuers"):
                self.assertEqual(result[field], saved[field])
            self.assertNotIn("run_token", json.dumps(saved))
            self.assertEqual(687, len(load_canonical_ticker_records()))
            for status in ("WAIT_QUOTA", "BUSY", "NOT_APPLICABLE"):
                semantic = classify_source_run({"status": status, "attempted_requests": 0})
                self.assertEqual("NOT_ATTEMPTED", semantic["classification"])
                self.assertFalse(semantic["result_usable"])

    def test_actual_collector_rotates_canonical_687_with_restarts_and_repeated_daily_runs(self):
        # Offline empty publisher fixtures exercise collection and persistent
        # quotas, not live coverage, identity acceptance or historical skill.
        tickers = [row["ticker"] for row in load_canonical_ticker_records()]
        with TemporaryDirectory() as directory:
            store = self.store(directory, tickers)
            client = EmptyPublicClient("fda")
            for day in range(18):
                clock = NOW + timedelta(days=day)
                store = ScoutStore(store.db_path)
                first = FdaRecallScoutService(store, client=client, max_subjects=100).run(as_of=clock)
                count = 40 if day < 17 else 7
                self.assertEqual(count, first["checked_issuers"])
                self.assertEqual(count * 4, first["daily_requests"])
                before = len(client.calls)
                repeated = FdaRecallScoutService(ScoutStore(store.db_path), client=client, max_subjects=100).run(as_of=clock)
                self.assertEqual(0, repeated["attempted_requests"])
                self.assertEqual(before, len(client.calls))
            self.assertEqual(687 * 4, len(client.calls))
            versions = HealthcareIdentityRegistry.load().identity_versions(store, "fda", as_of=clock)
            self.assertEqual(687, store.specialist_coverage(
                "fda", as_of=clock, identity_versions=versions)["current_complete"])
            self.assertEqual({f"Synthetic {ticker} Inc." for ticker in tickers}, set(client.calls))


if __name__ == "__main__":
    unittest.main()
