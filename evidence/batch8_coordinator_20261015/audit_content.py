"""Read-only byte audit of the bounded batch8 integration; no source or DB I/O."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BASE = "e252ec62796b0b02e733c55ebef713e0f29d4b44"
CODE = "cc769523e088e7781226b47622934cfe208ee6a7"
HERE = Path(__file__).resolve().parent


def pin(path):
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def main():
    baseline = json.loads((HERE / "baseline_manifest.json").read_text())
    old = baseline["prior_evidence"]
    protected = baseline["production_registries_and_canonical_input"]
    unchanged = {}
    for group, entries in (("prior_evidence", old), ("protected_production_inputs", protected)):
        mismatches = []
        for name, expected in entries.items():
            actual = pin(ROOT / name)
            if any(actual[key] != expected[key] for key in ("bytes", "sha256")):
                mismatches.append(name)
        assert not mismatches, (group, mismatches)
        unchanged[group] = {"files": len(entries), "bytes": sum(x["bytes"] for x in entries.values()), "mismatches": mismatches}

    changed = git("diff", "--name-only", BASE, "--", "market_checker_app", "tests").splitlines()
    current_sources = {p: pin(ROOT / p) for p in changed if p.endswith(".py")}
    for name, expected in current_sources.items():
        original = subprocess.check_output(["git", "show", f"{CODE}:{name}"], cwd=ROOT)
        assert hashlib.sha256(original).hexdigest() == expected["sha256"], name

    # Index only the publishable checkout, never private ancestry or excluded bodies.
    inventory = {}
    names = set(git("ls-files", "--", "evidence", "market_checker_app", "tests").splitlines())
    names.update(git("ls-files", "--others", "--exclude-standard", "--", "evidence").splitlines())
    for name in sorted(names):
        path = ROOT / name
        if path.is_file():
            inventory.setdefault(pin(path)["sha256"], []).append(name)

    handoffs = sorted((ROOT / "evidence").glob("batch8*/handoff.json"))
    handoffs += sorted((ROOT / "evidence").glob("batch8*/qualification_v1/handoff.json"))
    checks = []
    seen = set()

    def check(owner, location, name, expected):
        if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            return
        if "://" in name or name.startswith("/"):
            return
        key = (str(owner), location, name, expected)
        if key in seen:
            return
        seen.add(key)
        candidates = [ROOT / name, owner.parent / name]
        actual = next((p for p in candidates if p.is_file() and pin(p)["sha256"] == expected), None)
        if actual:
            checks.append({"handoff": str(owner.relative_to(ROOT)), "at": location, "path": name, "sha256": expected, "resolution": "CURRENT_EXACT", "resolved_path": str(actual.relative_to(ROOT))})
        elif expected in inventory:
            checks.append({"handoff": str(owner.relative_to(ROOT)), "at": location, "path": name, "sha256": expected, "resolution": "PRESERVED_VERSION_EXACT", "resolved_paths": inventory[expected]})
        else:
            # A lane's unchanged_shared_files pins its public starting version,
            # before the coordinator integrated other lanes. Verify those exact
            # public Git bytes without reclassifying them as current sources.
            public_bytes = None
            if name.startswith(("market_checker_app/", "tests/")):
                public = subprocess.run(["git", "show", f"{BASE}:{name}"], cwd=ROOT, capture_output=True)
                if public.returncode == 0:
                    public_bytes = public.stdout
            if public_bytes is None or hashlib.sha256(public_bytes).hexdigest() != expected:
                raise AssertionError((str(owner), location, name, expected))
            checks.append({"handoff": str(owner.relative_to(ROOT)), "at": location, "path": name, "sha256": expected, "resolution": "PUBLIC_BASE_VERSION_EXACT", "public_commit": BASE, "bytes": len(public_bytes)})

    def walk(owner, node, location="$"):
        if isinstance(node, dict):
            if "path" in node and "sha256" in node:
                check(owner, location, node["path"], node["sha256"])
            for key, value in node.items():
                if isinstance(value, str) and ("/" in key or key.endswith((".py", ".json", ".log", ".md", ".source"))):
                    check(owner, location, key, value)
                if isinstance(value, dict) and "sha256" in value and ("/" in key or key.endswith((".py", ".json", ".log", ".md", ".source"))):
                    check(owner, location, key, value["sha256"])
                walk(owner, value, location + "." + key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(owner, value, f"{location}[{index}]")

    for owner in handoffs:
        walk(owner, json.loads(owner.read_text()))
    result = json.loads((ROOT / "evidence/batch8_linux_20261016.json").read_text())
    assert result["successful"] and result["discovered_tests"] == result["tests_run"] == 2036
    assert all(result[key] == 0 for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
    console = ROOT / "evidence/batch8_linux_console_20261016.log"
    assert console.read_text().rstrip().endswith("\nOK")
    assert "Ran 2036 tests in 223.039s" in console.read_text()
    peer_path = ROOT / "evidence/batch8_regulatory_peer_20261016/final_integrated_snapshot.json"
    peer = json.loads(peer_path.read_text())
    assert peer["status"] == "PASS"
    output = {
        "schema_version": 1, "audited_at": datetime.now(timezone.utc).isoformat(),
        "public_base": BASE, "tested_code_snapshot": CODE, "source_bytes_still_equal_tested_snapshot": True,
        "unchanged": unchanged, "current_source_and_test_pins": current_sources,
        "handoff_pins_checked": len(checks), "handoff_pin_resolutions": checks,
        "handoffs": {str(p.relative_to(ROOT)): pin(p) for p in handoffs},
        "local_fullsuite": result, "local_fullsuite_console": pin(console),
        "public_original_and_whole_withheld_peer_audit": {"path": str(peer_path.relative_to(ROOT)), **pin(peer_path), "status": peer["status"]},
        "source_or_database_network_actions_by_this_audit": 0,
        "prior_qualification_versions_rewritten": False,
        "full_specialist_acceptance": 0, "hosted_current_batch_qualification": "PENDING_NEW_PUBLIC_HEAD",
        "limits": ["Hash equality does not prove historical availability, source truth, identity adoption, Windows e2e, coverage or OOS.", "Historical source hashes are matched to preserved whole producer files, not relabelled as current code."],
    }
    path = HERE / "content_audit.json"
    assert not path.exists(), "Audit output is immutable; use a new output filename for a later audit."
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "checked_handoff_pins": len(checks), "current_sources": len(current_sources), "unchanged": unchanged, "output": pin(path)}))


if __name__ == "__main__":
    main()
