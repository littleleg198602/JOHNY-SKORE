"""Read-only qualification of the CI-only batch9 Windows headroom repair."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = "b0e866de120c8d6366b93c8fef26916f0db4aa0e"
WORKFLOW = ".github/workflows/market-checker-test-agents.yml"
WORKER_COMMIT = "63e60a35a1f0c9a5cd450d70aa68e0af08f74f45"
FORBIDDEN_WHOLE_BODIES = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def pin(path):
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": sha(data)}


def main():
    baseline_path = Path(__file__).with_name("public_b0e_baseline_pins.json")
    baseline = json.loads(baseline_path.read_bytes())
    groups = {}
    for group, files in baseline.items():
        for name, expected in files.items():
            data = (ROOT / name).read_bytes()
            assert len(data) == expected["bytes"], name
            assert sha(data) == expected["sha256"], name
            assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == expected["git_blob"], name
        groups[group] = {"files": len(files), "bytes": sum(p["bytes"] for p in files.values()), "byte_identical": True}
    assert groups == {
        "evidence": {"files": 1535, "bytes": 74512826, "byte_identical": True},
        "production": {"files": 218, "bytes": 3577034, "byte_identical": True},
        "tests": {"files": 188, "bytes": 2440692, "byte_identical": True},
    }

    before = yaml.safe_load(git("show", BASE + ":" + WORKFLOW))
    after = yaml.safe_load((ROOT / WORKFLOW).read_bytes())
    assert before["jobs"]["windows-agent"]["timeout-minutes"] == 40
    assert after["jobs"]["windows-agent"]["timeout-minutes"] == 60
    after["jobs"]["windows-agent"]["timeout-minutes"] = 40
    assert before == after, "Unexpected additional YAML semantic change"
    assert sha((ROOT / WORKFLOW).read_bytes()) == "49126bef29f9f5034077ffe6b5aa0f043c69b8feb6182969093f99424f4ecc38"

    worker = ROOT / "evidence/batch9_windows_timeout_fix_20261018"
    handoff = json.loads((worker / "handoff.json").read_bytes())
    assert sha((worker / "handoff.json").read_bytes()) == "4cb278bf15b57ab77b029e633b0d51b01a5ea3fe2622ed08bdcb425cd7543ebf"
    for name, expected in handoff["artifact_pins"].items():
        assert pin(worker / name) == expected, name
    assert handoff["semantic_differences"] == [{"after": 60, "before": 40, "path": ["jobs", "windows-agent", "timeout-minutes"]}]
    assert handoff["test_suites_executed"] == handoff["source_HTTP_requests"] == handoff["database_accesses"] == 0

    original_verifier = ROOT / "evidence/batch9_windows_cancelled_20261017/verify_originals.py"
    result = subprocess.run([sys.executable, str(original_verifier)], check=True, capture_output=True, text=True)
    original_result = json.loads(result.stdout)
    assert original_result["original_Windows_report_absent"] is True
    assert original_result["actual_completed_ok_blocks"] == 1944
    assert original_result["new_batch9_methods_ok"] == 194

    old_pre_ci = ROOT / "evidence/batch9_integration_20261017.json"
    assert sha(old_pre_ci.read_bytes()) == "58e47c8b93328b3e12e571d1ca83ad3a9396c92c441f058e37058a5a36c93899"
    quota_path = ROOT.parent / "JOHNY-batch4-integration-20261013/outputs/market_checker_history.db"
    quota_hash = sha(quota_path.read_bytes())
    assert quota_hash == "6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af"

    existing_changes = set(git("diff", "--name-only", BASE).decode().splitlines())
    allowed = {WORKFLOW, "PARALLEL_SPECIALIST_WORK.md", "SCOUT_PROGRESS.md", "SPECIALIST_COMPLETION.md", "SPECIALIST_IMPLEMENTATION_CHECKPOINT_20261001.md"}
    assert all(p in allowed or p.startswith("evidence/batch9_windows_timeout_fix_20261018/") for p in existing_changes)
    new_pins = {}
    for directory in [worker, original_verifier.parent]:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                expected = pin(path)
                assert expected["sha256"] not in FORBIDDEN_WHOLE_BODIES
                new_pins[str(path.relative_to(ROOT))] = expected

    print(json.dumps({
        "schema": "batch9_windows_ci_repair_integration_v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "public_base": BASE,
        "implementation_snapshot": git("rev-parse", "HEAD").decode().strip(),
        "isolated_worker_commit": WORKER_COMMIT,
        "worker_handoff": pin(worker / "handoff.json"),
        "baseline_pin_file": pin(baseline_path),
        "unchanged_groups": groups,
        "sole_yaml_semantic_change": {"path": ["jobs", "windows-agent", "timeout-minutes"], "before": 40, "after": 60},
        "workflow": pin(ROOT / WORKFLOW),
        "all_commands_steps_platform_python_upload_release_gate_unchanged": True,
        "canonical_687_and_60_second_test_budget_unchanged": True,
        "default_modelconfig_hash_unchanged": "e8e0a4b5e010122a7d7fa2f8d41eb9174e25a6a1a57e206cb315b1a552d0d5f2",
        "original_pre_CI_proof_immutable": pin(old_pre_ci),
        "original_cancelled_artifact_verification": original_result,
        "timeout_cause_is_inference_from_40_minute_configuration_and_elapsed_time": True,
        "explicit_GitHub_timeout_annotation_observed": False,
        "retained_DB_hash_read_only_no_SQL_open": quota_hash,
        "local_full_suites_repeated": 0,
        "reason_no_local_full_rerun": "All 218 production and 188 test files are byte-identical; this change only raises the hosted Windows job time cap. Existing 2231 local source qualification is immutable. The new public HEAD still requires its own hosted required checks.",
        "specialist_source_collector_requests": 0,
        "genuine_forecasts_registrations_labels_added": 0,
        "quota_registry_scoring_fitter_trading_mutations": 0,
        "new_head_hosted_qualification": "PENDING",
        "full_specialist_acceptance": "0/21",
        "production_source_test_and_original_evidence_bytes_unchanged": True,
        "new_evidence_pins": new_pins,
    }, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
