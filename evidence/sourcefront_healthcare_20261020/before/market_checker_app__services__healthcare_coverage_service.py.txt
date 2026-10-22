"""Read-only healthcare coverage diagnostics for the exact 687-subject input.

Object mappings, current SEC query identities and completed name searches are
different denominators. None supplies issuer-wide negative or live acceptance.
The mutable runtime store supports an as-of availability filter, not a frozen
historical reconstruction; immutable replay belongs to the evaluation service.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry, healthcare_resolver_version,
)
from market_checker_app.services.healthcare_scout_service import CMS_URL, TRIALS_URL
from market_checker_app.services.research_profile_service import (
    ProfileRegistry, load_research_profiles,
)
from market_checker_app.storage.scout_store import ScoutStore, SPECIALIST_CHECK_SCOPES
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256, CANONICAL_TICKER_COUNT, load_canonical_tickers,
)


SOURCE_PROFILES = {
    "fda": frozenset({"PHARMA", "MEDTECH", "LIFE_TOOLS", "FOOD"}),
    "cms": frozenset({"HEALTH_SERVICES"}),
    "clinicaltrials": frozenset({"PHARMA", "MEDTECH"}),
}
SOURCE_ENDPOINTS = {
    "fda": ["https://api.fda.gov/drug/enforcement.json",
            "https://api.fda.gov/device/enforcement.json",
            "https://api.fda.gov/food/enforcement.json",
            "https://api.fda.gov/transparency/crl.json"],
    "cms": [CMS_URL],
    "clinicaltrials": [TRIALS_URL],
}
MAX_MANIFEST_BYTES = 10_000_000
MAX_RUNTIME_ROWS = 100_000


def _aware(value: str | datetime) -> datetime:
    clock = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Healthcare coverage needs an explicit timezone")
    return clock.astimezone(timezone.utc)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _manifest(path: Path, supplied: HealthcareIdentityRegistry | None):
    """Diagnose individual bad rows but fail closed for the whole collector scope."""
    errors, malformed, rows = [], [], []
    if supplied is not None:
        records = list(supplied.records())
        manifest_sha256 = _digest(records)
    else:
        try:
            with Path(path).open("rb") as handle:
                body = handle.read(MAX_MANIFEST_BYTES + 1)
            if len(body) > MAX_MANIFEST_BYTES:
                raise ValueError("Manifest exceeds byte budget")
            raw = json.loads(body)
            manifest_sha256 = hashlib.sha256(body).hexdigest()
        except (OSError, ValueError, UnicodeError):
            return None, [], [], {"status": "UNAVAILABLE_OR_INVALID", "errors": ["MANIFEST_READ_FAILED"],
                                 "sha256": None, "record_count": 0, "malformed_record_count": 0}
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            errors.append("UNSUPPORTED_MANIFEST_SCHEMA")
        records = raw.get("records") if isinstance(raw, dict) else None
        if not isinstance(records, list) or len(records) > 10000:
            return None, [], [], {"status": "INVALID", "errors": errors + ["INVALID_RECORD_LIST"],
                                 "sha256": manifest_sha256, "record_count": 0,
                                 "malformed_record_count": 0}
        if "record_count" in raw and raw["record_count"] != len(records):
            errors.append("RECORD_COUNT_MISMATCH")
    for position, record in enumerate(records):
        try:
            rows.append(HealthcareIdentityRegistry([record]).records()[0])
        except (ValueError, TypeError, KeyError, OverflowError):
            # Do not echo arbitrary malformed payloads, URLs or exception text.
            malformed.append({"position": position, "status": "MALFORMED",
                              "source": record.get("source") if isinstance(record, dict)
                              and record.get("source") in SOURCE_PROFILES else None,
                              "ticker": record.get("ticker") if isinstance(record, dict)
                              and isinstance(record.get("ticker"), str)
                              and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", record["ticker"]) else None})
    if malformed:
        errors.append("MALFORMED_OBJECT_RECORDS")
    try:
        registry = HealthcareIdentityRegistry(rows)
    except ValueError:
        errors.append("DUPLICATE_RECORD_IDS")
        registry = None
    return (registry if not errors else None), rows, malformed, {
        "status": "VALID" if not errors else "INVALID", "errors": errors,
        "sha256": manifest_sha256, "record_count": len(records),
        "valid_record_count": len(rows), "malformed_record_count": len(malformed),
    }


def _runtime_snapshot(store: ScoutStore | None, universe: set[str]):
    empty = {"scout_sec_identities": [], "scout_specialist_checks": [], "scout_findings": []}
    if store is None:
        return empty, {"status": "NOT_PROVIDED", "complete_read": False}
    path = Path(store.db_path)
    if not path.is_file():
        return empty, {"status": "DATABASE_NOT_FOUND", "complete_read": False}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing, truncated = [], []
            for table in empty:
                if table not in tables:
                    missing.append(table)
                    continue
                predicate = "" if table == "scout_sec_identities" else (
                    "source IN ('fda','cms','clinicaltrials') AND ")
                selected = sorted(universe)
                query = (f"SELECT * FROM {table} WHERE {predicate}subject_id IN ("
                         + ",".join("?" for _ in selected) + ") LIMIT ?")
                rows = conn.execute(query, (*selected, MAX_RUNTIME_ROWS + 1)).fetchall()
                if len(rows) > MAX_RUNTIME_ROWS:
                    truncated.append(table)
                empty[table] = [dict(row) for row in rows[:MAX_RUNTIME_ROWS]]
            return empty, {"status": "READY" if not missing and not truncated else "INCOMPLETE_READ",
                           "complete_read": not missing and not truncated,
                           "missing_tables": missing, "truncated_tables": truncated}
    except (OSError, sqlite3.Error):
        return {key: [] for key in empty}, {"status": "DATABASE_READ_ERROR", "complete_read": False}


def _issuer_states(rows: list[dict], tickers: list[str], clock: datetime) -> dict[str, dict]:
    grouped = {ticker: [] for ticker in tickers}
    for row in rows:
        grouped[row["subject_id"]].append(row)
    output = {}
    for ticker, candidates in grouped.items():
        known, future, malformed = [], 0, 0
        for row in candidates:
            try:
                if (not isinstance(row.get("cik"), str) or not re.fullmatch(r"\d{10}", row["cik"])
                        or not isinstance(row.get("company_name"), str) or not row["company_name"].strip()
                        or row.get("status") not in {"ACTIVE", "QUARANTINED"}):
                    raise ValueError("Malformed issuer")
                if _aware(row["first_observed_at"]) > clock:
                    future += 1
                else:
                    known.append(row)
            except (ValueError, TypeError, KeyError):
                malformed += 1
        active = [row for row in known if row["status"] == "ACTIVE"]
        if malformed:
            status = "MALFORMED_ISSUER"
        elif len(active) > 1 or (active and any(row["cik"] != active[0]["cik"] for row in known)):
            status = "CONFLICTING_ISSUER"
        elif len(active) == 1:
            status = "KNOWN_ACTIVE_ISSUER"
        elif known:
            status = "QUARANTINED_ISSUER"
        elif future:
            status = "FUTURE_ISSUER"
        else:
            status = "UNRESOLVED_ISSUER"
        identity = ({key: active[0][key] for key in ("subject_id", "cik", "company_name")}
                    if status == "KNOWN_ACTIVE_ISSUER" else None)
        output[ticker] = {"status": status, "identity": identity,
                          "future_rows": future, "malformed_rows": malformed}
    return output


def _object_states(rows: list[dict], registry: HealthcareIdentityRegistry | None,
                   clock: datetime, universe: set[str]) -> list[dict]:
    output = []
    for row in rows:
        start = date.fromisoformat(row["effective_from"])
        end = date.fromisoformat(row["effective_to"]) if row.get("effective_to") else None
        interval = "FUTURE" if start > clock.date() else "EXPIRED" if end and end <= clock.date() else "CURRENT"
        key = {"fda_crl": "letter_date", "clinical_trial_sponsor": "record_version_date"}.get(row["kind"])
        trial_snapshot = row["kind"] == "clinical_trial_sponsor_snapshot"
        captured = _aware(row["identifiers"]["source_observed_at"]) if trial_snapshot else None
        event = (captured.date() if trial_snapshot else clock.date() if row["time_basis"] == "OBSERVED_SNAPSHOT"
                 else date.fromisoformat(row["identifiers"][key]) if key else start)
        if row["ticker"] not in universe:
            status = "OUTSIDE_CANONICAL_UNIVERSE"
        elif _aware(row["known_at"]) > clock:
            status = "NOT_YET_KNOWN"
        elif event > clock.date() or start > clock.date():
            status = "FUTURE_OBJECT"
        elif not (start <= event and (end is None or event < end)):
            status = "OUTSIDE_EFFECTIVE_PERIOD"
        elif registry is None:
            status = "INVALID_MANIFEST_SCOPE"
        else:
            status = registry.resolve(source=row["source"], kind=row["kind"], ticker=row["ticker"],
                                      cik=row["issuer_cik"], entity_name=row["entity_name"],
                                      identifiers=row["identifiers"], event_date=event,
                                      as_of=clock, observed_at=captured)["identity_resolution"]
        output.append({"record_id": row["record_id"], "source": row["source"], "ticker": row["ticker"],
                       "issuer_cik": row["issuer_cik"], "kind": row["kind"], "status": status,
                       "known_at": row["known_at"], "coverage_date": event.isoformat(),
                       "time_basis": row["time_basis"], "effective_from": row["effective_from"],
                       "effective_to": row.get("effective_to"), "interval_status_now": interval,
                       "historical_event_only": row["time_basis"] == "EVENT_DATE" and interval == "EXPIRED",
                       "observed_trial_snapshot": trial_snapshot,
                       "source_observed_at": captured.isoformat() if captured else None,
                       "current_observed_trial_snapshot": trial_snapshot and interval == "CURRENT",
                       "historical_publisher_day_attribution_allowed": False if trial_snapshot else None})
    return output


def _source_observations(source: str, snapshot: dict, clock: datetime, cutoff: datetime):
    checks, counts, name_only = {}, Counter(), set()
    for row in snapshot["scout_specialist_checks"]:
        if row.get("source") != source:
            continue
        try:
            checked = _aware(row["checked_at"])
            if (not isinstance(row["candidate_count"], int) or row["candidate_count"] < 0
                    or row["truncated"] not in {0, 1} or not isinstance(row["identity_key"], str)):
                raise ValueError("Malformed source check")
        except (ValueError, KeyError, TypeError):
            counts["malformed_checks"] += 1
            continue
        if checked > clock:
            counts["future_checks"] += 1
            continue
        checks.setdefault(row["subject_id"], {})[row["identity_key"]] = dict(
            row, current=checked > cutoff)
    for row in snapshot["scout_findings"]:
        if row.get("source") != source:
            continue
        try:
            known = max(_aware(row["available_at"]), _aware(row["first_observed_at"]))
            if known > clock:
                counts["future_findings"] += 1
                continue
            detail = json.loads(row["details_json"])
            if not isinstance(detail, dict):
                raise ValueError("Malformed finding")
        except (ValueError, KeyError, TypeError):
            counts["malformed_findings"] += 1
            continue
        counts["available_findings"] += 1
        if detail.get("identity_status") == "NAME_ONLY" or detail.get("issuer_attribution_allowed") is False:
            counts["name_only_candidates"] += 1
            name_only.add(row["subject_id"])
        elif detail.get("identity_status") == "DATED_IDENTITY_VERIFIED" and detail.get("issuer_attribution_allowed") is True:
            counts["stored_dated_attribution_claims"] += 1
        elif detail.get("identity_status") == "OBSERVED_SNAPSHOT_IDENTITY_VERIFIED" and detail.get("issuer_attribution_allowed") is True:
            counts["stored_observed_snapshot_claims"] += 1
        else:
            counts["unclassified_findings"] += 1
    count_fields = ("malformed_checks", "future_checks", "future_findings", "malformed_findings",
                    "available_findings", "name_only_candidates", "stored_dated_attribution_claims", "stored_observed_snapshot_claims",
                    "unclassified_findings")
    return checks, {key: counts[key] for key in count_fields}, name_only


def build_healthcare_coverage_report(store: ScoutStore | None = None, *,
                                     as_of: datetime | None = None,
                                     identities: HealthcareIdentityRegistry | None = None,
                                     identities_path: Path = DEFAULT_HEALTHCARE_IDENTITIES,
                                     profiles: ProfileRegistry | None = None,
                                     refresh_days: int = 30) -> dict:
    """Measure mappings and scoped queries without a network call or database write.

    ``identities`` lets the coordinator reuse the exact registry used by its
    collectors. Otherwise ``identities_path`` can be the configured manifest.
    Sector profiles select research candidates, not proven exposure. Missing
    runtime evidence remains unknown, and invalid manifests invalidate every
    current-scope completeness claim rather than retaining old green checks.
    """
    clock = _aware(as_of or datetime.now(timezone.utc))
    if isinstance(refresh_days, bool) or not isinstance(refresh_days, int) or not 1 <= refresh_days <= 365:
        raise ValueError("Healthcare refresh interval must be between 1 and 365 days")
    tickers = load_canonical_tickers()
    universe = set(tickers)
    taxonomy = profiles if profiles is not None else load_research_profiles()
    registry, rows, malformed, manifest = _manifest(identities_path, identities)
    snapshot, runtime = _runtime_snapshot(store, universe)
    issuers = _issuer_states(snapshot["scout_sec_identities"], tickers, clock)
    objects = _object_states(rows, registry, clock, universe)
    unknown = [ticker for ticker in tickers if taxonomy.for_ticker(ticker) is None]
    sources = {}
    for source, codes in SOURCE_PROFILES.items():
        applicable = [ticker for ticker in tickers
                      if (profile := taxonomy.for_ticker(ticker)) is not None and profile.code in codes]
        outside = [ticker for ticker in tickers if ticker not in applicable and ticker not in unknown]
        own_objects = [row for row in objects if row["source"] == source]
        checks, observations, name_only = _source_observations(
            source, snapshot, clock, clock - timedelta(days=refresh_days))
        versions, details = {}, []
        for ticker in applicable:
            issuer = issuers[ticker]["identity"]
            documented = [row for row in own_objects if row["ticker"] == ticker
                          and row["status"] in {"EXACT_DATED_OBJECT", "EXACT_OBSERVED_SNAPSHOT"}]
            matching = [row for row in documented if issuer and row["issuer_cik"] == issuer["cik"]]
            queried = checks.get(ticker, {})
            current_check = None
            if registry is not None and issuer is not None:
                payload = {"source": source, "sec_identity": issuer,
                           "dated_records_sha256": registry.fingerprint(source, ticker, issuer["cik"], as_of=clock)}
                versions[ticker] = _digest(payload)
                key = f"{issuer['cik']}:{issuer['company_name']}" + SPECIALIST_CHECK_SCOPES[source] + ":registry-" + versions[ticker]
                current_check = queried.get(key)
            check_status = ("INVALID_MANIFEST_SCOPE" if registry is None else
                            "RUNTIME_UNAVAILABLE" if not runtime["complete_read"] else
                            "NO_CURRENT_ISSUER" if issuer is None else
                            "SCOPE_CHANGED" if current_check is None and queried else
                            "NEVER_QUERIED" if current_check is None else
                            "STALE" if not current_check["current"] else
                            "PARTIAL" if current_check["truncated"] else "COMPLETE_NAME_QUERY_SCOPE")
            details.append({"ticker": ticker, "profile_code": taxonomy.for_ticker(ticker).code,
                            "issuer_status": issuers[ticker]["status"],
                            "issuer_cik": issuer["cik"] if issuer else None,
                            "documented_object_records": len(documented),
                            "issuer_matched_object_records": len(matching),
                            "issuer_mismatch_object_records": len(documented) - len(matching) if issuer else 0,
                            "object_status_counts": dict(Counter(row["status"] for row in own_objects if row["ticker"] == ticker)),
                            "queried_any_scope": bool(queried), "source_check_status": check_status,
                            "current_scope_checked_at": current_check["checked_at"] if current_check else None,
                            "current_scope_candidate_count": current_check["candidate_count"] if current_check else None,
                            "stored_name_only_candidates": ticker in name_only})
        source_counts = Counter(row["source_check_status"] for row in details)
        mapped = [row["ticker"] for row in details if row["issuer_matched_object_records"]]
        documented_subjects = [row["ticker"] for row in details if row["documented_object_records"]]
        sources[source] = {
            "status": "MEASURED" if registry is not None else "INVALID_IDENTITY_MANIFEST",
            "applicable_profile_subjects": len(applicable), "profile_candidate_tickers": applicable,
            "unknown_applicability_tickers": unknown, "outside_profile_tickers": outside,
            "candidate_profiles": sorted(codes), "profile_applicability_is_object_verified": False,
            "outside_profile_is_no_exposure_proof": False,
            "documented_issuer_object_subjects": len(documented_subjects),
            "documented_issuer_object_tickers": documented_subjects,
            "mapped_subjects": len(mapped), "mapped_tickers": mapped,
            "unresolved_subjects": len(applicable) - len(mapped),
            "mapped_objects": sum(row["issuer_matched_object_records"] for row in details),
            "mapping_coverage_ratio": round(len(mapped) / len(applicable), 6) if applicable else 0.0,
            "mapping_coverage_basis": "profile candidates with current known SEC issuer AND dated object relationship",
            "queried_issuers": sum(row["queried_any_scope"] for row in details),
            "current_complete_source_checks": source_counts["COMPLETE_NAME_QUERY_SCOPE"],
            "current_partial_source_checks": source_counts["PARTIAL"],
            "stale_source_checks": source_counts["STALE"], "changed_scope_checks": source_counts["SCOPE_CHANGED"],
            "zero_candidate_complete_name_checks": sum(row["source_check_status"] == "COMPLETE_NAME_QUERY_SCOPE"
                                                       and row["current_scope_candidate_count"] == 0 for row in details),
            "issuer_wide_complete_checks": 0, "negative_issuer_completeness_verified": False,
            "live_verified": False, "historical_evaluation_verified": False,
            "scope": {"check_scope_version": SPECIALIST_CHECK_SCOPES[source],
                      "identity_resolver_version": healthcare_resolver_version(source), "taxonomy_schema_version": taxonomy.version,
                      "refresh_days": refresh_days, "endpoints": SOURCE_ENDPOINTS[source],
                      "query_scope": "at most five exact SEC/documented names; bounded publisher pagination",
                      "object_scope": "specific recorded product/application, trial intervention/role/version or provider/owner role",
                      "cms_time_basis": "OBSERVED_SNAPSHOT" if source == "cms" else None,
                      "clinical_trial_time_bases": ["EVENT_DATE", "OBSERVED_SNAPSHOT"] if source == "clinicaltrials" else [],
                      "clinical_snapshot_is_historical_publisher_version": False,
                      "issuer_wide_scope": False},
            "identity_versions": versions, "subject_details": details,
            "object_status_counts": dict(Counter(row["status"] for row in own_objects)),
            "expired_interval_records": sum(row["interval_status_now"] == "EXPIRED" for row in own_objects),
            "historical_event_only_records": sum(row["historical_event_only"] for row in own_objects),
            "documented_observed_trial_snapshots": sum(row["observed_trial_snapshot"]
                                                       and row["status"] == "EXACT_OBSERVED_SNAPSHOT" for row in own_objects),
            "current_observed_trial_snapshots": sum(row["current_observed_trial_snapshot"]
                                                    and row["status"] == "EXACT_OBSERVED_SNAPSHOT" for row in own_objects),
            "malformed_records": sum(row["source"] == source for row in malformed),
            "observations": observations,
        }
    return {"schema_version": 1, "report_type": "healthcare_identity_and_scoped_query_diagnostic",
            "generated_at": clock.isoformat(), "historical_replay_supported": False,
            "universe": {"expected_subjects": CANONICAL_TICKER_COUNT, "subjects": len(tickers),
                         "csv_sha256": CANONICAL_CSV_SHA256, "tickers": tickers},
            "identity_manifest": manifest, "runtime": runtime,
            "issuer_status_counts": dict(Counter(row["status"] for row in issuers.values())),
            "sources": sources, "object_diagnostics": objects, "malformed_object_diagnostics": malformed,
            "acceptance": {"live_verified": False, "negative_issuer_completeness_verified": False,
                           "actual_windows_end_to_end_verified": False, "out_of_sample_evaluation_verified": False},
            "limitations": ["Sector profiles are candidate scopes and do not establish complete group exposure.",
                            "The current FDA collector queries all active canonical issuers; the smaller FDA profile denominator is a research candidate cohort.",
                            "CVM, MAUDE, CMS reimbursement/plans, complete subsidiary chains and other-sector product exposure are outside these collector scopes.",
                            "Archived event-specific FDA/trial links can be valid for their event despite expiration today; CMS cannot backdate its observed snapshot.",
                            "Clinical observed snapshots bind only their exact captured object and independent issuer cohort; they never establish the publisher's historical event-day sponsor identity.",
                            "Stored name checks and attribution claims do not prove live, negative, financial-impact or investment-direction acceptance.",
                            "Current runtime identity/check rows are mutable and cannot reconstruct an immutable historical snapshot."]}
