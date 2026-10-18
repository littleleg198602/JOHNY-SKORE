"""One explicit native readiness capture, offline and without any production run.

The retained database is observed mode=ro. The manifest path is the absent output
of the already-consumed failed acquisition; this script never retries that plan.
No clocks, runtime identity or market contents are supplied as test substitutes.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from market_checker_app.services.user_windows_readiness_capture import UserWindowsReadinessCapture, load_user_windows_readiness_receipt

DATABASE = Path("/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db")
ARCHIVE_ROOT = REPO / "evidence/batch9_evaluation_20261016/actual_acquisition"
MANIFEST = ARCHIVE_ROOT / "acquisition_129e7d68557e7bc5a4cba80773598e05f6dd9d9bea8d4fa4a933c436fe34d57f/market_manifest.json"
OUTPUT = HERE / "actual_linux_readiness.json"
REPORT = HERE / "actual_capture_execution.json"
PRODUCER = REPO / "market_checker_app/services/user_windows_readiness_capture.py"


def pin(path):
    if not path.exists():
        return {"exists": False, "bytes": None, "sha256": None}
    raw = path.read_bytes()
    return {"exists": True, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    if OUTPUT.exists() or REPORT.exists() or (HERE / "actual_capture_producer.py").exists():
        raise FileExistsError("Preserve this original actual capture; do not rerun or overwrite it")
    if MANIFEST.exists():
        raise ValueError("The designated failed acquisition unexpectedly has a manifest; reassess explicitly")
    shutil.copyfile(PRODUCER, HERE / "actual_capture_producer.py")
    started, before, source = datetime.now(timezone.utc), pin(DATABASE), pin(PRODUCER)
    native_connect, connections, calls = sqlite3.connect, [], []
    class ReadOnlyConnection(sqlite3.Connection):
        closed = False
        def close(self):
            self.closed = True
            return super().close()
    def read_only_connect(*args, **kwargs):
        if args != (DATABASE.resolve().as_uri() + "?mode=ro",) or kwargs.get("uri") is not True:
            raise AssertionError("Only the explicitly authorized retained mode=ro database may be opened")
        calls.append({"URI_mode_ro": True, "URI_enabled": True})
        connection = native_connect(*args, **kwargs, factory=ReadOnlyConnection)
        connections.append(connection)
        return connection
    with patch("socket.socket.connect", side_effect=AssertionError("Actual readiness capture is strictly offline")), \
         patch("urllib.request.urlopen", side_effect=AssertionError("No acquisition retry")), \
         patch("sqlite3.connect", side_effect=read_only_connect):
        result = UserWindowsReadinessCapture(enabled=True).capture(output_path=OUTPUT, tickers=["AAPL"],
            db_path=DATABASE, market_manifest_path=MANIFEST, market_archive_root=ARCHIVE_ROOT)
    receipt = load_user_windows_readiness_receipt(OUTPUT)
    after = pin(DATABASE)
    if before != after or any(not connection.closed for connection in connections):
        raise AssertionError("Read-only database preservation/lifetime invariant failed")
    if receipt["fixture_only"] or receipt["runtime"]["os_system"] != "Linux":
        raise AssertionError("This qualification expects a genuine native Linux readiness limitation")
    if receipt["user_windows_end_to_end_verified"] or receipt["market_readiness"]["usable_count"] != 0:
        raise AssertionError("Absent input data cannot establish acceptance")
    report = {"scope": "ACTUAL_NATIVE_LINUX_READINESS_ONLY", "started_at": started.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(), "producer": source, "producer_after": pin(PRODUCER),
        "script": pin(Path(__file__)), "receipt": pin(OUTPUT), "receipt_status": result["status"],
        "readiness_status": receipt["readiness_status"], "retained_database_before": before,
        "retained_database_after": after, "retained_database_bytes_unchanged": before == after,
        "read_only_connection_calls": calls, "read_only_connections_closed": all(item.closed for item in connections),
        "manifest_absent": not MANIFEST.exists(), "actual_runtime_and_physical_clocks": True,
        "runtime_clock_or_platform_injection": False, "network_entrypoints_guarded": True,
        "source_HTTP_requests": 0, "production_database_writes": 0,
        "actual_forecasts_created": 0, "registrations_created": 0, "labels_created": 0,
        "user_windows_end_to_end_verified": False,
        "limitations": ["Linux is not the user's native Windows machine", "Readiness capture executes no normal production run",
            "No captured usable market manifest exists at the failed acquisition's explicit output path",
            "A reader platform cannot prove the operating system or authenticity of old stored rows",
            "The candidate producer is pinned by physical bytes; its new untracked module is not in the baseline Git tree",
            "UNKNOWN publisher revision timing remains excluded from strict PIT/OOS"]}
    with REPORT.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "readiness_status": receipt["readiness_status"],
        "database_status": receipt["database"]["status"], "observed_sample_counts": {name: row["sampled_rows"] for name, row in receipt["database"]["tables"].items()},
        "usable_market_inputs": receipt["market_readiness"]["usable_count"], "user_windows_end_to_end_verified": False,
        "receipt_sha256": result["receipt_sha256"], "retained_database_bytes_unchanged": before == after}, indent=2))


if __name__ == "__main__":
    main()
