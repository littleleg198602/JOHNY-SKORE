"""Check this lane's committed candidate/evidence; no SQL, pipeline or HTTP."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = "6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"
sys.path.insert(0, str(REPO))
from market_checker_app.services.user_windows_analysis_capture import load_user_windows_analysis_receipt


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def verify():
    source = pin(REPO / "market_checker_app/services/user_windows_analysis_capture.py")
    test = pin(REPO / "tests/test_user_windows_analysis_capture.py")
    report = json.loads((HERE / "qualification_v3/tests.json").read_text())
    assert report["producer_before"] == report["producer_after"] == source
    assert report["test_before"] == report["test_after"] == test
    assert report["discovered_tests"] == report["tests_run"] == 37 and report["successful"] is True
    assert all(report[name] == 0 for name in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    assert report["log"] == pin(HERE / "qualification_v3/tests.log")
    original = json.loads((HERE / "qualification_v2_collision_reproduction/tests.json").read_text())
    assert original["tests_run"] == original["failures"] == 1 and original["errors"] == 0
    assert original["producer"] == pin(HERE / "qualification_v2_collision_reproduction/user_windows_analysis_capture.py")
    for name, expected in json.loads((HERE / "independent_peer_before/copied_pins.json").read_text())["files"].items():
        assert pin(HERE / "independent_peer_before" / name) == expected
    receipt = load_user_windows_analysis_receipt(HERE / "actual_linux_refusal.json")
    actual = json.loads((HERE / "actual_linux_refusal_execution.json").read_text())
    assert actual["producer"] == actual["frozen_producer"] == source
    assert actual["receipt"] == pin(HERE / "actual_linux_refusal.json")
    record = receipt["record"]
    assert record["status"] == "UNSUPPORTED_PLATFORM" and record["pipeline_invoked"] is False
    assert record["database"] is record["database_before"] is record["market_preflight"] is None
    assert record["fixture_only"] is False and record["runtime"]["os_system"] == "Linux"
    assert all(value == 0 for value in actual["guarded_entrypoint_calls"].values())
    assert record["code"]["source_files"]["market_checker_app/services/user_windows_analysis_capture.py"] == {"exists": True, **source}
    assert receipt["runtime_execution_reverified"] is False and receipt["native_windows_selected_analysis_verified"] is False
    # Whole raw Git blob bytes are authoritative for all production, tests and
    # evidence. Only the nine already-configured .bat CRLF checkouts use Git's
    # exact whole filtered checkout bytes; no working file is normalized.
    tree = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "-z", BASE], check=True, capture_output=True).stdout
    groups = {name: {"files": 0, "bytes": 0} for name in ("production", "tests", "evidence", "other")}
    expected_bats = {"Nainstalovat_Patraci_Agenty.bat", "Nainstalovat_Tydenni_Shadow.bat", "Pripravit_SEC_Registry.bat",
        "Spustit_Market_Checker.bat", "Spustit_Patraci_Agenty.bat", "Spustit_Tydenni_Shadow.bat", "Vyhodnotit_Predikce.bat",
        "Vyhodnotit_Specialisty.bat", "Zkontrolovat_Zdravotnicke_Identity.bat"}
    filtered = []
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        assert kind == "blob" and mode in {"100644", "100755"}
        path = name.decode(); raw = (REPO / path).read_bytes()
        if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != blob:
            assert path in expected_bats, path
            expected = subprocess.run(["git", "-C", str(REPO), "cat-file", "--filters", "--path=" + path, BASE + ":" + path], check=True, capture_output=True).stdout
            assert raw == expected, path
            filtered.append(path)
        group = "production" if path.startswith("market_checker_app/") else "tests" if path.startswith("tests/") else "evidence" if path.startswith("evidence/") else "other"
        groups[group]["files"] += 1; groups[group]["bytes"] += len(raw)
    assert set(filtered) == expected_bats
    return {"verified_at": datetime.now(timezone.utc).isoformat(), "base": BASE, "source": source, "test": test,
        "qualification_tests": 37, "all_other_qualifiers": 0, "actual_refusal": actual["receipt"],
        "all_prior_production_test_evidence_bytes_match_raw_blobs": True, "whole_filtered_CRLF_launchers": sorted(filtered),
        "unchanged_baseline_groups": groups, "source_HTTP_requests": 0, "SQL_opened": False,
        "original_retained_DB_access": False, "actual_native_pipeline_executions": 0, "full_suite_rerun": False}


if __name__ == "__main__":
    result = verify()
    with (HERE / "saved_output_verification.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
