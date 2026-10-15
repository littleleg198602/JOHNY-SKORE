"""Download and inspect the exact failed hosted artifact; URL stays private."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tty
import urllib.request
import zipfile

tty.setraw(sys.stdin.fileno())
payload = json.loads(sys.stdin.buffer.read(int(sys.argv[1])))
directory = Path(sys.argv[2]).resolve()
directory.mkdir(parents=True, exist_ok=True)
assert payload["head_sha"] == "bfedb764da3a189df1d9f8eb799cf834867f8083"
assert payload["tree_sha"] == "e368e91e634cbc86b09c65381ae8a262db254898"
with urllib.request.urlopen(payload["download_url"], timeout=55) as response:
    body = response.read()
digest = hashlib.sha256(body).hexdigest()
assert digest == payload["digest"].removeprefix("sha256:")
assert len(body) == payload["size_in_bytes"] == 56555
zip_path = directory / ("artifact_" + str(payload["artifact_id"]) + ".zip")
with zip_path.open("xb") as output:
    output.write(body)
with zipfile.ZipFile(zip_path) as archive:
    assert set(archive.namelist()) == {"windows-tests.json", "windows-tests.log"}
    files = {name: archive.read(name) for name in archive.namelist()}
for name, raw in files.items():
    with (directory / name).open("xb") as output:
        output.write(raw)
report = json.loads(files["windows-tests.json"])
assert report["discovered_tests"] == report["tests_run"] == 1833
assert report["successful"] is False and report["failures"] == 9
assert all(report[key] == 0 for key in ("errors", "skipped", "expected_failures", "unexpected_successes"))
assert report["platform"].startswith("Windows")
log = files["windows-tests.log"]
assert re.search(rb"Ran 1833 tests in 2014\.103s\r?\n\r?\nFAILED \(failures=9\)\r?\n\Z", log)
assert len(re.findall(rb"^FAIL: ", log, re.M)) == 9
assert not re.search(rb"^ERROR: ", log, re.M)
assert b"RUNNER~1" in log
proof = {"head_sha": payload["head_sha"], "tree_sha": payload["tree_sha"],
    "run_id": payload["run_id"], "windows_job_id": payload["windows_job_id"],
    "artifact_id": payload["artifact_id"], "zip_sha256": digest, "zip_bytes": len(body),
    "published_digest_matches_actual_zip": True, "original_members_read_and_preserved": True,
    "files": {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                       "crlf_lines": raw.count(b"\r\n")} for name, raw in files.items()},
    "windows_report": report, "exact_failure_terminal_summary_verified": True,
    "failure_headers": re.findall(r"^FAIL: (.*)$", log.decode("utf-8"), re.M),
    "failed_version_is_acceptance": False, "all_21_complete": False}
with (directory / "content_verification.json").open("x", encoding="utf-8", newline="\n") as output:
    json.dump(proof, output, indent=2)
    output.write("\n")
print(json.dumps(proof))
