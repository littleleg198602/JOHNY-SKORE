from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.specialist_acceptance_service import build_specialist_acceptance_report
from market_checker_app.services.fdic_bank_scout_service import FdicBankScoutService, load_verified_banks
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import CANONICAL_CSV_SHA256, load_canonical_ticker_records


NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


class SpecialistAcceptanceTests(unittest.TestCase):
    def test_empty_windows_database_does_not_invent_a_run_or_completion(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            with patch("market_checker_app.services.specialist_acceptance_service.platform.system",
                       return_value="Windows"):
                report = build_specialist_acceptance_report(store, as_of=NOW, environment={})
            self.assertEqual("Windows", report["host"]["system"])
            self.assertFalse(report["completion_verified"])
            self.assertEqual(0, report["inventory_done_count"])
            self.assertEqual(687, report["universe"]["expected_subjects"])
            self.assertTrue(all(row["status"] == "NEVER_RUN" for row in report["source_runs"].values()))
            self.assertFalse(report["runtime"]["universe_archive"]["present"])
            self.assertFalse(report["runtime"]["universe_archive"]["matches_current_input"])
            with store._connect() as conn:
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM scout_universe_snapshots").fetchone()[0])
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM scout_source_runs").fetchone()[0])

    def test_archived_rows_and_scoped_runtime_are_measured_instead_of_assumed(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            records = load_canonical_ticker_records()
            store.record_universe_snapshot(source_name="test.csv", source_sha256=CANONICAL_CSV_SHA256,
                                           records=records, as_of=NOW)
            for ticker in ("HCA", "NVDA", "OUTSIDE"):
                store.observe_sec_identity(subject_id=ticker, cik="0000000001",
                                           company_name=ticker + " Inc.", as_of=NOW)
                job_id = store.enqueue(source="sec", subject_id=ticker,
                                       reason="daily_filings", due_at=NOW)
                with store._connect() as conn:
                    conn.execute("UPDATE scout_jobs SET status='DONE', updated_at=? WHERE job_id=?",
                                 (NOW.isoformat(), job_id))
            store.record_specialist_check("cms", subject_id="HCA", identity_key="0000000001:HCA Inc.",
                                          as_of=NOW, candidate_count=0, truncated=True)
            report = build_specialist_acceptance_report(store, as_of=NOW, environment={})
            self.assertTrue(report["runtime"]["universe_archive"]["matches_current_input"])
            self.assertEqual(687, report["runtime"]["universe_archive"]["archived_rows"])
            self.assertEqual({"ACTIVE": 2}, report["runtime"]["sec_identity_subjects"])
            self.assertEqual(2, report["runtime"]["completed_sec_index_subjects"])
            cms = report["rotating_source_coverage"]["cms"]
            self.assertEqual((19, 1, 1, 0), (cms["applicable_profile_subjects"], cms["active_identities"],
                                           cms["current_partial"], cms["current_complete"]))
            with store._connect() as conn:
                conn.execute("UPDATE scout_universe_input_rows SET yahoo_ticker='BROKEN' WHERE input_position=1")
            self.assertFalse(build_specialist_acceptance_report(
                store, as_of=NOW, environment={})["runtime"]["universe_archive"]["matches_current_input"])

    def test_access_flags_do_not_expose_credentials_or_replace_source_failures(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.record_source_run("recipient_discovery", as_of=NOW, summary={
                "status": "PARTIAL", "checked_names": 2, "new_candidates": 3,
                "truncated_names": 1, "failed_names": 1})
            store.record_source_run("finra", as_of=NOW, summary={"status": "WAIT_ACCESS"})
            env = {"JOHNY_SKORE_FINRA_CLIENT_ID": "PRIVATE_TEST_ID",
                   "JOHNY_SKORE_FINRA_CLIENT_SECRET": "PRIVATE_TEST_SECRET"}
            report = build_specialist_acceptance_report(store, as_of=NOW, environment=env)
            self.assertTrue(report["configured_access_present"]["finra"])
            self.assertFalse(report["access_presence_is_successful_authentication"])
            self.assertEqual("WAIT_ACCESS", report["source_runs"]["finra"]["status"])
            self.assertEqual(3, report["source_runs"]["recipient_discovery"]["new_candidates"])
            self.assertNotIn("PRIVATE_TEST", json.dumps(report))
            self.assertFalse(report["completion_verified"])

    def test_unknown_observation_time_is_rejected(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            with self.assertRaisesRegex(ValueError, "timezone"):
                build_specialist_acceptance_report(store, as_of=NOW.replace(tzinfo=None), environment={})

    def test_fdic_manifest_mapping_is_not_runtime_usable_or_group_completeness(self):
        clock = NOW + timedelta(hours=2)
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            empty_report = build_specialist_acceptance_report(store, as_of=clock, environment={})
            banks = empty_report["rotating_source_coverage"]["fdic"]
            self.assertEqual((22, 6, 16, 6, 0),
                             (banks["applicable_profile_subjects"], banks["mapped_subjects"],
                              banks["unmapped_profile_subjects"], banks["never_attempted_banks"],
                              banks["current_usable_banks"]))
            self.assertFalse(banks["complete_issuer_groups_verified"])
            identities = load_verified_banks()
            class Client:
                def financials(self, cert):
                    identity = next(entry for entry in identities if entry["cert"] == cert)
                    return {"data": [{"data": {"CERT": cert, "NAME": identity["financial_name"],
                                               "REPDTE": "20260630", "DEP": 1}}]}
            FdicBankScoutService(store, client=Client(), identities=identities).run(
                as_of=clock, universe={"PNC", "USB"})
            banks = build_specialist_acceptance_report(store, as_of=clock, environment={})["rotating_source_coverage"]["fdic"]
            self.assertEqual((2, 4), (banks["current_usable_banks"], banks["never_attempted_banks"]))
            earlier = build_specialist_acceptance_report(store, as_of=NOW, environment={})["rotating_source_coverage"]["fdic"]
            self.assertEqual((4, 0), (earlier["mapped_subjects"], earlier["current_usable_banks"]))

    def test_fdic_override_is_measured_and_invalid_manifest_does_not_invent_zero(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            store = ScoutStore(Path(directory) / "test.db")
            identity = next(entry for entry in load_verified_banks() if entry["ticker"] == "PNC")
            path.write_text(json.dumps([identity]), encoding="utf-8")
            env = {"JOHNY_SKORE_FDIC_BANKS_FILE": str(path)}
            report = build_specialist_acceptance_report(store, as_of=NOW + timedelta(hours=2), environment=env)
            self.assertEqual(1, report["rotating_source_coverage"]["fdic"]["mapped_banks"])
            self.assertNotIn(str(path), json.dumps(report))
            path.write_text('{broken', encoding="utf-8")
            banks = build_specialist_acceptance_report(store, as_of=NOW, environment=env)["rotating_source_coverage"]["fdic"]
            self.assertEqual("INVALID_IDENTITY_MANIFEST", banks["status"])
            self.assertNotIn("current_usable_banks", banks)


if __name__ == "__main__":
    unittest.main()
