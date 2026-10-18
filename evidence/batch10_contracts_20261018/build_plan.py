"""Build a bounded root-owned-dispatch plan from exact retained evidence pins."""
import json

from evidence.batch10_contracts_20261018.replay_retained import LANE, read_metadata, read_reference


if __name__ == "__main__":
    packet = json.loads((LANE/"selection.json").read_bytes())
    captures = {}
    for key in ("issuer","index","counterparty"):
        raw = read_reference(packet[key+"_body"])
        captures[key] = {"metadata":read_metadata(packet[key+"_capture"]),"body_available":True,
            "body_ref":{"path":packet[key+"_body"]["repository_relative_path"],
                        "sha256":packet[key+"_body"]["sha256"],"bytes":len(raw)}}
    plan = {"schema_version":"captured_identity_review_plan_v1","kind":"SEC_SUPPLIER_REVENUE_DENOMINATOR",
            "request":packet["request"],"captures":captures}
    with (LANE/"actual_review_plan.json").open("x",encoding="utf-8") as handle:
        json.dump(plan,handle,sort_keys=True,indent=2,allow_nan=False);handle.write("\n")
    print(json.dumps({"kind":plan["kind"],"captures":len(captures),"explicit_as_of_override":False,"source_http_requests":0}))
