"""Review the two preserved SWKS originals and earlier independent legal snapshot; no HTTP."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from market_checker_app.services.captured_identity_review_service import retained_file
from market_checker_app.services.sec_supplier_concentration_review import review_sec_supplier_concentration

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent


def build():
    selection = json.loads((EVIDENCE / "selection.json").read_bytes())
    def read(key):
        pin = selection[key]
        raw = retained_file(ROOT, pin["repository_relative_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == pin["sha256"]
        return raw
    captures, descriptors = {}, {}
    for key in ("issuer", "index", "counterparty"):
        metadata = json.loads(read(key + "_capture"))
        for part in selection[key + "_capture"].get("json_pointer", "").split("/")[1:]:
            metadata = metadata[int(part)] if isinstance(metadata, list) else metadata[part]
        body = read(key + "_body")
        captures[key] = (metadata, body)
        descriptors[key] = {"metadata": metadata, "body_available": True,
            "body_ref": {"path": selection[key + "_body"]["repository_relative_path"],
                         "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}}
    result = review_sec_supplier_concentration(selection["request"], captures)
    assert result["status"] == "CANDIDATE_REVIEWED", result
    assert [row["value_literal"] for row in result["claim"]["quantities"]] == ["67", "69", "66"]
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_SUPPLIER_CONCENTRATION",
            "request": selection["request"], "captures": descriptors}
    return result, plan


if __name__ == "__main__":
    result, plan = build()
    for name, record in (("actual_review.json", result), ("actual_review_plan.json", plan)):
        with (EVIDENCE / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(name)
