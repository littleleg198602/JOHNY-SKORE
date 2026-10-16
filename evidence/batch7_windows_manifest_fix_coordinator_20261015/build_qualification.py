"""Write immutable pre-hosted-CI qualification from actual saved test results."""
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "bfedb764da3a189df1d9f8eb799cf834867f8083"
FOLDER = ROOT / "evidence/batch7_windows_manifest_fix_coordinator_20261015"
CRITICAL = [
    "test_selected_source_symlink_or_symlink_parent_is_refused_before_body_reads",
    "test_symlink_active_lock_and_version_directory_are_not_followed",
    "test_windows_junction_aliases_cannot_escape_source_or_version_roots",
    "test_canonical_reparse_targets_work_after_posix_and_windows_root_alias_resolution",
    "test_actual_directory_reparse_is_rejected_for_sources_and_publication",
]


def load(name):
    return json.loads((ROOT / name).read_bytes())


def pin(name):
    body = (ROOT / name).read_bytes()
    return {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def assertion_calls(body):
    return Counter(ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(body))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("assert"))


failed = load("evidence/batch7_windows_failure_20261015/content_verification.json")
assert failed["windows_report"]["failures"] == 9 and not failed["windows_report"]["successful"]
handoff = load("evidence/batch7_windows_manifest_fix_20261015/handoff.json")
for name, expected in handoff["evidence_files"].items():
    actual = pin("evidence/batch7_windows_manifest_fix_20261015/" + name)
    assert (actual["sha256"], actual["bytes"]) == (expected["sha256"], expected["bytes"])
for name in ("current_test", "unchanged_service"):
    expected = handoff[name]
    assert pin(expected["path"])["sha256"] == expected["sha256"]
audit = load("evidence/batch7_windows_manifest_fix_coordinator_20261015/content_audit.json")
assert audit["all_prior_published_evidence_byte_identical"] and audit["all_production_files_byte_identical"]
old = (ROOT / "evidence/batch7_windows_manifest_fix_20261015/original_test_batch7_source_manifest.py").read_bytes()
new = (ROOT / "tests/test_batch7_source_manifest.py").read_bytes()
old_calls, new_calls = assertion_calls(old), assertion_calls(new)
assert sum(old_calls.values()) == 106
assert not old_calls - new_calls
report = load("evidence/batch7_linux_manifest_fix_20261015.json")
assert report["discovered_tests"] == report["tests_run"] == 1835 and report["successful"] is True
assert all(report[key] == 0 for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
console = (ROOT / "evidence/batch7_linux_manifest_fix_console_20261015.log").read_bytes()
assert re.search(rb"Ran 1835 tests in [\d.]+s\r?\n\r?\nOK\r?\n\Z", console)
assert not re.search(rb"^FAIL: |^ERROR: |^FAILED |^OK \(", console, re.M)
for method in CRITICAL:
    assert re.search(rb"^" + method.encode() + rb" \([^\r\n]+\) \.\.\. ok\r?$", console, re.M), method
proof = {
    "recorded_at": datetime.now(timezone.utc).isoformat(), "pre_hosted_ci": True,
    "expected_public_parent": BASE,
    "tested_code_snapshot": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
    "worker_commit": "2169cca3517344dc815ec0961bd23faa209f6cd1",
    "scope": "Test fixture identity and stronger alias/reparse regressions only",
    "original_failed_windows_head": failed["head_sha"], "original_windows_failure_count": 9,
    "original_windows_artifact": pin("evidence/batch7_windows_failure_20261015/artifact_11958427081.zip"),
    "original_windows_json": pin("evidence/batch7_windows_failure_20261015/windows-tests.json"),
    "original_windows_log": pin("evidence/batch7_windows_failure_20261015/windows-tests.log"),
    "preserved_original_assertion_calls_total": sum(old_calls.values()),
    "assertion_scope_note": "103 calls in the 31 original test methods plus 3 in the unchanged assertNoPublication helper",
    "new_test_methods": audit["new_test_methods"], "critical_regressions_executed_locally": CRITICAL,
    "local_result": report, "local_plain_console_ok": True,
    "local_json": pin("evidence/batch7_linux_manifest_fix_20261015.json"),
    "local_full_console": pin("evidence/batch7_linux_manifest_fix_console_20261015.log"),
    "separate_runner_log": pin("evidence/batch7_linux_manifest_fix_runner_20261015.log"),
    "worker_handoff": pin("evidence/batch7_windows_manifest_fix_20261015/handoff.json"),
    "content_audit": pin("evidence/batch7_windows_manifest_fix_coordinator_20261015/content_audit.json"),
    "repaired_test": pin("tests/test_batch7_source_manifest.py"),
    "production_helper": pin("market_checker_app/services/prospective_source_manifest_service.py"),
    "prior_evidence_files_byte_identical": audit["prior_evidence_files"],
    "prior_evidence_bytes_byte_identical": audit["prior_evidence_bytes"],
    "all_205_production_files_byte_identical": True,
    "new_source_http_requests": 0, "quota_db_reset_or_reconstruction": False,
    "new_hosted_windows_verified": False, "required_next": "Exact new-head CI with original Windows ZIP/JSON/log and all required checks",
    "full_specialist_acceptance_count": 0, "user_windows_or_genuine_oos_verified": False,
    "score_or_trading_changed": False,
}
with (ROOT / "evidence/batch7_windows_manifest_fix_20261015.json").open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(proof, indent=2) + "\n")
print(json.dumps({key: proof[key] for key in ("tested_code_snapshot", "local_result", "preserved_original_assertion_calls_total", "prior_evidence_files_byte_identical")}))
