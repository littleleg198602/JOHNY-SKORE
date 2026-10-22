"""Verify lane receipts/pins and byte preservation, without SQL or collection.

Run from this lane worktree. The baseline check is intentionally lane-scoped:
coordinator CLI/UI integration has its own later qualification and source tree.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = "32adb8edd6aa6ccc1748d2cfbc228f7db3a16fd4"
sys.path.insert(0, str(REPO))
from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.user_windows_readiness_capture import load_user_windows_readiness_receipt


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def inspect_saved():
    source = pin(REPO / "market_checker_app/services/user_windows_readiness_capture.py")
    test = pin(REPO / "tests/test_user_windows_readiness_capture.py")
    own = json.loads((HERE / "qualification_v4/tests.json").read_text())
    peer = json.loads((HERE / "peer_recheck_final/tests.json").read_text())
    assert own["producer_before"] == own["producer_after"] == peer["producer_before"] == peer["producer_after"] == source
    assert own["test_before"] == own["test_after"] == test
    assert own["discovered_tests"] == own["tests_run"] == 41 and peer["discovered_tests"] == peer["tests_run"] == 7
    for report in (own, peer):
        assert report["successful"] is True
        assert all(report[key] == 0 for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    final = load_user_windows_readiness_receipt(HERE / "actual_linux_v2/actual_linux_readiness.json")
    original = load_user_windows_readiness_receipt(HERE / "actual_linux_readiness.json")
    assert pin(HERE / "actual_linux_readiness.json")["sha256"] == "13edc710e67e82a566a678f8782a0f90f31014b1d47aa9d4494752a26d29b03d"
    assert final["database"]["SQL_opened"] is False and final["database"]["status"] == "WAIT_ACTIVE_DATABASE_JOURNAL"
    assert all(row["present"] is None and row["sampled_rows"] is None and row["observation_status"] == "UNOBSERVED" for row in final["database"]["tables"].values())
    assert final["code"]["source_files"]["market_checker_app/services/user_windows_readiness_capture.py"]["sha256"] == source["sha256"]
    assert final["database"]["file"] == original["database"]["file"]
    assert final["database"]["journals"] == original["database"]["journals"]
    assert final["user_windows_end_to_end_verified"] is False and final["fixture_only"] is False
    assert final["status"] == "UNSUPPORTED_PLATFORM" and final["readiness_status"] == "WAIT"
    assert final["market_readiness"]["usable_count"] == 0
    # Preserve original independent 6-failure reproduction, not just the repair.
    prior = json.loads((HERE / "independent_peer_originals/before_fix_01.json").read_text())
    assert prior["executed"] == 7 and prior["failures"] == 6 and prior["errors"] == 0
    copies = json.loads((HERE / "independent_peer_originals/copied_from_git.json").read_text())
    for row in copies["files"].values():
        assert pin(HERE / "independent_peer_originals" / row["copied_as"]) == {key: row[key] for key in ("bytes", "sha256")}
    return {"final_source": source, "final_test": test, "actual_receipt": pin(HERE / "actual_linux_v2/actual_linux_readiness.json"),
        "original_actual_receipt": pin(HERE / "actual_linux_readiness.json"), "focused_tests": 41, "unchanged_peer_tests": 7,
        "genuine_forecasts_created": 0, "native_windows_acceptance": False}


def inspect_baseline():
    raw = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "-z", BASE], check=True, capture_output=True).stdout
    groups = {key: {"files": 0, "bytes": 0, "pins": {}} for key in ("production", "tests", "evidence", "other")}
    mismatches = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        assert kind == "blob" and mode in {"100644", "100755"}
        relative = name.decode()
        body = (REPO / relative).read_bytes()
        observed = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        if observed != blob:
            mismatches.append(relative)
        group = "production" if relative.startswith("market_checker_app/") else "tests" if relative.startswith("tests/") else "evidence" if relative.startswith("evidence/") else "other"
        groups[group]["files"] += 1
        groups[group]["bytes"] += len(body)
        groups[group]["pins"][relative] = {"git_blob": blob, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
    assert not mismatches, mismatches
    for group in groups.values():
        group["exact_physical_file_pin_manifest_sha256"] = canonical_hash(group.pop("pins"))
    return {"baseline": BASE, "all_prior_tracked_files_byte_identical": True, "groups": groups}


if __name__ == "__main__":
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "saved_evidence": inspect_saved(),
        "baseline_preservation": inspect_baseline(), "SQL_opened": False, "network_requests": 0,
        "full_suite_rerun": False, "scope": "OFFLINE_LANE_EVIDENCE_VERIFICATION"}
    destination = HERE / "saved_evidence_verification.json"
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
