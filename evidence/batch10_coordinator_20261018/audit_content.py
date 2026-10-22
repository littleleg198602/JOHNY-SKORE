"""Byte audit of this batch; old producer pins retain their original versions."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "32adb8edd6aa6ccc1748d2cfbc228f7db3a16fd4"
APPROVED_EXISTING_SOURCE_CHANGES = {
    "market_checker_app/data/specialist_status.json",
    "market_checker_app/services/captured_identity_review_service.py",
    "market_checker_app/specialist_evidence_runner.py",
    "market_checker_app/specialist_evidence_ui.py",
}
WITHHELD = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}
RETAINED_DB = ROOT.parent / "JOHNY-batch4-integration-20261013/outputs/market_checker_history.db"
RETAINED_DB_SHA = "6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af"


def pin(path):
    body = path.read_bytes()
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def unchanged(path, expected):
    actual = pin(ROOT / path)
    assert actual == {k: expected[k] for k in ("bytes", "sha256")}, path
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads((ROOT / "evidence/batch10_coordinator_20261018/public_32_baseline_pins.json").read_text())
    report = {"schema_version": "batch10_content_audit_v1", "physical_recorded_at": datetime.now(timezone.utc).isoformat(),
        "public_base": BASE, "HEAD": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    for group in ("old_public_evidence", "existing_tests"):
        for name, expected in baseline[group].items():
            unchanged(name, expected)
        report[group] = {"files": len(baseline[group]), "bytes": sum(p["bytes"] for p in baseline[group].values()), "all_byte_identical": True}
    changes = []
    for name, expected in baseline["existing_production"].items():
        actual = pin(ROOT / name)
        if actual != {k: expected[k] for k in ("bytes", "sha256")}:
            assert name in APPROVED_EXISTING_SOURCE_CHANGES, name
            changes.append({"path": name, "before": expected, "after": actual})
    assert {p["path"] for p in changes} == APPROVED_EXISTING_SOURCE_CHANGES
    report["approved_existing_source_changes"] = changes
    protected = json.loads((ROOT / "evidence/batch9_coordinator_20261016/protected_inputs_baseline.json").read_text())
    for name, expected in protected.items():
        unchanged(name, expected)
    report["protected_production_inputs"] = {"files": len(protected), "bytes": sum(p["bytes"] for p in protected.values()), "all_byte_identical": True}
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    known = {name: pin(ROOT / name) for name in names if name and (ROOT / name).is_file()}
    assert not WITHHELD.intersection(p["sha256"] for p in known.values())
    frozen = {}
    for name, p in known.items():
        if name.startswith("evidence/batch10_"):
            frozen.setdefault((p["sha256"], p["bytes"]), []).append(name)
    verified = []
    historical = []
    def walk(value, handoff):
        if isinstance(value, list):
            for item in value: walk(item, handoff)
        elif isinstance(value, dict):
            for key, item in value.items():
                if (isinstance(key, str) and "/" in key and isinstance(item, dict)
                        and isinstance(item.get("sha256"), str) and type(item.get("bytes")) is int):
                    expected = {k: item[k] for k in ("bytes", "sha256")}
                    local_name = (Path(handoff).parent / key).as_posix()
                    resolved = key if known.get(key) == expected else local_name
                    if known.get(resolved) == expected:
                        verified.append({"handoff": handoff, "path": resolved, **expected})
                    elif key.startswith(("market_checker_app/", "tests/")):
                        copies = frozen.get((item["sha256"], item["bytes"]), [])
                        assert copies, (handoff, key, "MISSING_FROZEN_QUALIFIED_PRODUCER")
                        historical.append({"handoff": handoff, "original_producer_path": key,
                            "qualification_scope": "HISTORICAL_PRODUCER_VERSION_ONLY", "preserved_whole_copies": copies, **expected})
                    else:
                        raise AssertionError((handoff, key, "EVIDENCE_PIN_CHANGED_OR_MISSING"))
                walk(item, handoff)
    for path in sorted(ROOT.glob("evidence/batch10_*/handoff*.json")):
        walk(json.loads(path.read_text()), path.relative_to(ROOT).as_posix())
    report.update(current_or_immutable_file_pins=verified, preserved_historical_producer_pins=historical,
        whole_withheld_bodies_present=0, access_values_exported=False)
    image = "evidence/batch10_identity_20261018/captures/c05da755dd60ea349f6368eb97e74cd07d93a3a6a176bd1d427467a9f7481f45.jpg"
    metadata = json.loads((ROOT / "evidence/batch10_identity_20261018/captures/attempt-01.metadata.json").read_text())
    assert known[image] == {"bytes": 917915, "sha256": "c05da755dd60ea349f6368eb97e74cd07d93a3a6a176bd1d427467a9f7481f45"}
    assert metadata["http_status"] == 200 and metadata["raw_sha256"] == known[image]["sha256"]
    report["new_source_http"] = {"attempts": 1, "whole_HTTP200_originals": 1, "whole_original_bytes": 917915,
        "source_pin": known[image], "collector_API_requests": 0, "NHTSA_requests": 0, "market_requests": 0,
        "other_lanes_new_source_HTTP": 0, "retained_replays_counted_as_new_HTTP": False}
    if RETAINED_DB.is_file():
        before = pin(RETAINED_DB)
        journals = {suffix: pin(Path(str(RETAINED_DB) + suffix)) if Path(str(RETAINED_DB) + suffix).is_file() else None
            for suffix in ("-wal", "-shm", "-journal")}
        assert before["sha256"] == RETAINED_DB_SHA
        assert before == pin(RETAINED_DB)
        assert journals == {suffix: pin(Path(str(RETAINED_DB) + suffix)) if Path(str(RETAINED_DB) + suffix).is_file() else None
            for suffix in ("-wal", "-shm", "-journal")}
        report["retained_quota_database"] = {**before, "sidecar_pins": journals,
            "same_before_after_file_hash": True, "SQL_opened_by_auditor": False,
            "journal_replayed_or_removed": False, "current_table_and_forecast_counts": "UNKNOWN_NOT_READ_BY_AUDITOR"}
    else:
        report["retained_quota_database"] = {"status": "ORIGINAL_NOT_PRESENT_IN_THIS_CHECKOUT", "SQL_opened_by_auditor": False}
    report.update(registry_adoptions=0, genuine_forecasts=0, registrations=0, labels=0,
        full_specialist_acceptance="0/21", historical_OOS_verified=False,
        workflow_changed=subprocess.check_output(["git", "diff", BASE, "--", ".github/workflows"], cwd=ROOT) != b"")
    assert report["workflow_changed"] is False
    raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    with args.output.open("xb") as handle: handle.write(raw)
    print(json.dumps({"status": "CONTENT_VERIFIED", "output_bytes": len(raw), "output_sha256": hashlib.sha256(raw).hexdigest(),
        "verified_file_pins": len(verified), "historical_producer_pins": len(historical),
        "old_evidence_files": report["old_public_evidence"]["files"], "original_test_files": report["existing_tests"]["files"]}))


if __name__ == "__main__":
    main()
