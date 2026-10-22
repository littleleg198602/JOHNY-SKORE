"""One explicit retained P point proposal; zero collection or source rewrite."""
import hashlib
import json
from pathlib import Path

from market_checker_app.services.sec_dated_instrument_point_review import review_sec_dated_instrument_point
from market_checker_app.services.sec_instrument_class_scope_review import registered_security_tables
from market_checker_app.services.sec_symbol_action_scope_review import symbol_action_passages

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def build_inputs():
    paths = {"schedule": "evidence/batch9_identity_20261016/captures/attempt-03.metadata.json",
             "schedule_index": "evidence/batch9_identity_20261016/captures/attempt-04.metadata.json",
             "cover": "evidence/batch9_identity_20261016/captures/attempt-06.metadata.json",
             "submissions": "evidence/batch8_identity_20261015/captures/attempt-01.metadata.json"}
    captures, descriptors = {}, {}
    for key, relative in paths.items():
        path = ROOT / relative
        metadata = json.loads(path.read_bytes())
        if metadata["status"] != "PUBLIC_ORIGINAL_BYTES_CAPTURED":
            raise ValueError("The selected whole original is unavailable")
        original = path.parent / metadata["body_path"]
        body = original.read_bytes()
        if len(body) != metadata["bytes"] or hashlib.sha256(body).hexdigest() != metadata["raw_sha256"]:
            raise ValueError("The selected original byte pin changed")
        captures[key] = (metadata, body)
        descriptors[key] = {"metadata": metadata, "body_available": True,
                            "body_ref": {"path": original.relative_to(ROOT).as_posix(),
                                         "sha256": metadata["raw_sha256"], "bytes": len(body)}}
    body = captures["cover"][1]
    sections = [row for row in symbol_action_passages(body) if row["text"].startswith("Securities registered pursuant to Section 12(b) of the Act:")]
    tables = [row for row in registered_security_tables(body) if row["table_number"] == 6]
    if len(sections) != 1 or len(tables) != 1:
        raise ValueError("Select the unambiguous original registered cover scope")
    pin = lambda row: {key: row[key] for key in ("byte_start", "byte_end", "sha256")}
    registered = {"source_key": "cover", "context_ref": "c-1", "issuer_name": "Everpure, Inc.", "registration_section": sections[0],
                  "table_number": 6, "header_row": 2, "security_row": 3,
                  "header_pin": pin(tables[0]["rows"][1]), "security_pin": pin(tables[0]["rows"][2])}
    request = {"anchor_on": "2026-08-26",
               "identity": {"issuer_cik": "0001474432", "issuer_name": "Everpure, Inc.", "ticker": "P",
                            "class_title": "Class A Common Stock, $0.0001 par value per share", "cusip": "74624M102"},
               "instrument": {"source_key": "schedule", "index_key": "schedule_index", "form": "SCHEDULE 13G/A"},
               "cover": {"source_key": "cover", "proof_key": "submissions", "proof_kind": "SUBMISSIONS", "form": "8-K", "registered_claim": registered}}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_DATED_INSTRUMENT_POINT", "request": request, "captures": descriptors}
    return request, captures, plan


def main():
    request, captures, plan = build_inputs()
    report = review_sec_dated_instrument_point(request, captures)
    for name, value in (("captured_review_plan.json", plan), ("actual_review.json", report)):
        with (HERE / name).open("x", encoding="utf-8") as output:
            output.write(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"point_supported": report["point_supported"], "requested_anchor_on": report["requested_anchor_on"],
                      "schedule_event_on": report["schedule_instrument_fact"]["event_on"], "cover_anchor_on": report["cover_security_fact"]["context_anchor_on"],
                      "blockers": report["blockers"], "review_started_at": report["review_started_at"], "knowledge_at": report["knowledge_at"]}))


if __name__ == "__main__":
    main()
