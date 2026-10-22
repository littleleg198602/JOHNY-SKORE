"""Offline exact-tree proof for the sole Linux job-timeout 10 -> 20 change."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = "b24a59563d8284daf8d38df20568fc76d4f03b60"
BASE_TREE = "9e03573df7cfd65c2f878bc2ad65348851cc238e"
WORKFLOW = ".github/workflows/market-checker-test-agents.yml"
OWNED = "evidence/batch11_linux_timeout_fix_20261020/"
COMMENT = (b"    # Run39391828283 Linux was cancelled after about 10 minutes, mid-suite\n"
    b"    # without a final JSON/footer. A timeout is inferred from that boundary;\n"
    b"    # no explicit timeout annotation was observed. Allow bounded setup and\n"
    b"    # full-suite headroom while retaining every test budget and release gate.\n")
CRLF_FILES = {"Nainstalovat_Patraci_Agenty.bat", "Nainstalovat_Tydenni_Shadow.bat", "Pripravit_SEC_Registry.bat",
    "Spustit_Market_Checker.bat", "Spustit_Patraci_Agenty.bat", "Spustit_Tydenni_Shadow.bat", "Vyhodnotit_Predikce.bat",
    "Vyhodnotit_Specialisty.bat", "Zkontrolovat_Zdravotnicke_Identity.bat"}


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True).stdout


def pin(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class WorkflowLoader(yaml.SafeLoader):
    """Keep GitHub's 'on' string key; reject duplicate/ambiguous mapping keys."""


WorkflowLoader.yaml_implicit_resolvers = {key: [(tag, regex) for tag, regex in values
    if tag != "tag:yaml.org,2002:bool"] for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()}
WorkflowLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))


def mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("Workflow requires unique string mapping keys")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def differences(before, after, path=()):
    if type(before) is not type(after):
        return [{"path": list(path), "before": before, "after": after}]
    if isinstance(before, dict):
        if set(before) != set(after):
            return [{"path": list(path), "before_keys": sorted(before), "after_keys": sorted(after)}]
        return [item for key in sorted(before) for item in differences(before[key], after[key], (*path, key))]
    if isinstance(before, list):
        if len(before) != len(after):
            return [{"path": list(path), "before_length": len(before), "after_length": len(after)}]
        return [item for index, (left, right) in enumerate(zip(before, after)) for item in differences(left, right, (*path, index))]
    return [] if before == after else [{"path": list(path), "before": before, "after": after}]


def verify():
    assert git("rev-parse", BASE + "^{tree}").decode().strip() == BASE_TREE
    assert git("show", "-s", "--format=%P", BASE).decode().split() == ["6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"]
    original = git("show", BASE + ":" + WORKFLOW)
    revised = (REPO / WORKFLOW).read_bytes()
    assert (HERE / "original_workflow.yml").read_bytes() == original
    assert (HERE / "revised_workflow.yml").read_bytes() == revised
    assert original.count(b"    timeout-minutes: 10\n") == 1
    assert revised == original.replace(b"    timeout-minutes: 10\n", COMMENT + b"    timeout-minutes: 20\n", 1)
    before = yaml.load(original, Loader=WorkflowLoader)
    after = yaml.load(revised, Loader=WorkflowLoader)
    assert "on" in before and True not in before
    delta = differences(before, after)
    assert delta == [{"path": ["jobs", "contract-agent", "timeout-minutes"], "before": 10, "after": 20}]
    expected = deepcopy(before)
    expected["jobs"]["contract-agent"]["timeout-minutes"] = 20
    assert expected == after
    assert before["jobs"]["contract-agent"]["runs-on"] == after["jobs"]["contract-agent"]["runs-on"] == "ubuntu-latest"
    assert before["jobs"]["windows-agent"] == after["jobs"]["windows-agent"]
    assert after["jobs"]["windows-agent"]["timeout-minutes"] == 60
    assert before["jobs"]["release-agent"] == after["jobs"]["release-agent"]
    job_proofs = {}
    for name, job in before["jobs"].items():
        left, right = deepcopy(job), deepcopy(after["jobs"][name])
        if name == "contract-agent":
            left.pop("timeout-minutes"); right.pop("timeout-minutes")
        assert left == right
        job_proofs[name] = {"all_semantics_except_authorized_linux_timeout_identical": True,
            "normalized_semantic_sha256": canonical(left), "steps_sha256": canonical(job["steps"])}
    paths = git("diff", "--name-only", BASE, "--").decode().splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    assert all(path == WORKFLOW or path.startswith(OWNED) for path in paths), paths
    assert all(not path or path.startswith(OWNED) for path in untracked), untracked
    groups = {name: {"files": 0, "bytes": 0, "pins": {}} for name in ("production", "tests", "evidence", "other")}
    filtered = []
    for entry in git("ls-tree", "-r", "-z", BASE).split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split(); relative = name.decode()
        assert kind == "blob" and mode in {"100644", "100755"}
        if relative == WORKFLOW:
            continue
        body = (REPO / relative).read_bytes()
        raw_blob = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        if raw_blob != blob:
            assert relative in CRLF_FILES, relative
            attributes = git("check-attr", "-z", "text", "eol", "--", relative).split(b"\0")
            assert attributes == [name, b"text", b"set", name, b"eol", b"crlf", b""]
            assert body == git("cat-file", "--filters", "--path=" + relative, BASE + ":" + relative), relative
            filtered.append(relative)
        group = "production" if relative.startswith("market_checker_app/") else "tests" if relative.startswith("tests/") else "evidence" if relative.startswith("evidence/") else "other"
        groups[group]["files"] += 1; groups[group]["bytes"] += len(body)
        groups[group]["pins"][relative] = {"git_blob": blob, **pin(body)}
    assert set(filtered) == CRLF_FILES
    for value in groups.values():
        value["exact_physical_file_pin_manifest_sha256"] = canonical(value.pop("pins"))
    return {"verified_at": datetime.now(timezone.utc).isoformat(), "scope": "OFFLINE_CI_TIMEOUT_SEMANTIC_AND_BYTE_EQUIVALENCE",
        "base_commit": BASE, "base_tree": BASE_TREE, "original_workflow": pin(original), "revised_workflow": pin(revised),
        "parsed_semantic_changes": delta, "exact_bytes_changed_only_comment_and_one_timeout": True,
        "all_parsed_semantics_outside_one_key_equal": True, "jobs": job_proofs,
        "windows_timeout_unchanged": 60, "unchanged_baseline_groups": groups,
        "all_prior_production_test_evidence_match_raw_git_blobs": True,
        "whole_configured_CRLF_checkout_files": sorted(filtered), "working_files_normalized_or_rewritten": 0,
        "PyYAML_version": yaml.__version__, "PyYAML_module": pin(Path(yaml.__file__).read_bytes()),
        "verifier": pin(Path(__file__).read_bytes()), "full_suites_rerun": 0, "test_filter_or_assertion_changes": 0,
        "source_HTTP_requests": 0, "SQL_opened": False, "production_DB_access": False,
        "Windows_job_actions": 0, "GitHub_writes": 0, "new_head_hosted_CI_acceptance": False}


if __name__ == "__main__":
    report = verify()
    with (HERE / "qualification.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
