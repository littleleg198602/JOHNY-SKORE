from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.usaspending_scout_service import UsaSpendingScoutService, load_verified_identities
from market_checker_app.services.usaspending_recipient_discovery import UsaSpendingRecipientDiscovery
from market_checker_app.storage.scout_store import ScoutStore


CLOCK = datetime(2026, 9, 30, tzinfo=timezone.utc)


def identity(ticker="AAA", uei="ABCDEFGHIJKL", name="AAA INC"):
    return {"ticker": ticker, "uei": uei, "recipient_name": name,
            "uei_evidence_url": "https://www.usaspending.gov/award/example",
            "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/example",
            "effective_from": "2025-01-01", "effective_to": None,
            "known_at": "2026-09-01T00:00:00+00:00"}


def award(entry, object_id="CONT_A_1", **fields):
    return {"generated_internal_id": object_id, "Award ID": object_id,
            "Recipient UEI": entry["uei"], "Recipient Name": entry["recipient_name"],
            "Start Date": "2026-08-01", "Award Amount": 100,
            "Total Outlays": 0, "Last Modified Date": "2026-09-29", **fields}


def page(rows=(), has_next=False, **meta):
    return {"results": list(rows), "page_metadata": {"hasNext": has_next, **meta}}


class Awards:
    def __init__(self, callback):
        self.callback, self.calls = callback, []

    def awards(self, uei, *, start, end, page):
        self.calls.append((uei, start, end, page))
        return self.callback(uei, page)


class Names:
    def __init__(self, callback):
        self.callback, self.calls = callback, []

    def recipients(self, name, *, page):
        self.calls.append((name, page))
        return self.callback(name, page)


class ResumableContractsTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "scouts.db"
        self.store = ScoutStore(self.db_path)
        self.entry = identity()

    def records(self, ticker="AAA", as_of=CLOCK):
        return self.store.findings_as_of(ticker, as_of=as_of)

    def test_fair_per_uei_budget_reaches_every_eligible_identity(self):
        entries = [self.entry, identity("BBB", "MNOPQRSTUVWX", "BBB INC"),
                   identity("CCC", "ZYXWVUTSRQPO", "CCC INC")]
        client = Awards(lambda uei, number: page())
        service = UsaSpendingScoutService(self.store, client=client, identities=entries,
                                         max_uei=1, max_requests=1)
        results = [service.run(as_of=CLOCK, universe={"AAA", "BBB", "CCC", "ZZZ"}) for _ in entries]
        self.assertEqual(3, len({call[0] for call in client.calls}))
        self.assertEqual([1, 2, 3], [row["coverage_complete_uei"] for row in results])
        self.assertEqual("PARTIAL", results[0]["status"])
        self.assertEqual("OK", results[-1]["status"])
        self.assertEqual(1, results[-1]["identity_unmapped_tickers"])
        self.assertFalse(results[-1]["issuer_coverage_complete"])
        cached = service.run(as_of=CLOCK)
        self.assertEqual(0, cached["attempted_uei"])
        self.assertEqual(0, cached["attempted_requests"])

    def test_restart_resumes_the_next_page_of_the_pinned_window(self):
        client = Awards(lambda uei, number: page([award(self.entry, f"CONT_A_{number}")], number == 1))
        first = UsaSpendingScoutService(self.store, client=client, identities=[self.entry],
                                       max_pages=1).run(as_of=CLOCK)
        later = CLOCK + timedelta(days=1)
        restarted = ScoutStore(self.db_path)
        second = UsaSpendingScoutService(restarted, client=client, identities=[self.entry],
                                        max_pages=1).run(as_of=later)
        self.assertEqual([1, 2], [call[3] for call in client.calls])
        self.assertEqual(client.calls[0][1:3], client.calls[1][1:3])
        self.assertEqual(1, second["resumed_uei"])
        self.assertEqual(1, second["terminal_uei"])
        self.assertEqual("PARTIAL", second["status"])
        self.assertEqual(0, second["completed_uei"])
        self.assertEqual(0, second["coverage_complete_uei"])
        self.assertFalse(second["pagination_snapshot_guaranteed"])
        self.assertEqual(1, first["new_findings"])
        self.assertEqual(2, len(self.records(as_of=later)))

    def test_later_page_failure_preserves_rows_and_does_not_block_another_uei(self):
        other = identity("BBB", "MNOPQRSTUVWX", "BBB INC")
        failed = True

        def response(uei, number):
            if uei == self.entry["uei"]:
                if number == 2 and failed:
                    raise OSError("temporary outage")
                return page([award(self.entry, f"CONT_A_{number}")], number == 1)
            return page([award(other, "CONT_B_1")])

        client = Awards(response)
        result = UsaSpendingScoutService(self.store, client=client, identities=[self.entry, other]).run(as_of=CLOCK)
        self.assertEqual(2, result["new_findings"])
        self.assertEqual(1, result["failed_uei"])
        self.assertEqual(1, result["completed_uei"])
        self.assertEqual(1, len(self.records()))
        self.assertEqual(1, len(self.records("BBB")))
        failed = False
        later = CLOCK + timedelta(hours=2)
        recovery = UsaSpendingScoutService(ScoutStore(self.db_path), client=client,
                                          identities=[self.entry, other]).run(as_of=later)
        self.assertEqual((self.entry["uei"], 2), (client.calls[-1][0], client.calls[-1][3]))
        self.assertEqual(1, recovery["new_findings"])
        self.assertEqual(0, recovery["completed_uei"])
        self.assertEqual(2, len(self.records(as_of=later)))

    def test_valid_rows_survive_bad_metadata_and_retry_is_idempotent(self):
        client = Awards(lambda uei, number: {"results": [award(self.entry)], "page_metadata": {}})
        service = UsaSpendingScoutService(self.store, client=client, identities=[self.entry])
        bad = service.run(as_of=CLOCK)
        self.assertEqual(1, bad["new_findings"])
        self.assertEqual(0, bad["completed_uei"])
        self.assertEqual(1, bad["failed_uei"])
        self.assertEqual(0, service.run(as_of=CLOCK)["attempted_requests"])
        client.callback = lambda uei, number: page([award(self.entry)])
        recovered = service.run(as_of=CLOCK + timedelta(hours=2))
        self.assertEqual(1, client.calls[-1][3])
        self.assertEqual(0, recovered["new_findings"])
        self.assertEqual(0, recovered["completed_uei"])
        self.assertEqual(1, recovered["resumed_uei"])
        self.assertEqual("PARTIAL", recovered["status"])

    def test_failure_before_first_usable_page_cannot_later_complete_negative(self):
        client = Awards(lambda uei, number: None)
        service = UsaSpendingScoutService(self.store, client=client, identities=[self.entry])
        failed = service.run(as_of=CLOCK)
        self.assertEqual(1, failed["attempted_uei"])
        self.assertEqual(1, failed["failed_uei"])
        self.assertEqual(0, failed["completed_uei"])
        client.callback = lambda uei, number: page()
        retry = service.run(as_of=CLOCK + timedelta(hours=2))
        self.assertEqual([1, 1], [call[3] for call in client.calls])
        self.assertEqual(1, retry["resumed_uei"])
        self.assertEqual(0, retry["completed_uei"])
        self.assertEqual(1, retry["coverage_partial_uei"])
        self.assertEqual("PARTIAL", retry["status"])

    def test_same_as_of_continuations_rotate_instead_of_starving_other_ueis(self):
        entries = [self.entry, identity("BBB", "MNOPQRSTUVWX", "BBB INC"),
                   identity("CCC", "ZYXWVUTSRQPO", "CCC INC")]
        client = Awards(lambda uei, number: page([award(next(e for e in entries if e["uei"] == uei),
                                                       f"CONT_A_{number}")], True))
        service = UsaSpendingScoutService(self.store, client=client, identities=entries,
                                         max_uei=1, max_pages=1, max_requests=1)
        for _ in range(9):
            result = service.run(as_of=CLOCK)
            self.assertEqual(1, result["attempted_uei"])
            self.assertEqual(0, result["completed_uei"])
        for entry in entries:
            self.assertEqual([1, 2, 3], [call[3] for call in client.calls if call[0] == entry["uei"]])

    def test_database_row_failure_preserves_prior_findings_and_leaves_cursor_for_replay(self):
        client = Awards(lambda uei, number: page([award(self.entry, "one"), award(self.entry, "two")]))
        original = self.store.record_finding
        writes = 0

        def failing(**kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise sqlite3.OperationalError("temporary write failure")
            return original(**kwargs)

        with patch.object(self.store, "record_finding", side_effect=failing):
            result = UsaSpendingScoutService(self.store, client=client, identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual(1, result["new_findings"])
        self.assertEqual(1, result["failed_uei"])
        self.assertEqual(0, result["completed_uei"])
        self.assertEqual(1, len(self.records()))
        later = CLOCK + timedelta(hours=2)
        retry = UsaSpendingScoutService(ScoutStore(self.db_path), client=client,
                                       identities=[self.entry]).run(as_of=later)
        self.assertEqual([1, 1], [call[3] for call in client.calls])
        self.assertEqual(1, retry["new_findings"])
        self.assertEqual(0, retry["completed_uei"])
        self.assertEqual(2, len(self.records(as_of=later)))

    def test_incomplete_http_response_is_a_failed_attempt_without_blocking_another_uei(self):
        other = identity("BBB", "MNOPQRSTUVWX", "BBB INC")

        def response(uei, number):
            if uei == self.entry["uei"]:
                raise IncompleteRead(b"partial", 100)
            return page([award(other)])

        result = UsaSpendingScoutService(self.store, client=Awards(response),
                                        identities=[self.entry, other]).run(as_of=CLOCK)
        self.assertEqual(2, result["attempted_uei"])
        self.assertEqual(1, result["failed_uei"])
        self.assertEqual(1, result["completed_uei"])
        self.assertEqual(1, result["new_findings"])

    def test_time_budget_during_row_persistence_preserves_first_row_and_replays_page(self):
        client = Awards(lambda uei, number: page([award(self.entry, "one"), award(self.entry, "two")]))
        original = self.store.record_finding
        timed_out = False

        def saved(**kwargs):
            nonlocal timed_out
            result = original(**kwargs)
            timed_out = True
            return result

        with patch.object(self.store, "record_finding", side_effect=saved), \
             patch("market_checker_app.services.usaspending_request_budget.UsaSpendingRunBudget.remaining",
                   side_effect=lambda: 0 if timed_out else 60):
            result = UsaSpendingScoutService(self.store, client=client, identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual("RUN_BUDGET", result["stop_reason"])
        self.assertEqual(1, result["attempted_requests"])
        self.assertEqual(1, result["new_findings"])
        self.assertEqual(0, result["completed_uei"])
        retry = UsaSpendingScoutService(ScoutStore(self.db_path), client=client,
                                       identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual([1, 1], [call[3] for call in client.calls])
        self.assertEqual(1, retry["new_findings"])
        self.assertEqual(0, retry["completed_uei"])
        self.assertEqual(2, len(self.records()))

    def test_rejected_exact_rows_never_become_a_complete_negative(self):
        bad_rows = [award(self.entry, "wrong-name", **{"Recipient Name": "OTHER INC"}),
                    award(self.entry, "wrong-uei", **{"Recipient UEI": "MNOPQRSTUVWX"}),
                    award(self.entry, "old", **{"Start Date": "2020-01-01"}),
                    award(self.entry, "bad-date", **{"Last Modified Date": "2026-09-29 garbage"}),
                    award(self.entry, "future", **{"Last Modified Date": "2026-10-01"}),
                    award(self.entry, "bad-amount", **{"Award Amount": float("nan")}),
                    award(self.entry, "boolean-amount", **{"Award Amount": True}),
                    None]
        result = UsaSpendingScoutService(self.store, client=Awards(lambda uei, number: page(bad_rows)),
                                        identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual(8, result["rejected_rows"])
        self.assertEqual(0, result["new_findings"])
        self.assertEqual(0, result["completed_uei"])
        self.assertEqual(1, result["coverage_partial_uei"])
        self.assertEqual("PARTIAL", result["status"])

    def test_interrupted_row_write_replays_without_losing_or_duplicating_prior_rows(self):
        client = Awards(lambda uei, number: page([award(self.entry, "one"), award(self.entry, "two")]))
        original = self.store.record_finding
        writes = 0

        def interrupted(**kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise KeyboardInterrupt()
            return original(**kwargs)

        with patch.object(self.store, "record_finding", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                UsaSpendingScoutService(self.store, client=client, identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual(1, len(self.records()))
        recovery = UsaSpendingScoutService(ScoutStore(self.db_path), client=client,
                                          identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual([1, 1], [call[3] for call in client.calls])
        self.assertEqual(1, recovery["new_findings"])
        self.assertEqual(2, len(self.records()))
        self.assertEqual(2, recovery["daily_requests_used"])
        self.assertEqual(0, recovery["completed_uei"])
        self.assertEqual(1, recovery["resumed_uei"])

    def test_daily_refresh_preserves_valid_modification_versions_and_as_of_visibility(self):
        current = award(self.entry)
        client = Awards(lambda uei, number: page([current]))
        service = UsaSpendingScoutService(self.store, client=client, identities=[self.entry])
        self.assertEqual(1, service.run(as_of=CLOCK)["new_findings"])
        self.assertEqual(0, service.run(as_of=CLOCK + timedelta(hours=1))["attempted_requests"])
        current["Award Amount"] = 120.0
        current["Last Modified Date"] = "2026-10-01"
        later = CLOCK + timedelta(days=1)
        self.assertEqual(1, service.run(as_of=later)["new_findings"])
        self.assertEqual(1, len(self.records()))
        self.assertEqual(2, len(self.records(as_of=later)))
        current["Award Amount"] = 120  # Same monetary value is not a modification.
        self.assertEqual(0, service.run(as_of=later + timedelta(days=1))["new_findings"])
        details = [json.loads(row["details_json"]) for row in self.records(as_of=later)]
        self.assertTrue(all(not row["modification_history_complete"] for row in details))

    def test_failure_budget_leaves_other_identities_unattempted_and_due(self):
        entries = [self.entry, identity("BBB", "MNOPQRSTUVWX", "BBB INC"),
                   identity("CCC", "ZYXWVUTSRQPO", "CCC INC")]
        def failure(uei, number):
            raise OSError("outage")
        client = Awards(failure)
        result = UsaSpendingScoutService(self.store, client=client, identities=entries,
                                        max_failures=1).run(as_of=CLOCK)
        self.assertEqual(1, result["attempted_uei"])
        self.assertEqual(1, result["attempted_requests"])
        self.assertEqual(2, result["coverage_unchecked_uei"])
        self.assertEqual("FAILURE_BUDGET", result["stop_reason"])

    def test_future_checkpoint_cannot_complete_a_historical_observation(self):
        client = Awards(lambda uei, number: page())
        service = UsaSpendingScoutService(self.store, client=client, identities=[self.entry])
        service.run(as_of=CLOCK + timedelta(days=1))
        earlier = service.run(as_of=CLOCK)
        self.assertEqual(0, earlier["attempted_requests"])
        self.assertEqual(0, earlier["coverage_complete_uei"])
        self.assertEqual(1, earlier["coverage_unchecked_uei"])
        self.assertEqual("PARTIAL", earlier["status"])

    def test_awards_and_discovery_share_persistent_daily_request_accounting(self):
        client = Awards(lambda uei, number: page())
        first = UsaSpendingScoutService(self.store, client=client, identities=[self.entry],
                                       daily_request_limit=1).run(as_of=CLOCK)
        self.assertEqual(1, first["attempted_requests"])
        self.store.observe_sec_identity(subject_id="AAA", cik="0000000123", company_name="AAA INC", as_of=CLOCK)
        recipients = Names(lambda name, number: page())
        discovery = UsaSpendingRecipientDiscovery(ScoutStore(self.db_path), client=recipients,
                                                  daily_request_limit=1)
        waiting = discovery.run(as_of=CLOCK)
        self.assertEqual("WAIT_QUOTA", waiting["status"])
        self.assertEqual(0, waiting["attempted_requests"])
        self.assertEqual([], recipients.calls)
        self.assertEqual(1, waiting["daily_requests_used"])
        self.assertEqual(1, discovery.run(as_of=CLOCK + timedelta(days=1))["completed_names"])

    def test_default_daily_quota_stops_shared_collectors_at_120_requests(self):
        entries = [identity(f"U{number}", f"{number:012d}", f"CONTROL {number} INC")
                   for number in range(120)]
        client = Awards(lambda uei, number: page())
        settings = {"client": client, "identities": entries, "max_uei": 100,
                    "max_requests": 100, "max_pages": 1}
        first = UsaSpendingScoutService(self.store, **settings).run(as_of=CLOCK)
        second = UsaSpendingScoutService(ScoutStore(self.db_path), **settings).run(as_of=CLOCK)
        self.assertEqual(100, first["attempted_requests"])
        self.assertEqual(20, second["attempted_requests"])
        self.assertEqual(120, second["daily_requests_used"])
        self.store.observe_sec_identity(subject_id="AAA", cik="0000000123", company_name="AAA INC", as_of=CLOCK)
        names = Names(lambda name, number: page())
        discovery = UsaSpendingRecipientDiscovery(ScoutStore(self.db_path), client=names)
        blocked = discovery.run(as_of=CLOCK)
        self.assertEqual("WAIT_QUOTA", blocked["status"])
        self.assertEqual(0, blocked["attempted_requests"])
        self.assertEqual(120, blocked["daily_request_limit"])
        self.assertEqual([], names.calls)
        self.assertEqual(1, discovery.run(as_of=CLOCK + timedelta(days=1))["completed_names"])

    def test_http_rate_limit_cools_down_both_collectors_without_false_negatives(self):
        def rate_limit(uei, number):
            raise HTTPError("https://api.usaspending.gov/", 429, "Too many requests", {"Retry-After": "7200"}, None)
        result = UsaSpendingScoutService(self.store, client=Awards(rate_limit),
                                        identities=[self.entry]).run(as_of=CLOCK)
        self.assertEqual("RATE_LIMITED", result["status"])
        self.assertEqual(1, result["failed_uei"])
        self.assertEqual(0, result["completed_uei"])
        self.store.observe_sec_identity(subject_id="AAA", cik="0000000123", company_name="AAA INC", as_of=CLOCK)
        names = Names(lambda name, number: page())
        blocked = UsaSpendingRecipientDiscovery(self.store, client=names).run(as_of=CLOCK)
        self.assertEqual("RATE_LIMITED", blocked["status"])
        self.assertEqual(0, blocked["attempted_requests"])
        self.assertEqual([], names.calls)
        self.assertTrue(blocked["retry_at"].startswith("2026-09-30T02:00:"))


class ResumableRecipientTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ScoutStore(Path(self.directory.name) / "scouts.db")
        self.store.observe_sec_identity(subject_id="AAA", cik="0000000123", company_name="AAA INC", as_of=CLOCK)

    def test_discovery_continuation_remains_name_only_and_partial_not_monthly_complete(self):
        def response(name, number):
            return page([{"id": f"id-{number}", "uei": "ABCDEFGHIJKL", "name": name}], number == 1)
        names = Names(response)
        discovery = UsaSpendingRecipientDiscovery(self.store, client=names, max_pages=1)
        first = discovery.run(as_of=CLOCK)
        second = UsaSpendingRecipientDiscovery(ScoutStore(self.store.db_path), client=names,
                                              max_pages=1).run(as_of=CLOCK)
        self.assertEqual([("AAA INC", 1), ("AAA INC", 2)], names.calls)
        self.assertEqual(1, first["new_candidates"])
        self.assertEqual(1, second["new_candidates"])
        self.assertEqual(0, second["completed_names"])
        self.assertEqual("PARTIAL", second["status"])
        self.assertEqual([{"subject_id": "AAA", "company_name": "AAA INC"}],
                         self.store.recipient_discovery_due(as_of=CLOCK))
        rows = self.store.findings_as_of("AAA", as_of=CLOCK)
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["verification_status"] == "UNVERIFIED" for row in rows))
        self.assertTrue(all(not json.loads(row["details_json"])["contract_attribution_allowed"] for row in rows))
        self.assertEqual(1, discovery.run(as_of=CLOCK + timedelta(days=1))["attempted_requests"])
        self.assertEqual(("AAA INC", 1), names.calls[-1])

    def test_rejected_exact_candidate_does_not_become_complete_negative(self):
        names = Names(lambda name, number: page([{"id": "bad", "uei": "BAD", "name": name},
                                                  {"id": "good", "uei": "ABCDEFGHIJKL", "name": name}]))
        result = UsaSpendingRecipientDiscovery(self.store, client=names).run(as_of=CLOCK)
        self.assertEqual(1, result["new_candidates"])
        self.assertEqual(1, result["rejected_rows"])
        self.assertEqual(0, result["completed_names"])
        self.assertEqual(1, len(self.store.recipient_discovery_due(as_of=CLOCK)))

    def test_candidate_is_saved_before_bad_pagination_metadata(self):
        names = Names(lambda name, number: {"results": [{"id": "good", "uei": "ABCDEFGHIJKL", "name": name}],
                                           "page_metadata": {"hasNext": "false"}})
        result = UsaSpendingRecipientDiscovery(self.store, client=names).run(as_of=CLOCK)
        self.assertEqual(1, result["new_candidates"])
        self.assertEqual(1, result["failed_names"])
        self.assertEqual(0, result["completed_names"])
        self.assertEqual(1, len(self.store.findings_as_of("AAA", as_of=CLOCK)))

    def test_persistent_cursor_is_bound_to_sec_identity_and_company_name(self):
        names = Names(lambda name, number: page([{"id": f"id-{name}-{number}",
                                                  "uei": "ABCDEFGHIJKL", "name": name}], True))
        service = UsaSpendingRecipientDiscovery(self.store, client=names, max_pages=1)
        service.run(as_of=CLOCK)
        later = CLOCK + timedelta(days=1)
        with self.store._connect() as conn:
            conn.execute("UPDATE scout_sec_identities SET company_name='AAA RENAMED INC' WHERE subject_id='AAA'")
        service.run(as_of=later)
        self.assertEqual(("AAA RENAMED INC", 1), names.calls[-1])

    def test_same_as_of_recipient_continuations_rotate_across_names(self):
        self.store.observe_sec_identity(subject_id="BBB", cik="0000000456", company_name="BBB INC", as_of=CLOCK)
        names = Names(lambda name, number: page([{"id": f"id-{name}-{number}",
                                                  "uei": "ABCDEFGHIJKL", "name": name}], True))
        service = UsaSpendingRecipientDiscovery(self.store, client=names,
                                                max_subjects=1, max_pages=1, max_requests=1)
        for _ in range(6):
            service.run(as_of=CLOCK)
        for name in ("AAA INC", "BBB INC"):
            self.assertEqual([1, 2, 3], [number for query, number in names.calls if query == name])

    def test_retry_after_bad_metadata_stays_partial_and_does_not_cache_a_negative(self):
        names = Names(lambda name, number: {"results": [{"id": "one", "uei": "ABCDEFGHIJKL", "name": name}],
                                           "page_metadata": {"hasNext": False, "page": 1.0}})
        service = UsaSpendingRecipientDiscovery(self.store, client=names)
        failed = service.run(as_of=CLOCK)
        self.assertEqual(1, failed["new_candidates"])
        self.assertEqual(1, failed["failed_names"])
        self.assertEqual(0, failed["completed_names"])
        names.callback = lambda name, number: page()
        later = CLOCK + timedelta(hours=2)
        retry = service.run(as_of=later)
        self.assertEqual(1, retry["resumed_names"])
        self.assertEqual(0, retry["completed_names"])
        self.assertEqual("PARTIAL", retry["status"])
        self.assertEqual(1, len(self.store.recipient_discovery_due(as_of=later)))

    def test_failed_legacy_completion_write_does_not_count_a_completed_name(self):
        service = UsaSpendingRecipientDiscovery(self.store, client=Names(lambda name, number: page()))
        with patch.object(self.store, "record_recipient_discovery_check",
                          side_effect=sqlite3.OperationalError("temporary write failure")):
            result = service.run(as_of=CLOCK)
        self.assertEqual(1, result["attempted_names"])
        self.assertEqual(0, result["completed_names"])
        self.assertEqual(1, result["failed_names"])
        self.assertEqual(1, result["partial_names"])
        self.assertEqual(1, len(self.store.recipient_discovery_due(as_of=CLOCK)))


class ArchivedPublisherResponseTests(unittest.TestCase):
    def test_archived_lmt_rows_survive_metadata_failure_replay_and_unavailable_next_page(self):
        fixture_path = Path(__file__).resolve().parents[1] / "evidence" / "usaspending_live_20261001.json"
        case = json.loads(fixture_path.read_text())["cases"]["usaspending_positive_contract_uei"]
        clock = datetime.fromisoformat(case["observed_at"])
        entry = next(e for e in load_verified_identities() if e["uei"] == case["uei"])

        def archived(uei, number):
            self.assertEqual(case["uei"], uei)
            if number != 1:
                raise OSError("Only the archived first page is available; no next page is invented")
            return case["payload"]

        client = Awards(archived)
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "scouts.db"
            store = ScoutStore(db_path)
            service = UsaSpendingScoutService(store, client=client, identities=[entry], max_pages=1)
            with patch("market_checker_app.services.usaspending_scout_service.page_has_next",
                       side_effect=ValueError("injected pagination failure")):
                failed = service.run(as_of=clock)
            self.assertEqual(100, failed["new_findings"])
            self.assertEqual(1, failed["failed_uei"])
            self.assertEqual(0, failed["completed_uei"])
            later = clock + timedelta(hours=2)
            replay = UsaSpendingScoutService(ScoutStore(db_path), client=client,
                                            identities=[entry], max_pages=1).run(as_of=later)
            self.assertEqual(0, replay["new_findings"])
            self.assertEqual(1, replay["resumed_uei"])
            self.assertEqual(0, replay["completed_uei"])
            missing = service.run(as_of=later)
            self.assertEqual([1, 1, 2], [call[3] for call in client.calls])
            self.assertEqual(1, missing["failed_uei"])
            self.assertEqual("PARTIAL", missing["status"])
            self.assertEqual(0, missing["completed_uei"])
            self.assertEqual(100, len(store.findings_as_of("LMT", as_of=later)))
            self.assertEqual(3, missing["daily_requests_used"])
            self.assertTrue(all(not json.loads(row["details_json"])["modification_history_complete"]
                                for row in store.findings_as_of("LMT", as_of=later)))


if __name__ == "__main__":
    unittest.main()
