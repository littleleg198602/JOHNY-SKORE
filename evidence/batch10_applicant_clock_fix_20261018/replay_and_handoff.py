"""Replay pinned originals with current completion availability; no HTTP/DB."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from evidence.batch10_healthcare_20261018.replay_sources import inputs, route_audit
from market_checker_app.collectors.fda_legal_applicant_bridge_review import review_fda_legal_applicant_bridge

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = "1e28f6d2a8a431b252dbe106304c02cdfbf4cae5"
PRODUCER = "market_checker_app/collectors/fda_legal_applicant_bridge_review.py"
TESTS = "tests/test_fda_legal_applicant_bridge_review.py"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path):
    raw = (ROOT / path).read_bytes()
    return {"path": str(path), "sha256": sha(raw), "bytes": len(raw)}


def unchanged(path):
    base = subprocess.run(["git", "show", BASE + ":" + path], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    if (ROOT / path).read_bytes() != base:
        raise ValueError("Existing evidence/source changed: " + path)
    return {**pin(path), "byte_identical_to_base": True}


def clock(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Clock audit requires aware original values")
    return parsed


def availability(report):
    return {label: {field: report[label][field] for field in ("known_at", "ingested_at", "reviewed_at")}
            for label in ("applicant_source", "company_source")}


def write(name, value):
    with (HERE / name).open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    started = datetime.now(timezone.utc).isoformat()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    if head != BASE:
        raise ValueError("This evidence builder pins the pre-fix base")
    qualification = json.loads((HERE / "test_results.json").read_bytes())
    if (not qualification["successful"] or qualification["source_sha256"]["producer"] != pin(PRODUCER)["sha256"]
            or qualification["source_sha256"]["lane_tests"] != pin(TESTS)["sha256"]):
        raise ValueError("Current source must match the successful exact qualification")
    for name, expected in (("focused", 34), ("unchanged_peer", 7)):
        result = qualification["suites"][name]
        if (result["executed"] != expected or result["discovered"] != expected or not result["plain_OK"]
                or any(result[key] for key in ("errors", "failures", "skips", "expected_failures", "unexpected_successes"))
                or pin(result["log_path"])["sha256"] != result["log_sha256"]):
            raise ValueError("Full focused/peer logs must remain exact and successful")
    source_read_cutoff = datetime.now(timezone.utc)
    request, captures, plan = inputs()
    if plan != json.loads((HERE / "retained_review_plan.json").read_bytes()):
        raise ValueError("Original compatible review plan changed")
    after = review_fda_legal_applicant_bridge(request, captures, reviewed_at=source_read_cutoff)
    recorded = datetime.now(timezone.utc).isoformat()
    before = json.loads((HERE / "before_actual_review.json").read_bytes())["review"]
    old_children, new_children = availability(before), availability(after)
    old_ahead = {label: {field: (clock(value) - clock(before["known_at"])).total_seconds()
                         for field, value in fields.items() if clock(value) > clock(before["known_at"])}
                 for label, fields in old_children.items()}
    if not any(old_ahead.values()):
        raise ValueError("Original actual counterexample must remain present")
    end = clock(after["known_at"])
    if (end < clock(after["review_started_at"]) or end > clock(recorded)
            or any(clock(value) > end for fields in new_children.values() for value in fields.values())
            or any(clock(after[key]) != end for key in ("reviewed_at", "review_completed_at", "ingested_at"))
            or after["source_read_cutoff"] != source_read_cutoff.isoformat()):
        raise ValueError("New actual outer availability must follow every supporting child and start")
    if (after["status"] != "PARTIAL" or after["roster_facts"] != before["roster_facts"]
            or after["selected_roster_facts"] != before["selected_roster_facts"]
            or after["exact_applicant_roster_rows"] != [] or after["gaps"] != before["gaps"]
            or after["registry_adoptions"] != 0 or after["issuer_attribution_allowed"]):
        raise ValueError("Clock-only fix must not alter actual roster scope or ownership conclusions")
    source_pins = {key: unchanged(descriptor["body_ref"]["path"]) for key, descriptor in plan["captures"].items()}
    for key, item in source_pins.items():
        if item["sha256"] != plan["captures"][key]["body_ref"]["sha256"]:
            raise ValueError("Original body pin changed")
    old_paths = subprocess.run(["git", "ls-tree", "-rz", "--name-only", BASE, "--", "evidence/batch10_healthcare_20261018"],
                               cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout.decode().strip("\0").split("\0")
    old_evidence = [unchanged(path) for path in old_paths]
    protected_names = ["verified_healthcare_identities.json", "verified_13f_securities.json", "verified_nhtsa_models.json",
        "verified_fdic_banks.json", "verified_counterparty_identities.json", "verified_usaspending_uei.json",
        "european_authority_registry.json", "market_checker_687_tickers.csv"]
    protected = [unchanged("market_checker_app/data/" + name) for name in protected_names]
    write("actual_review.json", {"recorded_at": recorded, "source_read_cutoff": source_read_cutoff.isoformat(),
        "request": request, "review": after, "source_pins": source_pins, "route_audit": route_audit(captures),
        "new_HTTP_DB_quota_registry_score_or_model_changes": 0,
        "actual_original_replay_not_new_LIVE_collection": True, "full_specialist_acceptance": "0/21"})
    write("clock_audit.json", {"recorded_at": recorded,
        "before_outer_known_at": before["known_at"], "before_child_availability": old_children,
        "before_child_seconds_after_outer_known_at": old_ahead,
        "before_counterexample_remains_historical_failure": True,
        "after_started_at": after["review_started_at"], "after_completed_at": after["review_completed_at"],
        "after_known_at": after["known_at"], "after_child_availability": new_children,
        "after_source_read_cutoff": after["source_read_cutoff"],
        "completion_is_not_clamped_to_a_future_child": True,
        "actual_semantics_and_original_body_pins_unchanged": True})
    current = {"producer": pin(PRODUCER), "tests": pin(TESTS)}
    artifacts = [pin(path.relative_to(ROOT).as_posix()) for path in sorted(HERE.iterdir()) if path.is_file()]
    write("handoff.json", {"schema_version": "batch10_applicant_clock_fix_handoff_v1", "recorded_at": recorded,
        "evidence_build_started_at": started, "base_commit": BASE, "branch": "batch10/applicant-clock-fix-20261018",
        "kind": "FDA_LEGAL_APPLICANT_BRIDGE", "current": current,
        "api": "review_fda_legal_applicant_bridge(request, captures, *, reviewed_at=None)",
        "new_clock_contract": "Source-read cutoff stays independent. review_started_at records start; reviewed_at/review_completed_at/known_at/ingested_at are physical completion, >= start and both supporting children's reviewed/known/ingested clocks. Impossible rollback/future-child clocks raise rather than clamp.",
        "return_paths": ["Normal successful roster parse", "Caught roster/cover/source-scope rejection", "Missing/rejected original SEC body early return"],
        "qualification": qualification, "actual_report": pin(str((HERE / "actual_review.json").relative_to(ROOT))),
        "clock_audit": pin(str((HERE / "clock_audit.json").relative_to(ROOT))),
        "actual_plan": pin(str((HERE / "retained_review_plan.json").relative_to(ROOT))),
        "original_sources": source_pins, "all_prior_lane_evidence_byte_identical_to_base": old_evidence,
        "all_protected_registry_and_universe_inputs_byte_identical_to_base": protected,
        "limits": {"new_HTTP_requests": 0, "collector_requests": 0, "DB_or_quota_changes": 0,
            "registry_adoptions": 0, "score_model_budget_or_trading_changes": 0, "remote_writes": 0,
            "withheld_bodies_read_restored_or_substituted": False, "full_suite_rerun": False,
            "actual_result": "PARTIAL", "canonical_tickers": 687, "healthcare_registry_count_unchanged": 10,
            "full_specialist_acceptance": "0/21", "Windows_or_OOS_claims": False,
            "exact_current_legal_applicant_bridge_still_missing": True,
            "all_synthetic_clock_cases_are_contract_tests_not_LIVE": True},
        "artifacts": artifacts})
    print(json.dumps({"current": current, "handoff": pin(str((HERE / "handoff.json").relative_to(ROOT))),
        "actual_known_at": after["known_at"], "supporting_child_known_at": {k: v["known_at"] for k, v in new_children.items()},
        "focused": 34, "unchanged_peer": 7, "new_HTTP": 0}))


if __name__ == "__main__":
    main()
