"""Run only the new offline completeness-review suite and pin its version."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
SOURCES = ("market_checker_app/services/nhtsa_negative_scope_review.py",
           "tests/test_batch9_nhtsa_negative_scope.py")


def main():
    identifier = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1].isalnum() else None
    if identifier is None:
        raise SystemExit("Choose a new alphanumeric result identifier")
    log, result = FOLDER / f"focused_{identifier}.log", FOLDER / f"focused_{identifier}.json"
    assert not log.exists() and not result.exists()
    before = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES}
    started_at = datetime.now(timezone.utc).isoformat()
    began = time.perf_counter()
    command = [sys.executable, "-m", "unittest", "tests.test_batch9_nhtsa_negative_scope", "-v"]
    process = subprocess.run(command, cwd=ROOT, capture_output=True)
    body = process.stdout + process.stderr
    with log.open("xb") as stream:
        stream.write(body)
    after = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES}
    assert before == after
    record = {"command": command, "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter()-began,
        "exit_code": process.returncode, "code_test_sha256": after, "log_sha256": hashlib.sha256(body).hexdigest(),
        "only_new_focused_suite_executed": True, "actual_http_or_quota_database_actions": 0,
        "synthetic_http200_empty_is_contract_test_only": True,
        "actual_source_complete_negatives_claimed": 0, "publisher_or_production_assertions_relaxed": False}
    with result.open("x") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(body.decode())
    print(json.dumps(record, sort_keys=True))
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
