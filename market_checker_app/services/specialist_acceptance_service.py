"""Portable runtime diagnostics; exporting facts is not accepting a specialist."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Mapping, Sequence

from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.services.fdic_bank_scout_service import (
    DEFAULT_IDENTITIES, FDIC_REFRESH_DAYS, fdic_coverage, load_verified_banks,
)
from market_checker_app.services.specialist_status_service import load_specialist_status
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256, load_canonical_ticker_records,
)


SOURCE_NAMES = ("sec", "fred", "eia", "usaspending", "recipient_discovery", "fda",
                "finra", "fdic", "sec13f", "nhtsa", "cms", "clinicaltrials", "ofac", "ofac_non_sdn", "doj", "epa")

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPLETION_EVIDENCE_FIELDS = {
    "identity_evidence": "dated_issuer_instrument_product_identity_verified",
    "positive_live_evidence": "positive_live_case_verified",
    "negative_live_evidence": "negative_live_case_verified",
    "windows_run_evidence": "actual_windows_end_to_end_verified",
    "coverage_evidence": "measured_applicable_coverage_verified",
    "historical_evaluation_evidence": "out_of_sample_evaluation_verified",
}
SOURCE_STATUS_CLASSES = {
    "NEVER_RUN": "NOT_ATTEMPTED",
    "WAIT_ACCESS": "NOT_ATTEMPTED",
    "WAIT_IDENTITY": "NOT_ATTEMPTED",
    "OK": "COMPLETE",
    "NO_DUE_WORK": "COMPLETE",
    "SAMPLED": "BOUNDED_PARTIAL",
    "PARTIAL": "PARTIAL",
    "ACCESS_BLOCKED": "BLOCKED",
    "RATE_LIMITED": "BLOCKED",
    "LEASE_LOST": "BLOCKED",
    "ERROR": "FAILED",
}
OUTPUT_COUNT_FIELDS = (
    "new_findings", "saved_rows", "matched_rows", "usable_banks",
    "processed", "new_candidates",
)


def classify_source_run(summary: Mapping[str, object]) -> dict:
    """Normalize persisted source status without promoting it to acceptance."""
    status = summary.get("status")
    status_class = SOURCE_STATUS_CLASSES.get(status, "UNKNOWN")
    attempted = status_class not in {"NOT_ATTEMPTED", "UNKNOWN"}
    positive_output = any(
        isinstance(summary.get(field), (int, float))
        and not isinstance(summary.get(field), bool)
        and summary[field] > 0
        for field in OUTPUT_COUNT_FIELDS
    )
    result_usable = (
        status_class == "COMPLETE"
        or status_class == "BOUNDED_PARTIAL"
        or (status_class == "PARTIAL" and positive_output)
    )
    return {
        "status": status,
        "classification": status_class,
        "attempted": attempted,
        "result_usable": result_usable,
        "complete": status_class == "COMPLETE",
        "partial": status_class in {"PARTIAL", "BOUNDED_PARTIAL"},
        "blocked": status_class == "BLOCKED",
        "failed": status_class == "FAILED",
        "positive_output_recorded": positive_output,
        "unknown_status": status_class == "UNKNOWN",
        "acceptance_proven": False,
    }


def _read_evidence(reference: object, repository_root: Path) -> tuple[dict | None, dict]:
    result = {"reference": reference if isinstance(reference, str) else None,
              "content_verified": False, "issues": []}
    if not isinstance(reference, str) or not reference.startswith("evidence/"):
        result["issues"].append("INVALID_REFERENCE")
        return None, result
    path = (repository_root / reference).resolve()
    evidence_root = (repository_root / "evidence").resolve()
    if path.parent != evidence_root or path.suffix.lower() != ".json":
        result["issues"].append("OUTSIDE_EVIDENCE_ROOT")
        return None, result
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        result["issues"].append("MISSING_FILE")
        return None, result
    except (UnicodeDecodeError, json.JSONDecodeError):
        result["issues"].append("INVALID_JSON")
        return None, result
    if not isinstance(payload, dict):
        result["issues"].append("JSON_ROOT_NOT_OBJECT")
        return None, result
    if not isinstance(payload.get("schema_version"), int) or payload["schema_version"] < 1:
        result["issues"].append("UNSUPPORTED_SCHEMA")
        return payload, result
    result.update({"content_verified": True, "size_bytes": len(raw),
                   "sha256": hashlib.sha256(raw).hexdigest()})
    return payload, result


def audit_specialist_evidence(specialists: Sequence[dict], *,
                              repository_root: Path = REPOSITORY_ROOT) -> dict:
    """Read referenced JSON and fail closed on any claimed completion.

    A path or a non-empty file is deliberately insufficient. DONE/VERIFIED
    claims need six separate evidence documents whose content opts into the
    exact acceptance assertion under an acceptance object.
    """
    references: dict[str, dict] = {}
    completion_claims = []
    for row in specialists:
        specialist_id = str(row.get("id", ""))
        for key, reference in row.items():
            if not key.endswith("_evidence") or not isinstance(reference, str):
                continue
            _payload, result = _read_evidence(reference, repository_root)
            references.setdefault(reference, result)
        if row.get("code") != "DONE" and row.get("live") != "VERIFIED":
            continue
        checks = {}
        for field, assertion in COMPLETION_EVIDENCE_FIELDS.items():
            payload, result = _read_evidence(row.get(field), repository_root)
            accepted = bool(
                result["content_verified"] and isinstance(payload, dict)
                and isinstance(payload.get("acceptance"), dict)
                and payload["acceptance"].get(assertion) is True
            )
            checks[field] = {
                "reference": result["reference"],
                "content_verified": result["content_verified"],
                "assertion": assertion,
                "assertion_verified": accepted,
                "issues": result["issues"] + ([] if accepted else ["ASSERTION_NOT_VERIFIED"]),
            }
        completion_claims.append({
            "specialist_id": specialist_id,
            "state_claim_valid": row.get("code") == "DONE" and row.get("live") == "VERIFIED",
            "evidence_verified": all(check["assertion_verified"] for check in checks.values()),
            "checks": checks,
        })
    invalid_references = sorted(
        reference for reference, result in references.items() if not result["content_verified"])
    return {
        "referenced_files": len(references),
        "content_verified_files": len(references) - len(invalid_references),
        "invalid_references": invalid_references,
        "references": references,
        "completion_claims": completion_claims,
        "all_completion_claims_verified": bool(completion_claims) and all(
            claim["state_claim_valid"] and claim["evidence_verified"]
            for claim in completion_claims),
    }


def build_specialist_acceptance_report(store: ScoutStore, *, as_of: datetime | None = None,
                                      environment: Mapping[str, str] | None = None) -> dict:
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Acceptance diagnostic time needs timezone")
    records = load_canonical_ticker_records()
    universe = {row["ticker"] for row in records}
    profiles = load_research_profiles()
    inventory = load_specialist_status()
    runs = store.latest_source_runs()
    source_facts = {name: runs.get(name, {"status": "NEVER_RUN"}) for name in SOURCE_NAMES}
    source_semantics = {name: classify_source_run(source_facts[name]) for name in SOURCE_NAMES}
    source_semantic_counts = {
        key: sum(bool(row[key]) for row in source_semantics.values())
        for key in ("attempted", "result_usable", "complete", "partial",
                    "blocked", "failed", "unknown_status")
    }
    env = os.environ if environment is None else environment
    access_keys = {
        "sec_contact": ("JOHNY_SKORE_SEC_USER_AGENT",),
        "fred": ("JOHNY_SKORE_FRED_API_KEY",),
        "eia": ("JOHNY_SKORE_EIA_API_KEY",),
        "finra": ("JOHNY_SKORE_FINRA_CLIENT_ID", "JOHNY_SKORE_FINRA_CLIENT_SECRET"),
    }
    coverage = {}
    for source, interval, codes in (("fda", 30, None), ("finra", 15, None),
                                     ("cms", 30, {"HEALTH_SERVICES"}),
                                     ("clinicaltrials", 30, {"PHARMA", "MEDTECH"}),
                                     ("ofac", 1, None), ("ofac_non_sdn", 1, None), ("doj", 30, None),
                                     ("epa", 30, {"CHEMICALS", "METALS", "INDUSTRIAL", "HOME", "PACKAGING"})):
        applicable = (universe if codes is None else
                      {t for t, p in profiles.by_ticker.items() if p.code in codes} & universe)
        coverage[source] = {"applicable_profile_subjects": len(applicable),
                            "refresh_days": interval,
                            **store.specialist_coverage(source, as_of=clock,
                                                        refresh_days=interval, subjects=applicable)}
    bank_subjects = {t for t, p in profiles.by_ticker.items() if p.code == "BANK"} & universe
    try:
        bank_identities = load_verified_banks(Path(env.get("JOHNY_SKORE_FDIC_BANKS_FILE") or DEFAULT_IDENTITIES))
        bank_coverage = fdic_coverage(store, bank_identities, as_of=clock, subjects=bank_subjects)
        coverage["fdic"] = {"status": "MEASURED", "applicable_profile_subjects": len(bank_subjects),
                            "refresh_days": FDIC_REFRESH_DAYS, "query_scope": "latest_two_reports_per_CERT",
                            "complete_issuer_groups_verified": False, **bank_coverage,
                            "unmapped_profile_subjects": len(bank_subjects) - bank_coverage["mapped_subjects"]}
    except (OSError, ValueError, KeyError, TypeError):
        coverage["fdic"] = {"status": "INVALID_IDENTITY_MANIFEST",
                            "applicable_profile_subjects": len(bank_subjects)}
    specialists = inventory["specialists"]
    evidence_audit = audit_specialist_evidence(specialists)
    every_specialist_claims_completion = bool(specialists) and all(
        row["code"] == "DONE" and row["live"] == "VERIFIED" for row in specialists)
    return {
        "schema_version": 1, "generated_at": clock.astimezone(timezone.utc).isoformat(),
        "report_type": "runtime_diagnostic_not_completion_certificate",
        "historical_replay_supported": False,
        "host": {"system": platform.system(), "python": platform.python_version()},
        "universe": {"expected_subjects": len(records), "csv_sha256": CANONICAL_CSV_SHA256},
        "runtime": store.specialist_runtime_diagnostics(
            records=records, source_sha256=CANONICAL_CSV_SHA256, as_of=clock),
        "source_runs": source_facts,
        "source_run_semantics": source_semantics,
        "source_run_semantic_counts": source_semantic_counts,
        "source_status_is_not_acceptance": True,
        "rotating_source_coverage": coverage,
        "configured_access_present": {provider: all(bool(env.get(key, "").strip()) for key in keys)
                                      for provider, keys in access_keys.items()},
        "access_presence_is_successful_authentication": False,
        "inventory_as_of": inventory["as_of"], "specialists": specialists,
        "inventory_done_count": sum(row["code"] == "DONE" for row in specialists),
        "inventory_total": len(specialists),
        "evidence_content_audit": evidence_audit,
        "completion_verified": bool(
            len(specialists) == 21 and every_specialist_claims_completion
            and evidence_audit["all_completion_claims_verified"]),
        "unproven_acceptance": ["dated issuer/product/instrument coverage",
                                "positive and negative live cases for every specialist",
                                "Windows end-to-end run and relevant coverage",
                                "historical out-of-sample evaluation"],
        "scoring_applied": False,
    }
