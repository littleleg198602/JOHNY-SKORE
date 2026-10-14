"""Explicit source actions preserve physical archives without issuer promotion."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.scout_runner import run_award_capture_audit, run_primary_ir
from market_checker_app.storage.scout_store import ScoutStore
from tests import test_award_transaction_history_service as award_fixtures


class Batch5CoordinatorIntegrationTests(unittest.TestCase):
    def test_clinical_ui_keeps_snapshot_clock_and_does_not_invent_legacy_attribution_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            clock = datetime(2026, 10, 13, tzinfo=timezone.utc)
            for suffix, details in (("snapshot", {"trial_attribution_time_basis": "OBSERVED_SNAPSHOT",
                    "historical_trial_sponsor_attribution_allowed": False,
                    "trial_sponsor_identity_is_product_ownership": False,
                    "source_observation": {"body_sha256": "a" * 64, "observed_at": clock.isoformat()}}),
                    ("legacy", {})):
                store.record_finding(source="clinicaltrials", subject_id="LLY", source_object_id=suffix,
                    content_hash=suffix, title=suffix, source_url="https://clinicaltrials.gov/api/v2/studies",
                    locator="test", published_at=clock, available_at=clock, observed_at=clock,
                    verification_status="UNVERIFIED", details={"stage": "clinical_trial_candidate", **details})
            rows = {row["title"]: row for row in store.latest_findings(["LLY"], as_of=clock, source="clinicaltrials")}
            self.assertEqual("OBSERVED_SNAPSHOT", rows["snapshot"]["trial_attribution_time_basis"])
            self.assertEqual("a" * 64, rows["snapshot"]["source_body_sha256"])
            self.assertEqual(clock.isoformat(), rows["snapshot"]["source_observed_at"])
            self.assertFalse(rows["snapshot"]["historical_trial_sponsor_attribution_allowed"])
            self.assertFalse(rows["snapshot"]["trial_sponsor_identity_is_product_ownership"])
            self.assertIsNone(rows["legacy"]["historical_trial_sponsor_attribution_allowed"])
            self.assertEqual("UNVERIFIED", rows["snapshot"]["verification_status"])

    def test_selected_award_offline_comparison_retains_inputs_and_records_new_audit(self):
        with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect",
                side_effect=AssertionError("Offline comparison attempted HTTP")):
            store = ScoutStore(Path(directory) / "scout.db")
            fixture = award_fixtures.AwardTransactionHistoryTests()
            fixture.store = store
            finding_id = fixture.seed()
            captures = store.db_path.parent / "award_transaction_captures"
            first = award_fixtures.archive_transaction_capture(
                award_fixtures.capture(award_fixtures.page_payload([award_fixtures.transaction()])), captures,
            )
            before = {path.name: path.read_bytes() for path in captures.iterdir()}
            with patch("market_checker_app.scout_runner.load_verified_identities",
                       return_value=[award_fixtures.IDENTITY]):
                result = run_award_capture_audit(
                    db_path=store.db_path, subject_id="AAA", award_finding_id=finding_id,
                    baseline_manifest_names=[first.name], current_manifest_names=[first.name],
                )
            self.assertEqual("SAME_SOURCE_CAPTURE", result["status"])
            self.assertEqual("SAVED", result["audit_archive_status"])
            self.assertEqual(before, {path.name: path.read_bytes() for path in captures.iterdir()})
            archive = store.db_path.parent / "award_transaction_audits" / result["audit_archive_filename"]
            self.assertEqual(result["audit_archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            saved = json.loads(archive.read_bytes())
            self.assertGreater(datetime.fromisoformat(saved["audit_recorded_at"]), award_fixtures.CLOCK)
            self.assertEqual("SAME_SOURCE_CAPTURE", saved["status"])
            self.assertFalse(saved["new_source_capture_comparison"])
            self.assertFalse(saved["scoring_applied"])
            self.assertEqual("SAME_SOURCE_CAPTURE", store.latest_source_runs()["usaspending_transaction_audit"]["status"])

    def test_capture_path_escape_and_missing_file_are_rejected_before_body_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            captures = store.db_path.parent / "award_transaction_captures"
            captures.mkdir()
            outside = store.db_path.parent / "capture-outside.json"
            outside.write_text("not a capture", encoding="utf-8")
            for name in ("../capture-outside.json", str(outside), "capture-absent.json"):
                with self.subTest(name=name), patch("market_checker_app.scout_runner.load_verified_identities") as load:
                    result = run_award_capture_audit(db_path=store.db_path, subject_id="AAA",
                        award_finding_id="stored-award", baseline_manifest_names=[name], current_manifest_names=[name])
                self.assertEqual("INVALID_COMPARISON", result["status"])
                load.assert_not_called()
                self.assertFalse(result["publisher_deletion_verified"])
            self.assertEqual("not a capture", outside.read_text(encoding="utf-8"))

    def test_ir_completed_action_archives_all_canonical_slots_and_keeps_source_only_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            response = {"status": "SOURCE_VERIFIED", "runtime_mode": "FIXTURE",
                        "attempted_requests": 3, "issuer_attribution_allowed": False, "scoring_applied": False}
            with patch("market_checker_app.services.ir_primary_scout_service.IRPrimaryScoutService") as service:
                service.return_value.run.return_value = response
                result = run_primary_ir(db_path=store.db_path, ticker="MSFT", feed_url="https://example.com/rss",
                    authority_url="https://example.com/ir", publisher_hosts=("example.com",), candidate_id="existing-lead")
            self.assertEqual(("SOURCE_VERIFIED", "SAVED"), (result["status"], result["source_archive_status"]))
            service.return_value.run.assert_called_once_with("MSFT", feed_url="https://example.com/rss",
                authority_url="https://example.com/ir", publisher_hosts=("example.com",), candidate_id="existing-lead",
                original_url=None, requested_as_of=None)
            self.assertFalse(store.latest_source_runs()["rss"]["issuer_attribution_allowed"])
            archive = store.db_path.parent / "specialist_source_runs" / "rss" / result["source_archive_filename"]
            saved = json.loads(archive.read_bytes())
            self.assertEqual(687, saved["source_archive"]["universe"]["ticker_count"])
            self.assertFalse(saved["source_archive"]["completion_verified"])

    def test_ir_archive_failure_keeps_completed_source_result_without_exception_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            with patch("market_checker_app.services.ir_primary_scout_service.IRPrimaryScoutService") as service, \
                 patch("market_checker_app.scout_runner.capture_specialist_source_run",
                       side_effect=OSError("private access value must not be logged")):
                service.return_value.run.return_value = {"status": "WAIT_QUOTA", "attempted_requests": 0,
                                                        "issuer_attribution_allowed": False, "scoring_applied": False}
                result = run_primary_ir(db_path=store.db_path, ticker="MSFT", feed_url="https://example.com/rss",
                    authority_url="https://example.com/ir", publisher_hosts=("example.com",), candidate_id="existing-lead")
            self.assertEqual(("WAIT_QUOTA", "ERROR", "OSError"),
                             (result["status"], result["source_archive_status"], result["source_archive_error"]))
            self.assertNotIn("private access value", json.dumps(store.latest_source_runs()))


if __name__ == "__main__":
    unittest.main()
