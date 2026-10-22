"""Prepare explicit review plans from retained original evidence; zero HTTP."""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANS = ROOT / "evidence/specialist_review_plans"


def ref(path):
    body = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def capture(metadata, path=None):
    return {"metadata": metadata, "body_available": path is not None,
            "body_ref": ref(path) if path else None}


def save(name, kind, request, captures):
    value = {"schema_version": "captured_identity_review_plan_v1", "kind": kind,
             "request": request, "captures": captures}
    PLANS.mkdir(exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path = PLANS / (name + ".json")
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Preserve the existing exact plan version")
    else:
        path.write_bytes(raw)


def main():
    from evidence.batch6_sec_20261014.build_review import load_inputs
    from evidence.batch6_identity_20261014.replay_review import build_inputs

    folder = ROOT / "evidence/batch6_healthcare_20261014"
    issuer_meta = json.loads((folder / "sec_hca_submissions.metadata.json").read_bytes())
    projection = ROOT / "tests/fixtures/healthcare/hca_cms_captured_20261011.json"
    projection_ref = ref(projection)
    owner_meta = {"capture_form": "PROJECTED_PUBLISHER_FIELDS", "raw_publication_permitted": True,
                  "body_bytes": projection_ref["bytes"], "body_sha256": projection_ref["sha256"]}
    for index, row in enumerate(json.loads(projection.read_bytes()), 1):
        scope = {"enrollment_id": row["ENROLLMENT ID"], "owner_associate_id": row["ASSOCIATE ID - OWNER"],
                 "owner_role_code": row["ROLE CODE - OWNER"]}
        save(f"HCA_nemocnice_{index}", "CMS_PROVIDER_OWNER", {"scope": scope}, {
            "owner": capture(owner_meta, projection),
            "provider": capture({"capture_form": "MISSING_BODY"}),
            "issuer": capture(issuer_meta, folder / issuer_meta["body_path"]),
        })

    observation, source, evidence, _ = load_inputs()
    descriptors = {}
    old = ROOT / "evidence/batch4_sec_20261013/primary"
    paths = [old / "msft_form4.xml", old / "nadella_submissions.json",
             ROOT / "evidence/batch6_sec_20261014/primary/nadella_2014_form3.txt"]
    for key, source_capture, path in zip(("source", "profile", "packet"), (source, *evidence), paths):
        metadata = {k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in asdict(source_capture).items() if k != "body"}
        descriptors[key] = capture(metadata, path)
    for mode in ("EVENT_DATE", "OBSERVED_SNAPSHOT"):
        save("MSFT_Form4_" + mode, "FORM4_PERSON", {"observation": observation, "scope_mode": mode}, descriptors)

    service, claim, _, diagnostics = build_inputs()
    by_hash = {item["sha256"]: ROOT / item["path"] for item in diagnostics["source_inventory"]}
    descriptors = {}
    for index, snapshot in enumerate(service.snapshots.values()):
        metadata = {"kind": snapshot.kind, "url": snapshot.url, "snapshot_id": snapshot.snapshot_id,
                    "sha256": snapshot.sha256, "observed_at": snapshot.observed_at.isoformat(),
                    "published_at": snapshot.published_at.isoformat() if snapshot.published_at else None}
        descriptors["source_" + str(index)] = capture(metadata, by_hash[snapshot.sha256])
    save("LEG_vymena_instrumentu", "STOCK_MERGER_EXCHANGE", {
        "quarter": "2026q3", "tickers": ["LEG"], "proposal": claim}, descriptors)

    regulatory = ROOT / "evidence/batch6_regulatory_20261014"
    if (regulatory / "capture_plan.json").exists():
        descriptors = {}
        for meta in json.loads((regulatory / "capture_plan.json").read_bytes()):
            descriptors[meta["capture_id"]] = capture(meta,
                ROOT / meta["archive_path"] if meta.get("public_raw_available") else None)
        save("DOW_EPA_provozovna", "EPA_FRS_OWNER", {
            "proposal": json.loads((regulatory / "proposal.json").read_bytes())}, descriptors)

    operational = ROOT / "evidence/batch6_operational_20261014/selection.json"
    if operational.exists():
        pins = json.loads(operational.read_bytes())
        meta_path = ROOT / pins["source_metadata"]["repository_relative_path"]
        if ref(meta_path)["sha256"] != pins["source_metadata"]["sha256"]:
            raise ValueError("Operational source metadata pin changed")
        meta = json.loads(meta_path.read_bytes())
        body_path = ROOT / pins["source_body"]["repository_relative_path"]
        if ref(body_path)["sha256"] != pins["source_body"]["sha256"]:
            raise ValueError("Operational original body changed")
        save("DOW_provozni_rozsah_skupiny", "SEC_OPERATIONAL_METRIC", {
            "selection": pins["selection"]}, {"source": capture(meta, body_path)})


if __name__ == "__main__":
    main()
