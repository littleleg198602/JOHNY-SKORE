"""Explicit retained original certificate replay; zero source requests."""
import hashlib
import json
from pathlib import Path

from market_checker_app.collectors.sec_certificate_instrument_review import review_sec_certificate_instrument

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def build_inputs():
    selected = {
        "wrapper": ROOT / "evidence/batch9_identity_20261016/captures/attempt-01.metadata.json",
        "index": ROOT / "evidence/batch9_identity_20261016/captures/attempt-02.metadata.json",
        "image": HERE / "captures/attempt-01.metadata.json",
    }
    captures, descriptors = {}, {}
    for key, path in selected.items():
        metadata = json.loads(path.read_bytes())
        if metadata["status"] != "PUBLIC_ORIGINAL_BYTES_CAPTURED":
            raise ValueError("Original selected certificate body is unavailable")
        body_path = path.parent / metadata["body_path"]
        body = body_path.read_bytes()
        if len(body) != metadata["bytes"] or hashlib.sha256(body).hexdigest() != metadata["raw_sha256"]:
            raise ValueError("Original certificate bytes changed")
        captures[key] = (metadata, body)
        descriptors[key] = {"metadata": metadata, "body_available": True,
                            "body_ref": {"path": body_path.relative_to(ROOT).as_posix(),
                                         "sha256": metadata["raw_sha256"], "bytes": len(body)}}
    request = {"scope": {"issuer_cik": "0001474432", "issuer_name": "Everpure, Inc.",
                          "class_title": "Class A Common Stock, $0.0001 par value per share", "cusip": "74624M102",
                          "old_symbol": "PSTG", "new_symbol": "P"},
               "wrapper_key": "wrapper", "index_key": "index", "image_key": "image", "image_reference": "image_0.jpg",
               "measurement": json.loads((HERE / "visual_measurement.json").read_bytes())}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_CERTIFICATE_INSTRUMENT",
            "request": request, "captures": descriptors}
    return request, captures, plan


def main():
    request, captures, plan = build_inputs()
    report = review_sec_certificate_instrument(request, captures)
    for name, value in (("captured_review_plan.json", plan), ("actual_review.json", report)):
        with (HERE / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "literal_specimen_class_cusip_scope_observed": report["literal_specimen_class_cusip_scope_observed"],
                      "observed_cusip": report["observed_cusip"], "blockers": report["blockers"],
                      "review_recorded_at": report["review_recorded_at"], "source_knowledge_at": report["source_knowledge_at"]}))


if __name__ == "__main__":
    main()
