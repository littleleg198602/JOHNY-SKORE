"""Pin this lane's already completed offline review and focused test log.

No network/DB access; tracked inputs are compared byte-for-byte with the base.
This does not rerun tests or manufacture a new collection/acceptance event.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = "32adb8edd6aa6ccc1748d2cfbc228f7db3a16fd4"
PRODUCER = "market_checker_app/collectors/fda_legal_applicant_bridge_review.py"
TEST = "tests/test_fda_legal_applicant_bridge_review.py"
PRODUCER_SHA = "bf9f1afb16c29c0c0272229e5567ed5448e19db0d4fe4684908dd419af3b59a0"
TEST_SHA = "57f31059e0931b48707282f5eb7a1a9d943ca73f9ef984a64dc5390ee8e4d6c1"
WITHHELD = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path):
    full = ROOT / path
    raw = full.read_bytes()
    return {"path": str(path), "sha256": sha(raw), "bytes": len(raw)}


def unchanged(path):
    current = (ROOT / path).read_bytes()
    original = subprocess.run(["git", "show", BASE + ":" + path], cwd=ROOT,
                              check=True, stdout=subprocess.PIPE).stdout
    if current != original:
        raise ValueError("Tracked original changed: " + path)
    return {**pin(path), "byte_identical_to_base": True}


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    now = datetime.now(timezone.utc).isoformat()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()
    if head != BASE or pin(PRODUCER)["sha256"] != PRODUCER_SHA or pin(TEST)["sha256"] != TEST_SHA:
        raise ValueError("Current qualified producer, tests or base changed")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=True)
    plan = json.loads((HERE / "retained_review_plan.json").read_bytes())
    actual = json.loads((HERE / "source_review.json").read_bytes())
    review = actual["review"]
    if (plan["kind"] != "FDA_LEGAL_APPLICANT_BRIDGE" or review["status"] != "PARTIAL"
            or review["registry_adoptions"] != 0 or review["current_applicant_issuer_control_verified"]
            or review["roster_facts"]["entity_row_count"] != 468
            or review["roster_facts"]["table_count"] != 11
            or review["exact_applicant_roster_rows"]):
        raise ValueError("Actual result no longer matches this strictly withheld handoff")
    source_pins = {}
    for key, capture in plan["captures"].items():
        ref = capture["body_ref"]
        body = unchanged(ref["path"])
        if body["sha256"] != ref["sha256"] or body["bytes"] != ref["bytes"] or body["sha256"] in WITHHELD:
            raise ValueError("Original source body pin mismatch")
        metadata = capture["metadata"]
        if metadata["body_sha256"] != body["sha256"] or metadata["body_bytes"] != body["bytes"]:
            raise ValueError("Original metadata pin mismatch")
        source_pins[key] = {**body, "request_url": metadata["request_url"],
            "response_url": metadata["response_url"], "http_status": metadata["http_status"],
            "requested_at": metadata["requested_at"], "observed_at": metadata["observed_at"],
            "known_at": metadata.get("known_at", metadata["observed_at"]),
            "inline_metadata_canonical_sha256": sha(json.dumps(metadata, sort_keys=True,
                ensure_ascii=False, allow_nan=False).encode())}
    originals = [
        "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_10k.metadata.json",
        "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_10k.body.html.gz",
        "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_index.metadata.json",
        "evidence/batch3_healthcare_20261012/captures/sec_pfe_2017_index.body.html.gz",
        "evidence/batch7_healthcare_20261015/sec_pfe_2017_ex21.metadata.json",
        "evidence/batch9_healthcare_20261016/purple_book_review_plan.json",
        "evidence/batch9_healthcare_20261016/drugsfda_application_review_plan.json",
    ]
    original_pins = [unchanged(path) for path in originals]
    compressed = ROOT / originals[1]
    decoded = gzip.decompress(compressed.read_bytes())
    if sha(decoded) != source_pins["filing"]["sha256"] or len(decoded) != source_pins["filing"]["bytes"]:
        raise ValueError("Original gzip replay was not lossless")
    protected_names = ["verified_usaspending_uei.json", "verified_healthcare_identities.json",
        "verified_13f_securities.json", "verified_nhtsa_models.json", "verified_fdic_banks.json",
        "verified_counterparty_identities.json", "european_authority_registry.json", "market_checker_687_tickers.csv"]
    protected = [unchanged("market_checker_app/data/" + name) for name in protected_names]
    log = (HERE / "focused_tests.log").read_text(encoding="utf-8")
    summary = re.search(r"^Ran (\d+) tests in ([0-9.]+)s$", log, re.M)
    if (summary is None or int(summary[1]) != 25 or not log.rstrip().endswith("\nOK")
            or len(re.findall(r"^test_.* \.\.\. ok$", log, re.M)) != 25):
        raise ValueError("Complete successful focused test log required")
    tests = {"command": "/workspace/scratch/334b3a149083/johny-tests-20261011/bin/python -B -m unittest tests.test_fda_legal_applicant_bridge_review -v",
        "working_directory": str(ROOT), "log": pin(str((HERE / "focused_tests.log").relative_to(ROOT))),
        "discovered": 25, "executed": 25, "failures": 0, "errors": 0, "skips": 0,
        "expected_failures": 0, "unexpected_successes": 0, "plain_OK": True,
        "elapsed_seconds_from_log": float(summary[2]), "physical_test_start_end_not_recorded": True,
        "positive_contract_fixtures_are_synthetic_not_LIVE": True,
        "full_suite_or_previous_CI_rerun": False}
    write("development_diagnostics.json", {"recorded_at": now,
        "initial_original_replay": pin(str((HERE / "initial_source_review_before_duplicate_scope.json").relative_to(ROOT))),
        "pre_resumption_original_replay": pin(str((HERE / "source_review_before_resumption.json").relative_to(ROOT))),
        "initial_result": "Whole-roster duplicate rejection withheld row facts because Pfizer Limited repeats; no acceptance was granted.",
        "current_semantics": "Preserve all duplicate names; only selected/exact applicant ambiguity blocks that entity. Unrelated duplicates do not erase a unique literal source row.",
        "earlier_unfrozen_producer_bytes_not_reconstructed": True,
        "current_qualified_producer": pin(PRODUCER), "current_qualified_tests": pin(TEST),
        "new_transport_failures_created": 0,
        "old_HTTP403_index_failure_preserved": actual["route_audit"]["index_failure"]})
    write("validation.json", {"recorded_at": now, "base_commit": BASE,
        "producer": pin(PRODUCER), "tests": pin(TEST), "focused": tests,
        "actual_reviewed_at": review["reviewed_at"], "actual_recorded_at": actual["recorded_at"],
        "actual_review": pin(str((HERE / "source_review.json").relative_to(ROOT))),
        "runtime_plan": pin(str((HERE / "retained_review_plan.json").relative_to(ROOT))),
        "old_inputs_byte_identical_to_base": original_pins,
        "seven_registry_files_and_canonical_input_byte_identical_to_base": protected,
        "original_gzip_and_retained_raw_identical": True,
        "tracked_worktree_has_no_changes": True})
    artifact_pins = [pin(path.relative_to(ROOT).as_posix()) for path in sorted(HERE.iterdir())
                     if path.is_file() and path.name != "handoff.json"]
    write("handoff.json", {"schema_version": "batch10_healthcare_legal_applicant_bridge_handoff_v1",
        "recorded_at": now, "base_commit": BASE, "branch": "batch10/healthcare-20261018",
        "kind": "FDA_LEGAL_APPLICANT_BRIDGE",
        "api": {"module": "market_checker_app.collectors.fda_legal_applicant_bridge_review",
                "callable": "review_fda_legal_applicant_bridge(request, captures, *, reviewed_at=None)",
                "captures": "Exactly five request keys -> (metadata, original bytes or None)",
                "reviewed_at": "Aware source-read cutoff at or before physical review; new ingestion and knowledge use actual physical time"},
        "producer": pin(PRODUCER), "tests_source": pin(TEST), "focused_tests": tests,
        "retained_review_plan": pin(str((HERE / "retained_review_plan.json").relative_to(ROOT))),
        "actual_review": pin(str((HERE / "source_review.json").relative_to(ROOT))),
        "actual_reviewed_at": review["reviewed_at"], "original_sources": source_pins,
        "route_audit": actual["route_audit"], "old_input_pins": original_pins,
        "protected_registry_and_universe_inputs": protected,
        "actual_outcome": {"status": "PARTIAL", "decision": review["decision"],
            "roster_as_of": "2017-12-31", "roster_entity_rows": 468, "roster_tables": 11,
            "selected_exact_old_name": "Pfizer Ireland Pharmaceuticals", "jurisdiction_literal": "Ireland",
            "exact_current_applicant": "Pfizer Ireland Pharmaceuticals Unlimited Company", "exact_applicant_rows": 0,
            "retained_PurpleBook_product_numbers": ["001", "002"], "outer_container_closed": False,
            "direct_or_indirect_unknown": True, "ownership_start_and_current_control_unknown": True,
            "source_approval_2019_03_11_is_not_ownership_start": True,
            "independent_legacy_CIK_body_fact_verified": False, "gaps": review["gaps"]},
        "actions": {"new_source_requests": 0, "collector_HTTP_requests": 0,
            "fresh_SEC_GET_condition_already_captured_so_not_applicable": True,
            "DB_or_quota_writes": 0, "registry_writes_or_proposals": 0, "score_or_model_changes": 0,
            "remote_writes": 0, "merge": False, "withheld_bodies_read_restored_or_substituted": False},
        "acceptance": {"canonical_tickers": 687, "healthcare_registry_count_unchanged": 10,
            "new_verified_identities": 0, "full_specialist_acceptance": "0/21",
            "historical_availability_backdated": False, "Windows_or_OOS_acceptance_claimed": False,
            "synthetic_positive_is_LIVE_evidence": False},
        "next_work": [
            "Independently dated exact Unlimited Company legal-entity/issuer relationship evidence at the relevant applicant-object time; no short-name alias.",
            "Actual direct-parent chain and ownership-start/current-control documents; a dated incomplete subsidiary roster supplies only a point group listing.",
            "Product/application licensing or ownership assignment requires its own source object and parties; applicant/company/product roles remain separate.",
            "Preserve the original incomplete Purple Book outer container and require a genuine complete source for broader coverage.",
            "Original durable FDA/CMS/Clinical collector quota histories are still unproved; no fresh collector calls were authorized or made."],
        "artifacts": artifact_pins})
    print(json.dumps({"handoff": pin(str((HERE / "handoff.json").relative_to(ROOT))),
        "plan": pin(str((HERE / "retained_review_plan.json").relative_to(ROOT))),
        "actual_reviewed_at": review["reviewed_at"], "focused_tests": 25, "new_HTTP": 0}))


if __name__ == "__main__":
    main()
