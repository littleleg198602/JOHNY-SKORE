"""Reproduce the original fixture's nine alias misses without changing guards.

A real POSIX path containing an existing ``..`` alias resolves to the same
non-reparse directory. Like Windows short/long names, its lexical spelling
differs from the canonical root used by the service. This is an offline
fixture reproduction, not a claim that this host executed native Windows.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
ORIGINAL_SHA256 = "617e30e88b89c62c70888d9c1ae004b31eec5b11b2668713fd6de492bf159cbe"
CASES = (
    "test_selected_source_symlink_or_symlink_parent_is_refused_before_body_reads",
    "test_symlink_active_lock_and_version_directory_are_not_followed",
    "test_windows_junction_aliases_cannot_escape_source_or_version_roots",
)


def run():
    path = HERE / "original_test_batch7_source_manifest.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("The frozen original fixture changed")
    spec = importlib.util.spec_from_file_location("original_source_manifest_fixture", path)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    setup = original.SourceManifestTests.setUp
    def alias_setup(case):
        setup(case)
        canonical = case.root.resolve()
        alias = canonical / ".." / canonical.name
        if alias == canonical or alias.resolve() != canonical:
            raise ValueError("The fixture does not exercise a distinct canonical spelling")
        case.root = alias
        case.asset = alias / "selected_sources" / case.asset.name
        case.benchmark = alias / "selected_sources" / case.benchmark.name
        case.service = original.ProspectiveSourceManifestService(alias, observation_clock=lambda:original.NOW)
        case.row = original.entry(alias, [case.asset, case.benchmark])
    original.SourceManifestTests.setUp = alias_setup
    suite = unittest.TestSuite(original.SourceManifestTests(name) for name in CASES)
    with (HERE / "before_fix_alias_reproduction.log").open("x", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    record = {"platform": "POSIX_ALIAS_REPRODUCTION; NOT NATIVE WINDOWS", "original_test_sha256": ORIGINAL_SHA256,
              "executed_methods": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skips": len(result.skipped), "expected_failures": len(result.expectedFailures),
              "unexpected_successes": len(result.unexpectedSuccesses), "production_guard_changed": False,
              "source_http_requests": 0, "reproduced_failure_methods": [case.id() for case, _ in result.failures]}
    with (HERE / "before_fix_alias_reproduction.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    run()
