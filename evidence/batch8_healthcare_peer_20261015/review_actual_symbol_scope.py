"""Read-only replay of the six actual symbol-lane originals, no promotion."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch


def review(source_root):
    source_root = Path(source_root).resolve(strict=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))
    module = importlib.import_module("market_checker_app.services.sec_symbol_action_scope_review")
    producer = importlib.import_module("evidence.batch8_identity_20261015.replay_symbol_scope")
    service = source_root / "market_checker_app/services/sec_symbol_action_scope_review.py"
    if Path(module.__file__).resolve() != service:
        raise ValueError("Selected actual reviewer resolved outside requested source root")
    digest = hashlib.sha256(service.read_bytes()).hexdigest()
    with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), \
            patch("sqlite3.connect", side_effect=AssertionError("No database")), \
            patch.object(Path, "write_text", side_effect=AssertionError("No owner write")), \
            patch.object(Path, "write_bytes", side_effect=AssertionError("No owner write")):
        request, captures, _ = producer.build_inputs()
        result = module.review_symbol_action_scope(request, captures)
    if hashlib.sha256(service.read_bytes()).hexdigest() != digest:
        raise ValueError("Peer source changed during actual review")
    if result["new_verified_identities"] != 0 or result["current_q3_verified_coverage"] != 0:
        raise AssertionError("Actual partial source review claimed identities/current Q3")
    return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "input_classification": "ACTUAL_RETAINED_ORIGINAL_SOURCES",
            "reviewed_producer_sha256": digest, "selected_complete_original_bodies": len(captures),
            "actual_result": {key: result[key] for key in ("status", "effective_on", "supported_effective_symbol_days",
                "independent_effective_disclosures", "blockers", "new_verified_identities", "current_q3_verified_coverage",
                "automatic_alias_allowed", "interval_extension_allowed", "production_registry_changed",
                "historical_machine_availability_inferred", "historical_as_filed_versions_verified", "capture_pins")},
            "publisher_size_conflicts": {key: value.get("publisher_size_conflict") for key, value in result["filings"].items()},
            "source_http_requests": 0, "database_calls": 0, "owner_files_written": False,
            "historical_instrument_acceptance": False, "full_specialist_acceptance": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    args = parser.parse_args()
    print(json.dumps(review(args.source_root), ensure_ascii=False, indent=2))
