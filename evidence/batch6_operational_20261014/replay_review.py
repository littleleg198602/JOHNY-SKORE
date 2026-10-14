"""Replay pinned original SEC bytes; no network or body replacement/export."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

from market_checker_app.services.sec_operational_metrics_review import (
    MAX_BODY_BYTES, MAX_METADATA_BYTES, review_sec_operational_metric,
)


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent


def _read(path, maximum):
    with path.open("rb") as handle:
        encoded = handle.read(maximum + 1)
    if len(encoded) > maximum:
        raise ValueError("Pinned source exceeds bounded archive read")
    return encoded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    manifest = json.loads((DIRECTORY / "selection.json").read_bytes())
    source, meta_ref = manifest["source_body"], manifest["source_metadata"]
    metadata_bytes = _read(args.source_root / meta_ref["repository_relative_path"], MAX_METADATA_BYTES)
    assert hashlib.sha256(metadata_bytes).hexdigest() == meta_ref["sha256"]
    metadata = json.loads(metadata_bytes)
    # Withheld metadata stops before opening a body. Never use a mirror, wrapper,
    # cleaned capture or alternate encoding as a substitute for rejected bytes.
    assert metadata.get("public_raw_available") is True and metadata.get("status") == "CAPTURED"
    body = _read(args.source_root / source["repository_relative_path"], MAX_BODY_BYTES)
    assert len(body) == source["bytes"] and hashlib.sha256(body).hexdigest() == source["sha256"]
    selection = manifest["selection"]
    cases = {}
    for key, changes, cutoff in (
        ("combined_group_count", {}, None),
        ("cross_context_parent_name_chemical_cik", {"issuer_cik": "0000029915"}, None),
        ("chemical_dimension_requires_separate_review", {"issuer_context_id": "c-1", "issuer_name": "The Dow Chemical Company", "issuer_cik": "0000029915"}, None),
        ("single_issuer_attribution_withheld", {"scope_kind": "SINGLE_ISSUER_GROUP"}, None),
        ("historical_capture_is_not_today_ingestion", {}, datetime.fromisoformat(metadata["observed_at"])),
    ):
        proposed = {**deepcopy(selection), **changes}
        cases[key] = review_sec_operational_metric(body, capture_metadata=metadata,
                          expected_body_sha256=source["sha256"], selection=proposed, as_of=cutoff)
    assert cases["combined_group_count"]["status"] == "CANDIDATE_REVIEWED"
    candidate = cases["combined_group_count"]["candidate"]
    assert candidate["value"] == "91" and candidate["unit_literal"] == "manufacturing sites"
    assert candidate["scope_kind"] == "COMBINED_REPORT_GROUP"
    assert not candidate["individual_issuer_quantity_attribution_verified"]
    assert cases["cross_context_parent_name_chemical_cik"]["reason"] == "DEI_NAME_CIK_SAME_CONTEXT_MISMATCH"
    assert cases["chemical_dimension_requires_separate_review"]["reason"] == "CONTEXT_ENTITY_OR_DIMENSIONS_NEED_SEPARATE_REVIEW"
    assert cases["single_issuer_attribution_withheld"]["reason"] == "INDIVIDUAL_ATTRIBUTION_AMBIGUOUS_IN_COFILING"
    assert cases["historical_capture_is_not_today_ingestion"]["reason"] == "AFTER_PHYSICAL_KNOWLEDGE_CUTOFF"
    assert body == _read(args.source_root / source["repository_relative_path"], MAX_BODY_BYTES)
    assert metadata_bytes == _read(args.source_root / meta_ref["repository_relative_path"], MAX_METADATA_BYTES)
    paths = ["market_checker_app/services/sec_operational_metrics_review.py",
             "tests/test_batch6_sec_operational_metrics.py",
             "evidence/batch6_operational_20261014/selection.json",
             "evidence/batch6_operational_20261014/replay_review.py"]
    result = {"schema_version": 1, "kind": "offline_review_of_genuine_original_sec_capture",
              "started_at": started.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
              "python_version": platform.python_version(), "new_source_http_attempts": 0,
              "source_body_copied_or_replaced": False, "source_bytes_and_metadata_unchanged": True,
              "source_references": {"body": source, "metadata": meta_ref}, "cases": cases,
              "source_scope": "One literal operating count in one combined 2025 co-filing; no 687 coverage or current instrument inference",
              "code_and_selection_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths},
              "acceptance": {"individual_issuer_quantity_attribution_verified": False, "asset_ownership_verified": False,
                             "current_instrument_identity_verified": False, "current_q3_identity_verified": False,
                             "quantity_or_revenue_share_inferred": False, "scoring_applied": False,
                             "historical_availability_reconstructed": False, "windows_end_to_end_verified": False,
                             "historical_oos_verified": False, "specialist_done": False}}
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps({"actual_candidates": 1, "literal_value": "91", "unit": "manufacturing sites",
                      "scope": "COMBINED_REPORT_GROUP", "individual_attribution": False,
                      "original_body_sha256": source["sha256"], "new_http": 0,
                      "cases": {name: item["status"] for name, item in cases.items()}}))


if __name__ == "__main__":
    main()
