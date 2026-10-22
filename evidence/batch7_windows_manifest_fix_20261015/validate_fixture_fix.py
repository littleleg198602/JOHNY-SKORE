"""Offline qualification of the fixture fix; never a Windows acceptance claim."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time
import unittest
from unittest.mock import patch

from tests import test_batch7_source_manifest as current


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ORIGINAL_SHA256 = "617e30e88b89c62c70888d9c1ae004b31eec5b11b2668713fd6de492bf159cbe"
SERVICE_SHA256 = "62eb0605953f5622f48dbb7246d64f9ab7fc5ad04745aa006510ea0a586fb005"
CASES = (
    "test_selected_source_symlink_or_symlink_parent_is_refused_before_body_reads",
    "test_symlink_active_lock_and_version_directory_are_not_followed",
    "test_windows_junction_aliases_cannot_escape_source_or_version_roots",
    "test_canonical_reparse_targets_work_after_posix_and_windows_root_alias_resolution",
    "test_actual_directory_reparse_is_rejected_for_sources_and_publication",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assertions(path):
    tree = ast.parse(path.read_bytes())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SourceManifestTests")
    result = {}
    for method in cls.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        calls = sorted((node for node in ast.walk(method) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "self" and node.func.attr.startswith("assert")),
                       key=lambda node: (node.lineno, node.col_offset))
        result[method.name] = [ast.dump(node, include_attributes=False) for node in calls]
    return result


def run_suite(suite, filename):
    started_at = datetime.now(timezone.utc).isoformat()
    began = time.monotonic()
    with (HERE / filename).open("x", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return {"started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.monotonic() - began, "executed_methods": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
            "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
            "successful": result.wasSuccessful(), "log": filename}


def run():
    original_path = HERE / "original_test_batch7_source_manifest.py"
    current_path = ROOT / "tests/test_batch7_source_manifest.py"
    service_path = ROOT / "market_checker_app/services/prospective_source_manifest_service.py"
    if digest(original_path) != ORIGINAL_SHA256 or digest(service_path) != SERVICE_SHA256:
        raise AssertionError("Original test or unchanged production service does not match its pin")
    before, after = assertions(original_path), assertions(current_path)
    for method, original_assertions in before.items():
        pending = iter(after[method])
        for original_assertion in original_assertions:
            if not any(candidate == original_assertion for candidate in pending):
                raise AssertionError("Original assertion removed or changed: " + method)

    focused = run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(current.SourceManifestTests),
                        "focused_tests.log")
    real_temporary_directory = current.tempfile.TemporaryDirectory
    alias_pairs = []

    class AliasedTemporaryDirectory:
        def __init__(self, *args, **kwargs):
            self.original = real_temporary_directory(*args, **kwargs)
            canonical = Path(self.original.name).resolve()
            alias = canonical / ".." / canonical.name
            if alias == canonical or alias.resolve() != canonical:
                raise AssertionError("The injected temporary root must have a distinct spelling")
            self.name = str(alias)
            alias_pairs.append({"temporary_name": self.name, "canonical": str(canonical),
                                "distinct_spelling": True, "same_resolved_directory": True})

        def cleanup(self):
            self.original.cleanup()

    with patch.object(current.tempfile, "TemporaryDirectory", AliasedTemporaryDirectory):
        aliased = run_suite(unittest.TestSuite(current.SourceManifestTests(name) for name in CASES),
                            "after_fix_alias_reproduction.log")
    record = {"schema_version": "source_manifest_fixture_fix_qualification_v1",
              "platform": platform.platform(), "python_version": platform.python_version(),
              "native_windows_executed": platform.system() == "Windows",
              "native_8dot3_available": "NOT_OBSERVED_ON_THIS_POSIX_HOST",
              "production_service_changed": False, "production_service_sha256": digest(service_path),
              "original_test_sha256": digest(original_path), "current_test_sha256": digest(current_path),
              "original_assertion_calls_preserved": sum(map(len, before.values())),
              "original_test_methods_preserved": sum(name.startswith("test_") for name in before),
              "new_test_methods": sorted(set(after) - set(before)),
              "focused": focused, "forced_distinct_temporary_root_alias": aliased,
              "forced_alias_pairs": alias_pairs, "source_http_requests": 0,
              "synthetic_fixtures_are_acceptance": False}
    with (HERE / "fixture_qualification.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("original_assertion_calls_preserved", "new_test_methods", "focused",
                                                  "forced_distinct_temporary_root_alias", "current_test_sha256")}))
    if not focused["successful"] or not aliased["successful"]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
