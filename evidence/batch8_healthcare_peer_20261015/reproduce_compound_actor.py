"""Independent synthetic actor/action binding repro; no source HTTP or writes.

Run with an explicit reviewed repository root. The owner retains the old
producer and fixture snapshots; this diagnostic records the actual tested hash
and never calls the synthetic paragraphs primary/live acceptance evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch


BUG_PRODUCER_SHA256 = "8a24a55fdc647c9692d8ded0d255797f502584365831e20b134c980717d45dd7"
PASSAGE = ("On 2026-04-01, Everpure, Inc. changed its logo. "
           "Different Company changed its ticker symbol from PSTG to P.")


def digest(path):
    with path.open("rb") as stream:
        raw = stream.read(4_000_001)
    if not 0 < len(raw) <= 4_000_000:
        raise ValueError("Bounded explicit peer source required")
    return hashlib.sha256(raw).hexdigest()


def _load_snapshot(name, path):
    parent, attribute = name.rsplit(".", 1)
    package = importlib.import_module(parent)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    setattr(package, attribute, module)
    spec.loader.exec_module(module)
    return module


def reproduce(source_root, expected_producer_sha=None, producer_snapshot=None, fixture_snapshot=None):
    source_root = Path(source_root).resolve(strict=True)
    service = source_root / (producer_snapshot or "market_checker_app/services/sec_symbol_action_scope_review.py")
    fixture_path = source_root / (fixture_snapshot or "tests/test_batch8_symbol_action_scope.py")
    for path in (service, fixture_path):
        if not path.resolve(strict=True).is_relative_to(source_root) or path.suffix != ".py":
            raise ValueError("Select an explicit retained source version inside reviewed repository")
    producer_sha, fixture_sha = digest(service), digest(fixture_path)
    if expected_producer_sha is not None and producer_sha != expected_producer_sha:
        raise ValueError("Peer producer changed; do not assign old findings to a new hash")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))
    module = (_load_snapshot("market_checker_app.services.sec_symbol_action_scope_review", service)
              if producer_snapshot else importlib.import_module("market_checker_app.services.sec_symbol_action_scope_review"))
    fixture = (_load_snapshot("tests.test_batch8_symbol_action_scope", fixture_path)
               if fixture_snapshot else importlib.import_module("tests.test_batch8_symbol_action_scope"))
    if Path(module.__file__).resolve() != service or Path(fixture.__file__).resolve() != fixture_path:
        raise ValueError("Peer source import resolved to a different repository")
    request, captures = fixture.fixture()
    if len(request["action_claims"]) != 3:
        raise ValueError("Original synthetic fixture contract changed")
    continuity = PASSAGE + f" CUSIP 74624M102 remained unchanged for its {fixture.CLASS}."
    with patch.object(module, "_physical_clock", return_value=fixture.NOW), \
            patch("socket.socket.connect", side_effect=AssertionError("No HTTP in peer repro")), \
            patch("sqlite3.connect", side_effect=AssertionError("No DB in peer repro")), \
            patch.object(Path, "write_bytes", side_effect=AssertionError("No peer file mutation")), \
            patch.object(Path, "write_text", side_effect=AssertionError("No peer file mutation")):
        control = module.review_symbol_action_scope(request, captures)
        fixture.replace_body(captures, "first", fixture.html(f"<p>{PASSAGE}</p><p>{continuity}</p>"))
        fixture.replace_body(captures, "second", fixture.html(f"<p>{PASSAGE}</p>"))
        for claim in request["action_claims"]:
            index = 1 if claim["event_kind"] == "CUSIP_CONTINUITY" else 0
            claim["selection"] = module.symbol_action_passages(captures[claim["source_key"]][1])[index]
        report = module.review_symbol_action_scope(request, captures)
    if digest(service) != producer_sha or digest(fixture_path) != fixture_sha:
        raise ValueError("Owner changed peer inputs while the diagnostic was running")
    false_ready = (report["status"] == "PRIMARY_SCOPE_READY_FOR_EXPLICIT_REVIEW"
                   and not report["blockers"] and report["effective_on"] == "2026-04-01")
    for key in ("new_verified_identities", "current_q3_verified_coverage", "network_requests_performed"):
        if report[key] != 0:
            raise ValueError("Synthetic diagnostic unexpectedly claimed production activity")
    return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "input_classification": "SYNTHETIC_CONTRACT_MUTATION",
            "live_primary_acceptance": False, "full_specialist_acceptance": False,
            "reviewed_producer_sha256": producer_sha, "reviewed_fixture_sha256": fixture_sha,
            "reviewed_producer_path": service.relative_to(source_root).as_posix(),
            "reviewed_fixture_path": fixture_path.relative_to(source_root).as_posix(),
            "original_bug_producer_sha256": BUG_PRODUCER_SHA256,
            "same_as_original_bug_producer": producer_sha == BUG_PRODUCER_SHA256,
            "synthetic_review_clock": fixture.NOW.isoformat(),
            "synthetic_control_status": control["status"],
            "synthetic_control_blockers": control["blockers"],
            "trigger_passage": PASSAGE, "continuity_passage": continuity,
            "mutation": "Replace first source p0 and second source p0 with trigger; first source p1 with continuity passage; recompute entire body hashes and whole-paragraph selections with producer fixture helpers",
            "expected": "Issuer logo action cannot supply issuer/date for a different company's ticker action",
            "false_ready_trigger_present": false_ready,
            "actual_status": report["status"], "actual_effective_on": report["effective_on"],
            "actual_blockers": report["blockers"],
            "action_results": [{key: item[key] for key in
                                ("source_key", "event_kind", "effective_action_supported", "effective_on", "blockers")}
                               for item in report["action_claims"]],
            "new_verified_identities": report["new_verified_identities"],
            "current_q3_verified_coverage": report["current_q3_verified_coverage"],
            "automatic_alias_allowed": report["automatic_alias_allowed"],
            "production_registry_changed": report["production_registry_changed"],
            "source_http_requests": 0, "database_calls": 0, "owner_files_written": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--expected-producer-sha")
    parser.add_argument("--producer-snapshot")
    parser.add_argument("--fixture-snapshot")
    args = parser.parse_args()
    print(json.dumps(reproduce(args.source_root, args.expected_producer_sha, args.producer_snapshot,
                               args.fixture_snapshot), ensure_ascii=False, indent=2))
