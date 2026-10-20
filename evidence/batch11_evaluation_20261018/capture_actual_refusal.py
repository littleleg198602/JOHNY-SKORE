"""One real physical Linux/hosted refusal; no selected DB or source inspection.

This does not reference or inspect the retained journal-bearing production DB.
It does not execute a pipeline, retry acquisition or create a database.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from market_checker_app.services import user_windows_analysis_capture as module

OUTPUT = HERE / "actual_linux_refusal.json"
REPORT = HERE / "actual_linux_refusal_execution.json"
PRODUCER = REPO / module.PRODUCER_PATH
PLACEHOLDER_DB = REPO / "outputs/native_windows_analysis_not_executed.db"
ARCHIVE = REPO / "evidence/batch9_evaluation_20261016/actual_acquisition"
MANIFEST = ARCHIVE / "acquisition_129e7d68557e7bc5a4cba80773598e05f6dd9d9bea8d4fa4a933c436fe34d57f/market_manifest.json"


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    frozen = HERE / "actual_refusal_producer.py"
    if any(path.exists() for path in (OUTPUT, REPORT, frozen)):
        raise FileExistsError("Preserve the original actual refusal and its exact producer")
    facts = module._runtime_facts()
    if facts["os_system"] == "Windows" and not facts["ci_detected"]:
        raise RuntimeError("This evidence script is only for an actual unsupported/hosted refusal")
    shutil.copyfile(PRODUCER, frozen)
    calls = {"provider": 0, "SQL_read": 0, "store_init": 0, "network": 0}
    def forbidden(name):
        def fail(*args, **kwargs):
            calls[name] += 1
            raise AssertionError("Actual refusal reached forbidden " + name)
        return fail
    started = datetime.now(timezone.utc)
    with patch.object(module, "CapturedMarketDataProvider", side_effect=forbidden("provider")), \
         patch.object(module, "_read_runs", side_effect=forbidden("SQL_read")), \
         patch.object(module.SQLiteStore, "__init__", side_effect=forbidden("store_init")), \
         patch("socket.socket.connect", side_effect=forbidden("network")), \
         patch("urllib.request.urlopen", side_effect=forbidden("network")):
        result = module.UserWindowsAnalysisCapture(enabled=True).capture(output_path=OUTPUT, tickers=["AAPL"],
            db_path=PLACEHOLDER_DB, market_manifest_path=MANIFEST, market_archive_root=ARCHIVE)
    record = result["receipt"]
    assert result["status"] in {"UNSUPPORTED_PLATFORM", "HOSTED_CI_REFUSED"}
    assert not result["pipeline_invoked"] and not result["native_windows_selected_analysis_verified"]
    assert record["market_preflight"] is None and record["database_before"] is None and record["database"] is None
    assert record["fixture_only"] is False and all(value == 0 for value in calls.values())
    assert record["saved_run_count_returned_by_store"] == 0
    loaded = module.load_user_windows_analysis_receipt(OUTPUT)
    assert loaded["content_hash_verified"] is True and loaded["runtime_execution_reverified"] is False
    report = {"scope": "ACTUAL_PHYSICAL_NATIVE_LINUX_OR_HOSTED_REFUSAL_ONLY", "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(), "producer": pin(PRODUCER), "frozen_producer": pin(frozen),
        "script": pin(Path(__file__)), "receipt": pin(OUTPUT), "status": result["status"],
        "actual_runtime": record["runtime"], "actual_completion_clocks": record["clocks"],
        "API_clock_or_runtime_injection": False, "pipeline_invoked": False,
        "guarded_entrypoint_calls": calls, "selected_DB_or_market_input_inspection": False,
        "original_retained_DB_referenced_read_opened_copied_or_changed": False,
        "source_HTTP_requests": 0, "actual_native_pipeline_executions": 0,
        "new_genuine_forecasts": 0, "new_registrations": 0, "new_labels": 0,
        "native_windows_selected_analysis_verified": False, "user_windows_end_to_end_verified": False,
        "full_specialist_acceptance": False,
        "limits": ["This actual platform cannot provide the requested native user-Windows run",
            "Selected market/DB facts remain unobserved; no absence or coverage is inferred",
            "A loaded receipt verifies content integrity only, never runtime execution",
            "Candidate physical source pins are exact; the new producer is not yet in the observed baseline Git tree"]}
    with REPORT.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "receipt": pin(OUTPUT), "pipeline_invoked": False,
        "guarded_entrypoint_calls": calls, "native_windows_selected_analysis_verified": False}, indent=2))


if __name__ == "__main__":
    main()
