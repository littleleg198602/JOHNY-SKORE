"""Runtime qualification of actual retained reviews and failure-before-I/O guards."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.services import captured_identity_review_service as review
from market_checker_app import specialist_evidence_runner as runner
from market_checker_app.specialist_evidence_ui import available_review_plans

ROOT = Path(__file__).resolve().parents[1]
PLANS = "evidence/specialist_review_plans/"


class CapturedEvidenceActionTests(unittest.TestCase):
    def test_actual_operational_runtime_cannot_assign_group_count_to_one_issuer(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            result = review.run_captured_identity_review(source_root=ROOT,
                plan_name=PLANS + "DOW_provozni_rozsah_skupiny.json", archive_root=Path(directory))
        self.assertEqual("CANDIDATE_REVIEWED", result["review"]["status"])
        candidate = result["review"]["candidate"]
        self.assertEqual("COMBINED_REPORT_GROUP", candidate["scope_kind"])
        self.assertFalse(result["issuer_attribution_allowed"])
        self.assertFalse(result["scoring_applied"])
        self.assertEqual(0, result["registry_adoptions"])

    def test_actual_epa_runtime_keeps_partial_facility_and_exact_parent_cik(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            result = review.run_captured_identity_review(source_root=ROOT,
                plan_name=PLANS + "DOW_EPA_provozovna.json", archive_root=Path(directory))
        self.assertEqual("PARTIAL", result["review"]["status"])
        self.assertTrue(result["review"]["issuer_identity_supported"])
        self.assertFalse(result["review"]["facility_identity_supported"])
        self.assertFalse(result["review"]["ownership_statement_supported"])
        self.assertEqual("0001751788", result["review"]["issuer_identity_fact"]["issuer_cik"])
        self.assertEqual(0, result["registry_adoptions"])

    def test_real_hca_and_person_plans_archive_exact_results_without_registry_or_network(self):
        paths = ["HCA_nemocnice_1.json", "HCA_nemocnice_2.json", "MSFT_Form4_EVENT_DATE.json",
                 "MSFT_Form4_OBSERVED_SNAPSHOT.json"]
        registries = {path: path.read_bytes() for path in (ROOT / "market_checker_app/data").glob("verified_*.json")}
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            for name in paths:
                with self.subTest(name=name):
                    before = datetime.now(timezone.utc)
                    result = review.run_captured_identity_review(source_root=ROOT,
                        plan_name=PLANS + name, archive_root=Path(directory))
                    self.assertGreaterEqual(datetime.fromisoformat(result["recorded_at"]), before)
                    self.assertEqual(0, result["source_http_requests"])
                    self.assertEqual(0, result["registry_adoptions"])
                    self.assertFalse(result["full_specialist_acceptance"])
                    self.assertFalse(result["issuer_attribution_allowed"])
                    raw = (Path(directory) / result["archive_filename"]).read_bytes()
                    self.assertEqual(result["archive_sha256"], hashlib.sha256(raw).hexdigest())
                    saved = json.loads(raw)
                    self.assertEqual(result["review"], saved["review"])
                    if name.startswith("HCA"):
                        self.assertEqual("WITHHOLD", result["review"]["decision"])
                        self.assertIsNone(result["review"]["proposed_record"])
                    else:
                        self.assertEqual(8, result["review"]["source_row_count"])
                        self.assertTrue(all(row["status"] == "WITHHELD" for row in result["review"]["row_reviews"]))
        self.assertTrue(all(path.read_bytes() == raw for path, raw in registries.items()))

    def test_real_merger_runtime_preserves_canonical_universe_and_q3_gap(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            result = review.run_captured_identity_review(source_root=ROOT,
                plan_name=PLANS + "LEG_vymena_instrumentu.json", archive_root=Path(directory))
        bridge = result["review"]["corporate_action_bridge_reviews"][0]
        self.assertEqual("PRIMARY_BRIDGE_PARTIAL", bridge["status"])
        self.assertTrue(bridge["blockers"])
        self.assertEqual(0, result["registry_adoptions"])
        self.assertFalse(result["scoring_applied"])

    def test_cutoff_excludes_later_capture_before_any_body_is_opened(self):
        cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
        plan = {"captures": {"source": {"metadata": {"observed_at": (cutoff + timedelta(days=1)).isoformat()},
            "body_available": True, "body_ref": {"path": "missing.body", "sha256": "0" * 64, "bytes": 1}}}}
        with patch.object(Path, "open", side_effect=AssertionError("A later original body was opened")):
            with self.assertRaisesRegex(ValueError, "unavailable at the requested cutoff"):
                review._load_captures(plan, ROOT, cutoff)

    def test_withheld_and_metadata_only_descriptors_never_resolve_substitute_body(self):
        now = datetime.now(timezone.utc)
        missing = {"metadata": {"retention": "MISSING_BODY"}, "body_available": False, "body_ref": None}
        with patch.object(Path, "open", side_effect=AssertionError("Unavailable original body was opened")):
            self.assertEqual({"source": (missing["metadata"], None)},
                review._load_captures({"captures": {"source": missing}}, ROOT, now))
            substitute = deepcopy(missing)
            substitute["body_available"] = True
            substitute["body_ref"] = {"path": "substitute.body", "sha256": "0" * 64, "bytes": 1}
            with self.assertRaisesRegex(ValueError, "cannot be restored"):
                review._load_captures({"captures": {"source": substitute}}, ROOT, now)
            missing["body_ref"] = substitute["body_ref"]
            with self.assertRaisesRegex(ValueError, "substitute file"):
                review._load_captures({"captures": {"source": missing}}, ROOT, now)

    def test_changed_raw_body_fails_exact_pin_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw.body").write_bytes(b"changed")
            plan = {"captures": {"source": {"metadata": {}, "body_available": True,
                "body_ref": {"path": "raw.body", "sha256": hashlib.sha256(b"original").hexdigest(), "bytes": 8}}}}
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                review._load_captures(plan, root, datetime.now(timezone.utc))
            self.assertEqual(["raw.body"], [path.name for path in root.iterdir()])

    def test_total_body_budget_is_charged_before_next_body_read(self):
        item = {"metadata": {}, "body_available": True,
                "body_ref": {"path": "not-read.body", "sha256": "0" * 64, "bytes": 9}}
        with patch.object(review, "MAX_TOTAL_BYTES", 8), \
             patch.object(Path, "open", side_effect=AssertionError("Over-budget body was opened")):
            with self.assertRaisesRegex(ValueError, "byte budget exceeded"):
                review._load_captures({"captures": {"source": item}}, ROOT, datetime.now(timezone.utc))

    def test_relative_original_paths_refuse_windows_posix_and_parent_escapes(self):
        for name in ("/etc/passwd", "../outside.body", "a/../body", "C:\\outside", "a\\body", "a//body", "./body"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                review.retained_file(ROOT, name)
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(ValueError):
            review.retained_file(ROOT, PLANS + "HCA_nemocnice_1.json")

    def test_duplicate_nonfinite_and_escaped_access_bearing_plans_are_wholly_refused(self):
        for body in (b'{"kind":1,"kind":2}', b'{"value":NaN}', b'{"password":"synthetic-fixture-only"}',
                     b'{"pass\\u0077ord":"synthetic-fixture-only"}'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                review._json_bytes(body)

    def test_archive_is_content_addressed_and_never_overwrites_a_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name, digest = review._publish({"value": "first"}, root)
            self.assertEqual((name, digest), review._publish({"value": "first"}, root))
            (root / name).write_bytes(b"different existing bytes")
            with self.assertRaisesRegex(ValueError, "collision"):
                review._publish({"value": "first"}, root)
            self.assertEqual(b"different existing bytes", (root / name).read_bytes())
            self.assertEqual(1, len(list(root.iterdir())))

    def test_cli_real_review_and_failed_selection_return_truthful_http_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["review", "--source-root", str(ROOT), "--plan", PLANS + "HCA_nemocnice_1.json",
                    "--archive-root", directory]
            output = io.StringIO()
            with redirect_stdout(output), patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
                runner.main(args)
            self.assertEqual("WITHHOLD", json.loads(output.getvalue())["review"]["decision"])
            args[args.index("--plan") + 1] = "../missing"
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as error:
                runner.main(args)
            self.assertEqual(1, error.exception.code)
            self.assertEqual("INVALID_RETAINED_EVIDENCE", json.loads(output.getvalue())["status"])
            self.assertNotIn("../missing", output.getvalue())

    def test_prime_validation_has_no_store_and_does_not_promote_an_incomplete_manifest(self):
        original = ROOT / "evidence/batch3_contracts_live_20261012/capture-6524208d7c5a40c4b5e36637aded7d32.json"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.storage.scout_store.ScoutStore", side_effect=AssertionError("Validation opened a DB")), \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            root = Path(directory)
            (root / "summary.json").write_bytes(original.read_bytes())
            plan = {"schema_version": runner.PRIME_PLAN_VERSION, "subject_id": "LMT", "manifests": [
                {"path": "summary.json", "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}]}
            (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            result = runner.run_prime_archive_recovery(db_path=None, source_root=root, plan_name="plan.json",
                archive_root=root / "reports")
            self.assertEqual("REJECTED", result["review"]["status"])
            self.assertTrue(result["validate_only"])
            self.assertEqual(0, result["registry_adoptions"])
            self.assertEqual(["plan.json", "reports", "summary.json"], sorted(p.name for p in root.iterdir()))
            result = runner.run_prime_archive_recovery(db_path=root / "not-created.db", source_root=root,
                plan_name="plan.json", archive_root=root / "reports", validate_only=False)
            self.assertEqual("REJECTED", result["review"]["status"])
            self.assertFalse((root / "not-created.db").exists())

    def test_actual_empty_prospective_runtime_report_never_creates_forecast_or_database(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected HTTP")):
            history = Path(directory) / "absent-history"
            result = runner.run_prospective_history_action(action="report", history_directory=history)
            self.assertEqual(0, result["registered_forecast_count"])
            self.assertEqual(0, result["eligible_real_label_count"])
            self.assertEqual("WAIT_PROSPECTIVE_HISTORY", result["status"])
            self.assertFalse(history.exists())
            self.assertEqual([], list(Path(directory).iterdir()))
            with self.assertRaises(ValueError):
                runner.run_prospective_history_action(action="report", history_directory=history,
                    label_observation_name="later-label.json")

    def test_missing_database_is_not_created_by_import_or_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            with self.assertRaises(ValueError):
                runner._existing_database(path)
            self.assertFalse(path.exists())

    def test_available_plans_contain_actual_named_bounded_cases(self):
        plans = available_review_plans(ROOT)
        self.assertIn(PLANS + "HCA_nemocnice_1.json", plans)
        self.assertIn(PLANS + "MSFT_Form4_EVENT_DATE.json", plans)
        self.assertIn(PLANS + "LEG_vymena_instrumentu.json", plans)
        self.assertLessEqual(len(plans), 100)


if __name__ == "__main__":
    unittest.main()
