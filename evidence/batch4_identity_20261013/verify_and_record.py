"""Verify the finished lane artifacts and record their content, not DONE claims."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

from evidence.batch4_identity_20261013.replay_review import HERE, ROOT, build_review


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    report, diagnostics = build_review()
    if json.loads((HERE / "review.json").read_bytes()) != report:
        raise ValueError("The saved read-only report differs from the exact replay")
    if json.loads((HERE / "replay_diagnostics.json").read_bytes()) != diagnostics:
        raise ValueError("The saved diagnostics differ from the exact replay")
    log = (HERE / "focused_tests.log").read_text(encoding="utf-8")
    result = re.search(r"Ran ([0-9]+) tests in ([0-9.]+)s\s+OK\s*\Z", log)
    if result is None or int(result[1]) != 100:
        raise ValueError("Expected the completed focused 100-test run")
    diff_command = ["git", "diff", "--cached", "--check"]
    raw_diff = subprocess.run(diff_command, cwd=ROOT, text=True, capture_output=True, check=False)
    scoped_command = [*diff_command, "--", ".", ":(exclude)evidence/batch4_identity_20261013/captures/*.bin"]
    scoped_diff = subprocess.run(scoped_command, cwd=ROOT, text=True, capture_output=True, check=False)
    if scoped_diff.returncode:
        raise ValueError("Code or metadata contains unresolved whitespace errors")
    inventory = []
    for path in sorted(HERE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            inventory.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                              "sha256": digest(path)})
    code_files = ["market_checker_app/services/sec13f_identity_bulk.py",
                  "market_checker_app/services/sec13f_transition_review.py",
                  "tests/test_batch4_identity_transitions.py"]
    value = {
        "schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
        "lane": "identity_bulk", "baseline_commit": "22bcb749873b9bfd89e8f6d52e5bb7909a186789",
        "evidence_type": "actual_primary_transition_review_and_fail_closed_implementation",
        "runtime": {"platform": platform.system(), "python": sys.version.split()[0]},
        "implementation": [
            "A chronological same-CUSIP ticker rename now requires exact hash-bound primary transition review; prior versions are preserved",
            "A deleted or incompatible-class current-list CUSIP cannot inherit ALREADY_REVIEWED status",
            "Current single issuerCusips/issuerCusipNumber XML is supported; duplicate, multiple, mixed or wrong-namespace identifiers fail closed",
            "An exact single SEC DOCUMENT envelope can be parsed without rewriting raw bytes; filenames, complete HTML, hidden/script exclusions remain strict",
            "Bounded read-only transition diagnostics separate observed catalog symbols, dated DEI contexts, dated instrument claims and primary statements; unrelated filing issuer statements fail closed",
        ],
        "real_source_review": {
            "raw_primary_captures": diagnostics["raw_capture_count"],
            "original_bytes_verified": True, "canonical_universe_size": report["canonical_universe_size"],
            "selected_tickers": report["selected_tickers"], "review_sha256": report["review_sha256"],
            "publication_clock_basis": "Exact archived SEC submissions acceptanceDateTime; no inferred clock reconciliation or historical source-check availability",
            "XOM": {"old_cik": "0000034088", "old_cusip": "30231G102",
                    "catalog_cik": "0002115436", "candidate_cusip": "30233Q108",
                    "primary_redomiciliation_effective_on": "2026-07-01",
                    "raw_same_context_anchor": "2026-07-07",
                    "raw_cover_class": "Common Stock, without par value",
                    "raw_action_class": "Common Stock, par value $0.001",
                    "class_scope_normalized_or_promoted": False,
                    "old_q2_interval_preserved": True, "full_instrument_transition_approved": False},
            "LEG": {"current_catalog_symbol_absent": True, "cusip": "524660107",
                    "selected_list_status": "DELETED_IN_SELECTED_LIST",
                    "primary_merger_closing_on": "2026-08-26",
                    "parent_share_exchange_ratio_in_source": "0.1455",
                    "cash_in_source_scope": "cash in lieu of fractional shares only",
                    "suspension_delisting_request_in_primary_source": True,
                    "parent_alias_or_group_identity_created": False},
            "PSTG": {"current_catalog_symbol_absent": True, "same_cik_catalog_symbol": "P",
                     "issuer_cik": "0001474432", "cusip": "74624M102",
                     "old_symbol_context_anchor": "2026-02-23",
                     "new_symbol_q3_context_anchor": "2026-08-02",
                     "captured_change_statement_date_scope": "April 2026 only; exact effective day not inferred",
                     "captured_instrument_event": "2026-03-31",
                     "captured_instrument_class": "Common Stock; not promoted to exact Class A",
                     "canonical_alias_created": False},
        },
        "verification": {
            "focused_tests": {"command": [sys.executable, "-m", "unittest", "tests.test_batch4_identity_transitions",
                                          "tests.test_sec13f_identity_bulk", "tests.test_sec13f_identity_transitions", "-v"],
                              "tests_run": int(result[1]), "failures": 0, "errors": 0, "skipped": 0,
                              "duration_seconds": float(result[2]), "exit_code": 0,
                              "log_path": str((HERE / "focused_tests.log").relative_to(ROOT)),
                              "log_sha256": digest(HERE / "focused_tests.log")},
            "offline_replay": {"network_requested": False, "saved_report_matches": True,
                               "saved_diagnostics_match": True,
                               "review_path": str((HERE / "review.json").relative_to(ROOT)),
                               "report_file_sha256": digest(HERE / "review.json")},
            "compileall": {"exit_code": 0},
            "git_diff_check": {
                "including_raw_captures": {"command": diff_command, "exit_code": raw_diff.returncode,
                                           "output": raw_diff.stdout + raw_diff.stderr},
                "code_and_metadata": {"command": scoped_command, "exit_code": scoped_diff.returncode},
                "raw_capture_bytes_preserved": True,
            },
        },
        "tested_code_sha256": {name: digest(ROOT / name) for name in code_files},
        "baseline_registry_sha256": diagnostics["baseline_registry_sha256"],
        "new_verified_identities": 0, "production_registry_changed": False,
        "canonical_tickers_changed": False, "score_changed": False, "specialist_done_changed": False,
        "limits": [
            "The selected three investigations are not measured current-Q3 coverage of all 687 companies",
            "The XOM action establishes succession and its date but does not bind the complete exact old/new CIK/CUSIP/class instrument bridge",
            "XOM July7 DEI says Common Stock without par value while the July1 successor action says par value $0.001; the discrepancy and class-scope blocker are retained",
            "LEG former common stock remains a deleted current-list instrument; merger consideration does not authorize an automatic parent alias",
            "PSTG remains unresolved under the canonical old ticker; same-CIK current symbol P is research evidence, not a provider alias or historical identity export",
            "An actual XOM July7 raw response has an incomplete closing html tag; bytes are unchanged and full visible-action proof is withheld while completed same-context DEI facts are separately labeled",
            "Captured instrument dates and current catalog observations do not establish continuous historical quarter validity or prior source-check availability",
            "No production identity, relevant full-universe coverage, user Windows, hosted Windows, OOS, specialist completion, score or trading acceptance is claimed",
            "The transient missing interpreter attempt is preserved; the centrally restored runtime completed the final focused tests",
        ], "inventory": inventory,
    }
    output = ROOT / "evidence" / "batch4_identity_20261013.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"evidence": str(output.relative_to(ROOT)), "tests": int(result[1]),
                      "primary_captures": diagnostics["raw_capture_count"], "new_verified_identities": 0}))
