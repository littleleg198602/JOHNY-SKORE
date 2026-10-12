from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app import healthcare_coverage as cli
from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import CANONICAL_CSV_SHA256, DEFAULT_TICKER_UNIVERSE_PATH


RECORDS = json.loads(DEFAULT_HEALTHCARE_IDENTITIES.read_text())["records"]
NOW = max(datetime.fromisoformat(row["known_at"]) for row in RECORDS) + timedelta(seconds=1)
ROOT = Path(__file__).resolve().parents[1]


class HealthcareCoverageCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = self.directory / "database" / "scout.db"
        self.output = self.directory / "reports" / "coverage.json"
        self.env = patch.dict(os.environ, {"JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, args=None):
        output = io.StringIO()
        arguments = ["--db-path", str(self.db), "--output", str(self.output), "--as-of", NOW.isoformat()]
        with redirect_stdout(output):
            cli.main(arguments if args is None else args)
        return json.loads(output.getvalue())

    def test_absent_db_saves_truthful_report_and_never_initializes_store(self):
        with patch.object(ScoutStore, "__init__", side_effect=AssertionError("No DB initialization allowed")):
            summary = self.run_cli()
        report = json.loads(self.output.read_text())
        self.assertFalse(self.db.exists())
        self.assertFalse(self.db.parent.exists())
        self.assertEqual("DATABASE_NOT_FOUND", report["runtime"]["status"])
        self.assertEqual((687, CANONICAL_CSV_SHA256), (report["universe"]["subjects"], report["universe"]["csv_sha256"]))
        self.assertEqual("DEFAULT", report["inputs"]["healthcare_identities_configuration"])
        self.assertFalse(report["inputs"]["database_initialized"])
        self.assertFalse(report["inputs"]["network_collectors_run"])
        self.assertFalse(summary["completion_verified"])
        self.assertEqual([], list(self.output.parent.glob("*.tmp")))

    def test_configured_registry_is_the_one_reported(self):
        registry = self.directory / "custom-registry.json"
        registry.write_text(json.dumps({"schema_version": 1, "record_count": 1, "records": RECORDS[:1]}))
        os.environ["JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE"] = str(registry)
        self.run_cli()
        report = json.loads(self.output.read_text())
        self.assertEqual(str(registry), report["inputs"]["healthcare_identities_path"])
        self.assertEqual("JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE", report["inputs"]["healthcare_identities_configuration"])
        self.assertEqual(hashlib.sha256(registry.read_bytes()).hexdigest(), report["identity_manifest"]["sha256"])
        self.assertEqual(1, report["identity_manifest"]["record_count"])
        self.assertEqual(0, report["sources"]["cms"]["documented_issuer_object_subjects"])

    def test_existing_db_is_read_only_and_scoped_checks_are_reused(self):
        store = ScoutStore(self.db)
        store.observe_sec_identity(subject_id="LLY", cik="0000059478", company_name="Eli Lilly and Company", as_of=NOW)
        registry = HealthcareIdentityRegistry(RECORDS)
        version = registry.identity_versions(store, "fda", as_of=NOW)["LLY"]
        store.record_specialist_check("fda", subject_id="LLY", identity_key="0000059478:Eli Lilly and Company",
                                     as_of=NOW, candidate_count=0, truncated=False, identity_version=version)
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with patch.object(ScoutStore, "__init__", side_effect=AssertionError("No migration allowed")):
            summary = self.run_cli()
        self.assertEqual(digest, hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual("READY", summary["runtime_status"])
        self.assertEqual((1, 1), (summary["sources"]["fda"]["mapped_subjects"],
                                  summary["sources"]["fda"]["current_complete_source_checks"]))

    def test_invalid_time_is_rejected_before_output_or_db_creation(self):
        for value in ("2026-10-11T12:00:00", "invalid", "X" * 65, ""):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.run_cli(["--db-path", str(self.db), "--output", str(self.output), "--as-of", value])
            self.assertFalse(self.output.parent.exists())
            self.assertFalse(self.db.parent.exists())

    def test_z_and_offset_cutoffs_normalize_to_utc(self):
        text = NOW.isoformat().replace("+00:00", "Z")
        self.run_cli(["--db-path", str(self.db), "--output", str(self.output), "--as-of", text])
        first = self.output.read_text()
        local = NOW.astimezone(timezone(timedelta(hours=2)))
        self.run_cli(["--db-path", str(self.db), "--output", str(self.output), "--as-of", local.isoformat()])
        self.assertEqual(first, self.output.read_text())

    def test_invalid_paths_and_non_json_output_are_bounded(self):
        for output in (self.directory / "file.db", "x" * 4097, "bad\nfile.json", "https://host/report.json"):
            with self.subTest(output=output), self.assertRaises(SystemExit):
                self.run_cli(["--db-path", str(self.db), "--output", str(output)])
        self.assertFalse(self.db.parent.exists())
        self.assertFalse(self.output.parent.exists())

    def test_output_cannot_overwrite_configured_registry_or_json_database(self):
        for kind in ("registry", "db"):
            path = self.directory / f"protected-{kind}.json"
            path.write_text("original-protected-content")
            if kind == "registry":
                os.environ["JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE"] = str(path)
            with self.subTest(kind=kind), self.assertRaises(SystemExit):
                self.run_cli(["--db-path", str(path) if kind == "db" else str(self.db), "--output", str(path)])
            self.assertEqual("original-protected-content", path.read_text())

    def test_output_cannot_overwrite_any_production_registry(self):
        before = DEFAULT_HEALTHCARE_IDENTITIES.read_bytes()
        for path in (DEFAULT_HEALTHCARE_IDENTITIES, DEFAULT_TICKER_UNIVERSE_PATH,
                     DEFAULT_TICKER_UNIVERSE_PATH.parent / "unrelated.json"):
            with self.subTest(path=path), self.assertRaises(SystemExit):
                self.run_cli(["--db-path", str(self.db), "--output", str(path)])
        self.assertEqual(before, DEFAULT_HEALTHCARE_IDENTITIES.read_bytes())
        self.assertFalse((DEFAULT_TICKER_UNIVERSE_PATH.parent / "unrelated.json").exists())

    def test_hardlink_and_symlink_output_aliases_cannot_overwrite_protected_input(self):
        protected = self.directory / "protected.json"
        protected.write_text("protected-original")
        os.environ["JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE"] = str(protected)
        for link_type in ("hard", "symbolic"):
            alias = self.directory / f"{link_type}.json"
            try:
                if link_type == "hard":
                    os.link(protected, alias)
                else:
                    alias.symlink_to(protected)
            except OSError:
                if link_type == "symbolic":
                    continue  # Windows may not grant symlink creation privileges.
                raise
            with self.subTest(link_type=link_type), self.assertRaises(SystemExit):
                self.run_cli(["--db-path", str(self.db), "--output", str(alias)])
            self.assertEqual("protected-original", protected.read_text())

    def test_parent_directory_alias_into_production_is_rejected(self):
        alias = self.directory / "production-alias"
        try:
            alias.symlink_to(DEFAULT_TICKER_UNIVERSE_PATH.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Platform does not allow directory symlinks")
        with self.assertRaises(SystemExit):
            self.run_cli(["--db-path", str(self.db), "--output", str(alias / "forbidden.json")])
        self.assertFalse((DEFAULT_TICKER_UNIVERSE_PATH.parent / "forbidden.json").exists())

    def test_existing_report_survives_serialization_and_replace_failures(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_text("previous report")
        for target, error in (("json.dump", ValueError("serialization failed")),
                              ("os.replace", OSError("replace failed"))):
            with self.subTest(target=target), patch(f"market_checker_app.healthcare_coverage.{target}", side_effect=error), self.assertRaises(SystemExit):
                self.run_cli()
            self.assertEqual("previous report", self.output.read_text())
            self.assertEqual([], list(self.output.parent.glob(".healthcare_coverage_*.tmp")))
            self.assertFalse(self.db.exists())

    def test_missing_or_bad_registry_is_saved_as_a_gap(self):
        registry = self.directory / "not-present.json"
        os.environ["JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE"] = str(registry)
        summary = self.run_cli()
        self.assertEqual("UNAVAILABLE_OR_INVALID", summary["identity_manifest_status"])
        self.assertEqual(0, summary["sources"]["fda"]["mapped_subjects"])
        self.assertFalse(registry.exists())

    def test_subprocess_module_runs_from_spaced_directory_without_app_or_database(self):
        cwd = self.directory / "Folder with spaces"
        cwd.mkdir()
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        result = subprocess.run([sys.executable, "-m", "market_checker_app.healthcare_coverage",
            "--as-of", NOW.isoformat()], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(687, json.loads(result.stdout)["canonical_subjects"])
        report = json.loads((cwd / "outputs" / "healthcare_coverage_latest.json").read_text())
        self.assertEqual("DATABASE_NOT_FOUND", report["runtime"]["status"])
        self.assertFalse((cwd / "outputs" / "market_checker_history.db").exists())

    def test_windows_launcher_calls_only_the_offline_diagnostic(self):
        launcher = (ROOT / "Zkontrolovat_Zdravotnicke_Identity.bat").read_text()
        self.assertIn('cd /d "%~dp0"', launcher)
        self.assertIn("-m market_checker_app.healthcare_coverage", launcher)
        self.assertIn("market_checker_app\\.venv\\Scripts\\python.exe", launcher)
        self.assertIn('--output "outputs\\healthcare_coverage_latest.json"', launcher)
        self.assertNotIn("scout_runner", launcher)
        self.assertNotIn("app.py", launcher)


if __name__ == "__main__":
    unittest.main()
