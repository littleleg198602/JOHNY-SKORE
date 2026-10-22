"""Offline final replay; preserve the first producer/report and every raw byte.

Run from the repository root with PYTHONPATH=. No requests or collection occur.
Output files use exclusive creation so a subsequent replay needs new filenames.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.services.sec_instrument_class_scope_review import review_sec_instrument_class_scope
from replay_instrument_scope import HERE, ROOT, build_inputs


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(name, value):
    with (HERE / name).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def main():
    source = ROOT / "market_checker_app/services/sec_instrument_class_scope_review.py"
    tests = ROOT / "tests/test_batch9_instrument_class_scope.py"
    assert sha(source) == "7a009fdc6729dd4932039e8190c4f041f1680ca4f7885e4f54d536b931818689"
    assert sha(tests) == "03564e3bd6bd536c9b76e91fceb8cf53a9834823870d8363d81b66d2b510d59c"
    request, captures, plan = build_inputs()
    assert plan == json.loads((HERE / "captured_review_plan.json").read_bytes())
    research = json.loads((HERE / "request_plan.json").read_bytes())
    research_sha = sha(HERE / "request_plan.json")
    attempts = []
    for number in range(1, 7):
        metadata_path = HERE / "captures" / f"attempt-{number:02d}.metadata.json"
        reservation_path = HERE / "captures" / f"attempt-{number:02d}.reserved.json"
        metadata = json.loads(metadata_path.read_bytes())
        reserved = json.loads(reservation_path.read_bytes())
        assert reserved["status"] == "REQUEST_RESERVED"
        assert metadata["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
        assert metadata["http_status"] == 200
        assert metadata["requested_url"] == reserved["requested_url"] == research["urls"][number - 1]
        assert metadata["requested_url"] not in research["prior_batch8_urls_forbidden"]
        assert metadata["request_plan_sha256"] == reserved["request_plan_sha256"] == research_sha
        assert metadata["started_at"] == reserved["started_at"]
        assert datetime.fromisoformat(metadata["started_at"]) <= datetime.fromisoformat(metadata["observed_at"])
        assert metadata["redirects_allowed"] is False and metadata["timeout_seconds"] == 25
        assert metadata["max_bytes"] == 4_000_000 and 0 < metadata["bytes"] <= 4_000_000
        body_path = metadata_path.parent / metadata["body_path"]
        assert sha(body_path) == metadata["raw_sha256"] and body_path.stat().st_size == metadata["bytes"]
        attempts.append({"metadata_path": metadata_path.relative_to(ROOT).as_posix(),
                         "metadata_sha256": sha(metadata_path),
                         "reservation_path": reservation_path.relative_to(ROOT).as_posix(),
                         "reservation_sha256": sha(reservation_path),
                         "metadata": metadata})
    assert len(captures) == 7 and sum(row[0]["bytes"] for row in captures.values()) < 24_000_000
    assert sum(item["metadata"]["bytes"] for item in attempts) == 91_010

    # This deterministic historical-version verification is not a fresh past
    # observation. The new report below always samples the real physical clock.
    historical_path = HERE / "actual_review.json"
    historical = json.loads(historical_path.read_bytes())
    frozen_path = HERE / "producer_versions/first_actual_instrument_scope_service.py"
    spec = importlib.util.spec_from_file_location("batch9_first_actual_producer", frozen_path)
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    original_clock = datetime.fromisoformat(historical["review_recorded_at"])
    with patch.object(frozen, "_physical_clock", return_value=original_clock):
        reproduced = frozen.review_sec_instrument_class_scope(request, captures)
    assert reproduced == historical
    final = review_sec_instrument_class_scope(request, captures)
    assert final["blockers"] == historical["blockers"]
    assert final["registered_source_anchor_count"] == 2
    assert final["new_verified_identities"] == final["current_q3_verified_instrument_coverage"] == 0
    assert final["review_recorded_at"] > historical["review_recorded_at"]
    write_new("final_actual_review.json", final)
    write_new("version_verification.json", {
        "schema_version": "batch9_original_version_verification_v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "original_producer_path": frozen_path.relative_to(ROOT).as_posix(),
        "original_producer_sha256": sha(frozen_path),
        "original_report_sha256": sha(historical_path),
        "exact_original_version_replay_equal": True,
        "historical_clock_injection_only_for_exact_old_version_verification": original_clock.isoformat(),
        "new_final_report_uses_physical_current_reviewer_clock": True,
        "current_producer_sha256": sha(source), "current_test_sha256": sha(tests),
        "plan_sha256": sha(HERE / "captured_review_plan.json"),
        "final_report_sha256": sha(HERE / "final_actual_review.json"),
        "all_original_source_bytes_and_reservations_validated": True,
        "network_requests_performed": 0,
    })
    write_new("capture_verification.json", {
        "schema_version": "batch9_capture_content_verification_v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "request_plan_sha256": research_sha, "new_http_transports": 6,
        "new_original_response_bytes": 91_010, "plan_capture_count": len(captures),
        "plan_total_original_bytes": sum(row[0]["bytes"] for row in captures.values()),
        "all_six_bounded_requests_have_prior_reservations": True,
        "all_six_original_bodies_hash_and_size_match": True,
        "prior_batch8_urls_repeated": [],
        "retained_source": {"key": "submissions", "new_http_transports": 0,
                            "descriptor": plan["captures"]["submissions"]},
        "attempts": attempts,
        "collector_api_used": False, "app_quota_managed_live_claimed": False,
        "quota_database_created_or_reset": False,
        "known_withheld_bodies_read_or_restored": False,
        "images_captured": False,
    })
    print(json.dumps({"new_physical_review_recorded_at": final["review_recorded_at"],
                      "source_knowledge_at": final["source_knowledge_at"],
                      "registered_anchor_count": final["registered_source_anchor_count"],
                      "blocker_count": len(final["blockers"]),
                      "final_report_sha256": sha(HERE / "final_actual_review.json")}))


if __name__ == "__main__":
    main()
