"""Explicit six-capture replay; no transport, glob, adoption or source rewrite."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from market_checker_app.services.sec_symbol_action_scope_review import review_symbol_action_scope, symbol_action_passages

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = (("submissions", 1), ("annual", 2), ("proxy", 3), ("release_index", 4), ("release", 5), ("instrument", 6))


def build_inputs():
    captures, descriptors = {}, {}
    for key, number in SOURCES:
        metadata_path = HERE / "captures" / f"attempt-{number:02d}.metadata.json"
        metadata = json.loads(metadata_path.read_bytes())
        if metadata.get("status") != "PUBLIC_ORIGINAL_BYTES_CAPTURED":
            raise ValueError("The selected whole original capture is unavailable")
        body_path = HERE / "captures" / metadata["body_path"]
        body = body_path.read_bytes()
        if len(body) != metadata["bytes"] or hashlib.sha256(body).hexdigest() != metadata["raw_sha256"]:
            raise ValueError("Selected original bytes no longer match their capture")
        captures[key] = (metadata, body)
        descriptors[key] = {"metadata": metadata, "body_available": True,
                            "body_ref": {"path": body_path.relative_to(ROOT).as_posix(),
                                         "sha256": metadata["raw_sha256"], "bytes": len(body)}}
    passages = symbol_action_passages(captures["release"][1])
    selected = [item for item in passages if item["text"].startswith("• Formally transitioned the corporate brand")]
    if len(selected) != 1:
        raise ValueError("Exact new release observation is missing or ambiguous")
    request = {
        "scope": {"issuer_cik": "0001474432", "from_symbol": "PSTG", "to_symbol": "P",
                  "class_title": "Class A Common Stock, par value $0.0001 per share", "cusip": "74624M102"},
        "filings": [{"source_key": key, "proof_key": "submissions", "proof_kind": "SUBMISSIONS", "form": form}
                    for key, form in (("annual", "10-K"), ("proxy", "DEF 14A"), ("instrument", "SCHEDULE 13G"))]
                   + [{"source_key": "release", "proof_key": "release_index", "proof_kind": "INDEX", "form": "EX-99.1"}],
        "action_claims": [{"source_key": "release", "event_kind": "SYMBOL_CHANGE", "asserted_phase": "EFFECTIVE",
                           "day": None, "date_text": None, "date_role": "UNKNOWN", "selection": selected[0]}],
        "cover_claims": [{"source_key": "annual", "role": "FROM", "context_ref": "c-1", "issuer_name": "Everpure, Inc."},
                         {"source_key": "proxy", "role": "NAME_ONLY", "context_ref": "c-1", "issuer_name": "Everpure, Inc."}],
        "instrument_claims": [{"source_key": "instrument", "role": "FROM", "cover_source_key": "annual", "cover_context_ref": "c-1"}],
    }
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_SYMBOL_ACTION_SCOPE",
            "request": request, "captures": descriptors}
    return request, captures, plan


def main():
    request, captures, plan = build_inputs()
    report = review_symbol_action_scope(request, captures)
    for name, record in (("captured_review_plan.json", plan), ("actual_review.json", report)):
        with (HERE / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "effective_on": report["effective_on"], "blockers": report["blockers"],
                      "knowledge_at": report["knowledge_at"], "source_knowledge_at": report["source_knowledge_at"],
                      "physical_replay_at": datetime.now(timezone.utc).isoformat()}))


if __name__ == "__main__":
    main()
