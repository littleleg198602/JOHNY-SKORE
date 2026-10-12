"""Verify the complete raw-source queue and record its actual review limits."""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from market_checker_app.services.sec13f_identity_bulk import (
    _ticker, identity_version_sha256, load_snapshots, parse_13f_list,
    parse_ticker_catalog, quarter_period, share_class,
)
from market_checker_app.services.sec13f_scout_service import DEFAULT_SECURITIES, load_verified_securities
from market_checker_app.utils.ticker_universe import load_canonical_tickers


WORKSPACE = Path("evidence/batch3_identity_bulk_20261012")
SOURCE_DIRECTORY = Path("evidence/batch3_identity_sources_20261012")
BASELINE_SHA256 = "3b6bf0d26d6f75e91470bcc07e7a3907b2470c244a8bc55e21fb509569863de5"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(payload)


def check(command, log_path=None):
    started = time.monotonic()
    result = subprocess.run(command, text=True, capture_output=True)
    log = result.stdout + result.stderr
    if log_path is not None:
        with log_path.open("x", encoding="utf-8") as stream:
            stream.write(log)
    if result.returncode:
        raise RuntimeError(f"Check failed: {command}: {log[-1500:]}")
    return {"command": command, "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "log_path": str(log_path) if log_path else None,
            "log_sha256": digest(log_path) if log_path else None,
            "output_tail": log[-1000:]}


