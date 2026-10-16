"""Offline exact NVDA/TSMC source review; no source requests or raw rewrites."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

from market_checker_app.services.sec_supply_chain_scope_review import review_sec_supply_chain_scope


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent


def _read(ref, maximum):
    path = ROOT / ref["repository_relative_path"]
    with path.open("rb") as handle:
        encoded = handle.read(maximum + 1)
    assert len(encoded) <= maximum and hashlib.sha256(encoded).hexdigest() == ref["sha256"]
    return encoded


def main():
    started = datetime.now(timezone.utc)
    packet = json.loads((DIRECTORY / "selection.json").read_bytes())
    issuer = _read(packet["issuer_body"], 4_000_000)
    issuer_capture = json.loads(_read(packet["issuer_capture"], 64_000))
    legal_capture = json.loads(_read(packet["counterparty_capture"], 64_000))
    assert legal_capture["public_raw_available"] is True and legal_capture["status"] == "CAPTURED"
    legal = _read(packet["counterparty_body"], 4_000_000)
    selection = packet["selection"]
    base = {"issuer_capture": issuer_capture, "selection": selection}
    with_legal = {**base, "counterparty_body": legal, "counterparty_capture": legal_capture,
                  "counterparty_selection": packet["counterparty_selection"]}
    cases = {
        "named_supplier_and_object_only": review_sec_supply_chain_scope(issuer, **base),
        "independent_legal_source_snapshot": review_sec_supply_chain_scope(issuer, **with_legal),
        "wrong_object_withheld": review_sec_supply_chain_scope(issuer, **{**with_legal, "selection": {**selection, "product_or_input": "HBM memory"}}),
        "name_count_cannot_create_equal_purchase_share": review_sec_supply_chain_scope(issuer, **{**with_legal, "selection": {**selection,
            "quantity": {"value_literal": "50", "unit_literal": "%", "denominator_literal": "our purchases of semiconductor wafers",
                         "passage": selection["claim_passage"]}}}),
        "abbreviated_profile_name_not_a_legal_alias": review_sec_supply_chain_scope(issuer, **{**with_legal, "counterparty_selection": {
            **packet["counterparty_selection"], "legal_name": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"}}),
        "old_source_capture_cannot_backdate_review": review_sec_supply_chain_scope(issuer, **with_legal,
            as_of=datetime.fromisoformat(legal_capture["observed_at"])),
    }
    assert cases["named_supplier_and_object_only"]["claim"]["counterparty_legal_identity"]["status"] == "NAMED_ONLY"
    actual = cases["independent_legal_source_snapshot"]
    assert actual["status"] == "CANDIDATE_REVIEWED"
    identity = actual["claim"]["counterparty_legal_identity"]
    assert identity["independent_source_snapshot_supported"] and identity["cik"] == "0001046179"
    assert not identity["relationship_date_identity_verified"] and not actual["counterparty_identity_verified"]
    assert not actual["claim"]["quantified_exposure"]["supplier_specific_quantity_reported"]
    assert cases["wrong_object_withheld"]["status"] == "REJECTED"
    assert cases["name_count_cannot_create_equal_purchase_share"]["status"] == "REJECTED"
    assert not cases["abbreviated_profile_name_not_a_legal_alias"]["claim"]["counterparty_legal_identity"]["independent_source_snapshot_supported"]
    assert cases["old_source_capture_cannot_backdate_review"]["reason"] == "AFTER_PHYSICAL_KNOWLEDGE_CUTOFF"
    requests = [json.loads(path.read_bytes()) for path in sorted((DIRECTORY / "requests").glob("*.metadata.json"))]
    reservations = list((DIRECTORY / "requests").glob("*.reserved.json"))
    assert len(requests) == len(reservations) == 3
    assert all(row["http_status"] == 200 and row["public_raw_available"] is True and row["max_bytes"] == 4_000_000
               and row["automatic_redirects"] is False for row in requests)
    for row in requests:
        body = _read({"repository_relative_path": row["archive_path"], "sha256": row["raw_response_sha256"]}, 4_000_000)
        assert len(body) == row["raw_response_bytes"]
    assert issuer == _read(packet["issuer_body"], 4_000_000) and legal == _read(packet["counterparty_body"], 4_000_000)
    paths = ["market_checker_app/services/sec_supply_chain_scope_review.py", "tests/test_batch7_supply_chain_scope.py",
             "market_checker_app/services/sec_operational_metrics_review.py", "evidence/batch7_contracts_20261015/replay_review.py",
             "evidence/batch7_contracts_20261015/selection.json", "evidence/batch7_contracts_20261015/capture_primary.py"]
    result = {"schema_version": 1, "kind": "genuine_primary_supplier_object_and_independent_legal_snapshot_review",
              "started_at": started.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
              "python_version": platform.python_version(), "cases": cases,
              "scope": {"unique_primary_relationships": 1, "issuer_hint": "NVDA", "reported_dei_name": "NVIDIA CORP",
                        "issuer_cik": "0001045810", "issuer_fiscal_period_end": "2026-01-25",
                        "counterparty_name": selection["counterparty_name"], "reported_counterparty_cik": "0001046179",
                        "counterparty_renderer_period_end": "2025-12-31", "claim_role": "FOUNDRY_SUPPLIER",
                        "product_or_input": "semiconductor wafers", "quantity_verified": 0, "dated_counterparty_identity_verified": 0,
                        "sole_source_verified": 0, "relevant_687_coverage_measured": False},
              "source_research": {"new_sec_get_attempts": 3, "new_sec_http200_complete_original_bodies": 3,
                                  "maximum_authorized_attempts": 4, "fourth_get_not_used": True,
                                  "bytes_per_capture_limit": 4_000_000, "reserve_before_get": True,
                                  "automatic_redirects": False, "usaspending_requests": 0,
                                  "nvda_body_refetched_or_copied": False,
                                  "large_20f_not_requested": {"index_reported_bytes": 10_355_816,
                                                              "reason": "Complete bounded R1 already supports this legal source snapshot"}},
              "original_source_bytes_unchanged": True, "source_references": {key: packet[key] for key in
                  ("issuer_body", "issuer_capture", "counterparty_body", "counterparty_capture", "original_issuer_capture_manifest")},
              "code_and_selection_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths},
              "acceptance": {"registry_adoption": 0, "scoring_applied": False, "prediction_input": False,
                             "counterparty_identity_at_relationship_date_verified": False, "quantity_or_revenue_share_inferred": False,
                             "current_instrument_identity_verified": False, "current_q3_identity_verified": False,
                             "historical_availability_reconstructed": False, "windows_end_to_end_verified": False,
                             "historical_oos_verified": False, "specialist_done": False}}
    with (DIRECTORY / "review.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"primary_relationships": 1, "independent_legal_source_snapshot": True,
                      "dated_legal_identity_verified": 0, "quantity_verified": 0, "new_sec_gets": 3,
                      "new_http_during_replay": 0, "usaspending_requests": 0,
                      "cases": {name: result["status"] for name, result in cases.items()}}))


if __name__ == "__main__":
    main()
