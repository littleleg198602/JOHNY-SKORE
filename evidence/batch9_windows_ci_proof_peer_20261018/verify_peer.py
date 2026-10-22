"""Offline peer checks for the exact final-tree integration verifier repair."""
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PUBLISHED = "151a4296202eaa4302f85df6966a84d620d56baf"
PUBLISHED_TREE = "37a24549193d86e7237eacb370c03114978e2117"
VERIFIER = "evidence/batch9_windows_ci_coordinator_20261018/verify_integration.py"
QUALIFICATION = "evidence/batch9_windows_ci_coordinator_20261018/qualification.json"
OLD_QUALIFICATION_SHA256 = "2b58c05d7e66936b8de8e12d4da9aa347b2e550c0992af5e79072b0171b72a1e"
DB = ROOT.parent / "JOHNY-batch4-integration-20261013/outputs/market_checker_history.db"


def sha(body):
    return hashlib.sha256(body).hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def write_once(name, record):
    with (HERE / name).open("x", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(record, sort_keys=True, indent=2) + "\n")


def main():
    assert git("rev-parse", PUBLISHED + "^{tree}").decode().strip() == PUBLISHED_TREE
    old_qualification = (ROOT / QUALIFICATION).read_bytes()
    assert sha(old_qualification) == OLD_QUALIFICATION_SHA256
    assert old_qualification == (HERE / "original_published_qualification.json").read_bytes()
    original_source = git("show", PUBLISHED + ":" + VERIFIER)
    assert original_source == (HERE / "original_verify_integration.py").read_bytes()
    old_diagnostic = json.loads((HERE / "original_failure_diagnostic.json").read_bytes())
    assert old_diagnostic["exit_code"] == 1
    assert len(old_diagnostic["paths_rejected_by_original_whitelist"]) == 21

    # Pin every existing published evidence/production/test/doc byte. Only the
    # explicitly owned verifier is exempt; no checkout normalization is used.
    counts = Counter(); sizes = Counter(); manifest = []
    doc_names = {"PARALLEL_SPECIALIST_WORK.md", "SCOUT_PROGRESS.md", "SPECIALIST_COMPLETION.md",
                 "SPECIALIST_IMPLEMENTATION_CHECKPOINT_20261001.md"}
    for entry in git("ls-tree", "-r", "-z", PUBLISHED).split(b"\0"):
        if not entry:
            continue
        prefix, raw_name = entry.split(b"\t", 1)
        mode, kind, expected_oid = prefix.decode().split()
        name = raw_name.decode()
        category = ("evidence" if name.startswith("evidence/") else
                    "production" if name.startswith("market_checker_app/") else
                    "tests" if name.startswith("tests/") else
                    "workflow_and_four_docs" if name in doc_names or name == ".github/workflows/market-checker-test-agents.yml" else None)
        if category is None or name == VERIFIER:
            continue
        path = ROOT / name
        assert kind == "blob" and mode in {"100644", "100755"} and not path.is_symlink(), name
        body = path.read_bytes()
        assert hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest() == expected_oid, name
        counts[category] += 1; sizes[category] += len(body)
        manifest.append([name, len(body), sha(body), expected_oid])
    db_before = sha(DB.read_bytes())
    assert db_before == "6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af"

    spec = importlib.util.spec_from_file_location("exact_final_tree_verifier_peer", ROOT / VERIFIER)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    actual_git = module.git
    output = io.StringIO()
    negative_checks = []
    with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")) as network, \
         patch("sqlite3.connect", side_effect=AssertionError("No SQL or DB initialization")) as database:
        with redirect_stdout(output):
            module.main()
        repaired = json.loads(output.getvalue())
        for path in (
            "market_checker_app/unapproved.py", "tests/unapproved.py",
            "evidence/batch9_windows_timeout_fix_20261018/unapproved.raw",
            "evidence/batch9_windows_cancelled_20261017/unapproved.raw",
            "evidence/batch9_windows_ci_coordinator_20261018/unapproved.json",
            "evidence/batch9_windows_ci_proof_peer_20261018/unapproved.json",
        ):
            def with_extra_path(*args):
                data = actual_git(*args)
                if args == ("diff", "--name-only", module.BASE):
                    return data + path.encode() + b"\n"
                return data
            module.git = with_extra_path
            rejected = False
            try:
                with redirect_stdout(io.StringIO()):
                    module.main()
            except AssertionError as error:
                assert error.args == ([path],), (path, error.args)
                rejected = True
            finally:
                module.git = actual_git
            assert rejected, path
            negative_checks.append({"synthetic_diff_path": path, "rejected_at_exact_allowlist": True,
                                    "file_created_or_modified": False})
        assert network.call_count == database.call_count == 0
    assert sha(DB.read_bytes()) == db_before
    assert sha((ROOT / QUALIFICATION).read_bytes()) == OLD_QUALIFICATION_SHA256
    assert repaired["unchanged_groups"] == {
        "evidence": {"files": 1535, "bytes": 74512826, "byte_identical": True},
        "production": {"files": 218, "bytes": 3577034, "byte_identical": True},
        "tests": {"files": 188, "bytes": 2440692, "byte_identical": True},
    }
    assert repaired["sole_yaml_semantic_change"] == {"path": ["jobs", "windows-agent", "timeout-minutes"], "before": 40, "after": 60}
    assert repaired["new_head_hosted_qualification"] == "PENDING"
    assert repaired["published_integration_allowlist_snapshot"] == PUBLISHED
    assert repaired["published_integration_allowlist_tree"] == PUBLISHED_TREE
    assert repaired["original_cancelled_artifact_verification"]["Windows_cancelled_not_acceptance"] is True
    assert repaired["retained_DB_hash_read_only_no_SQL_open"] == db_before
    (HERE / "repaired_qualification.json").open("x", encoding="utf-8", newline="\n").write(output.getvalue())
    report = {
        "schema": "batch9_final_tree_verifier_peer_v1", "verified_at": datetime.now(timezone.utc).isoformat(),
        "published_snapshot": PUBLISHED, "published_tree": PUBLISHED_TREE,
        "qualification_phase": "Candidate working tree before the owned repair/proof commit; verifier bytes are pinned separately. The final committed tree must also be verified read-only.",
        "original_verifier_sha256": sha(original_source), "repaired_verifier_sha256": sha((ROOT / VERIFIER).read_bytes()),
        "old_published_qualification_unchanged_sha256": OLD_QUALIFICATION_SHA256,
        "original_repro_exit_code": 1, "legitimate_final_paths_rejected_before_fix": 21,
        "repaired_verifier_exit_equivalent": 0, "negative_unrelated_path_checks": negative_checks,
        "unchanged_published_scope_counts_excluding_owned_verifier": dict(counts),
        "unchanged_published_scope_bytes_excluding_owned_verifier": dict(sizes),
        "unchanged_published_scope_SHA256_manifest_digest": sha(json.dumps(manifest, sort_keys=True).encode()),
        "original_baseline_groups": repaired["unchanged_groups"], "YAML_sole_timeout40_to60_preserved": True,
        "original_cancelled_artifact_verifier_passed": True, "retained_DB_readonly_hash_before_and_after": db_before,
        "SQL_connections": 0, "HTTP_requests": 0, "runtime_or_unittest_suites": 0,
        "new_head_CI_status": "PENDING; run39092974314 targets151 only and cannot qualify this later verifier repair",
        "full_specialist_acceptance": "0/21",
    }
    write_once("peer_qualification.json", report)
    print(json.dumps({"original_repro_exit_code": 1, "rejected_final_paths_before_fix": 21,
        "repaired_verifier_passed": True, "negative_path_checks_passed": len(negative_checks),
        "published_scope_counts": dict(counts), "peer_qualification_sha256": sha((HERE / "peer_qualification.json").read_bytes())}, sort_keys=True))


if __name__ == "__main__":
    main()