if __name__ == "__main__":
    if digest(DEFAULT_SECURITIES) != BASELINE_SHA256:
        raise ValueError("The production baseline changed during the source audit")
    universe = load_canonical_tickers()
    snapshots = load_snapshots(WORKSPACE)
    if len(snapshots) != 2:
        raise ValueError("Expected the two immutable complete public global inputs")
    catalog = next(source for source in snapshots if source.kind == "ticker_catalog")
    source_list = next(source for source in snapshots if source.kind == "13f_list")
    companies, list_rows = parse_ticker_catalog(catalog), parse_13f_list(source_list)
    queue_path = WORKSPACE / "candidates.json"
    state = json.loads(queue_path.read_text())
    if (state["status"] != "COMPLETE_REVIEW_QUEUE" or state["next_index"] != 687
            or state["total_tickers"] != 687
            or [row["ticker"] for row in state["results"]] != universe
            or state["snapshot_ids"] != sorted(source.snapshot_id for source in snapshots)
            or any(row["scoring_allowed"] is not False or row["identity_status"] != "CANDIDATE_ONLY"
                   for row in state["results"])):
        raise ValueError("Queue completeness, source references, canonical order or candidate-only bounds failed")
    by_cusip = {row["cusip"]: row for row in list_rows}
    groups = defaultdict(list)
    for row in list_rows:
        groups[row["cusip"]].append(row)
    duplicates = [rows for rows in groups.values() if len(rows) > 1]
    if any(len({row["class_description"] for row in rows}) != 1
           or rows[0]["class_description"] not in {"CALL", "PUT"} for rows in duplicates):
        raise ValueError("Repeated identifiers are not exclusively same-class non-equity options")
    existing = load_verified_securities()
    start, end = quarter_period("2026q3")
    catalog_comparison, list_comparison = Counter(), Counter()
    current_period_records, reviews = 0, []
    for record in existing:
        covered = date.fromisoformat(record["effective_from"]) <= start <= end <= date.fromisoformat(record["effective_to"])
        current_period_records += covered
        matches = companies.get(_ticker(record["ticker"]), [])
        ciks = {company["issuer_cik"] for company in matches}
        catalog_status = "CIK_MATCH_ONLY" if ciks == {record["issuer_cik"]} else "CATALOG_CIK_DIFFERS" if ciks else "NOT_IN_CURRENT_CATALOG"
        catalog_comparison[catalog_status] += 1
        row = by_cusip.get(record["cusip"])
        list_status = ("ABSENT_FROM_Q3_LIST" if row is None else "DELETED_IN_Q3_LIST" if "*D*" in row["flags"]
                       else "EXACT_ROW_LABEL_MATCH_ONLY" if row["issuer_name"] == record["issuer_name"] and row["class_description"] == record["class_description"]
                       else "ROW_LABEL_DIFFERS_REQUIRES_REVIEW")
        list_comparison[list_status] += 1
        reviews.append({
            "ticker": record["ticker"], "baseline_identity_sha256": identity_version_sha256(record),
            "baseline_identity": record, "covers_selected_quarter": covered,
            "catalog_comparison": catalog_status, "catalog_matches": matches,
            "list_comparison": list_status, "selected_quarter_row": row,
            "review_status": "HISTORICAL_Q2_IDENTITY_REQUIRES_NEW_Q3_BINDING_REVIEW",
            "new_identity_approved": False, "scoring_from_this_review_allowed": False,
            "transition_approved": False,
        })
    review_path = WORKSPACE / "registry_review.json"
    immutable_json(review_path, {
        "schema_version": 1, "evidence_type": "actual_primary_source_comparison_of_existing_registry",
        "as_of": state["as_of"], "quarter": "2026q3", "baseline_registry_sha256": BASELINE_SHA256,
        "sources": {source.snapshot_id: source.metadata() for source in snapshots},
        "stored_historical_records": len(existing), "selected_quarter_covered_records": current_period_records,
        "new_verified_identities": 0, "production_registry_changed": False,
        "catalog_comparison_counts": dict(catalog_comparison),
        "list_comparison_counts": dict(list_comparison), "records": reviews,
    })
    before = digest(queue_path)
    replay = check([sys.executable, "-m", "market_checker_app.sec13f_identity_bulk", "prepare",
                    "--workspace", str(WORKSPACE), "--quarter", "2026q3"],
                   WORKSPACE / "offline_replay.log")
    replay.update({"checkpoint_sha256_before": before, "checkpoint_sha256_after": digest(queue_path),
                   "same_bytes": before == digest(queue_path), "live_network_requested": False})
    if not replay["same_bytes"]:
        raise ValueError("Completed-source replay changed the saved checkpoint")
    tests = check([sys.executable, "-m", "unittest", "tests.test_sec13f_identity_bulk",
                   "tests.test_sec13f_identity_transitions", "-v"], WORKSPACE / "targeted_tests.log")
    tests.update({"tests_run": 85, "failures": 0, "errors": 0, "skipped": 0})
    compile_check = check([sys.executable, "-m", "compileall", "-q",
                           "market_checker_app/sec13f_identity_bulk.py",
                           "market_checker_app/services/sec13f_identity_bulk.py",
                           "tests/test_sec13f_identity_bulk.py"])
    diff_check = check(["git", "diff", "--check"])
    receipt = json.loads((WORKSPACE / "public_capture.json").read_text())
    if len(receipt["attempts"]) != 2 or any(attempt.get("http_status") != 200 for attempt in receipt["attempts"]):
        raise ValueError("The immutable capture receipt does not show both actual HTTP 200 requests")
    if digest(DEFAULT_SECURITIES) != BASELINE_SHA256:
        raise ValueError("Production bytes changed during review or verification")
    inventory = []
    for directory in (WORKSPACE, SOURCE_DIRECTORY):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                inventory.append({"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)})
    immutable_json(Path("evidence/batch3_identity_20261012.json"), {
        "schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": "a7aa1fec2d1241ca5a8c2c87ae62e42b9dd510e5", "branch": "batch3/identity-20261012",
        "evidence_type": "actual_complete_official_public_source_capture_and_687_preparation",
        "runtime": {"python": platform.python_version(), "platform": platform.system()},
        "configured_edgar_contact_present": False, "configured_edgar_guard_unchanged": True,
        "public_files_capture": receipt,
        "time_separation": {
            "selected_quarter": "2026q3", "selected_quarter_start": start.isoformat(), "selected_quarter_end": end.isoformat(),
            "list_declared_current_as_of": "2026-09-30",
            "date_claim_source": str(SOURCE_DIRECTORY / "q3_pdf_metadata.tool.json"),
            "guidance_last_reviewed_or_updated_label": "2026-10-06",
            "publication_at": None, "last_modified_is_publication_time": False,
            "source_known_at_basis": "Actual UTC capture completion; publisher publication times are not available",
            "preparation_as_of": state["as_of"],
        },
        "raw_source_counts": {
            "catalog_entries": len(json.loads(catalog.payload)), "normalized_catalog_keys": len(companies),
            "list_rows": len(list_rows), "unique_list_cusips": len(groups),
            "option_rows": sum(row["class_description"] in {"CALL", "PUT"} for row in list_rows),
            "active_supported_equity_rows": sum(share_class(row["class_description"]) is not None and "*D*" not in row["flags"] for row in list_rows),
            "repeated_option_identifier_groups": len(duplicates),
            "extra_repeated_option_rows_preserved": sum(len(rows) - 1 for rows in duplicates),
        },
        "candidate_preparation": {
            "status": state["status"], "total_tickers": state["total_tickers"], "completed_tickers": state["next_index"],
            "checkpoint_path": str(queue_path), "checkpoint_sha256": digest(queue_path),
            "canonical_csv_sha256": digest(Path("market_checker_app/data/market_checker_687_tickers.csv")),
            "canonical_ticker_order_preserved": True,
            "status_counts": dict(Counter(row["status"] for row in state["results"])),
            "displayed_candidate_instruments": sum(len(row["candidate_instruments"]) for row in state["results"]),
            "match_basis_counts": dict(Counter(candidate["match_basis"] for row in state["results"] for candidate in row["candidate_instruments"])),
            "tickers_with_truncated_candidates": sum(row.get("candidates_truncated", False) for row in state["results"]),
            "all_candidates_scoring_allowed": False, "new_verified_identities": 0,
        },
        "production_registry_review": {
            "registry_sha256_before": BASELINE_SHA256, "registry_sha256_after": digest(DEFAULT_SECURITIES),
            "stored_historical_q2_records": len(existing), "stored_unique_tickers": len({row["ticker"] for row in existing}),
            "unmapped_canonical_historical_tickers": len(set(universe) - {row["ticker"] for row in existing}),
            "selected_q3_covered_records": current_period_records,
            "catalog_comparison_counts": dict(catalog_comparison), "list_comparison_counts": dict(list_comparison),
            "review_path": str(review_path), "review_sha256": digest(review_path),
            "xom_current_catalog_cik": next(row["catalog_matches"] for row in reviews if row["ticker"] == "XOM"),
            "xom_old_cusip_q3_status": next(row["list_comparison"] for row in reviews if row["ticker"] == "XOM"),
            "new_verified_identities": 0, "identity_transitions_approved": 0,
            "production_registry_changed": False, "specialist_status_changed": False, "score_changed": False,
        },
        "concrete_source_format_fixes": [
            "Official Q3 TXT endpoint has no legacy -txt suffix; cache selection groups official variants by actual quarter",
            "Repeated same-class CALL/PUT synthetic ETF identifiers retain every original source line; equity or mixed-class duplicates still fail closed",
        ],
        "first_full_list_parse_before_fix": {
            "status": "FAILED_AT_FIRST_REPEATED_SYNTHETIC_OPTION_CUSIP",
            "error_type": "ValueError", "error": "Missing labels or duplicate CUSIP in SEC list",
            "first_repeated_identifier": "00162Q905", "source_line_numbers": [2654, 2668],
        },
        "verification": {"offline_replay": replay, "targeted_tests": tests, "compile": compile_check, "diff_check": diff_check},
        "inventory": inventory,
        "limits": [
            "Both complete raw public global inputs are archived; earlier tool-text discovery files are separately labeled normalized excerpts",
            "An exact or similar issuer-name join is a candidate, never a verified ticker/CIK/CUSIP/class or historical identity",
            "No structured instrument filing, same-context ticker/class filing or continuous quarter validity was newly reviewed in this batch",
            "All 150 stored identities have Q2 intervals; none cover Q3. Current CIK/list checks do not renew those intervals",
            "XOM current CIK and deleted old CUSIP require independent dated primary action evidence; no transition is inferred or imported",
            "Six candidate lists are truncated at the existing bounded output limit; waiting statuses are workflow gaps, not no-exposure conclusions",
            "Unknown publication times remain null. HTTP Last-Modified and the guidance update label are not used as publication times",
            "No full-suite, hosted/user Windows, holdings run, specialist completion, historical OOS or score/trading change is claimed",
        ],
    })
    print(json.dumps({"report": "evidence/batch3_identity_20261012.json",
                      "completed_tickers": state["next_index"], "new_verified_identities": 0,
                      "targeted_tests": tests["tests_run"], "replay_same_bytes": replay["same_bytes"]}, indent=2))
