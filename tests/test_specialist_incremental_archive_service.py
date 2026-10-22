from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.specialist_incremental_archive_service import (
    capture_specialist_source_run, load_specialist_source_run_archive,
    replay_specialist_source_history, specialist_run_archive_directory,
)
from market_checker_app.services.specialist_source_archive_service import build_specialist_source_archive
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import UsaSpendingCheckpointStore, fresh_state
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
CLOCK_TARGET = "market_checker_app.services.specialist_incremental_archive_service._capture_clock"


class SpecialistIncrementalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ScoutStore(Path(self.temporary.name) / "scout.db")
        self.directory = specialist_run_archive_directory(self.store)

    def capture(self, source="fda", *, clock=NOW, captured=None, summary=None,
                started=None, provider=None, **bounds):
        summary = summary or {"status": "OK", "checked_issuers": 1, "new_findings": 0}
        self.store.record_source_run(source, as_of=clock, summary=summary)
        with patch(CLOCK_TARGET, return_value=captured or clock + timedelta(seconds=1)):
            path = capture_specialist_source_run(self.store, source=source,
                observed_at=clock, summary=summary, started_at=started, provider_as_of=provider,
                evidence_origin="DETERMINISTIC_TEST_FIXTURE", **bounds)
        return path, load_specialist_source_run_archive(path)

    def check(self, source="fda", *, ticker="AAPL", clock=NOW, count=0, partial=False):
        self.store.record_specialist_check(source, subject_id=ticker, identity_key="test-identity",
            as_of=clock, candidate_count=count, truncated=partial)

    def finding(self, source="fda", *, ticker="AAPL", clock=NOW, object_id="test-object"):
        urls = {"fda": "https://api.fda.gov/drug/enforcement.json",
                "cms": "https://data.cms.gov/data-api/v1/dataset/029c119f-f79c-49be-9100-344d31d10344/data",
                "fred": "https://fred.stlouisfed.org/series/CPIAUCSL",
                "eia": "https://www.eia.gov/opendata/browser/petroleum/pri/spt"}
        details = {"stage": "recall_candidate", "report_date": "2026-08-01"}
        return self.store.record_finding(source=source, subject_id=ticker, source_object_id=object_id,
            content_hash=canonical_hash(details), title="deterministic fixture; no live acceptance",
            source_url=urls[source], locator="test-locator", published_at=clock,
            available_at=clock, observed_at=clock, verification_status="UNVERIFIED",
            details=details)[0]

    def replay(self, *, as_of=NOW + timedelta(days=3), source="fda", **bounds):
        return replay_specialist_source_history(self.directory, as_of=as_of, source=source, **bounds)

    def test_zero_then_overwritten_partial_check_both_survive_reopened_store(self):
        self.check()
        first_path, first = self.capture(provider=NOW, started=NOW - timedelta(seconds=1))
        self.store = ScoutStore(self.store.db_path)
        later = NOW + timedelta(days=1)
        self.check(clock=later, count=50, partial=True)
        self.capture(clock=later, provider=later,
            summary={"status": "PARTIAL", "checked_issuers": 1, "truncated_issuers": 1})
        earlier = build_specialist_source_archive(self.store, as_of=NOW, source="fda")
        self.assertEqual([], earlier["checks"], "mutable latest state lost the older check")
        self.assertEqual(first, load_specialist_source_run_archive(first_path))
        report = self.replay()
        self.assertEqual({"QUERY_COMPLETE_ZERO_CANDIDATES", "QUERY_PARTIAL"},
            {row["record"]["status"] for row in report["checks"]})
        cell = next(row for row in report["source_ticker_history"] if row["ticker"] == "AAPL")
        self.assertEqual(2, cell["preserved_check_versions"])
        self.assertEqual("QUERY_PARTIAL", cell["latest_preserved_check_status"])
        self.assertFalse(report["historical_checks_complete"])
        self.assertFalse(report["out_of_sample_evaluation_verified"])

    def test_no_due_busy_and_wait_quota_preserve_status_without_new_empty_queries(self):
        self.check(clock=NOW - timedelta(days=1))
        for index, status in enumerate(("NO_DUE_WORK", "BUSY", "WAIT_QUOTA")):
            clock = NOW + timedelta(minutes=index)
            _, item = self.capture(clock=clock, started=clock - timedelta(seconds=1), provider=clock,
                summary={"status": status, "checked_issuers": 0, "attempted_requests": 0, "new_findings": 0})
            self.assertEqual("NOT_PERFORMED_REPORTED", item["attempt_semantics"]["state"])
            self.assertEqual("PRIOR_STORED_STATE", item["check_time_basis"][0]["time_basis"])
            self.assertFalse(item["attempt_semantics"]["empty_result_inferred_from_zero_new_findings"])
        report = self.replay()
        self.assertEqual(3, len(report["source_runs"]))
        self.assertEqual(1, len(report["checks"]), "prior check repeated in captures is one preserved version")
        self.assertEqual({"NO_DUE_WORK", "BUSY", "WAIT_QUOTA"},
                         {row["run_summary"]["status"] for row in report["source_runs"]})

    def test_logical_provider_clock_before_physical_call_does_not_drop_actual_check(self):
        provider = NOW - timedelta(hours=2)
        self.check(clock=provider)
        _, item = self.capture(started=NOW - timedelta(seconds=5), provider=provider)
        self.assertEqual("PROVIDER_LOGICAL_WINDOW", item["check_time_basis"][0]["time_basis"])
        self.assertEqual(1, len(item["source_archive"]["checks"]))
        self.assertFalse(item["check_time_basis"][0]["run_association_verified"])
        self.assertEqual(NOW.isoformat(timespec="microseconds"), item["observed_at"])
        self.assertGreater(item["available_at"], item["observed_at"])

    def test_capture_is_one_source_687_scope_and_independent_of_other_source_limits(self):
        self.finding()
        self.finding(ticker="BRKB", object_id="canonical")
        self.finding(ticker="BRK-B", object_id="alias")
        self.finding(ticker="OUTSIDE", object_id="unapproved")
        self.check()
        for ticker in ("AAPL", "MSFT", "JPM"):
            self.finding("cms", ticker=ticker)
            self.check("cms", ticker=ticker)
        _, item = self.capture(maximum_observations=2, maximum_checks=1)
        archive = item["source_archive"]
        self.assertEqual(687, len(archive["source_ticker_missingness"]))
        self.assertEqual({"fda"}, set(archive["source_routing"]))
        self.assertEqual({"AAPL", "BRKB"}, {row["ticker"] for row in archive["observations"]})
        self.assertEqual(1, len(archive["checks"]))
        self.assertFalse(archive["bounds"]["check_export_limit_reached"])
        self.assertTrue(all(not row["relevant_coverage_verified"] for row in archive["source_ticker_missingness"]))

    def test_archive_availability_prevents_retrospective_backdating(self):
        self.check(clock=NOW - timedelta(days=90))
        _, item = self.capture(captured=NOW + timedelta(days=1))
        unavailable = self.replay(as_of=NOW + timedelta(hours=1))
        self.assertEqual([], unavailable["checks"])
        self.assertEqual([], unavailable["source_runs"])
        self.assertEqual(1, unavailable["input"]["excluded_not_yet_available"])
        available = self.replay(as_of=NOW + timedelta(days=1))
        self.assertEqual(1, len(available["checks"]))
        self.assertEqual(item["available_at"], available["checks"][0]["archive_available_at"])

    def test_retry_keeps_first_immutable_capture_after_database_state_changes(self):
        self.check()
        path, original = self.capture()
        self.check(clock=NOW + timedelta(hours=1), count=3, partial=True)
        with patch(CLOCK_TARGET, return_value=NOW + timedelta(days=1)):
            retry = capture_specialist_source_run(self.store, source="fda", observed_at=NOW,
                summary={"status": "OK", "checked_issuers": 1, "new_findings": 0},
                evidence_origin="DETERMINISTIC_TEST_FIXTURE")
        self.assertEqual(path, retry)
        self.assertEqual(original, load_specialist_source_run_archive(path))
        self.assertEqual(1, len(list(path.parent.glob("specialist_source_run_*.json"))))
        path.unlink()
        repaired = capture_specialist_source_run(self.store, source="fda", observed_at=NOW,
            summary={"status": "OK", "checked_issuers": 1, "new_findings": 0},
            evidence_origin="DETERMINISTIC_TEST_FIXTURE")
        self.assertEqual(path, repaired)
        self.assertEqual(original, load_specialist_source_run_archive(repaired))

    def test_concurrent_retries_publish_one_complete_capture(self):
        self.store.record_source_run("fda", as_of=NOW, summary={"status": "OK", "checked_issuers": 1})
        self.check()
        counter, lock = itertools.count(1), threading.Lock()

        def capture_clock():
            with lock:
                return NOW + timedelta(seconds=next(counter))

        def perform(_):
            return capture_specialist_source_run(self.store, source="fda", observed_at=NOW,
                summary={"status": "OK", "checked_issuers": 1}, evidence_origin="DETERMINISTIC_TEST_FIXTURE")

        with patch(CLOCK_TARGET, side_effect=capture_clock), ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(perform, range(8)))
        self.assertEqual(1, len(set(paths)))
        self.assertEqual(1, len(list(paths[0].parent.glob("specialist_source_run_*.json"))))
        self.assertEqual(1, len(self.replay()["source_runs"]))

    def test_capture_is_read_only_and_does_not_create_optional_source_schema(self):
        self.check()
        self.store.record_source_run("fda", as_of=NOW, summary={"status": "OK", "checked_issuers": 1})
        before = hashlib.sha256(self.store.db_path.read_bytes()).hexdigest()
        with patch(CLOCK_TARGET, return_value=NOW + timedelta(seconds=1)):
            capture_specialist_source_run(self.store, source="fda", observed_at=NOW)
        self.assertEqual(before, hashlib.sha256(self.store.db_path.read_bytes()).hexdigest())
        with self.store._connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='scout_usaspending_checkpoints'").fetchone())

    def test_exact_persisted_run_and_matching_summary_are_required_even_on_retry(self):
        with self.assertRaisesRegex(ValueError, "Exact source run"):
            capture_specialist_source_run(self.store, source="fda", observed_at=NOW, summary={"status": "OK"})
        path, original = self.capture()
        with self.assertRaisesRegex(ValueError, "Supplied summary differs"):
            capture_specialist_source_run(self.store, source="fda", observed_at=NOW, summary={"status": "PARTIAL"})
        self.store.record_source_run("fda", as_of=NOW, summary={"status": "PARTIAL"})
        with self.assertRaisesRegex(ValueError, "changed"):
            capture_specialist_source_run(self.store, source="fda", observed_at=NOW,
                summary={"status": "PARTIAL"}, evidence_origin="DETERMINISTIC_TEST_FIXTURE")
        self.assertEqual(original, load_specialist_source_run_archive(path))
        with self.store._connect() as conn:
            conn.execute("DELETE FROM scout_source_runs")
        with self.assertRaisesRegex(ValueError, "Exact source run"):
            capture_specialist_source_run(self.store, source="fda", observed_at=NOW,
                evidence_origin="DETERMINISTIC_TEST_FIXTURE")

    def test_performed_failed_request_is_distinct_from_returned_payload_and_zero_query(self):
        token = self.store.claim_provider("fda", as_of=NOW, seconds=30)
        request = self.store.reserve_public_request("fda", subject_id="AAPL", identity_key="test-identity",
            as_of=NOW, daily_issuer_limit=2, daily_request_limit=4, lease_token=token)
        self.store.finish_public_request(request["request_id"], outcome="FAILED")
        _, item = self.capture(summary={"status": "PARTIAL", "checked_issuers": 0,
            "failed_issuers": 1, "attempted_requests": 1, "attempted_issuers": 1})
        self.assertEqual("PERFORMED_REPORTED", item["attempt_semantics"]["state"])
        self.assertEqual("FAILED", item["attempts"][0]["outcome"])
        self.assertFalse(item["attempts"][0]["outcome_timestamp_known"])
        self.assertEqual([], item["source_archive"]["checks"])
        self.assertFalse(item["attempts"][0]["usable_check_inferred"])

    def test_fdic_future_finish_is_preserved_as_running_until_known(self):
        token = self.store.claim_provider("fdic", as_of=NOW, seconds=30)
        request = self.store.reserve_fdic_request(subject_id="JPM", identity_key="test-cert",
            as_of=NOW, daily_limit=10, lease_token=token)
        later = NOW + timedelta(hours=1)
        self.store.finish_fdic_request(request, as_of=later, outcome="EMPTY")
        _, first = self.capture("fdic", summary={"status": "PARTIAL", "attempted_banks": 1})
        self.assertEqual("RUNNING", first["attempts"][0]["outcome"])
        self.assertIsNone(first["attempts"][0]["finished_at"])
        self.capture("fdic", clock=later, summary={"status": "PARTIAL", "attempted_banks": 1, "empty_banks": 1})
        report = self.replay(source="fdic")
        self.assertEqual({"RUNNING", "EMPTY"}, {row["record"]["outcome"] for row in report["attempts"]})
        self.assertEqual({"RUNNING", "EMPTY"}, {row["record"]["status"] for row in report["checks"]})

    def test_shared_macro_and_dataset13f_evidence_never_credit_issuer_coverage(self):
        self.finding("fred", ticker="MACRO:US")
        self.finding("eia", ticker="COMMODITY:WTI")
        token = self.store.claim_provider("eia", as_of=NOW, seconds=30)
        request = self.store.reserve_public_request("eia", subject_id="COMMODITY:WTI", identity_key="test-series",
            as_of=NOW, daily_issuer_limit=2, daily_request_limit=2, lease_token=token)
        self.store.finish_public_request(request["request_id"], outcome="RETURNED")
        for source, summary in (("fred", {"status": "OK", "new_findings": 1}),
                                ("eia", {"status": "OK", "new_findings": 1, "attempted_series": 1, "attempted_requests": 1})):
            _, item = self.capture(source, summary=summary)
            self.assertEqual([], item["source_archive"]["observations"])
            self.assertEqual(1, len(item["shared_context_observations"]))
            self.assertIsNone(item["shared_context_observations"][0]["ticker"])
            self.assertTrue(all(row["latest_check_status"] == "UNKNOWN" for row in item["source_archive"]["source_ticker_missingness"]))
            if source == "fred":
                self.assertEqual("UNREPORTED", item["attempt_semantics"]["state"])
            else:
                self.assertEqual("SHARED_SERIES", item["attempt_semantics"]["query_unit"])
                self.assertIsNone(item["attempts"][0]["ticker"])
        self.check("sec13f", ticker="DATASET", count=123)
        self.capture("sec13f", summary={"status": "OK", "matched_rows": 123, "saved_rows": 10})
        report = self.replay(source="sec13f")
        self.assertEqual("DATASET", report["checks"][0]["record"]["scope"])
        self.assertTrue(all(row["preserved_check_versions"] == 0 for row in report["source_ticker_history"]))
        self.assertTrue(all(not row["relevant_coverage_verified"] for row in report["source_ticker_history"]))

    def test_bounds_explicitly_report_capture_replay_and_scan_omissions(self):
        for index, ticker in enumerate(("AAPL", "MSFT")):
            self.finding(ticker=ticker, object_id=f"test-{index}")
            self.check(ticker=ticker)
        self.capture(maximum_observations=1, maximum_checks=1)
        self.capture(clock=NOW + timedelta(minutes=1))
        report = self.replay(maximum_archives=1)
        self.assertTrue(report["bounds"]["archive_scan_limit_reached"])
        self.assertEqual(1, report["input"]["inspected_archives"])
        limited = self.replay(maximum_checks=1, maximum_observations=1)
        self.assertTrue(limited["bounds"]["capture_export_incomplete"])
        self.assertTrue(limited["bounds"]["check_history_limit_reached"])
        self.assertTrue(limited["bounds"]["observation_history_limit_reached"])
        self.assertEqual(1, len(limited["checks"]))
        self.assertEqual(1, len(limited["observations"]))

    def test_projection_drops_errors_secrets_and_unbounded_text(self):
        secret = "test-only-access-secret"
        _, item = self.capture(summary={"status": "PARTIAL", "checked_issuers": 0,
            "error": secret, "password": secret, "query_scope": secret,
            "dataset_url": f"https://example.org/file?api_key={secret}",
            "attempted_requests": True, "new_findings": -1, "stop_reason": secret})
        self.assertNotIn(secret, json.dumps(item))
        self.assertEqual({"status": "PARTIAL", "checked_issuers": 0}, item["run_summary"])

    def test_oversized_checkpoint_is_rejected_without_unbounded_state_export(self):
        checkpoints = UsaSpendingCheckpointStore(self.store)
        state = {**fresh_state(NOW), "status": "COMPLETE", "terminal": True,
                 "untrusted_cursor": "x" * 131_073}
        checkpoints.save("awards", "test-uei", "AAPL", state, as_of=NOW)
        _, item = self.capture("usaspending", summary={"status": "PARTIAL", "attempted_uei": 1})
        self.assertEqual([], item["source_archive"]["checks"])
        self.assertEqual({"INVALID_USASPENDING_CHECKPOINT_JSON": 1}, item["source_archive"]["input"]["rejected_reasons"])
        self.assertTrue(self.replay(source="usaspending")["bounds"]["capture_export_incomplete"])

    def test_tamper_and_rehashed_noncanonical_scope_fail_closed(self):
        path, item = self.capture()
        tampered = deepcopy(item)
        tampered["run_summary"]["checked_issuers"] = 50
        path.write_text(json.dumps(tampered))
        with self.assertRaisesRegex(ValueError, "content hash"):
            load_specialist_source_run_archive(path)
        forged = deepcopy(item)
        forged["source_archive"]["universe"]["records"][0]["ticker"] = "OUTSIDE"
        inner = forged["source_archive"]
        inner["archive_hash"] = canonical_hash({key: value for key, value in inner.items() if key != "archive_hash"})
        forged["snapshot_hash"] = canonical_hash({key: value for key, value in forged.items() if key != "snapshot_hash"})
        forged_path = path.parent / f"specialist_source_run_{forged['source_run_id']}_{forged['snapshot_hash']}.json"
        forged_path.write_text(json.dumps(forged))
        with self.assertRaisesRegex(ValueError, "687"):
            load_specialist_source_run_archive(forged_path)

    def test_rehashed_backdated_availability_and_performance_claims_fail_closed(self):
        path, item = self.capture()
        for change, message in (("availability", "availability"), ("performance", "performance")):
            forged = deepcopy(item)
            if change == "availability":
                forged["available_at"] = forged["captured_at"] = (NOW - timedelta(days=1)).isoformat(timespec="microseconds")
            else:
                inner = forged["source_archive"]
                inner["out_of_sample_evaluation_verified"] = True
                inner["archive_hash"] = canonical_hash({key: value for key, value in inner.items() if key != "archive_hash"})
            forged["snapshot_hash"] = canonical_hash({key: value for key, value in forged.items() if key != "snapshot_hash"})
            forged_path = path.parent / f"specialist_source_run_{forged['source_run_id']}_{forged['snapshot_hash']}.json"
            forged_path.write_text(json.dumps(forged))
            with self.assertRaisesRegex(ValueError, message):
                load_specialist_source_run_archive(forged_path)

    def test_replay_directory_entry_budget_is_bounded_even_with_unrelated_files(self):
        leaf = self.directory / "fda"
        leaf.mkdir(parents=True)
        for index in range(10):
            (leaf / f"unrelated-{index}.txt").write_text("not a run archive")
        report = self.replay(maximum_archives=1)
        self.assertEqual(3, report["input"]["directory_entries_scanned"])
        self.assertTrue(report["bounds"]["directory_entry_scan_limit_reached"])
        self.assertEqual([], report["source_runs"])

    def test_invalid_inputs_fail_without_archive_or_missing_database_creation(self):
        self.store.record_source_run("fda", as_of=NOW, summary={"status": "OK"})
        for options in ({"maximum_checks": 0}, {"maximum_attempts": True},
                        {"maximum_shared_observations": 101}, {"started_at": NOW.replace(tzinfo=None)},
                        {"provider_as_of": NOW + timedelta(seconds=1)}, {"source": "recipient_discovery"}):
            arguments = {"source": "fda", "observed_at": NOW, **options}
            with self.assertRaises(ValueError):
                capture_specialist_source_run(self.store, **arguments)
        with patch(CLOCK_TARGET, return_value=NOW - timedelta(seconds=1)):
            with self.assertRaisesRegex(ValueError, "capture time"):
                capture_specialist_source_run(self.store, source="fda", observed_at=NOW)
        self.assertFalse(self.directory.exists())
        self.store.db_path.unlink()
        with self.assertRaises(FileNotFoundError):
            capture_specialist_source_run(self.store, source="fda", observed_at=NOW)
        self.assertFalse(self.store.db_path.exists())
        with self.assertRaises(ValueError):
            self.replay(maximum_archives=0)

    def test_replay_hash_and_empty_missing_history_are_reproducible(self):
        report = self.replay()
        self.assertEqual(report, self.replay())
        self.assertEqual(load_canonical_ticker_records(), report["universe"]["records"])
        self.assertEqual(687, len(report["source_ticker_history"]))
        self.assertTrue(all(row["latest_preserved_check_status"] == "UNKNOWN" for row in report["source_ticker_history"]))
        self.assertEqual(report["replay_hash"], canonical_hash({key: value for key, value in report.items() if key != "replay_hash"}))


if __name__ == "__main__":
    unittest.main()
