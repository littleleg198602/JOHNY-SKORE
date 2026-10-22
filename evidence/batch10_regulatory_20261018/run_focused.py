"""Qualify the exact new caption reader/test bytes; never runs the full suite."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
SOURCES = ("market_checker_app/collectors/doj_case_caption_review.py", "tests/test_doj_case_caption_review.py")


def main():
    run = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1].isalnum() else None
    if run is None:
        raise SystemExit("Choose one new alphanumeric run identifier")
    log, record = FOLDER / f"focused_{run}.log", FOLDER / f"focused_{run}.json"
    assert not log.exists() and not record.exists()
    before = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}
    started = datetime.now(timezone.utc).isoformat()
    began = time.perf_counter()
    command = [sys.executable, "-m", "unittest", "tests.test_doj_case_caption_review", "-v"]
    process = subprocess.run(command, cwd=ROOT, capture_output=True)
    body = process.stdout+process.stderr
    with log.open("xb") as stream:
        stream.write(body)
    after = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}
    assert before == after
    data = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter()-began, "command": command, "exit_code": process.returncode,
        "code_test_sha256": after, "log_sha256": hashlib.sha256(body).hexdigest(),
        "new_http_requests": 0, "database_connections_or_writes": 0,
        "source_or_hash_assertions_relaxed": False, "only_new_focused_suite": True,
        "synthetic_pdfs_are_contract_tests_not_acceptance_originals": True}
    with record.open("x") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(body.decode())
    print(json.dumps(data, sort_keys=True))
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
