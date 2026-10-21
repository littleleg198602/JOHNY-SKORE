"""Supervise one original full run; never replace an existing launch or log."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = ROOT.parent / "sourcefront21_20261021"


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, obj):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(obj, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected = json.loads((ROOT / "evidence/sourcefront21_expected_methods_20261021.json").read_bytes())
    audit = json.loads((HERE / "content_audit.json").read_bytes())
    assert expected["new_exact_method_count"] == 51 and expected["expected_full_test_count"] == 2818
    pins = audit["current_source_test_pins"]
    for pin in pins:
        raw = (ROOT / pin["path"]).read_bytes()
        assert len(raw) == pin["bytes"] and hashlib.sha256(raw).hexdigest() == pin["sha256"]
    console = ROOT / "evidence/sourcefront21_linux_console_20261021.log"
    runner = ROOT / "evidence/sourcefront21_linux_runner_20261021.log"
    result = ROOT / "evidence/sourcefront21_linux_20261021.json"
    assert not any(path.exists() for path in (console, runner, result, OUT / "local_full_launch.json"))
    command = ["/workspace/scratch/334b3a149083/johny-tests-20261011/bin/python", "-X", "utf8",
               "-m", "market_checker_app.deterministic_test_runner", "--log-path", str(runner),
               "--result-path", str(result)]
    started = now()
    with console.open("xb") as output:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        save(OUT / "local_full_launch.json", {"launched_at": started, "pid": process.pid,
             "tested_code_snapshot": head, "source_test_pins": pins,
             "expected_full_test_count": 2818, "expected_new_methods": 51,
             "command": command, "console_path": str(console.relative_to(ROOT)),
             "final_outcome": "PENDING", "duplicate_launch_permitted": False})
        print(json.dumps({"launched": True, "pid": process.pid, "head": head,
                          "expected_count": 2818}), flush=True)
        code = process.wait()
    save(OUT / "local_full_process_exit.json", {"pid": process.pid, "exit_code": code,
                                               "finished_at": now()})
    print(json.dumps({"exit_code": code, "finished_at": now()}), flush=True)
