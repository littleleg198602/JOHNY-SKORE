"""Read-only preservation check for already-qualified public9d CI originals."""
from pathlib import Path
import hashlib
import json
import zipfile


def main():
    root = Path(__file__).resolve().parent
    handoff = json.loads((root / "handoff.json").read_bytes())
    assert handoff["qualified_public_head"] == "9d60cc650ff0d64b720f84fabfe1348e44c6bcf4"
    assert handoff["qualifies_sourcefront21_code"] is False
    for row in handoff["copies"]:
        raw = (root / row["path"]).read_bytes()
        assert len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"], row["path"]
    original = root / "originals"
    run = json.loads((original / "run.json").read_bytes())
    jobs = json.loads((original / "jobs.json").read_bytes())["jobs"]
    meta = json.loads((original / "artifact_metadata.json").read_bytes())
    assert run["head_sha"] == handoff["qualified_public_head"]
    assert run["status"] == "completed" and run["conclusion"] == "success"
    assert len(jobs) == 5 and all(j["conclusion"] == "success" for j in jobs)
    archive = original / "windows/actual.zip"
    raw = archive.read_bytes()
    assert len(raw) == meta["size_in_bytes"]
    assert hashlib.sha256(raw).hexdigest() == meta["digest"].removeprefix("sha256:")
    with zipfile.ZipFile(archive) as stream:
        assert sorted(stream.namelist()) == ["windows-tests.json", "windows-tests.log"]
        assert stream.testzip() is None
        for name in stream.namelist():
            assert stream.read(name) == (original / "windows/originals" / name).read_bytes()
    result = json.loads((original / "windows/originals/windows-tests.json").read_bytes())
    assert result["discovered_tests"] == result["tests_run"] == 2767 and result["successful"] is True
    assert all(result[k] == 0 for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    for name in ("118878044354.exact_methods.json", "118878043754.exact_methods.json", "windows/originals/original_exact_methods.json"):
        proof = json.loads((original / name).read_bytes())
        assert proof["new_exact_methods_original_ok"] == 79 and proof["strict_test_boundary_blocks_used"] is True
    print(json.dumps({"exact_copies": len(handoff["copies"]), "bytes": handoff["copied_bytes"],
        "qualified_public_head": handoff["qualified_public_head"], "qualifies_sourcefront21_code": False,
        "new_downloads_or_unit_test_runs": 0, "full_specialist_acceptance": "0/21"}))


if __name__ == "__main__":
    main()
