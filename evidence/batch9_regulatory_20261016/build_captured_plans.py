"""Pin actual new/retained originals and an honestly unavailable charged scope.

Pure file replay only. This builder performs no HTTP, database operation or
identity adoption, and does not rewrite publisher bytes or old metadata.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent


def sha(body):
    return hashlib.sha256(body).hexdigest()


def pin(relative):
    body = (ROOT / relative).read_bytes()
    return {"path": relative, "bytes": len(body), "sha256": sha(body)}


def write(name, value):
    path = FOLDER / name
    path.parent.mkdir(exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def catalog():
    prefix = "evidence/batch4_regulatory_20261013/raw/nhtsa_models_chevrolet_2026"
    original_metadata_path = prefix + ".metadata.json"
    original = json.loads((ROOT / original_metadata_path).read_text())
    body_ref = pin(prefix + ".json")
    assert original["http_status"] == 200
    assert original.get("raw_response_sha256", original.get("archive_sha256")) == body_ref["sha256"]
    identifier = "catalog_chevrolet_2026"
    metadata = {**original, "capture_id": identifier, "raw_response_sha256": body_ref["sha256"],
        "raw_response_bytes": body_ref["bytes"], "automatic_redirects": original.get("automatic_redirects"), "public_raw_available": True,
        "status": "ORIGINAL_BYTES_CAPTURED", "content_type": "application/json",
        "known_at": original["observed_at"], "archive_encoding": "raw",
        "source_metadata": pin(original_metadata_path),
        "legacy_catalog_redirect_policy_recorded": "automatic_redirects" in original,
        "metadata_role": "Derived original-byte pin descriptor; original metadata remains unchanged"}
    metadata.setdefault("final_url", original["requested_url"])
    return identifier, {"metadata": metadata, "body_available": True, "body_ref": body_ref}


def recall(identifier, review_relative):
    original_path = ROOT / review_relative
    original = json.loads(original_path.read_text())
    response_relative = original_path.parent.relative_to(ROOT).as_posix() + "/response.json"
    body_ref = pin(response_relative)
    assert body_ref["sha256"] == original["raw_response_sha256"]
    assert body_ref["bytes"] == original["raw_response_bytes"]
    metadata = {"capture_id": identifier, "requested_url": original["requested_url"], "final_url": original["final_url"],
        "http_status": original["http_status"], "content_type": original["content_type"],
        "started_at": original["started_at"], "observed_at": original["source_observed_at"],
        "known_at": original["known_at"], "raw_response_sha256": body_ref["sha256"], "raw_response_bytes": body_ref["bytes"],
        "automatic_redirects": False, "public_raw_available": True, "archive_encoding": "raw",
        "status": "ORIGINAL_BYTES_CAPTURED", "recorded_capture_origin": original["capture_origin"],
        "source_metadata": pin(review_relative), "recorded_review_id": original["review_id"],
        "metadata_role": "Derived original-byte descriptor; source classification is unchanged"}
    return {"metadata": metadata, "body_available": True, "body_ref": body_ref}


def plan(model, identifier, recall_descriptor, catalogue):
    catalog_id, catalog_descriptor = catalogue
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "NHTSA_NEGATIVE_SCOPE",
        "request": {"make": "CHEVROLET", "model": model, "model_year": 2026,
                    "catalog_capture_id": catalog_id, "recall_capture_id": identifier},
        "captures": {catalog_id: catalog_descriptor, identifier: recall_descriptor}}


def main():
    catalogue = catalog()
    new_id = "recall_chevrolet_7500xd_2026"
    new = recall(new_id, "evidence/batch9_regulatory_20261016/captures/1385b4bd35710e9d981e76974551f7dcd20cee1f31aa7127046587db823b6d84/review.json")
    positive = plan("7500XD", new_id, new, catalogue)
    write("actual_review_plan.json", positive)
    write("plans/chevrolet_7500xd_2026.json", positive)
    old_id = "recall_chevrolet_brightdrop_2026"
    retained = recall(old_id, "evidence/batch7_nhtsa_20261015/captures/e4a069681adf2511dd1403cada087166cff9c62c564ded6f1dd4551c1ed1ae01/review.json")
    write("plans/chevrolet_brightdrop_2026_http400.json", plan("BrightDrop", old_id, retained, catalogue))
    after = json.loads((FOLDER / "retained_ledger_after_approval_block.json").read_text())
    old_ids = {row["request_id"] for row in json.loads((FOLDER / "retained_ledger_before.json").read_text())["requests"]}
    charged = [r for r in after["requests"] if r["request_id"] not in old_ids and r["outcome"] == "RUNNING"]
    assert len(charged) == 1
    preplan = json.loads((FOLDER / "precommitted_plan.json").read_text())
    selected = [r for r in preplan["scopes"] if r["scope_key"] == charged[0]["identity_key"]]
    assert len(selected) == 1 and selected[0]["make"] == "CHEVROLET" and selected[0]["model"] == "5500XG"
    blocked_id = "recall_chevrolet_5500xg_2026_blocked"
    missing = {"metadata": {"capture_id": blocked_id, "status": "APPROVAL_BLOCKED_RESPONSE_UNAVAILABLE",
        "public_raw_available": False, "archive_path": None, "raw_response_sha256": None,
        "raw_response_bytes": None, "http_status": None, "started_at": None, "observed_at": None,
        "known_at": after["recorded_at"], "automatic_redirects": False,
        "recorded_request_id": charged[0]["request_id"], "recorded_request_row_sha256": charged[0]["full_row_sha256"],
        "request_reserved_at": charged[0]["attempted_at"], "requested_url": None, "final_url": None,
        "source_response_observed": False, "source_metadata": pin("evidence/batch9_regulatory_20261016/retained_ledger_after_approval_block.json"),
        "unavailable_reason": "Automatic approval rejected the live operation; no source response or transport outcome is established"},
        "body_available": False, "body_ref": None}
    write("plans/chevrolet_5500xg_2026_approval_blocked.json", plan("5500XG", blocked_id, missing, catalogue))
    write("capture_index.json", {"frozen_at": datetime.now(timezone.utc).isoformat(),
        "new_original_source_responses": 1, "new_original_source_bytes": new["body_ref"]["bytes"],
        "new_successful_http200_responses": 1, "new_complete_negatives": 0,
        "new_charged_ledger_rows": 2, "approval_interrupted_rows_without_source_response": 1,
        "planned_but_not_attempted_scopes": [{"make": "FORD", "model": name, "model_year": 2026,
            "status": "NOT_ATTEMPTED_AFTER_APPROVAL_BLOCK", "request_id": None, "http_status": None} for name in ("F-650", "F-750")],
        "new_actual_response": new, "retained_http400_response_used_offline": retained,
        "approval_blocked_response": missing, "old_raw_or_metadata_files_rewritten": False,
        "additional_http_or_database_actions_by_builder": 0})
    print(json.dumps({"actual_plan_count": 3, "new_public_original_responses": 1,
                      "retained_http400_replay": 1, "honestly_missing_blocked_response": 1}))


if __name__ == "__main__":
    main()
