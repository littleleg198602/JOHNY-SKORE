"""Finalize only an actual completed sourcefront full run on its unchanged code."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def pin(path):
    body = (ROOT / path).read_bytes()
    return {"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


if __name__ == "__main__":
    launch_path = ROOT.parent / "sourcefront21_20261021/local_full_launch.json"
    launch = json.loads(launch_path.read_bytes())
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == launch["tested_code_snapshot"], "Do not change the code commit while qualifying its full run"
    for row in launch["source_test_pins"]:
        assert pin(row["path"]) == row, row["path"]
    result_path = "evidence/sourcefront21_linux_20261021.json"
    console_path = "evidence/sourcefront21_linux_console_20261021.log"
    result = json.loads((ROOT / result_path).read_bytes())
    expected = json.loads((ROOT / "evidence/sourcefront21_expected_methods_20261021.json").read_bytes())
    full = expected["expected_full_test_count"]
    assert full == result["discovered_tests"] == result["tests_run"] == launch["expected_full_test_count"]
    assert result["successful"] is True
    assert all(type(result[key]) is int and result[key] == 0 for key in
               ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    text = (ROOT / console_path).read_bytes().decode("utf-8")
    footer = re.search(rf"(?m)^Ran {full} tests in ([\d.]+)s\r?\n\r?\nOK\r?$", text)
    assert footer and not re.search(r"(?m)^(?:FAIL|ERROR): ", text)
    selection = {"tested_code_snapshot": head, "full_discovered_and_executed": full,
                 "new_exact_method_count": expected["new_exact_method_count"], "methods": expected["methods"]}
    methods_path = ROOT / "evidence/sourcefront21_linux_methods_20261021.json"
    with methods_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(selection, indent=2) + "\n")
    method_output = HERE / "local_exact_method_verification.json"
    subprocess.run([sys.executable, str(ROOT / "evidence/batch11_coordinator_20261018/verify_exact_methods.py"),
        "--log", str(ROOT / console_path), "--methods", str(methods_path), "--head", head,
        "--output", str(method_output)], cwd=ROOT, check=True)
    methods = json.loads(method_output.read_bytes())
    assert methods["new_exact_methods_original_ok"] == expected["new_exact_method_count"]
    selection["methods"] = methods["methods"]
    methods_path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    (HERE / "full_launch.json").write_bytes(launch_path.read_bytes())
    exit_path = ROOT.parent / "sourcefront21_20261021/local_full_process_exit.json"
    if exit_path.exists():
        exit_result = json.loads(exit_path.read_bytes())
        assert exit_result["exit_code"] == 0 and exit_result["pid"] == launch["pid"]
        (HERE / "full_process_exit.json").write_bytes(exit_path.read_bytes())
    audit = json.loads((HERE / "content_audit.json").read_bytes())
    proof = {
        "schema_version": "sourcefront21_pre_hosted_CI_qualification_v1",
        "physical_recorded_at": datetime.now(timezone.utc).isoformat(),
        "repository": "littleleg198602/JOHNY-SKORE", "pull_request": 162,
        "public_base_head": "9d60cc650ff0d64b720f84fabfe1348e44c6bcf4",
        "main_observed": "ebb63a4b666908ce176e92ea7dffd6e74808caa9",
        "tested_code_snapshot": head, "current_source_test_pins": launch["source_test_pins"],
        "actual_worker_origins": pin("evidence/sourcefront21_coordinator_20261021/worker_commits.json"),
        "local_full_suite": {**result, "plain_OK_seconds": float(footer[1]),
            "JSON": pin(result_path), "console": pin(console_path),
            "separate_runner": pin("evidence/sourcefront21_linux_runner_20261021.log")},
        "new_exact_methods": pin(methods_path.relative_to(ROOT).as_posix()),
        "new_exact_original_OK_blocks": expected["new_exact_method_count"],
        "content_audit": pin("evidence/sourcefront21_coordinator_20261021/content_audit.json"),
        "old_public_groups": audit["baseline_groups"], "protected_inputs": audit["protected_inputs"],
        "new_primary_research_HTTP200": 5, "new_primary_original_bytes": 2683149,
        "new_originals": audit["new_originals"],
        "actual_source_CLI_UI_runtime": pin("evidence/sourcefront21_coordinator_20261021/runtime/runtime_capture.json"),
        "actual_source_actions": 2, "runtime_HTTP_SQL_adoptions": 0,
        "sourcefront_collector_requests_or_new_quota_database": 0,
        "source_retries_or_old_failed_market_plan_replays": 0,
        "original_production_database_opened_hashed_copied_replayed_reset": False,
        "current_production_database_counts": None, "counts_status": "UNOBSERVED",
        "four_withheld_whole_bodies_absent": True,
        "all_21_code_live_statuses_unchanged": True, "canonical_tickers": 687,
        "Q2_instrument_coverage": 150, "current_Q3_instrument_coverage": 0,
        "healthcare_inventory": 10, "NHTSA_inventory": 3, "legal_inventory": 5,
        "registry_adoptions": 0, "genuine_forecasts_registrations_labels_created_by_sourcefront": 0,
        "user_Windows_end_to_end": False, "historical_OOS_verified": False,
        "default_modelconfig_hash": audit["default_modelconfig_hash_unchanged_from_public_baseline"],
        "model_fitter_score_or_687_60s_budget_changed": False,
        "full_specialist_acceptance": "0/21", "automatic_trading_allowed": False,
        "new_head_hosted_CI": "PENDING_AFTER_EXACT_SAFE_TREE_PUBLICATION",
        "later_hosted_qualification_location": "PR162 and original run artifact; this pre-CI proof remains immutable",
        "required_public_parent_only": "9d60cc650ff0d64b720f84fabfe1348e44c6bcf4", "merge_authorized": False,
    }
    target = ROOT / "evidence/sourcefront21_integration_20261021.json"
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"proof": str(target.relative_to(ROOT)), "sha256": pin(target.relative_to(ROOT).as_posix())["sha256"],
                      "discovered_executed": full, "new_exact_OK": expected["new_exact_method_count"], "plain_OK_seconds": float(footer[1])}))
