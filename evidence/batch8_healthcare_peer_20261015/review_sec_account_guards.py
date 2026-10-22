"""Bounded read-only actual SEC replay plus separately labelled mutations."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch


def review(source_root):
    source_root = Path(source_root).resolve(strict=True)
    service = source_root / "market_checker_app/services/sec_owner_instrument_scope_review.py"
    producer_sha = hashlib.sha256(service.read_bytes()).hexdigest()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))
    module = importlib.import_module("market_checker_app.services.sec_owner_instrument_scope_review")
    producer = importlib.import_module("evidence.batch8_sec_20261015.build_review")
    if Path(module.__file__).resolve() != service:
        raise ValueError("SEC peer import resolved outside selected source root")
    prior, packet, xml, index, selection, _ = producer.load_inputs()
    args = dict(prior_account_capture=prior, packet_capture=packet, xml_capture=xml,
                index_capture=index, selection=selection)
    clock = datetime.now(timezone.utc)
    checks = {}
    with patch.object(module, "_physical_clock", return_value=clock), \
            patch("socket.socket.connect", side_effect=AssertionError("No source HTTP")), \
            patch("sqlite3.connect", side_effect=AssertionError("No DB")), \
            patch.object(Path, "write_bytes", side_effect=AssertionError("No peer file mutation")), \
            patch.object(Path, "write_text", side_effect=AssertionError("No peer file mutation")):
        actual = module.review_sec_owner_instrument_scope(**args)
        synthetic = (
            ("xml_owner_cik_is_not_issuer_cik", "xml_capture", lambda c: replace(c,
                body=c.body.replace(b"<rptOwnerCik>0001513142</rptOwnerCik>", b"<rptOwnerCik>0000789019</rptOwnerCik>"))),
            ("xml_account_name_is_not_signature_alias", "xml_capture", lambda c: replace(c,
                body=c.body.replace(b"Nadella Satya", b"Satya Nadella"))),
            ("xml_report_period_must_match_index_and_selection", "xml_capture", lambda c: replace(c,
                body=c.body.replace(b"<periodOfReport>2026-08-31</periodOfReport>", b"<periodOfReport>2026-08-30</periodOfReport>"))),
            ("current_routing_cik_is_bound", "xml_capture", lambda c: replace(c,
                source_url=c.source_url.replace("/data/789019/", "/data/1513142/"))),
            ("current_capture_future_is_unavailable", "xml_capture", lambda c: replace(c,
                observed_at=clock + timedelta(seconds=1), known_at=clock + timedelta(seconds=1))),
        )
        for label, key, mutate in synthetic:
            source = mutate(args[key])
            if source == args[key]:
                raise ValueError("Selected original scope changed; synthetic mutation no longer applies")
            if source.body is not None:
                source = replace(source, sha256=hashlib.sha256(source.body).hexdigest(), size_bytes=len(source.body))
            try:
                out = module.review_sec_owner_instrument_scope(**dict(args, **{key: source}))
                checks[label] = {"input_classification": "SYNTHETIC_MUTATION_OF_ORIGINAL_BYTES",
                                "source_scope_supported": out["source_only_xml_scope_supported"], "gaps": out["gaps"]}
                if out["source_only_xml_scope_supported"]:
                    raise AssertionError("Synthetic conflict unexpectedly passed source scope")
            except ValueError as error:
                checks[label] = {"input_classification": "SYNTHETIC_MUTATION_OF_ORIGINAL_BYTES",
                                "rejected": True, "error": str(error)}
        past = module.review_sec_owner_instrument_scope(**args, knowledge_at=clock - timedelta(microseconds=1))
    false_flags = ("natural_person_identity_verified", "person_cik_binding_verified", "approved_name_alias",
                   "instrument_identity_verified", "cusip_class_validity_verified", "role_continuity_verified",
                   "account_continuity_verified", "execution_verified", "issuer_attribution_allowed",
                   "historical_machine_availability_inferred", "registry_modified", "trading_allowed")
    if any(actual[key] for key in false_flags):
        raise AssertionError("Actual account review escaped source-only scope")
    if packet.body is not None or packet.sha256 is not None or packet.size_bytes is not None:
        raise AssertionError("Unavailable original packet was reconstructed")
    if hashlib.sha256(service.read_bytes()).hexdigest() != producer_sha:
        raise ValueError("SEC owner changed reviewer during peer diagnostic")
    return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_producer_sha256": producer_sha,
            "actual_original_source_review": {key: actual.get(key) for key in
                ("source_only_xml_scope_supported", "complete_primary_packet_scope_supported",
                 "current_header_corroborated", "dated_reporting_account_match_supported", "source_account",
                 "as_filed_role", "as_filed_signatures", "source_row_count", "gaps", *false_flags)},
            "original_capture_pins": {key: source.summary() for key, source in
                (("prior_account", prior), ("packet", packet), ("xml", xml), ("index", index))},
            "synthetic_guard_checks": checks,
            "past_cutoff": {"source_scope_supported": past["source_only_xml_scope_supported"], "gaps": past["gaps"]},
            "actual_source_http_requests": 0, "database_calls": 0, "owner_files_written": False,
            "registry_adoptions": 0, "q3_instrument_acceptance": 0,
            "natural_person_acceptance": False, "full_specialist_acceptance": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    args = parser.parse_args()
    print(json.dumps(review(args.source_root), ensure_ascii=False, indent=2))
