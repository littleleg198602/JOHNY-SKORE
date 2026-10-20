"""Pin current source bytes and preserve the already qualified public baseline."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "96c4e90a8e72dcd11087e6a08371ee0c924fac83"
CHANGED = {
    "market_checker_app/services/captured_identity_review_service.py",
    "market_checker_app/services/doj_enforcement_scope_review.py",
    "market_checker_app/services/healthcare_identity_service.py",
    "market_checker_app/services/healthcare_scout_service.py",
    "market_checker_app/services/sec_corporate_action_bridge.py",
    "market_checker_app/services/sec_form4_person_bridge.py",
    "market_checker_app/specialist_evidence_ui.py",
    "tests/test_healthcare_identity_service.py",
}
PROTECTED = ["verified_13f_securities.json", "verified_healthcare_identities.json",
             "verified_nhtsa_models.json", "verified_counterparty_identities.json",
             "verified_fdic_banks.json", "verified_usaspending_uei.json", "market_checker_687_tickers.csv"]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def pin(path):
    body = (ROOT / path).read_bytes()
    return {"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


if __name__ == "__main__":
    groups = {key: {"files": 0, "unchanged_files": 0, "unchanged_bytes": 0} for key in
              ("production", "tests", "evidence")}
    differences, exceptions = [], []
    for record in git("ls-tree", "-r", "-z", BASE).split(b"\0"):
        if not record:
            continue
        info, raw_path = record.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        path = raw_path.decode()
        group = next((name for prefix, name in (("market_checker_app/", "production"),
                     ("tests/", "tests"), ("evidence/", "evidence")) if path.startswith(prefix)), None)
        if group is None:
            continue
        assert kind == "blob" and mode != "120000", path
        groups[group]["files"] += 1
        body = (ROOT / path).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        if actual != oid:
            if path in CHANGED:
                differences.append(path)
                continue
            old = git("cat-file", "blob", oid)
            assert path.endswith(".bat") and body == old.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"), path
            exceptions.append({**pin(path), "original_git_blob": oid, "physical_checkout_CRLF_exception": True})
        else:
            groups[group]["unchanged_files"] += 1
            groups[group]["unchanged_bytes"] += len(body)
    assert set(differences) == CHANGED, differences
    assert groups["evidence"]["files"] == groups["evidence"]["unchanged_files"]
    protected = [pin("market_checker_app/data/" + name) for name in PROTECTED]
    for item in protected:
        assert git("show", BASE + ":" + item["path"]) == (ROOT / item["path"]).read_bytes()
    status = json.loads((ROOT / "market_checker_app/data/specialist_status.json").read_bytes())
    assert len(status["specialists"]) == 21
    assert not any(row["code"] == "DONE" or row["live"] == "VERIFIED" for row in status["specialists"])
    originals = []
    for path in sorted((ROOT / "evidence/sourcefront_20261020/primary").glob("*.metadata.json")):
        metadata = json.loads(path.read_bytes())
        body_path = path.parent / metadata["body_path"]
        item = pin(body_path.relative_to(ROOT).as_posix())
        assert metadata["http_status"] == 200 and metadata["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
        assert item["bytes"] == metadata["bytes"] and item["sha256"] == metadata["raw_sha256"]
        originals.append(item)
    assert len(originals) == 4 and sum(x["bytes"] for x in originals) == 3_449_563
    withheld = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
                "evidence/batch4_ir_20261013/primary/ir_authority.html",
                "evidence/batch5_ir_20261014/primary/ir_authority.html",
                "evidence/batch8_regulatory_20261015/raw/njd_case_page.html"]
    assert not any((ROOT / path).exists() for path in withheld)
    expected = json.loads((ROOT / "evidence/sourcefront_expected_methods_20261020.json").read_bytes())
    current = [pin(path) for path in sorted(CHANGED)] + [pin(x["path"]) for x in expected["files"]]
    runtime = json.loads((ROOT / "evidence/sourcefront_coordinator_20261020/runtime/runtime_capture.json").read_bytes())
    assert len(runtime["actions"]) == 8 and runtime["HTTP_attempts"] == runtime["SQL_operations"] == 0
    for row in runtime["actions"]:
        assert pin(row["archive"])["sha256"] == row["archive_sha256"]
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(), "public_baseline": BASE,
              "baseline_groups": groups, "approved_existing_source_test_changes": sorted(CHANGED),
              "physical_checkout_exceptions": exceptions, "current_source_test_pins": current,
              "protected_inputs": protected, "new_originals": originals,
              "actual_runtime_actions": 8, "runtime_HTTP_SQL_adoptions": 0,
              "all_21_code_live_unchanged": True, "withheld_originals_present": False,
              "retained_production_database_opened_hashed_copied_or_replayed": False,
              "current_production_DB_counts": None, "counts_status": "UNOBSERVED",
              "default_modelconfig_hash_unchanged_from_public_baseline":
              "e8e0a4b5e010122a7d7fa2f8d41eb9174e25a6a1a57e206cb315b1a552d0d5f2",
              "full_specialist_acceptance": "0/21", "user_windows_or_OOS": False}
    target = ROOT / "evidence/sourcefront_coordinator_20261020/content_audit.json"
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"groups": groups, "source_test_pins": len(current), "originals": len(originals),
                      "runtime_actions": 8, "protected_inputs": len(protected)}))
