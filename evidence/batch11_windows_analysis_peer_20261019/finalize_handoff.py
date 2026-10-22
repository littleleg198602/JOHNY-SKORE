"""Pin before/after peer evidence after the exact finalized owner base exists."""
from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PEER = "tests/test_batch11_windows_analysis_peer.py"
PRODUCER = "market_checker_app/services/user_windows_analysis_capture.py"
FIXTURE_SHA = "03e5fdb7e250163a0a557d59f38acb978307ac4d77095b9f57c7eb28ba456f66"
PEER_SHA = "bca13c9786e678885c1c849ecc17405df745534735c105064a5368b7602c657d"


def pin(path):
    raw = (ROOT / path).read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def bodies(text, *, peer):
    result = {}
    for node in ast.parse(text).body:
        if not peer and isinstance(node, ast.FunctionDef) and node.name in {"fixture_runtime", "fixture_signals"}:
            result[node.name] = hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()
        if isinstance(node, ast.ClassDef) and node.name == ("WindowsAnalysisPeerTests" if peer else "WindowsAnalysisCaptureTests"):
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith("test_") == peer:
                    result[method.name] = hashlib.sha256(ast.get_source_segment(text, method).encode()).hexdigest()
    return result


def main():
    if len(sys.argv) != 2 or re.fullmatch(r"[a-f0-9]{40}", sys.argv[1]) is None:
        raise SystemExit("Exact finalized evaluation commit required")
    base = sys.argv[1]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    if head != base:
        raise ValueError("Peer must be based directly on finalized owner commit")
    subprocess.run(["git", "diff", "--quiet", base, "--"], cwd=ROOT, check=True)
    before = json.loads((HERE / "before.json").read_bytes())
    after = json.loads((HERE / "after.json").read_bytes())
    qualified = json.loads((HERE / "qualified.json").read_bytes())
    if (before["discovered"] != 10 or before["executed"] != 10 or before["failures"] != 4 or before["errors"] != 0
            or after["discovered"] != 10 or after["executed"] != 10 or not after["plain_OK"] or not after["successful"]
            or any(after[key] for key in ("failures", "errors", "skips", "expected_failures", "unexpected_successes"))
            or before["peer_test_sha256"] != PEER_SHA or after["peer_test_sha256"] != PEER_SHA
            or after["frozen_fixture_helper_sha256"] != FIXTURE_SHA
            or pin(str((HERE / "before_owner_tests.py.txt").relative_to(ROOT)))["sha256"] != FIXTURE_SHA):
        raise ValueError("Identical focused peer methods/fixture helper and real before/after outcomes required")
    if (qualified["module_discovered_test_count"] != 10 or qualified["discovered"] != 10 or qualified["executed"] != 10
            or not qualified["successful"] or not qualified["plain_OK"]
            or any(qualified[key] for key in ("failures", "errors", "skips", "expected_failures", "unexpected_successes"))
            or qualified["peer_test_sha256"] != pin(PEER)["sha256"]
            or qualified["producer_sha256"] != after["producer_sha256"]
            or qualified["frozen_fixture_helper_sha256"] != FIXTURE_SHA):
        raise ValueError("Current namespace-only correction must qualify exactly ten discovered methods")
    original_methods = bodies((HERE / "before_peer_tests.py.txt").read_text(), peer=True)
    current_methods = bodies((ROOT / PEER).read_text(), peer=True)
    original_helpers = bodies((HERE / "before_owner_tests.py.txt").read_text(), peer=False)
    current_helpers = bodies((ROOT / "tests/test_user_windows_analysis_capture.py").read_text(), peer=False)
    if original_methods != current_methods or len(current_methods) != 10 or original_helpers != current_helpers or len(current_helpers) != 9:
        raise ValueError("Assertion methods and actual fixture helper bodies must be byte-identical")
    for label, result in (("before", before), ("after", after), ("qualified", qualified)):
        if pin(str((HERE / (label + ".log")).relative_to(ROOT)))["sha256"] != result["log_sha256"]:
            raise ValueError("Original peer log changed")
    if pin(PRODUCER)["sha256"] != after["producer_sha256"]:
        raise ValueError("Current producer differs from after qualification")
    raw_base = subprocess.run(["git", "show", base + ":" + PRODUCER], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    if hashlib.sha256(raw_base).hexdigest() != after["producer_sha256"]:
        raise ValueError("Qualified producer is not exact finalized owner bytes")
    discovery = {"recorded_at": datetime.now(timezone.utc).isoformat(), "original_peer_full_sha256": PEER_SHA,
        "current_peer_full_sha256": pin(PEER)["sha256"], "standard_module_discovered_test_count": 10,
        "correction": "Import owner fixture module rather than exposing its TestCase as a global alias. This prevents duplicate owner discovery.",
        "ten_test_method_source_bodies_byte_identical": True, "test_method_source_sha256": current_methods,
        "nine_fixture_helper_source_bodies_byte_identical": True, "fixture_helper_source_sha256": current_helpers,
        "owner37_or_full_suite_executed": False}
    discovery_path = HERE / "discovery_scope.json"
    if discovery_path.exists():
        original_discovery = json.loads(discovery_path.read_bytes())
        if ({key: value for key, value in original_discovery.items() if key != "recorded_at"}
                != {key: value for key, value in discovery.items() if key != "recorded_at"}):
            raise ValueError("Original discovery scope proof changed")
        discovery = original_discovery
    else:
        with discovery_path.open("x") as output:
            json.dump(discovery, output, indent=2)
            output.write("\n")
    protected = []
    for name in ("verified_healthcare_identities.json", "verified_13f_securities.json", "verified_nhtsa_models.json",
                 "verified_counterparty_identities.json", "verified_fdic_banks.json", "verified_usaspending_uei.json",
                 "european_authority_registry.json", "market_checker_687_tickers.csv"):
        path = "market_checker_app/data/" + name
        raw = subprocess.run(["git", "show", base + ":" + path], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
        current = pin(path)
        if hashlib.sha256(raw).hexdigest() != current["sha256"]:
            raise ValueError("Protected input changed")
        protected.append({**current, "byte_identical_to_owner_base": True})
    artifacts = [pin(path.relative_to(ROOT).as_posix()) for path in sorted(HERE.iterdir()) if path.is_file() and path.name != "handoff.json"]
    handoff = {"schema_version": "batch11_independent_windows_analysis_peer_v1", "recorded_at": datetime.now(timezone.utc).isoformat(),
        "exact_owner_base_commit": base, "branch": "batch11/windows-analysis-peer-20261019",
        "peer_test": pin(PEER), "current_owner_producer": pin(PRODUCER),
        "before_owner_producer": pin(str((HERE / "before_producer.py.txt").relative_to(ROOT))),
        "frozen_fixture_helper": pin(str((HERE / "before_owner_tests.py.txt").relative_to(ROOT))),
        "before": before, "original_unchanged_after": after, "current_namespace_qualified": qualified,
        "discovery_scope": discovery,
        "additive_historical_readme_recovery": pin(str((HERE / "historical_readme_recovery.json").relative_to(ROOT))),
        "reproduced_gap": {"before_failure_entries": 4, "before_failed_methods": 3,
            "returned_run_id_cases": ["True equals actual integer1", "float1.0 equals actual integer1"],
            "returned_signal_claim_cases": ["run_id987654 ignored", "row id987654 ignored"],
            "effect": "Missing result/persisted-identifier binding rejection while true temporary saved rows were observed separately.",
            "not_observed": "No genuine native Windows qualification or full21 promotion was executed."},
        "valid_and_failure_controls": ["Real temporary provider/SQLiteStore save with exact persisted ID/value binding",
            "Wrong integer returned ID preserves actual committed rows", "Post-save callback failure preserves true saved subset",
            "Post-fsync extra database run is not a store-confirmed commit", "Post-fsync source drift stays unqualified",
            "Post-fsync physical rollback keeps clock integrity false", "Resealed JSON is never runtime re-execution proof"],
        "scope": {"only_peer_test_and_new_evidence_files_changed": True, "existing_owner_tests_modified": False,
            "original_retained_DB_opened_or_modified": False, "temporary_fixture_DB_used": True,
            "canonical_temp_roots": True, "real_pipeline_model_or_forecast_executed": False,
            "genuine_native_Windows_execution": False, "new_HTTP_source_collector_quota_registry_actions": 0,
            "score_model_budget_trading_or_remote_writes": 0, "full_specialist_acceptance": False},
        "protected_inputs": protected, "artifacts": artifacts}
    with (HERE / "handoff.json").open("x", encoding="utf-8") as output:
        json.dump(handoff, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"handoff": pin(str((HERE / "handoff.json").relative_to(ROOT))), "base": base,
        "current_peer": pin(PEER), "before": {"methods": 10, "failure_entries": 4, "errors": 0}, "after": {"methods": 10, "failures": 0, "errors": 0}}))


if __name__ == "__main__":
    main()
