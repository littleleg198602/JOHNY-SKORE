"""Fresh physical-end replay of the exact retained certificate plan, zero HTTP."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.collectors.sec_certificate_instrument_review import review_sec_certificate_instrument

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    original = ROOT / "evidence/batch10_identity_20261018"
    spec = importlib.util.spec_from_file_location("retained_certificate_replay_clock_fix", original / "replay_certificate.py")
    replay = importlib.util.module_from_spec(spec); spec.loader.exec_module(replay)
    request, captures, plan = replay.build_inputs()
    original_plan = original / "captured_review_plan.json"
    assert json.loads(original_plan.read_bytes()) == plan
    with patch("socket.socket.connect", side_effect=AssertionError("No source HTTP")), patch("sqlite3.connect", side_effect=AssertionError("No DB")):
        current = review_sec_certificate_instrument(request, captures)
    previous = json.loads((original / "final_actual_review.json").read_bytes())
    old_time = {"as_of", "review_recorded_at", "knowledge_at", "review_sha256"}
    new_time = old_time | {"review_started_at", "source_as_of"}
    assert {key: value for key, value in previous.items() if key not in old_time} == {key: value for key, value in current.items() if key not in new_time}
    assert current["review_started_at"] == current["as_of"] == current["source_as_of"]
    assert current["knowledge_at"] == current["review_recorded_at"] >= current["review_started_at"]
    assert current["knowledge_at"] >= current["measurement_recorded_at"] >= current["source_knowledge_at"]
    snapshot = json.loads((HERE / "before_snapshot.json").read_bytes())
    for name, pin in snapshot["prior_certificate_evidence_pins"].items():
        raw = (ROOT / name).read_bytes()
        assert len(raw) == pin["bytes"] and hashlib.sha256(raw).hexdigest() == pin["sha256"], name
    with (HERE / "actual_review.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    verification = {"schema_version": "certificate_clock_real_replay_content_verification_v1",
        "verified_at": datetime.now(timezone.utc).isoformat(), "new_source_requests": 0, "db_writes": 0,
        "actual_review_started_at": current["review_started_at"], "actual_review_completed_at": current["review_recorded_at"],
        "source_cutoff_at_start": current["source_as_of"], "source_knowledge_at_unchanged": current["source_knowledge_at"],
        "visual_measurement_clock_unchanged": current["measurement_recorded_at"],
        "existing_plan_path": original_plan.relative_to(ROOT).as_posix(), "existing_plan_sha256": hashlib.sha256(original_plan.read_bytes()).hexdigest(),
        "exact_original_source_plan_reused": True, "all_original_data_fields_and_nine_blockers_unchanged": True,
        "all_prior_certificate_evidence_pins_unchanged": True, "prior_certificate_evidence_count": len(snapshot["prior_certificate_evidence_pins"]),
        "actual_report_sha256": hashlib.sha256((HERE / "actual_review.json").read_bytes()).hexdigest(),
        "automated_pixel_text_authentication": False, "registry_adoptions": 0, "current_q3_verified_instrument_coverage": 0,
        "historical_or_oos_acceptance": False}
    with (HERE / "actual_replay_verification.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(verification, sort_keys=True, indent=2) + "\n")
    print(json.dumps(verification))


if __name__ == "__main__":
    main()
