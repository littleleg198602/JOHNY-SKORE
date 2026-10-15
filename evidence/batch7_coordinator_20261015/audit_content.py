"""Read exact retained originals and protected inputs for batch7; no HTTP."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

ROOT = Path(__file__).resolve().parents[2]
BASE = "089ce4dbe03952d84daac5cfe47760b09020954b"
WITHHELD = {
    "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
}


def pin(path):
    body = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


def load(path):
    return json.loads(path.read_bytes())


def build():
    baseline = load(Path(__file__).with_name("baseline_manifest.json"))
    assert baseline["base_head"] == BASE
    for name, expected in {**baseline["prior_evidence"], **baseline["production_registries_and_canonical_input"]}.items():
        actual = pin(ROOT / name)
        assert (actual["sha256"], actual["bytes"]) == (expected["sha256"], expected["bytes"]), name
    originals = []
    def add(path, digest, size, url, observed, status, *, started=None, cap=4_000_000):
        actual = pin(path)
        assert actual["sha256"] == digest and actual["bytes"] == size and size <= cap
        assert not _has_sensitive_body(path.read_bytes()), actual["path"]
        observed_clock = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        assert observed_clock.utcoffset() is not None
        if started:
            assert datetime.fromisoformat(started.replace("Z", "+00:00")) <= observed_clock
        assert subprocess.check_output(["git", "check-attr", "text", "--", actual["path"]], cwd=ROOT).rstrip().endswith(b": unset")
        originals.append({**actual, "source_url": url, "observed_at": observed,
                          "http_status": status, "whole_body_gate_positive": False})
    folder = ROOT / "evidence/batch7_identity_20261015/captures"
    for path in sorted(folder.glob("*.metadata.json")):
        meta = load(path)
        add(path.with_name(meta["body_path"]), meta["raw_sha256"], meta["bytes"],
            meta["requested_url"], meta["observed_at"], meta["http_status"], started=meta["started_at"])
    folder = ROOT / "evidence/batch7_healthcare_20261015"
    meta = load(folder / "sec_pfe_2017_ex21.metadata.json")
    add(folder / meta["body_path"], meta["body_sha256"], meta["body_bytes"], meta["request_url"],
        meta["observed_at"], meta["http_status"], started=meta["requested_at"])
    for pattern in ("evidence/batch7_regulatory_20261015/raw/*.metadata.json",
                    "evidence/batch7_contracts_20261015/requests/*.metadata.json"):
        for path in sorted(ROOT.glob(pattern)):
            meta = load(path)
            add(ROOT / meta["archive_path"], meta["raw_response_sha256"], meta["raw_response_bytes"],
                meta["requested_url"], meta["observed_at"], meta["http_status"], started=meta["started_at"])
    folder = ROOT / "evidence/batch7_sec_20261015"
    for path in sorted(folder.glob("attempt-*-result.json")):
        meta = load(path)
        add(folder / meta["path"], meta["sha256"], meta["size_bytes"], meta["url"],
            meta["observed_at"], meta["http_status"], started=meta["reserved_at"])
    assert len(originals) == 16 and all(row["http_status"] == 200 for row in originals)
    folder = ROOT / "evidence/batch7_nhtsa_20261015/captures"
    for path in sorted(folder.glob("*/review.json")):
        meta = load(path)
        add(path.with_name("response.json"), meta["raw_response_sha256"], meta["raw_response_bytes"],
            meta["requested_url"], meta["source_observed_at"], meta["http_status"], started=meta["started_at"])
        assert meta["complete_negative"] is False and meta["issuer_wide_absence_verified"] is False
    assert len(originals) == 20 and len({row["path"] for row in originals}) == 20
    names = set(subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT).decode().splitlines())
    files = [ROOT / name for name in sorted(names) if (ROOT / name).is_file()]
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() not in WITHHELD for path in files)
    by_hash = {}
    for path in files:
        by_hash.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), []).append(path.relative_to(ROOT).as_posix())
    checked_pins, historical_pins = [], []
    def verify(name, digest, size=None):
        if not isinstance(name, str) or not name.startswith(("evidence/", "market_checker_app/", "tests/")):
            return
        if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            return
        current = pin(ROOT / name)
        if current["sha256"] == digest:
            assert size is None or size == current["bytes"]
            checked_pins.append({"path": name, "sha256": digest})
        else:
            alternatives = by_hash.get(digest, [])
            assert alternatives, (name, digest, "No preserved exact historical bytes")
            assert size is None or any((ROOT / other).stat().st_size == size for other in alternatives)
            historical_pins.append({"referenced_path": name, "historical_sha256": digest,
                                    "exact_retained_version_paths": alternatives, "current_version": False})
    def walk(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                verify(value["path"], value["sha256"], value.get("bytes", value.get("size_bytes")))
            for key, item in value.items():
                if isinstance(item, str):
                    verify(key, item)
                elif isinstance(item, dict) and "sha256" in item:
                    verify(key, item["sha256"], item.get("bytes", item.get("size_bytes")))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for path in sorted((ROOT / "evidence").glob("batch7*/**/handoff.json")):
        walk(load(path))
    raw_files = [path for path in files if "evidence" in path.parts and (
        "primary" in path.parts or "raw" in path.parts or path.suffix == ".response"
        or path.name == "response.json" or "captures" in path.parts and path.name.endswith(".body.html"))
        and path.suffix.lower() in {".html", ".htm", ".response", ".xml", ".txt", ".pdf", ".json", ".bin"}
        and not path.name.endswith(".metadata.json")]
    assert all(not _has_sensitive_body(path.read_bytes()) for path in raw_files)
    sources = [pin(path) for folder in (ROOT / "market_checker_app", ROOT / "tests")
        for path in sorted(folder.rglob("*.py")) if path.relative_to(ROOT).as_posix() not in names
        or subprocess.check_output(["git", "hash-object", str(path)], cwd=ROOT).decode().strip()
        != subprocess.run(["git", "rev-parse", BASE + ":" + path.relative_to(ROOT).as_posix()], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()]
    from market_checker_app.utils.ticker_universe import load_canonical_tickers
    assert len(load_canonical_tickers()) == 687
    return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(), "base_head": BASE,
        "prior_evidence_files": len(baseline["prior_evidence"]),
        "prior_evidence_bytes": sum(row["bytes"] for row in baseline["prior_evidence"].values()),
        "all_prior_evidence_byte_identical": True, "protected_production_inputs": baseline["production_registries_and_canonical_input"],
        "protected_inputs_byte_identical": True, "canonical_tickers": 687, "verified_adoptions": 0,
        "new_original_http_responses": originals, "new_http_status_counts": dict(Counter(row["http_status"] for row in originals)),
        "new_primary_research_requests": 16, "new_normal_quota_nhtsa_requests": 4,
        "retained_rearchives_are_new_http": False, "all_current_raw_gate_scan_files": len(raw_files),
        "checked_explicit_handoff_file_pins": checked_pins, "historical_handoff_file_versions": historical_pins,
        "pin_audit_scope": "Explicit repository-relative path plus file SHA fields in batch7 handoffs; semantic record hashes remain separate",
        "whole_withheld_hashes_absent_from_all_current_files": sorted(WITHHELD),
        "current_source_test_versions": sources, "hashes_prove_historical_availability": False,
        "source_or_label_history_reconstructed": False, "full_specialist_acceptance": False}


if __name__ == "__main__":
    target = Path(__file__).with_name("content_audit.json")
    result = build()
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"prior_evidence": result["prior_evidence_files"], "new_http": len(result["new_original_http_responses"]),
                      "raw_scan": result["all_current_raw_gate_scan_files"], "code_pins": len(result["current_source_test_versions"])}))
