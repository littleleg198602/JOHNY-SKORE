"""Versioned fixture-lifetime regression proof; no HTTP or archive rewrites."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = ["tests.test_batch5_price_provenance", "tests.test_yahoo_ohlc_cache_store",
           "tests.test_prediction_label_service", "tests.test_batch4_evaluation_label_persistence"]
LOG = ROOT / "evidence/batch5_price_sqlite_close_20261014.log"
OUT = ROOT / "evidence/batch5_price_sqlite_close_20261014.json"
suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
discovered = suite.countTestCases()
started = time.perf_counter()
with LOG.open("w", encoding="utf-8") as handle:
    tested = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
elapsed = time.perf_counter() - started
owned = ["tests/test_batch5_price_provenance.py", "evidence/batch5_price_sqlite_close_20261014.py"]
audited = ["market_checker_app/storage/yahoo_ohlc_cache_store.py",
           "market_checker_app/storage/sqlite_connection.py",
           "market_checker_app/services/yahoo_price_provenance_service.py"]
old = ["evidence/batch5_prices_20261014/handoff.json", "evidence/batch5_prices_20261014/live_probe.json",
       "evidence/batch5_prices_20261014/reproduce.py", "evidence/batch5_prices_20261014/tests.log"]
proof = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
    "local_base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "trigger": {"published_head": "e550b334", "windows_run_id": "38551067090", "full_tests_executed": 1371,
        "reported_cleanup_errors": 12, "prices_lane_cleanup_errors": 2,
        "error": "TemporaryDirectory cleanup WinError 32 history.db",
        "failed_tests": ["test_changed_cache_bytes_without_matching_capture_are_corrupt_and_not_overwritten",
                         "test_legacy_cache_without_capture_remains_usable_and_unproven"],
        "original_windows_artifact_preserved_by_coordinator": True,
        "prior_windows_run_successful": False},
    "correction": {"explicit_fixture_connection_blocks": 3,
        "transaction_context": "native sqlite3.Connection commit/rollback",
        "lifetime_context": "contextlib.closing finally close",
        "corrupt_cache_hash_and_unknown_provenance_assertions_preserved": True,
        "production_connection_factory": "ClosingSQLiteConnection already closes in finally",
        "production_code_changed": False,
        "linux_visible_regressions": ["retained connection rejects SELECT 1 after commit",
            "retained connection rejects SELECT 1 after exception with rollback verified",
            "all connections created by the two actual failing fixtures are retained and probed",
            "negative control detects unclosed native transaction context on Linux"],
        "ignore_cleanup_gc_sleep_or_retry_used": False},
    "tests": {"modules": MODULES, "discovered": discovered, "executed": tested.testsRun,
        "failures": len(tested.failures), "errors": len(tested.errors), "skips": len(tested.skipped),
        "expected_failures": len(tested.expectedFailures), "unexpected_successes": len(tested.unexpectedSuccesses),
        "successful": tested.wasSuccessful(), "elapsed_seconds": round(elapsed, 6),
        "log_path": LOG.relative_to(ROOT).as_posix(), "log_sha256": hashlib.sha256(LOG.read_bytes()).hexdigest()},
    "owned_file_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in owned},
    "audited_unchanged_production_file_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in audited},
    "preserved_old_handoff_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in old},
    "claims": {"fresh_http_requests": 0, "price_data_changed": False, "score_changed": False,
        "target_formula_changed": False, "sixty_second_budget_changed": False,
        "windows_current_fix_verified": False, "specialist_acceptance_verified": False},
    "limitations": ["Focused Linux regressions establish deterministic close behavior; this is not a successful new hosted Windows run.",
        "The old hash-pinned batch5_prices_20261014/reproduce.py is preserved as its original version; its standard SQLite diagnostic context remains an old-version limitation.",
        "The coordinator handles the separate SEC/IR cleanup fixtures and preserves the full original failing Windows artifacts."]}
OUT.write_text(json.dumps(proof, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps(proof["tests"], sort_keys=True))
raise SystemExit(0 if tested.wasSuccessful() else 1)
