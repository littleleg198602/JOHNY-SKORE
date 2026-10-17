"""Explicit original responses and exact registered rows; no new transport."""
import hashlib
import json
from pathlib import Path

from market_checker_app.services.sec_instrument_class_scope_review import registered_security_tables,review_sec_instrument_class_scope
from market_checker_app.services.sec_symbol_action_scope_review import symbol_action_passages

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def pin(row):
    return {key:row[key] for key in ("byte_start","byte_end","sha256")}


def build_inputs():
    captures,descriptors={},{}
    for key,n in (("certificate",1),("certificate_index",2),("schedule",3),("schedule_index",4),("may_cover",5),("august_cover",6),("submissions",0)):
        folder=HERE if n else ROOT/"evidence/batch8_identity_20261015"
        path=folder/"captures"/f"attempt-{n or 1:02d}.metadata.json"
        metadata=json.loads(path.read_bytes())
        if metadata["status"]!="PUBLIC_ORIGINAL_BYTES_CAPTURED":raise ValueError("Selected whole body is unavailable")
        body_path=path.parent/metadata["body_path"];body=body_path.read_bytes()
        if len(body)!=metadata["bytes"] or hashlib.sha256(body).hexdigest()!=metadata["raw_sha256"]:raise ValueError("Original response bytes changed")
        captures[key]=(metadata,body)
        descriptors[key]={"metadata":metadata,"body_available":True,"body_ref":{"path":body_path.relative_to(ROOT).as_posix(),"sha256":metadata["raw_sha256"],"bytes":len(body)}}
    request={"scope":{"issuer_cik":"0001474432","symbol":"P","class_title":"Class A Common Stock, $0.0001 par value per share","cusip":"74624M102"},
             "filings":[{"source_key":key,"proof_key":"submissions","proof_kind":"SUBMISSIONS","form":"8-K"} for key in ("may_cover","august_cover")]
              +[{"source_key":"certificate","proof_key":"certificate_index","proof_kind":"INDEX","form":"EX-4.1"},
                {"source_key":"schedule","proof_key":"schedule_index","proof_kind":"SCHEDULE13_INDEX","form":"SCHEDULE 13G/A"}],
             "registered_claims":[],"schedule_claims":[{"source_key":"schedule"}],"certificate_claims":[{"source_key":"certificate"}]}
    for key in ("may_cover","august_cover"):
        body=captures[key][1]
        sections=[item for item in symbol_action_passages(body) if item["text"].startswith("Securities registered pursuant to Section 12(b) of the Act:")]
        tables=[table for table in registered_security_tables(body) if table["table_number"]==6]
        if len(sections)!=1 or len(tables)!=1:raise ValueError("Actual original registered declaration is missing or ambiguous")
        table=tables[0]
        request["registered_claims"].append({"source_key":key,"context_ref":"c-1","issuer_name":"Everpure, Inc.",
            "registration_section":sections[0],"table_number":6,"header_row":2,"security_row":3,
            "header_pin":pin(table["rows"][1]),"security_pin":pin(table["rows"][2])})
    return request,captures,{"schema_version":"captured_identity_review_plan_v1","kind":"SEC_INSTRUMENT_CLASS_SCOPE","request":request,"captures":descriptors}


def main():
    request,captures,plan=build_inputs();report=review_sec_instrument_class_scope(request,captures)
    for name,value in (("captured_review_plan.json",plan),("actual_review.json",report)):
        with (HERE/name).open("x",encoding="utf-8") as stream:stream.write(json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"registered_anchor_count":report["registered_source_anchor_count"],"blockers":report["blockers"],"knowledge_at":report["knowledge_at"]}))


if __name__=="__main__":main()
