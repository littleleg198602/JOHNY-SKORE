"""Exact retained public SWKS financial-statement replay, with zero transport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from market_checker_app.collectors.sec_supplier_revenue_denominator_review import review_sec_supplier_revenue_denominator


ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "evidence/batch10_contracts_20261018"


def read_reference(reference):
    relative = PurePosixPath(reference["repository_relative_path"])
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Unsafe repository-relative evidence path")
    path = ROOT.joinpath(*relative.parts)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink evidence refused")
    with path.open("rb") as handle:
        body = handle.read(4_000_001)
    if len(body) > 4_000_000 or hashlib.sha256(body).hexdigest() != reference["sha256"]:
        raise ValueError("Original retained evidence bound or SHA mismatch")
    return body


def read_metadata(reference):
    record = json.loads(read_reference(reference))
    for part in reference.get("json_pointer", "").split("/")[1:]:
        record = record[int(part)] if isinstance(record, list) else record[part]
    return record


def load_inputs():
    packet = json.loads((LANE / "selection.json").read_bytes())
    captures = {name:(read_metadata(packet[name+"_capture"]),read_reference(packet[name+"_body"]))
                for name in ("issuer","index","counterparty")}
    return packet["request"],captures


def run():
    request,captures = load_inputs()
    return review_sec_supplier_revenue_denominator(request,captures)


if __name__ == "__main__":
    report = run()
    with (LANE / "actual_review.json").open("x",encoding="utf-8") as handle:
        json.dump(report,handle,sort_keys=True,indent=2,allow_nan=False);handle.write("\n")
    print(json.dumps({"status":report["status"],"reason":report["reason"],
        "ingested_at":report["ingested_at"],"source_http_requests":report["source_http_requests"]}))
