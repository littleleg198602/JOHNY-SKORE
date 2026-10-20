"""Explicit existing PFE originals and honest missing chronology endpoints."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from evidence.batch10_healthcare_20261018.replay_sources import inputs as retained_inputs, route_audit
from market_checker_app.services.fda_applicant_chronology_review import review_fda_applicant_chronology

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = "6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"
PRODUCER = "market_checker_app/services/fda_applicant_chronology_review.py"
TESTS = "tests/test_fda_applicant_chronology_review.py"


def pin(path):
    raw = (ROOT / path).read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def unchanged(path):
    original = subprocess.run(["git", "show", BASE + ":" + path], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    if original != (ROOT / path).read_bytes():
        raise ValueError("Original tracked input changed: " + path)
    return {**pin(path), "byte_identical_to_base": True}


def inputs():
    point, captures, old_plan = retained_inputs()
    request = {"scope": dict(point["scope"], jurisdiction="Ireland"),
        "entities": [{"legal_name": point["scope"]["applicant_name"], "jurisdiction": "Ireland"},
                     {"legal_name": "Pfizer Ireland Pharmaceuticals", "jurisdiction": "Ireland"}],
        "roster_points": [
            {"id": "roster2017", "requested_roster_as_of": "2017-12-31", "review_request": point, "universe_statement": None},
            {"id": "approval2019_missing_roster", "requested_roster_as_of": "2019-03-11", "review_request": None, "universe_statement": None},
            {"id": "roster2025_missing_original", "requested_roster_as_of": "2025-12-31", "review_request": None, "universe_statement": None}],
        "application_points": [{"id": "PurpleBook_observed", "review_request": point["application_request"]},
                               {"id": "DrugsFDA_company_observed", "review_request": point["company_request"]}]}
    plan = {"schema_version": "captured_identity_review_plan_v1", "kind": "FDA_APPLICANT_CHRONOLOGY",
            "request": request, "captures": old_plan["captures"]}
    return request, captures, plan


def write(name, value):
    if name == "retained_review_plan.json" and (HERE / name).exists():
        if json.loads((HERE / name).read_bytes()) != value:
            raise ValueError("Original retained chronology plan changed")
        return
    with (HERE / name).open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    qualified = json.loads((HERE / "qualified2.json").read_bytes())
    if (not qualified["successful"] or qualified["executed"] != 27 or qualified["discovered"] != 27
            or qualified["source_sha256"] != {"producer": pin(PRODUCER)["sha256"], "tests": pin(TESTS)["sha256"]}
            or pin(str((HERE / "qualified2.log").relative_to(ROOT)))["sha256"] != qualified["log_sha256"]):
        raise ValueError("Exact current successful focused qualification required")
    subprocess.run(["git", "diff", "--quiet", BASE, "--"], cwd=ROOT, check=True)
    before_read = datetime.now(timezone.utc)
    request, captures, plan = inputs()
    result = review_fda_applicant_chronology(request, captures, reviewed_at=before_read)
    recorded = datetime.now(timezone.utc).isoformat()
    if (result["data_status"] != "WAIT_DATA" or result["independent_roster_versions"] != 1
            or result["independent_roster_dates"] != 1 or result["application_comparisons"]
            or result["roster_points"][0]["source_review"]["roster_facts"]["entity_row_count"] != 468
            or result["roster_points"][0]["universe"]["complete"]
            or any(point["source_roster_as_of"] is not None for point in result["roster_points"][1:])
            or any(row["outcome"] != "WAIT_DATA" for row in result["comparisons"])
            or result["application_points"][0]["original_container_explicitly_closed"] is not False):
        raise ValueError("Real retained case must not fabricate a complete chronology")
    source_pins = {}
    for key, descriptor in plan["captures"].items():
        item = unchanged(descriptor["body_ref"]["path"])
        if item["sha256"] != descriptor["body_ref"]["sha256"] or item["bytes"] != descriptor["body_ref"]["bytes"]:
            raise ValueError("Original whole-body reference changed")
        source_pins[key] = {**item, "request_url": descriptor["metadata"]["request_url"],
            "observed_at": descriptor["metadata"]["observed_at"],
            "capture_is_new_or_historical_first_availability": False}
    write("retained_review_plan.json", plan)
    write("source_review.json", {"schema_version": "batch11_actual_chronology_replay_v1", "recorded_at": recorded,
        "request": request, "review": result, "source_pins": source_pins, "route_audit": route_audit(captures),
        "new_HTTP_or_DB_requests": 0, "original_captures_unchanged": True,
        "requested_missing_days_are_not_source_dates": True, "full_specialist_acceptance": "0/21"})
    protected = [unchanged("market_checker_app/data/" + name) for name in (
        "verified_healthcare_identities.json", "verified_13f_securities.json", "verified_nhtsa_models.json",
        "verified_fdic_banks.json", "verified_counterparty_identities.json", "verified_usaspending_uei.json",
        "european_authority_registry.json", "market_checker_687_tickers.csv")]
    dependency_paths = ["market_checker_app/collectors/fda_legal_applicant_bridge_review.py",
        "market_checker_app/services/fda_application_product_review.py", "market_checker_app/services/fda_structured_product_archive.py",
        "market_checker_app/services/cms_provider_ownership_review.py", "market_checker_app/services/healthcare_identity_service.py",
        "evidence/batch10_healthcare_20261018/retained_review_plan.json",
        "evidence/batch7_healthcare_20261015/sec_pfe_2017_ex21.metadata.json",
        "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_10k.metadata.json",
        "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_index.metadata.json"]
    write("validation.json", {"recorded_at": recorded, "current_producer": pin(PRODUCER), "current_tests": pin(TESTS),
        "focused": qualified, "original_sources": source_pins, "protected_inputs": protected,
        "existing_dependencies_and_old_evidence": [unchanged(path) for path in dependency_paths],
        "tracked_worktree_byte_identical_to_base": True, "new_HTTP_DB_quota_registry_model_score_trading_actions": 0})
    write("development_diagnostics.json", {"recorded_at": recorded,
        "initial_23_case_pass": pin(str((HERE / "initial.json").relative_to(ROOT))),
        "application_diff_fixture_failures": pin(str((HERE / "final.json").relative_to(ROOT))),
        "failure_trigger": "The exact synthetic fixture contains 420MG; a trigger looking for 420 MG changed no bytes. The second test therefore reobserved an identical version.",
        "correction": "Use the exact existing fixture literal 420MG -> 421MG and assert raw version inequality. No production/parser or original source data were relaxed.",
        "failed_tests_bytes": pin(str((HERE / "application_comparison_fixture_failure_tests.py.txt").relative_to(ROOT))),
        "earlier_26_case_pass": pin(str((HERE / "qualified.json").relative_to(ROOT))),
        "qualified_27_case_pass": pin(str((HERE / "qualified2.json").relative_to(ROOT))),
        "universe_context_guard": "An explicit complete quote cannot override unrecognized qualification text; unsupported one-cell scope prose keeps the universe unknown.",
        "synthetic_contract_cases_not_LIVE": True})
    artifacts = [pin(path.relative_to(ROOT).as_posix()) for path in sorted(HERE.iterdir()) if path.is_file()]
    write("handoff.json", {"schema_version": "batch11_healthcare_chronology_handoff_v1", "recorded_at": recorded,
        "base_commit": BASE, "branch": "batch11/healthcare-20261018", "kind": "FDA_APPLICANT_CHRONOLOGY",
        "api": "market_checker_app.services.fda_applicant_chronology_review.review_fda_applicant_chronology(request, captures, *, reviewed_at=None)",
        "current_producer": pin(PRODUCER), "current_tests": pin(TESTS), "focused": qualified,
        "actual_plan": pin(str((HERE / "retained_review_plan.json").relative_to(ROOT))),
        "actual_report": pin(str((HERE / "source_review.json").relative_to(ROOT))),
        "source_pins": source_pins, "protected_registry_and_universe_inputs": protected,
        "clock_contract": "Outer reviewed/completed/known/ingested use physical end, after start and every supporting child; source-read cutoff is independent. Future/rollback clocks raise.",
        "actual_outcome": {"status": "PARTIAL", "data_status": "WAIT_DATA", "dated_roster_originals": 1,
            "roster_as_of": "2017-12-31", "roster_entity_rows": 468, "roster_tables": 11,
            "roster_universe_complete": False, "full_current_applicant_listed_rows": 0, "short_name_listed_rows": 1,
            "missing_original_roster_query_days": ["2019-03-11", "2025-12-31"],
            "application_observed_versions": 2, "same_role_application_comparisons": 0,
            "PurpleBook_product_numbers": ["001", "002"], "PurpleBook_original_outer_container_closed": False,
            "Company_label_is_Applicant_or_product_owner": False},
        "limits": {"new_HTTP_or_source_GET": 0, "SQL_or_quota_actions": 0, "registry_writes_or_adoptions": 0,
            "score_model_budget_or_trading_changes": 0, "remote_writes": 0, "merge": False,
            "withheld_whole_bodies_read_restored_or_substituted": False, "canonical_tickers": 687,
            "healthcare_registry_count_unchanged": 10, "full_specialist_acceptance": "0/21", "Windows_or_OOS_claims": False},
        "next_work": ["Independently dated exact applicant/legal-parent roster originals at missing endpoints; no carry-forward or inferred aliases.",
            "Original explicit complete source universe for any proposed negative; lack of an omission sentence is insufficient.",
            "A second independently observed same-role applicant/product source version; Company and Applicant are separate roles.",
            "A dated product/IP ownership or licensing assignment is separate from a roster point or approval date."],
        "artifacts": artifacts})
    print(json.dumps({"producer": pin(PRODUCER), "tests": pin(TESTS), "handoff": pin(str((HERE / "handoff.json").relative_to(ROOT))),
        "plan": pin(str((HERE / "retained_review_plan.json").relative_to(ROOT))), "known_at": result["known_at"],
        "actual_data_status": result["data_status"], "focused": 27, "new_HTTP": 0}))


if __name__ == "__main__":
    main()
