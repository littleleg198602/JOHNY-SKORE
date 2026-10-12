from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.specialist_source_archive_service import (
    ARCHIVE_SCHEMA_VERSION, MAX_DETAILS_BYTES, SOURCES,
    build_specialist_source_archive, export_specialist_source_archive,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import (
    UsaSpendingCheckpointStore, fresh_state,
)
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
URLS = {
    "sec": "https://www.sec.gov/Archives/edgar/data/1/document.htm",
    "fda": "https://api.fda.gov/drug/enforcement.json",
    "fdic": "https://api.fdic.gov/financials",
    "sec13f": "https://www.sec.gov/Archives/edgar/data/1/filing.htm",
    "cms": "https://data.cms.gov/data-api/v1/dataset/029c119f-f79c-49be-9100-344d31d10344/data",
    "clinicaltrials": "https://clinicaltrials.gov/api/v2/studies",
    "usaspending": "https://api.usaspending.gov/api/v2/search/spending_by_award/",
    "rss": "https://example.org/news",
}


class SpecialistSourceArchiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "scout.db"
        self.store = ScoutStore(self.path)

    def finding(self, *, source="sec", ticker="AAPL", clock=NOW, published=None,
                details=None, object_id="synthetic-object", url=None):
        content = details if details is not None else {"stage": "filing_document", "form": "10-K"}
        return self.store.record_finding(
            source=source, subject_id=ticker, source_object_id=object_id,
            content_hash=canonical_hash(content), title="synthetic unit test source, not live acceptance",
            source_url=url or URLS[source], locator="synthetic-locator",
            published_at=published or clock, available_at=clock, observed_at=clock,
            verification_status="UNVERIFIED" if source in {"fda", "cms", "clinicaltrials", "rss"} else "SOURCE_VERIFIED",
            details=content,
        )[0]

    def archive(self, **options):
        return build_specialist_source_archive(self.store, as_of=options.pop("as_of", NOW), **options)

    def cell(self, archive, source, ticker="AAPL"):
        return next(row for row in archive["source_ticker_missingness"] if (row["source"], row["ticker"]) == (source, ticker))

    def test_empty_source_archive_has_every_canonical_slot_without_false_coverage(self):
        report = self.archive()
        canonical = load_canonical_ticker_records()
        self.assertEqual(canonical, report["universe"]["records"])
        self.assertEqual(687, report["universe"]["ticker_count"])
        self.assertEqual(len(SOURCES) * 687, len(report["source_ticker_missingness"]))
        self.assertEqual([], report["observations"])
        self.assertTrue(all(row["latest_check_status"] == "UNKNOWN" for row in report["source_ticker_missingness"]))
        self.assertTrue(all(not row["relevant_coverage_verified"] for row in report["source_ticker_missingness"]))
        self.assertFalse(report["completion_verified"])
        self.assertFalse(report["out_of_sample_evaluation_verified"])

    def test_reads_existing_scoutstore_without_writes_or_optional_schema_creation(self):
        self.finding()
        with self.store._connect() as conn:
            conn.execute("CREATE TABLE prediction_snapshots(snapshot_id TEXT, aggregate_features TEXT)")
            conn.execute("INSERT INTO prediction_snapshots VALUES('immutable', '{\"regulatory_count\": 50}')")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.archive()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        with self.store._connect() as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='scout_usaspending_checkpoints'").fetchone())
            self.assertEqual("immutable", conn.execute("SELECT snapshot_id FROM prediction_snapshots").fetchone()[0])

    def test_canonical_brkb_is_kept_and_alias_or_unrelated_subjects_are_excluded(self):
        self.finding(ticker="BRKB")
        self.finding(ticker="BRK-B")
        self.finding(ticker="MACRO:US")
        self.finding(ticker="OUTSIDE")
        report = self.archive()
        self.assertEqual(["BRKB"], [row["ticker"] for row in report["observations"]])
        self.assertEqual(1, report["input"]["known_scoped_finding_versions"])

    def test_future_observation_never_backfills_an_old_source_event(self):
        self.finding(clock=NOW + timedelta(days=1), published=NOW - timedelta(days=50),
                     details={"stage": "recall_candidate", "report_date": "20260801"})
        self.assertEqual([], self.archive()["observations"])
        report = self.archive(as_of=NOW + timedelta(days=1))
        item = report["observations"][0]
        self.assertEqual((NOW + timedelta(days=1)).isoformat(timespec="microseconds"), item["knowledge_at"])

    def test_historical_report_dates_and_known_time_are_distinct(self):
        self.finding(source="fda", details={"stage": "crl_candidate", "letter_date": "01/02/2026", "report_date": "20260103"})
        item = self.archive()["observations"][0]
        self.assertEqual({"letter_date": "2026-01-02", "report_date": "2026-01-03"}, item["historical_event_dates"])
        self.assertEqual(NOW.isoformat(timespec="microseconds"), item["stored_publication_at"])
        self.assertFalse(item["publisher_publication_time_verified"])
        self.assertEqual(NOW.isoformat(timespec="microseconds"), item["knowledge_at"])

    def test_per_source_attribution_never_credits_fda_to_cms_or_13f(self):
        self.finding(source="fda", details={"stage": "recall_candidate", "identity_status": "NAME_ONLY"})
        report = self.archive()
        self.assertEqual("OBSERVED", self.cell(report, "fda")["source_observation_status"])
        for source in ("cms", "fdic", "sec13f"):
            self.assertEqual("MISSING", self.cell(report, source)["source_observation_status"])
        self.assertEqual("NAME_ONLY", report["observations"][0]["source_fields"]["identity_status"])
        self.assertFalse(self.cell(report, "fda")["relevant_coverage_verified"])

    def test_complete_zero_query_partial_query_and_never_checked_remain_different(self):
        self.store.record_specialist_check("fda", subject_id="AAPL", identity_key="dated-key", as_of=NOW, candidate_count=0, truncated=False)
        self.store.record_specialist_check("cms", subject_id="AAPL", identity_key="dated-key", as_of=NOW, candidate_count=0, truncated=True)
        report = self.archive()
        self.assertEqual("QUERY_COMPLETE_ZERO_CANDIDATES", self.cell(report, "fda")["latest_check_status"])
        self.assertEqual("QUERY_PARTIAL", self.cell(report, "cms")["latest_check_status"])
        self.assertEqual("UNKNOWN", self.cell(report, "clinicaltrials")["latest_check_status"])
        self.assertEqual("MISSING", self.cell(report, "fda")["source_observation_status"])

    def test_overwritten_latest_check_is_not_an_earlier_negative_evidence(self):
        self.store.record_specialist_check("finra", subject_id="AAPL", identity_key="identity", as_of=NOW, candidate_count=0, truncated=False)
        frozen = self.archive()
        self.store.record_specialist_check("finra", subject_id="AAPL", identity_key="identity", as_of=NOW + timedelta(days=1), candidate_count=50, truncated=True)
        later_dump = self.archive()
        self.assertEqual("QUERY_COMPLETE_ZERO_CANDIDATES", self.cell(frozen, "finra")["latest_check_status"])
        self.assertEqual("UNKNOWN", self.cell(later_dump, "finra")["latest_check_status"])
        self.assertFalse(self.cell(later_dump, "finra")["historical_completeness_reconstructible"])
        self.assertTrue(any("Overwritten" in text for text in later_dump["limitations"]))

    def test_dataset13f_check_does_not_mark_all_tickers_complete(self):
        self.store.record_specialist_check("sec13f", subject_id="DATASET", identity_key="quarter-hash", as_of=NOW, candidate_count=500, truncated=False)
        report = self.archive()
        self.assertEqual("DATASET", report["checks"][0]["scope"])
        self.assertIsNone(report["checks"][0]["ticker"])
        self.assertTrue(all(self.cell(report, "sec13f", row["ticker"])["latest_check_status"] == "UNKNOWN" for row in report["universe"]["records"]))

    def test_fdic_finish_after_cutoff_is_running_with_no_future_counts(self):
        token = self.store.claim_provider("fdic", as_of=NOW, seconds=30)
        request = self.store.reserve_fdic_request(subject_id="JPM", identity_key="dated-cert", as_of=NOW, daily_limit=10, lease_token=token)
        self.store.finish_fdic_request(request, as_of=NOW + timedelta(hours=1), outcome="USABLE", usable_rows=15)
        report = self.archive()
        item = next(row for row in report["checks"] if row["source"] == "fdic")
        self.assertEqual("RUNNING", item["status"])
        self.assertEqual(0, item["usable_rows"])
        later = self.archive(as_of=NOW + timedelta(hours=1))
        self.assertEqual("USABLE", self.cell(later, "fdic", "JPM")["latest_check_status"])

    def test_usaspending_traversal_export_is_not_a_historical_snapshot_claim(self):
        states = UsaSpendingCheckpointStore(self.store)
        state = {**fresh_state(NOW), "status": "COMPLETE", "terminal": True, "pages_saved": 2, "usable_rows": 30,
                 "cursor": "do-not-export-cursor", "api_key": "do-not-export-key"}
        states.save("awards", "dated-uei", "AAPL", state, as_of=NOW)
        report = self.archive()
        item = next(row for row in report["checks"] if row["source"] == "usaspending")
        self.assertEqual("TRAVERSAL_COMPLETE", item["status"])
        self.assertEqual(2, item["pages_saved"])
        self.assertNotIn("do-not-export", json.dumps(report))
        self.assertFalse(self.cell(report, "usaspending")["relevant_coverage_verified"])
        states.save("awards", "dated-uei", "AAPL", state, as_of=NOW + timedelta(days=1))
        self.assertEqual("UNKNOWN", self.cell(self.archive(), "usaspending")["latest_check_status"])

    def test_bounded_export_reports_omitted_findings_and_checks(self):
        for index in range(3):
            self.finding(object_id=f"version-{index}")
        self.finding(source="fdic", ticker="JPM")
        for ticker in ("AAPL", "MSFT"):
            self.store.record_specialist_check("finra", subject_id=ticker, identity_key=ticker, as_of=NOW, candidate_count=0, truncated=False)
        report = self.archive(maximum_observations=1, observations_per_source_ticker=1, maximum_checks=1)
        self.assertEqual(4, report["input"]["known_scoped_finding_versions"])
        self.assertEqual(1, len(report["observations"]))
        self.assertEqual(1, len(report["checks"]))
        self.assertFalse(report["bounds"]["observation_export_complete"])
        self.assertTrue(report["bounds"]["check_export_limit_reached"])
        self.assertFalse(self.cell(report, "sec")["observation_export_complete"])

    def test_immutable_observation_hash_survives_mutable_retrieval_heartbeat(self):
        self.finding()
        original = self.archive()["observations"][0]
        self.finding(clock=NOW + timedelta(days=1), published=NOW)
        replayed = self.archive()["observations"][0]
        self.assertEqual(original["observation_hash"], replayed["observation_hash"])
        self.assertIsNone(replayed["latest_retrieved_at_known_at_cutoff"])
        self.assertEqual(original["first_observed_at"], replayed["first_observed_at"])

    def test_changed_source_content_retains_both_immutable_versions(self):
        self.finding(source="fdic", ticker="JPM", details={"cert": 123, "amounts_thousands_usd": {"ASSET": 100}})
        self.finding(source="fdic", ticker="JPM", details={"cert": 123, "amounts_thousands_usd": {"ASSET": 120}})
        rows = self.archive()["observations"]
        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({row["observation_hash"] for row in rows}))
        self.assertEqual({100, 120}, {row["source_fields"]["amounts_thousands_usd"]["ASSET"] for row in rows})

    def test_future_or_naive_identity_knowledge_is_rejected(self):
        self.finding(details={"relationship_known_at": (NOW + timedelta(seconds=1)).isoformat()}, object_id="future")
        self.finding(details={"identity_evidence": {"known_at": "2026-10-11T12:00:00"}}, object_id="naive")
        report = self.archive()
        self.assertEqual([], report["observations"])
        self.assertEqual({"INVALID_OR_FUTURE_IDENTITY_KNOWLEDGE": 2}, report["input"]["rejected_reasons"])
        self.assertFalse(report["bounds"]["observation_export_complete"])

    def test_exact_subsecond_cutoff_and_timezone_normalisation(self):
        self.finding(clock=NOW + timedelta(microseconds=1))
        self.assertEqual([], self.archive()["observations"])
        equivalent = NOW.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(self.archive(), self.archive(as_of=equivalent))
        self.assertEqual(1, len(self.archive(as_of=NOW + timedelta(microseconds=1))["observations"]))

    def test_explicit_projection_omits_credentials_queries_errors_and_raw_text(self):
        secret = "unit-test-access-value"
        self.finding(url=URLS["sec"] + f"?api_key={secret}#token={secret}", object_id=secret,
                     details={"stage": "filing_document", "api_key": secret, "password": secret,
                              "http_headers": {"Authorization": secret}, "error": secret,
                              "query": secret, "form": "Bearer " + secret,
                              "relationship_evidence_url": "https://www.sec.gov/evidence?token=" + secret})
        self.store.record_specialist_check("finra", subject_id="AAPL", identity_key=secret, as_of=NOW, candidate_count=0, truncated=False)
        self.store.record_source_run("sec", as_of=NOW, summary={"status": "PARTIAL", "error": secret})
        report = self.archive()
        self.assertNotIn(secret, json.dumps(report))
        self.assertEqual(URLS["sec"], report["observations"][0]["source_url"])
        self.assertEqual("https://www.sec.gov/evidence", report["observations"][0]["source_fields"]["relationship_evidence_url"])

    def test_invalid_and_oversized_json_is_rejected_and_nonfinite_values_are_omitted(self):
        first = self.finding(object_id="broken")
        second = self.finding(object_id="large")
        self.finding(source="fdic", ticker="JPM", details={"amounts_thousands_usd": {"ASSET": 5, "BAD": float("nan")}, "value": float("inf")}, object_id="numbers")
        with self.store._connect() as conn:
            conn.execute("UPDATE scout_findings SET details_json=? WHERE finding_id=?", ("{broken", first))
            conn.execute("UPDATE scout_findings SET details_json=? WHERE finding_id=?", (json.dumps({"omitted": "x" * MAX_DETAILS_BYTES}), second))
        report = self.archive()
        self.assertEqual({"INVALID_DETAILS_JSON": 2}, report["input"]["rejected_reasons"])
        self.assertEqual({"ASSET": 5}, report["observations"][0]["source_fields"]["amounts_thousands_usd"])
        self.assertNotIn("value", report["observations"][0]["source_fields"])
        json.dumps(report, allow_nan=False)

    def test_list_applications_are_preserved_as_candidates_without_identity_credit(self):
        self.finding(source="fda", details={"stage": "crl_candidate", "application_number": ["NDA123456", "ANDA234567"], "issuer_attribution_allowed": False})
        row = self.archive()["observations"][0]
        self.assertEqual(["NDA123456", "ANDA234567"], row["source_fields"]["application_number"])
        self.assertFalse(row["source_fields"]["issuer_attribution_allowed"])

    def test_report_metadata_cannot_mutate_later_archives(self):
        report = self.archive()
        report["source_routing"]["fda"].append("pretend-specialist")
        report["limitations"].clear()
        fresh = self.archive()
        self.assertEqual(["fda"], fresh["source_routing"]["fda"])
        self.assertTrue(fresh["limitations"])

    def test_content_addressed_file_is_valid_idempotent_and_refuses_tampering(self):
        self.finding()
        report = self.archive()
        destination = export_specialist_source_archive(report, Path(self.directory.name) / "exports")
        self.assertIn(report["archive_hash"], destination.name)
        self.assertEqual(report, json.loads(destination.read_text()))
        self.assertEqual(destination, export_specialist_source_archive(report, destination.parent))
        destination.write_text("tampered immutable archive")
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            export_specialist_source_archive(report, destination.parent)
        self.assertEqual("tampered immutable archive", destination.read_text())
        changed = deepcopy(report)
        changed["completion_verified"] = True
        with self.assertRaisesRegex(ValueError, "content hash"):
            export_specialist_source_archive(changed, destination.parent)

    def test_archive_and_observation_hashes_are_reproducible_and_checkable(self):
        self.finding(source="cms", details={"identity_evidence": {"known_at": NOW.isoformat(), "effective_from": "2020-01-01", "evidence": [{"url": "https://www.sec.gov/public"}]}})
        report = self.archive()
        self.assertEqual(report, self.archive())
        self.assertEqual(ARCHIVE_SCHEMA_VERSION, report["schema_version"])
        self.assertEqual(report["archive_hash"], canonical_hash({key: value for key, value in report.items() if key != "archive_hash"}))
        for row in report["observations"]:
            self.assertEqual(row["observation_hash"], canonical_hash({key: value for key, value in row.items() if key not in {"observation_hash", "latest_retrieved_at_known_at_cutoff"}}))

    def test_invalid_limits_missing_database_or_invalid_canonical_input_fail_closed(self):
        for options in ({"maximum_observations": 0}, {"maximum_checks": 20_001}, {"observations_per_source_ticker": True}):
            with self.assertRaises(ValueError):
                self.archive(**options)
        with self.assertRaises(ValueError):
            self.archive(as_of=NOW.replace(tzinfo=None))
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.archive()
        self.assertFalse(self.path.exists())
        self.store = ScoutStore(self.path)
        with patch("market_checker_app.services.specialist_source_archive_service.load_canonical_ticker_records", return_value=[{"ticker": "AAPL", "yahoo_ticker": "AAPL"}]):
            with self.assertRaisesRegex(ValueError, "687"):
                self.archive()


if __name__ == "__main__":
    unittest.main()
