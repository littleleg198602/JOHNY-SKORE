"""Preserve and qualify connector-decoded logs; original ZIP proof is separate."""
from pathlib import Path
import hashlib
import json
import re
import sys
import tty

tty.setraw(sys.stdin.fileno())
payload = json.loads(sys.stdin.buffer.read(int(sys.argv[1])))
body = payload["content"].encode("utf-8")
text = body.decode("utf-8")
summaries = re.findall(r"Ran (\d+) tests in ([\d.]+)s", text)
assert any(int(count) == payload["expected_count"] for count, _ in summaries)
unprefixed = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?", "", text, flags=re.M)
if payload["successful"]:
    assert re.search(r"^Ran " + str(payload["expected_count"]) + r" tests in [\d.]+s\r?\n\r?\nOK\r?$", unprefixed, re.M)
    assert not re.search(r"^(?:FAIL: |ERROR: |FAILED \(|OK \()", unprefixed, re.M)
else:
    failures = payload["failures"]
    assert len(re.findall(r"^FAIL: ", unprefixed, re.M)) == failures
    assert not re.search(r"^ERROR: ", unprefixed, re.M)
    assert re.search(r"^FAILED \(failures=" + str(failures) + r"\)\r?$", unprefixed, re.M)
    assert "RUNNER~1" in text
if payload.get("checkpoint"):
    assert any(int(count) == 63 for count, _ in summaries)
directory = Path(sys.argv[2]).resolve()
directory.mkdir(parents=True, exist_ok=True)
job_id = payload["job_id"]
with (directory / f"job_{job_id}_decoded.log").open("xb") as stream:
    stream.write(body)
proof = {
    "head_sha": payload["head_sha"], "run_id": payload["run_id"], "job_id": job_id,
    "decoded_utf8_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
    "test_summaries": [{"tests": int(count), "seconds": float(seconds)} for count, seconds in summaries],
    "plain_ok": payload["successful"], "failure_terminal_summary_verified": not payload["successful"],
    "failures": payload.get("failures", 0),
    "original_zip_members_verified_separately": True,
    "full_specialist_acceptance": False,
}
with (directory / f"job_{job_id}_verification.json").open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(proof, indent=2) + "\n")
print(json.dumps(proof))
