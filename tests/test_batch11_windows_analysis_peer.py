"""Independent adversarial synthetic execution; never native Windows proof.

Only canonical temporary fixture inputs/DB are used. The explicit injected
pipeline exercises actual SQLite saves; no real market/model/forecast runs.
"""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from market_checker_app.services import user_windows_analysis_capture as target
from tests import test_user_windows_analysis_capture as fixtures

NOW = fixtures.NOW
NATIVE_CONNECT = fixtures.NATIVE_CONNECT


class WindowsAnalysisPeerTests(unittest.TestCase):
    def setUp(self):
        self.harness = fixtures.WindowsAnalysisCaptureTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.assertEqual(self.harness.root.resolve(), self.harness.root)
        self.assertFalse(self.harness.root.is_relative_to(target.SOURCE_ROOT))

    def capture(self, **kwargs):
        result = self.harness.capture(**kwargs)
        record = result["receipt"]
        self.harness.assert_nonacceptance(record)
        self.assertTrue(record["fixture_only"])
        self.assertEqual(record["evidence_origin"], "EXPLICIT_SYNTHETIC_TEST_EXECUTION")
        return record

    def binding_rejected(self, record):
        self.assertTrue(any(reason in record["qualification_reasons"] for reason in (
            "PRODUCTION_PIPELINE_RESULT_NOT_COMPLETE", "RETURNED_RESULT_OR_PHYSICAL_RUN_CLOCK_BINDING_NOT_VERIFIED",
            "RETURNED_SIGNAL_IDENTIFIERS_NOT_BOUND_TO_PERSISTED_ROWS")), record["qualification_reasons"])

    def test_valid_actual_temp_save_control_is_bound_but_still_fixture_only(self):
        self.harness.market_fixture()
        record = self.capture()
        self.assertEqual(record["saved_run_ids_returned_by_store"], [1])
        self.assertEqual(record["database"]["new_run_ids"], [1])
        self.assertEqual(record["database"]["signal_count"], 1)
        self.assertEqual(record["database"]["signals"][0]["run_id"], 1)
        self.assertEqual(record["database"]["signals"][0]["current_price"], 100.0)
        self.assertNotIn("RETURNED_RESULT_OR_PHYSICAL_RUN_CLOCK_BINDING_NOT_VERIFIED", record["qualification_reasons"])
        self.assertNotIn("PRODUCTION_PIPELINE_RESULT_NOT_COMPLETE", record["qualification_reasons"])

    def test_boolean_returned_run_id_cannot_alias_actual_integer_store_id(self):
        self.harness.market_fixture()
        self.harness.return_transform = lambda result: dict(result, run_id=True)
        record = self.capture()
        self.assertEqual(record["saved_run_ids_returned_by_store"], [1])
        self.assertIs(type(record["database"]["runs"][0]["run_id"]), int)
        self.binding_rejected(record)

    def test_float_returned_run_id_cannot_alias_actual_integer_store_id(self):
        self.harness.market_fixture()
        self.harness.return_transform = lambda result: dict(result, run_id=1.0)
        record = self.capture()
        self.assertEqual(record["database"]["new_run_ids"], [1])
        self.binding_rejected(record)

    def test_mismatched_integer_returned_run_id_preserves_actual_saved_rows(self):
        self.harness.market_fixture()
        self.harness.return_transform = lambda result: dict(result, run_id=result["run_id"] + 20)
        record = self.capture()
        self.binding_rejected(record)
        self.assertEqual(record["database"]["new_run_ids"], [1])
        self.assertEqual(record["database"]["signal_count"], 1)

    def test_returned_signal_run_and_row_identity_claims_cannot_be_silently_ignored(self):
        self.harness.market_fixture()
        for column in ("run_id", "id"):
            with self.subTest(column=column):
                def mutate(result):
                    result["signals"][column] = 987654
                    return result
                self.harness.return_transform = mutate
                record = self.capture(output_path=self.harness.output.parent / ("signal-" + column + ".json"))
                self.assertTrue(all(row["run_id"] in record["saved_run_ids_returned_by_store"] for row in record["database"]["signals"]))
                self.binding_rejected(record)

    def test_post_save_callback_failure_keeps_true_partial_subset_and_store_id(self):
        self.harness.market_fixture(tickers=("AAPL", "NVDA"))
        self.harness.selected_rows = ["AAPL"]
        def callback(value):
            if value == "SYNTHETIC_AFTER_COMMIT":
                raise RuntimeError("SYNTHETIC_CALLBACK_FAILURE_AFTER_ACTUAL_SAVE")
        self.harness.after_save = lambda pipeline, kwargs, run_id: kwargs["progress_callback"]("SYNTHETIC_AFTER_COMMIT")
        record = self.capture(tickers=["AAPL", "NVDA"], progress_callback=callback)
        self.assertEqual(record["saved_run_ids_returned_by_store"], [1])
        self.assertEqual(record["database"]["signal_count"], 1)
        self.assertEqual([row["ticker"] for row in record["database"]["signals"]], ["AAPL"])
        self.assertIn("SYNTHETIC_CALLBACK_FAILURE_AFTER_ACTUAL_SAVE", record["qualification_reasons"])
        self.assertIn("INCOMPLETE_OR_FAILED_SELECTED_RUN", record["qualification_reasons"])

    def late(self, mutate):
        fsync = target.os.fsync
        hits = []
        def changed(descriptor):
            fsync(descriptor)
            if not hits:
                hits.append(1)
                mutate()
        with patch.object(target.os, "fsync", side_effect=changed):
            record = self.capture()
        self.assertEqual(hits, [1])
        self.assertIn("POST_FSYNC_PUBLICATION_REVALIDATION_FAILED", record["qualification_reasons"])
        self.assertEqual(list(self.harness.output.parent.glob(".user_windows_analysis_*.tmp")), [])
        return record

    def test_fsync_late_extra_DB_run_is_reported_without_claiming_same_process_store_commit(self):
        self.harness.market_fixture()
        def add_run():
            columns = [field for field in target.RUN_COLUMNS if field != "run_id"]
            names = ",".join(columns)
            with closing(NATIVE_CONNECT(self.harness.db)) as connection:
                connection.execute("INSERT INTO runs(" + names + ") SELECT " + names + " FROM runs WHERE run_id=1")
                connection.commit()
        record = self.late(add_run)
        self.assertEqual(record["saved_run_ids_returned_by_store"], [1])
        self.assertEqual(record["database"]["new_run_ids"], [1, 2])
        self.assertEqual(record["database"]["new_run_count"], 2)
        self.assertEqual(record["database"]["signal_count"], 1)

    def test_fsync_late_source_drift_keeps_initial_pins_and_withholds_qualification(self):
        self.harness.market_fixture()
        original = self.harness.manifest.read_bytes()
        record = self.late(lambda: self.harness.manifest.write_bytes(original + b" "))
        self.assertEqual(record["saved_run_ids_returned_by_store"], [1])
        self.assertEqual(record["database"]["signal_count"], 1)
        self.assertEqual(record["publication_qualification"], "FAILED_UNQUALIFIED_OBSERVATION_ONLY")

    def test_fsync_late_wall_rollback_cannot_publish_verified_clock_integrity(self):
        self.harness.market_fixture()
        record = self.late(lambda: self.harness.clock.__setitem__(0, NOW - timedelta(microseconds=1)))
        self.assertFalse(record["clocks"]["integrity_verified"])
        self.assertEqual(record["saved_run_ids_returned_by_store"], [1])
        self.assertEqual(record["database"]["signal_count"], 1)

    def test_resealed_loaded_native_claim_is_never_reexecution_proof(self):
        self.harness.market_fixture()
        record = self.capture()
        resealed = deepcopy(record)
        resealed["native_windows_selected_analysis_verified"] = True
        destination = self.harness.root / "resealed_claim.json"
        destination.write_text(json.dumps(target._seal(resealed)), encoding="utf-8")
        with patch.object(target, "_runtime_facts", side_effect=AssertionError("Loader must not claim a current execution")):
            result = target.load_user_windows_analysis_receipt(destination)
        self.assertTrue(result["content_hash_verified"])
        self.assertTrue(result["loaded_json_is_not_execution_proof"])
        self.assertFalse(result["runtime_execution_reverified"])
        self.assertFalse(result["native_windows_selected_analysis_verified"])


if __name__ == "__main__":
    unittest.main()
