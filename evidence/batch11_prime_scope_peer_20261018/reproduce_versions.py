"""Compare inactive34 and committed current35 on exact new synthetic fixtures.

The owner's current35 guard predates this independent peer execution. Neither
producer, original qualification nor runtime source is changed or reintroduced.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.services import usaspending_prime_scope_review as current
from tests.test_batch11_prime_scope_peer import spliced_second_body_into_first_manifest, two_pages
from tests.test_usaspending_prime_scope_review import NOW, mutate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    frozen = ROOT / "evidence/batch11_contracts_20261018/producer_versions/34_method_pre_cursor/usaspending_prime_scope_review.py"
    if hashlib.sha256(frozen.read_bytes()).hexdigest() != "5182d280908b3512a3fb639c2efd113c0a6dae18f96f1f836536930ec7897eb1":
        raise ValueError("Preserved historical34 source changed")
    if hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest() != "2a05f8cc86bff37236102c1231cfb2a962f7203f0af4f460dfa29ba77c24722f":
        raise ValueError("Peer requires the exact committed current35 producer")
    spec = importlib.util.spec_from_file_location("inactive_prime_scope34_peer", frozen)
    old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    wrong_cursor = mutate(two_pages(), page=2, payload=lambda payload: payload["page_metadata"].update(last_record_unique_id=101))
    cases = {"valid_two_page_control": two_pages(), "same_hash_consistent_wrong_page_body_splice": spliced_second_body_into_first_manifest(),
             "historical_cross_page_cursor_case": wrong_cursor}
    results = {}
    for label, producer in (("inactive_historical34", old), ("committed_current35", current)):
        with patch.object(producer, "_physical_clock", return_value=NOW), patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), patch("sqlite3.connect", side_effect=AssertionError("No DB")):
            results[label] = {name: producer.review_usaspending_prime_scope(*inputs) for name, inputs in cases.items()}
    assert results["inactive_historical34"]["valid_two_page_control"]["status"] == "TRAVERSAL_REVIEWED"
    assert results["committed_current35"]["valid_two_page_control"]["status"] == "TRAVERSAL_REVIEWED"
    assert results["inactive_historical34"]["historical_cross_page_cursor_case"]["observed_page_chain_complete"] is True
    assert results["committed_current35"]["historical_cross_page_cursor_case"]["reason"] == "PRIME_LAST_RECORD_CURSOR_CONFLICT"
    assert all(values["same_hash_consistent_wrong_page_body_splice"]["status"] == "REJECTED" for values in results.values())
    report = {"schema_version": "prime_scope_independent_version_comparison_v1", "executed_at": datetime.now(timezone.utc).isoformat(),
              "owner_commit": "cc23d43adc44839a16d33464a7d8bc56a3700e8d", "owner_current35_qualification_at": "2026-10-18T19:33:44.006644+00:00",
              "historical34_source_sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
              "current35_source_sha256": hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest(),
              "cursor_guard_was_independently_qualified_before_peer_execution": True,
              "peer_did_not_cause_owner_cursor_fix": True, "active35_was_never_reverted": True,
              "historical34_is_the_owner_preserved_exact_hash_reconstruction": True,
              "synthetic_injected_review_clock": NOW.isoformat(), "synthetic_fixtures_are_source_acceptance": False,
              "new_source_requests": 0, "db_operations": 0,
              "input_source_pins": {name: {key: {"sha256": hashlib.sha256(pair[1]).hexdigest(), "bytes": len(pair[1])}
                                                   for key, pair in inputs[1].items()} for name, inputs in cases.items()},
              "results": results}
    with (HERE / "version_comparison.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"current35_findings": "NO_ADDITIONAL_FAILURE_IN_SELECTED_CASES", "cases": len(cases),
                      "historical34_cursor_accepted": True, "current35_cursor_rejected": True,
                      "peer_caused_owner_fix": False, "source_requests": 0, "db_operations": 0}))


if __name__ == "__main__":
    main()
