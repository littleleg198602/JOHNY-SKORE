"""Add explicit existing-parent pins; preserve the first audit output exactly.

No review parser/test suite is repeated. Only public retained bytes are hashed.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent


def pin(relative):
    body = (ROOT / relative).read_bytes()
    return {"path": relative, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def main():
    started = datetime.now(timezone.utc)
    original = FOLDER / "source_plan.json"
    body = original.read_bytes()
    assert hashlib.sha256(body).hexdigest() == "fd064539d34a6dccd2dc2895ab7ab08d76fc541b83d955fe7253d2f2ea4e1ab0"
    plan = json.loads(body)
    opinion = "evidence/batch8_regulatory_20261015/raw/govinfo_case_opinion.pdf"
    opinion_pin = pin(opinion)
    assert (opinion_pin["bytes"], opinion_pin["sha256"]) == (
        1_344_697, "dfc68609ea10dbb6f2e86b99df9b797030c17fc3224e020bd33f8a40478b0efa")
    opinion_meta = "evidence/batch8_regulatory_20261015/raw/govinfo_case_opinion.metadata.json"
    opinion_metadata = json.loads((ROOT / opinion_meta).read_bytes())
    order_route = plan["route_assessments"][0]
    assert opinion_pin["sha256"] == order_route["original_opinion_clause"]["evidence"]["whole_original_sha256"]
    order_route.update(original_opinion_parent=opinion_pin, original_opinion_metadata=pin(opinion_meta),
        original_opinion_observed_at=opinion_metadata["observed_at"],
        original_opinion_reinterpreted_by_this_followup=False)
    audit_path = FOLDER / "doj_index_link_audit.json"
    audit = json.loads(audit_path.read_bytes())
    dated = next(row for row in audit["case_document_link_occurrences"]
                 if row["exact_href"] == "/atr/media/1358781/dl?inline")
    assert "May 30, 2024" in dated["publisher_paragraph"]["exact_html"]
    index = (ROOT / audit["whole_original"]["path"]).read_bytes()
    assert hashlib.sha256(index).hexdigest() == audit["whole_original"]["sha256"]
    lo, hi = dated["anchor_raw_byte_span"]
    assert hashlib.sha256(index[lo:hi]).hexdigest() == dated["anchor_raw_sha256"]
    plan["route_assessments"][1]["paired_publisher_dated_letter_anchor"] = dated
    assert plan["status"] == "BLOCKED" and plan["eligible_candidate_count"] == 0
    assert plan["source_requests_allowed_by_this_plan"] == 0
    prior_relative = str((FOLDER / "initial_source_plan_before_parent_pin.json").relative_to(ROOT))
    with (ROOT / prior_relative).open("xb") as stream:
        stream.write(body)
    plan["initial_audit_plan"] = pin(prior_relative)
    plan["initial_audit_verifier"] = plan["verifier"]
    plan["verifier"] = pin(str(Path(__file__).relative_to(ROOT)))
    plan["provenance_revision"] = {
        "started_at": started.isoformat(),
        "change": "Add explicit whole Doc283 parent/metadata and paired dated letter anchor. Same BLOCKED scope, no source review/test repeat or request.",
        "initial_audit_and_history_files_preserved_exactly": True,
    }
    completed = datetime.now(timezone.utc)
    assert completed >= started >= datetime.fromisoformat(plan["known_at"])
    plan["known_at"] = completed.isoformat()
    original.write_bytes(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")
    print(json.dumps({"status": plan["status"], "source_plan": pin(str(original.relative_to(ROOT))),
        "eligible_candidates": 0, "HTTP": 0, "database": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
