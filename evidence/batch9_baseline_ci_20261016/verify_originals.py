"""Verify original b81 baseline CI bytes; this does not execute tests or accept specialists."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
HEAD = "b81a9084c064dc1a12bf17088855b5ed384ba3dd"
PINS = {
    "artifact_12033093458.zip": "0bc48e34d0d3dc12e578aac53668de6870c35254c46e4140c05c5c390a974bf8",
    "windows-tests.json": "2e2470e6a64e07dbdbf748bf20a2dd0c9fb4c085c6ae282be37036abaa9b83db",
    "windows-tests.log": "cdd6f6f534fffaaa0e7c20ebfb3e621ecf02d3a173453b5392aa391758676432",
    "job_116862664073_decoded.log": "7f8de78a89bd821ac7db17bcdf2591a8acb3bb12da4e7cac05388261f439e4e7",
    "job_116862664095_decoded.log": "a9ccc9fee8951ae73e546f82fe161c776d6189bc8d9234a71fcbecadd17766df",
}


def verify():
    originals = {name: (ROOT / name).read_bytes() for name in PINS}
    for name, body in originals.items():
        assert hashlib.sha256(body).hexdigest() == PINS[name], name
    assert len(originals["artifact_12033093458.zip"]) == 61411
    with zipfile.ZipFile(ROOT / "artifact_12033093458.zip") as archive:
        assert set(archive.namelist()) == {"windows-tests.json", "windows-tests.log"}
        for name in archive.namelist():
            assert archive.read(name) == originals[name], name
    report = json.loads(originals["windows-tests.json"])
    assert report["discovered_tests"] == report["tests_run"] == 2037
    assert report["successful"] is True and report["platform"].startswith("Windows")
    assert all(report[key] == 0 for key in (
        "failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    log = originals["windows-tests.log"].decode("utf-8")
    assert re.search(r"Ran 2037 tests in 2063\.549s\r\n\r\nOK\r\n\Z", log)
    planned = json.loads((ROOT / "original_planned_windows_log_checks.json").read_bytes())
    assert planned["public_head"] == HEAD and planned["executed_verified"] is False
    starts = list(re.finditer(r"(?m)^test_[^\r\n]+\([^\r\n]+\) \.\.\. ", log))
    assert len(planned["methods"]) == 202
    for row in planned["methods"]:
        heading = f"{row['method']} ({row['module']}.{row['class']}.{row['method']}) ... "
        hits = [(i, match) for i, match in enumerate(starts) if match.group(0) == heading]
        assert len(hits) == 1, heading
        index, match = hits[0]
        end = starts[index + 1].start() if index + 1 < len(starts) else log.index(
            "\r\n----------------------------------------------------------------------", match.start())
        block = log[match.start():end].strip()
        assert re.search(r"(?:\.\.\. |\r?\n)ok\Z", block), heading
    github = json.loads((ROOT / "github_run_jobs_artifacts.json").read_bytes())
    run = github["workflow_run"]
    assert run["id"] == 38867202786 and run["head_sha"] == HEAD
    assert run["status"] == "completed" and run["conclusion"] == "success"
    jobs = github["jobs"]["jobs"]
    assert len(jobs) == 5 and all(job["conclusion"] == "success" for job in jobs)
    assert any(job["id"] == 116874853777 for job in jobs)
    artifact = github["artifacts"][0]
    assert artifact["id"] == 12033093458 and artifact["workflow_run"]["head_sha"] == HEAD
    assert artifact["digest"] == "sha256:" + PINS["artifact_12033093458.zip"]
    return {"head_sha": HEAD, "run_id": run["id"], "five_required_checks_success": True,
            "actual_original_zip_and_members_verified": True, "windows_tests": 2037,
            "batch8_exact_methods_executed_ok": 202, "plain_ok": True,
            "all_21_complete": False, "user_windows_end_to_end_verified": False,
            "genuine_oos_verified": False, "qualifies_only_this_baseline_head": True}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
