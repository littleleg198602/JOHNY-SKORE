"""Pin current source bytes and preserve the already qualified public baseline."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "9d60cc650ff0d64b720f84fabfe1348e44c6bcf4"
CHANGED = {
    "market_checker_app/services/captured_identity_review_service.py",
    "market_checker_app/specialist_evidence_ui.py",
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
    assert git("show", BASE + ":market_checker_app/data/specialist_status.json") == (ROOT / "market_checker_app/data/specialist_status.json").read_bytes()
    assert len(status["specialists"]) == 21
    assert not any(row["code"] == "DONE" or row["live"] == "VERIFIED" for row in status["specialists"])
    for protected_path in (".github/workflows/market-checker-test-agents.yml", "evidence/sourcefront_integration_20261020.json", "evidence/batch11_integration_20261019.json", "evidence/batch10_integration_20261018.json"):
        assert git("show", BASE + ":" + protected_path) == (ROOT / protected_path).read_bytes(), protected_path
    originals = []
    for path in sorted((ROOT / "evidence/sourcefront21_primary_20261021/primary").glob("*.metadata.json")):
        metadata = json.loads(path.read_bytes())
        body_path = path.parent / metadata["body_path"]
        item = pin(body_path.relative_to(ROOT).as_posix())
        assert metadata["http_status"] == 200 and metadata["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
        assert item["bytes"] == metadata["bytes"] and item["sha256"] == metadata["raw_sha256"]
        originals.append(item)
    assert len(originals) == 5 and sum(x["bytes"] for x in originals) == 2_683_149
    withheld = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
                "evidence/batch4_ir_20261013/primary/ir_authority.html",
                "evidence/batch5_ir_20261014/primary/ir_authority.html",
                "evidence/batch8_regulatory_20261015/raw/njd_case_page.html"]
    assert not any((ROOT / path).exists() for path in withheld)
    expected = json.loads((ROOT / "evidence/sourcefront21_expected_methods_20261021.json").read_bytes())
    current = [pin(path) for path in sorted(CHANGED | {"market_checker_app/services/wafer_capacity_deposit_review.py"})] + [pin(x["path"]) for x in expected["files"]]
    runtime = json.loads((ROOT / "evidence/sourcefront21_coordinator_20261021/runtime/runtime_capture.json").read_bytes())
    assert len(runtime["actions"]) == 2 and runtime["HTTP_attempts"] == runtime["SQL_operations"] == 0
    for row in runtime["actions"]:
        assert pin(row["archive"])["sha256"] == row["archive_sha256"]
    identity = json.loads((ROOT / "evidence/sourcefront21_identity_20261021/primary_review.json").read_bytes())
    identity_bodies = {}
    for key, source in identity["sources"].items():
        raw = (ROOT / source["body_path"]).read_bytes()
        assert len(raw) == source["body_bytes"] and hashlib.sha256(raw).hexdigest() == source["body_sha256"]
        identity_bodies[key] = raw
    for passage in identity["source_local_passages"].values():
        raw = identity_bodies[passage["source_key"]][passage["byte_start"]:passage["byte_end_exclusive"]]
        assert hashlib.sha256(raw).hexdigest() == passage["raw_excerpt_sha256"]
        assert raw.decode("utf-8") == passage["raw_excerpt_utf8"]
    assert len(identity["source_local_passages"]) == 13
    capture_audit = json.loads((ROOT / "evidence/sourcefront21_evaluation_20261021/capture_audit.json").read_bytes())
    assert capture_audit["status"] == "FIVE_WHOLE_ORIGINALS_AND_DATED_EX31_POINT_VERIFIED_WITH_LIMITS"
    assert capture_audit["totals"]["distinct_research_HTTP200_originals"] == 5
    content_reports = [pin("evidence/sourcefront21_identity_20261021/primary_review.json"),
                       pin("evidence/sourcefront21_healthcare_20261021/primary_review.json"),
                       pin("evidence/sourcefront21_evaluation_20261021/capture_audit.json")]
    # Verify only explicit whole-file declarations, not substring/passage hashes.
    # A changed producer may be qualified by an exact preserved historical copy.
    declared_pins, historical_pins = [], []
    catalog = {}
    public_paths = {record.split(b"\t", 1)[1].decode() for record in
                    git("ls-tree", "-r", "-z", BASE).split(b"\0") if record}
    new_paths = sorted(set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0"))
                       - public_paths - {""})
    for path in new_paths:
        if path.startswith("evidence/sourcefront21") and (ROOT / path).is_file():
            item = pin(path)
            catalog.setdefault((item["bytes"], item["sha256"]), []).append(path)
    def visit(value, declaration):
        if isinstance(value, dict):
            path, size, digest = value.get("path"), value.get("bytes"), value.get("sha256")
            if (isinstance(path, str) and isinstance(size, int) and isinstance(digest, str)
                    and len(digest) == 64 and not any(k in value for k in ("byte_start", "byte_end", "start", "end"))
                    and path.startswith(("market_checker_app/", "tests/", "evidence/"))):
                item = {"declaration": declaration, "path": path, "bytes": size, "sha256": digest}
                if (ROOT / path).is_file() and pin(path)["bytes"] == size and pin(path)["sha256"] == digest:
                    declared_pins.append(item)
                else:
                    carriers = catalog.get((size, digest), [])
                    old_public = None
                    if not carriers and path in public_paths:
                        old_bytes = git("show", BASE + ":" + path)
                        if len(old_bytes) == size and hashlib.sha256(old_bytes).hexdigest() == digest:
                            old_public = {"commit": BASE, "path": path}
                    assert carriers or old_public, item
                    historical_pins.append({**item, "exact_preserved_carriers": carriers,
                                            "immutable_public_blob": old_public,
                                            "qualifies_current_producer": False})
            for key, child in value.items():
                visit(child, declaration + "/" + str(key))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, declaration + "/" + str(i))
    for path in new_paths:
        if path.startswith("evidence/sourcefront21") and path.endswith(".json"):
            visit(json.loads((ROOT / path).read_bytes()), path)
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(), "public_baseline": BASE,
              "baseline_groups": groups, "approved_existing_source_test_changes": sorted(CHANGED),
              "physical_checkout_exceptions": exceptions, "current_source_test_pins": current,
              "protected_inputs": protected, "new_originals": originals,
              "actual_runtime_actions": 2, "runtime_HTTP_SQL_adoptions": 0,
              "new_primary_content_reports": content_reports,
              "independently_reverified_identity_raw_byte_spans": 13,
              "verified_current_or_immutable_whole_file_declarations": declared_pins,
              "verified_explicitly_historical_whole_file_declarations": historical_pins,
              "all_21_code_live_unchanged": True, "withheld_originals_present": False,
              "retained_production_database_opened_hashed_copied_or_replayed": False,
              "current_production_DB_counts": None, "counts_status": "UNOBSERVED",
              "default_modelconfig_hash_unchanged_from_public_baseline":
              "e8e0a4b5e010122a7d7fa2f8d41eb9174e25a6a1a57e206cb315b1a552d0d5f2",
              "full_specialist_acceptance": "0/21", "user_windows_or_OOS": False}
    target = ROOT / "evidence/sourcefront21_coordinator_20261021/content_audit.json"
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"groups": groups, "source_test_pins": len(current), "originals": len(originals),
                      "runtime_actions": 2, "protected_inputs": len(protected)}))
