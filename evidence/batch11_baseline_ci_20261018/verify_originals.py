"""Offline original CI qualification; applies only to public6ba/2476."""
import hashlib
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parent
HEAD = "6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"
manifest = json.loads((ROOT / "handoff.json").read_bytes())
assert manifest["qualified_head_only"] == HEAD
for pin in manifest["files"]:
    body = (ROOT / pin["path"]).read_bytes()
    assert len(body) == pin["bytes"]
    assert hashlib.sha256(body).hexdigest() == pin["sha256"]
metadata = json.loads((ROOT / "github_metadata.json").read_bytes())
assert metadata["run"]["head_sha"] == HEAD
assert metadata["run"]["status"] == "completed"
assert metadata["run"]["conclusion"] == "success"
assert len(metadata["jobs"]) == 5
assert all(j["conclusion"] == "success" and j["run_id"] == 39137308800 for j in metadata["jobs"])
artifact = next(a for a in metadata["artifacts"] if a["id"] == 12140789098)
archive = (ROOT / "windows/artifact_12140789098.zip").read_bytes()
assert "sha256:" + hashlib.sha256(archive).hexdigest() == artifact["digest"]
assert len(archive) == artifact["size_in_bytes"] == 75136
with zipfile.ZipFile(ROOT / "windows/artifact_12140789098.zip") as zipped:
    assert set(zipped.namelist()) == {"windows-tests.json", "windows-tests.log"}
    for name in zipped.namelist():
        assert zipped.read(name) == (ROOT / "windows" / name).read_bytes()
report = json.loads((ROOT / "windows/windows-tests.json").read_bytes())
assert report["discovered_tests"] == report["tests_run"] == 2476
assert report["successful"] is True
assert all(report[k] == 0 for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
raw = (ROOT / "windows/windows-tests.log").read_bytes()
assert re.search(rb"Ran 2476 tests in 2680\.206s\r\n\r\nOK\r\n\Z", raw)
assert len(re.findall(rb"^test_[^\r\n]+\([^\r\n]+\) \.\.\. ", raw, re.M)) == 2476
for name in ("job_117704346611_batch10_methods.json", "job_117704346747_batch10_methods.json",
             "windows/windows_original_245_methods.json"):
    proof = json.loads((ROOT / name).read_bytes())
    assert proof["head_sha"] == HEAD and proof["new_exact_methods_original_ok"] == 245
    assert proof["strict_test_boundary_blocks_used"] is True
    assert proof["original_log_rewritten"] is False
print(json.dumps({"qualified_head_only": HEAD, "original_pins": len(manifest["files"]),
                  "windows_discovered_and_executed": 2476, "new_methods": 245,
                  "all_five_checks_success": True, "full_specialist_acceptance_count": 0}))
