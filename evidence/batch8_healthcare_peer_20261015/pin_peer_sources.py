"""Pin two explicit peer plans and their bounded, available original bodies."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body


def original(path, maximum):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not 0 < len(raw) <= maximum:
        raise ValueError("Explicit peer file exceeds bounded review cap")
    return raw


def pin(root, plan_ref, prefixes, modules):
    root = Path(root).resolve(strict=True)
    plan_raw = original(root / plan_ref, 128_000)
    if _has_sensitive_body(plan_raw):
        raise ValueError("Whole unsafe peer metadata is unavailable")
    plan = json.loads(plan_raw)
    if not isinstance(plan.get("captures"), dict) or not 1 <= len(plan["captures"]) <= 16:
        raise ValueError("Select bounded original capture roster")
    pins, total = {}, 0
    for key, item in plan["captures"].items():
        metadata = item["metadata"]
        encoded = json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 64_000 or _has_sensitive_body(encoded):
            raise ValueError("Unsafe or unbounded whole peer capture metadata")
        info = {"metadata_sha256": hashlib.sha256(encoded).hexdigest(), "body_available": item["body_available"],
                "source_url": metadata.get("source_url", metadata.get("requested_url")),
                "observed_at": metadata.get("observed_at"), "known_at": metadata.get("known_at", metadata.get("observed_at")),
                "source_date_is_review_clock": False, "source_first_historical_availability_proven": False}
        if item["body_available"] is False:
            if item["body_ref"] is not None:
                raise ValueError("Unavailable whole source cannot have substitute bytes")
            info["body_ref"] = None
        elif item["body_available"] is True:
            if (metadata.get("retention") in ("WITHHELD_SENSITIVE_BODY", "MISSING_BODY", "NOT_CAPTURED_COMPLETE_BODY")
                    or metadata.get("public_raw_available") is False or metadata.get("raw_publication_permitted") is False):
                raise ValueError("Unavailable peer source cannot be read or restored")
            ref = item["body_ref"]
            if (not isinstance(ref, dict) or set(ref) != {"path", "sha256", "bytes"}
                    or not isinstance(ref["path"], str) or not ref["path"].startswith(prefixes)
                    or type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= 4_000_000):
                raise ValueError("Only explicitly pinned public SEC bodies may be read")
            path = (root / ref["path"]).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("Peer body path escapes selected root")
            raw = original(path, ref["bytes"])
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) != ref["bytes"] or digest != ref["sha256"] or _has_sensitive_body(raw):
                raise ValueError("Peer original source hash/length/publication conflict")
            total += len(raw)
            if total > 24_000_000:
                raise ValueError("Peer capture roster exceeds bounded total")
            info.update(body_ref=ref, original_whole_bytes_match=True, unsafe_body_rewritten=False)
        else:
            raise ValueError("Explicit body availability required")
        pins[key] = info
    code_pins = {}
    for ref in modules:
        raw = original(root / ref, 4_000_000)
        code_pins[ref] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return {"plan_path": plan_ref, "plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "kind": plan["kind"],
            "capture_count": len(pins), "available_original_body_count": sum(p["body_available"] for p in pins.values()),
            "available_original_body_bytes": total, "capture_pins": pins, "reviewed_code_pins": code_pins}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sec-root", required=True)
    parser.add_argument("--symbol-root", required=True)
    args = parser.parse_args()
    common = ("market_checker_app/services/ir_primary_scout_service.py",
              "market_checker_app/services/sec_person_kind_review.py")
    sec = pin(args.sec_root, "evidence/batch8_sec_20261015/actual_review_plan.json",
              ("evidence/batch6_sec_20261014/primary/", "evidence/batch8_sec_20261015/primary/"),
              (*common, "market_checker_app/services/sec_owner_instrument_scope_review.py",
               "market_checker_app/services/sec_form4_person_bridge.py"))
    symbol = pin(args.symbol_root, "evidence/batch8_identity_20261015/captured_review_plan.json",
                 ("evidence/batch8_identity_20261015/captures/",),
                 (*common, "market_checker_app/services/sec_symbol_action_scope_review.py",
                  "market_checker_app/services/sec13f_identity_bulk.py", "market_checker_app/services/sec_corporate_action_bridge.py",
                  "tests/test_batch8_symbol_action_scope.py",
                  "evidence/batch8_identity_20261015/producer_versions/pre_clause_guard_service.py",
                  "evidence/batch8_identity_20261015/producer_versions/pre_clause_guard_tests.py"))
    print(json.dumps({"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
                      "sec_owner_instrument": sec, "symbol_action": symbol,
                      "actual_new_source_requests": 0, "database_calls": 0,
                      "original_bodies_copied_or_rewritten": False, "known_withheld_bodies_read_or_restored": 0,
                      "registry_adoptions": 0, "full_specialist_acceptance": False}, ensure_ascii=False, indent=2))
