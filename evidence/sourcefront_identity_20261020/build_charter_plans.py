"""Prepare two explicit retained-byte charter cases; no source collection."""
from pathlib import Path
import hashlib
import json

from bs4 import BeautifulSoup

from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot
from market_checker_app.services.sec_corporate_action_bridge import action_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _read(path):
    return json.loads((ROOT / path).read_bytes())


def _source(meta_path, published):
    meta = _read(meta_path)
    body_path = Path(meta_path).parent / meta.get("body_path", meta.get("raw_file"))
    raw = (ROOT / body_path).read_bytes()
    expected = meta.get("raw_sha256", meta.get("sha256"))
    if (meta["http_status"] != 200 or meta["requested_url"] != meta["final_url"]
            or len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != expected):
        raise ValueError("Exact public original-byte pin failed")
    snapshot = SourceSnapshot("corporate_action_filing", meta["requested_url"], meta["observed_at"], raw, published)
    return snapshot, {"metadata": {**snapshot.metadata(), "snapshot_id": snapshot.snapshot_id},
                      "body_available": True,
                      "body_ref": {"path": body_path.as_posix(), "sha256": snapshot.sha256, "bytes": len(raw)}}


def _paragraph(snapshot, prefix):
    # Author the explicit selection from the full original, then the runtime
    # independently checks the resulting quote against its visible-byte parser.
    for tag in BeautifulSoup(snapshot.payload, "html.parser").find_all("p"):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if text.startswith(prefix):
            if text not in action_text(snapshot):
                raise ValueError("Selected original paragraph is not visible runtime text")
            return text
    raise ValueError("Missing exact original paragraph")


def build_plans():
    previous_path = "evidence/batch7_identity_20261015/captured_review_plan.json"
    previous = _read(previous_path)
    catalog_sources = {entry["metadata"]["kind"]: entry for entry in previous["captures"].values()
                       if entry["metadata"]["kind"] in {"ticker_catalog", "13f_list"}}
    if set(catalog_sources) != {"ticker_catalog", "13f_list"}:
        raise ValueError("Missing exact retained catalog/list selections")
    rows = [
        ("LEG", "0000058492", "Leggett & Platt, Incorporated", "SURVIVING_AUTHORIZED_CLASS", "10000",
         "evidence/sourcefront_20261020/primary/leg_final_articles.metadata.json",
         "evidence/batch4_identity_20261013/captures/0727cd2189a797d82f066ded90209aca3f94a15d7480061b0b1c916da146b5a6.json",
         "evidence/batch4_identity_20261013/captures/4c272eeb028484145b93a691fdc329021dd1d2d3d3a6133991dcff6bb7baa9ff.json",
         "ARTICLE I.", "ARTICLE II.", "ARTICLE IV.", "ARTICLE V.", "On August 26, 2026", "2026-08-26", None,
         "LEG_pravni_kapital_po_fuzi.json"),
        ("XOM", "0002115436", "EXXONMOBIL HOLDINGS CORPORATION", "SUCCESSOR_AUTHORIZED_CLASS", "9000000000",
         "evidence/sourcefront_20261020/primary/xom_successor_formation.metadata.json",
         "evidence/batch4_identity_20261013/captures/8efa2c0f68db6520ddaf11e5d3f8dee0c3abfa0b14c3f3f79230e2d93b549021.json",
         "evidence/batch4_identity_20261013/captures/6184e86d63ee20e590a23859d25a9fa36503d36de0a23c67c4c766c07cbcb03c.json",
         "FIRST.", "SECOND.", "FOURTH.", "(1)", "On July 1, 2026", "2026-07-01", "2026-07-01",
         "XOM_pravni_kapital_nastupce.json"),
    ]
    plans, lineage = {}, []
    for (ticker, cik, name, role, count, charter_meta, parent_meta, submissions_meta,
         name_start, name_end, class_start, class_end, parent_prefix, executed, effective, filename) in rows:
        parent_capture = _read(parent_meta)
        accession = parent_capture["requested_url"].split("/")[-2]
        sm = _read(submissions_meta)
        sbpath = Path(submissions_meta).parent / sm.get("body_path", sm.get("raw_file"))
        sb = (ROOT / sbpath).read_bytes()
        if len(sb) != sm["bytes"] or hashlib.sha256(sb).hexdigest() != sm.get("raw_sha256", sm.get("sha256")):
            raise ValueError("Original SEC submissions pin failed")
        recent = json.loads(sb)["filings"]["recent"]
        acc = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
        i = recent["accessionNumber"].index(acc)
        published = recent["acceptanceDateTime"][i]
        charter, charter_entry = _source(charter_meta, published)
        parent, parent_entry = _source(parent_meta, published)
        text = action_text(charter)
        def section(start, end):
            a = text.index(start)
            return text[a:text.index(end, a + len(start))].strip()
        execution = text[text.index("IN WITNESS WHEREOF"):text.index("/s/", text.index("IN WITNESS WHEREOF"))].strip()
        effective_excerpt = None
        if effective:
            a = text.index("(As Amended Effective")
            effective_excerpt = text[a:text.index(")", a)+1]
        claim = {"bridge_kind": "CHARTER_AUTHORIZED_CLASS", "ticker": ticker, "issuer_cik": cik,
                 "issuer_name_text": name, "class_role": role,
                 "charter_snapshot_id": charter.snapshot_id, "parent_filing_snapshot_id": parent.snapshot_id,
                 "parent_role_excerpt": _paragraph(parent, parent_prefix),
                 "issuer_excerpt": section(name_start, name_end),
                 "authorized_class_excerpt": section(class_start, class_end),
                 "class_title": "common stock, par value $0.001 per share", "authorized_shares": count,
                 "effective_on": effective, "effective_excerpt": effective_excerpt,
                 "execution_on": executed, "execution_excerpt": execution}
        plans[filename] = {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_CHARTER_CLASS",
                           "request": {"quarter": "2026q3", "claim": claim},
                           "captures": {**catalog_sources, "parent_filing": parent_entry, "charter": charter_entry}}
        lineage.append({"ticker": ticker, "submissions_metadata_path": submissions_meta,
                        "submissions_body_path": sbpath.as_posix(), "submissions_body_sha256": hashlib.sha256(sb).hexdigest(),
                        "recent_index": i, "accession": acc, "parent_acceptanceDateTime": published,
                        "attachment_publication_basis": "Original attachment shares the exact linked parent accession; publication clock is that SEC submissions acceptance, not charter execution/effectiveness or HTTP Last-Modified.",
                        "same_accession_independent_action_or_control_proof": False})
    return plans, lineage


if __name__ == "__main__":
    plans, lineage = build_plans()
    for name, value in {**plans, "source_lineage.json": lineage}.items():
        body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+"\n").encode()
        path = HERE / name
        if path.exists():
            if path.read_bytes() != body:
                raise ValueError("Preserve existing original case/version")
        else:
            path.write_bytes(body)
    print(json.dumps({"plans": list(plans), "source_collection_requests": 0}))
