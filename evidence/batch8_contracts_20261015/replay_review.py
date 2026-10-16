"""Offline replay of exact already retained public originals; zero transport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from market_checker_app.services.sec_purchase_obligation_scope_review import review_sec_purchase_obligation_scope


ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "evidence/batch8_contracts_20261015"


def read_reference(reference):
    relative = PurePosixPath(reference["repository_relative_path"])
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Unsafe repository-relative evidence reference")
    path = ROOT.joinpath(*relative.parts)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink evidence reference refused")
    with path.open("rb") as handle:
        raw = handle.read(4_000_001)
    if len(raw) > 4_000_000 or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("Exact retained evidence bytes/hash mismatch")
    return raw


def read_metadata(reference):
    record = json.loads(read_reference(reference))
    for part in reference.get("json_pointer", "").split("/")[1:]:
        record = record[int(part)] if isinstance(record, list) else record[part]
    return record


def run():
    packet = json.loads((LANE / "selection.json").read_bytes())
    return review_sec_purchase_obligation_scope(read_reference(packet["issuer_body"]),
        issuer_capture=read_metadata(packet["issuer_capture"]), index_body=read_reference(packet["index_body"]),
        index_capture=read_metadata(packet["index_capture"]), selection=packet["selection"])


if __name__ == "__main__":
    report = run()
    # Keep each actual physical review immutable; a replay never overwrites it.
    with (LANE / "review.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": report["status"], "reason": report["reason"],
        "ingested_at": report["ingested_at"], "source_http_attempts": report["source_http_attempts"]}))
