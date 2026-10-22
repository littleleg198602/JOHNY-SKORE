"""Audit the fixture-only repair against the exact published failed version."""
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "bfedb764da3a189df1d9f8eb799cf834867f8083"
TEST = "tests/test_batch7_source_manifest.py"
WITHHELD = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def assertions(body):
    tree = ast.parse(body)
    return {node.name: Counter(ast.dump(call, include_attributes=False)
        for call in ast.walk(node) if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute) and call.func.attr.startswith("assert"))
        for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        and node.name.startswith("test_")}


def build():
    old_tree = git("ls-tree", "-r", "-z", BASE)
    prior_evidence = []
    production = []
    for row in old_tree.split(b"\0"):
        if not row:
            continue
        fields, raw_name = row.split(b"\t", 1)
        mode, kind, raw_sha = fields.split()
        name = raw_name.decode()
        if not name.startswith(("evidence/", "market_checker_app/")):
            continue
        assert kind == b"blob"
        body = (ROOT / name).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest() == raw_sha.decode(), name
        pin = {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        (prior_evidence if name.startswith("evidence/") else production).append(pin)
    changed_tests = [name for name in git("diff", "--name-only", BASE, "--", "tests").decode().splitlines()]
    assert changed_tests == [TEST], changed_tests
    old = git("show", BASE + ":" + TEST)
    new = (ROOT / TEST).read_bytes()
    before, after = assertions(old), assertions(new)
    assert set(before) <= set(after)
    for name, calls in before.items():
        assert not calls - after[name], (name, "An original assertion was removed or changed")
    assert new != old
    files = {name for name in git("ls-files", "-co", "--exclude-standard").decode().splitlines()}
    by_hash = {}
    for name in files:
        path = ROOT / name
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest not in WITHHELD, name
            by_hash.setdefault(digest, []).append(name)
    old_sha = hashlib.sha256(old).hexdigest()
    assert old_sha in by_hash, "The original failed fixture source must remain exactly preserved"
    status_path = ROOT / "market_checker_app/data/specialist_status.json"
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(), "base_public_head": BASE,
        "base_public_tree": git("rev-parse", BASE + "^{tree}").decode().strip(),
        "prior_evidence_files": len(prior_evidence),
        "prior_evidence_bytes": sum(row["bytes"] for row in prior_evidence),
        "all_prior_published_evidence_byte_identical": True,
        "all_production_files_byte_identical": True, "production_files": len(production),
        "specialist_status_sha256": hashlib.sha256(status_path.read_bytes()).hexdigest(),
        "changed_test_files": changed_tests, "original_test_methods": len(before),
        "current_test_methods": len(after), "all_original_assertions_preserved": True,
        "original_assertion_calls": sum(sum(c.values()) for c in before.values()),
        "new_test_methods": sorted(set(after) - set(before)),
        "failed_fixture_sha256": old_sha,
        "failed_fixture_exact_retained_paths": by_hash[old_sha],
        "repaired_fixture_sha256": hashlib.sha256(new).hexdigest(),
        "whole_withheld_body_hashes_absent": sorted(WITHHELD),
        "new_source_http_requests": 0, "quota_db_reset_or_reconstruction": False,
        "model_score_budget_changes": False, "full_specialist_acceptance": False,
        "original_published_evidence_pins": prior_evidence,
    }


if __name__ == "__main__":
    result = build()
    with Path(__file__).with_name("content_audit.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "original_published_evidence_pins"}))
