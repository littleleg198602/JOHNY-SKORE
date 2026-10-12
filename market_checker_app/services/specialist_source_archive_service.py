"""Bounded, read-only archives of actual source observations, never proxy lift.

Finding versions are immutable except for their latest retrieval heartbeat.
Checks/checkpoints are mutable latest state, so exporting them now cannot
recreate overwritten negative checks at an earlier prediction timestamp.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256, CANONICAL_SOURCE_FILE, CANONICAL_TICKER_COUNT,
    load_canonical_ticker_records,
)


ARCHIVE_SCHEMA_VERSION = "specialist_source_archive_v1"
MAX_DETAILS_BYTES = 131_072
SOURCES = ("sec", "rss", "fred", "eia", "usaspending", "fda", "finra", "fdic",
           "sec13f", "nhtsa", "cms", "clinicaltrials", "ofac", "ofac_non_sdn", "doj", "epa")
# These are source routing labels, not a grant of specialist acceptance.
SOURCE_SPECIALISTS = {
    "sec": ["sec", "insider", "suppliers"], "rss": ["ir_news"],
    "fred": ["commodities"], "eia": ["commodities", "oil_utilities"],
    "usaspending": ["contracts", "aerospace"], "fda": ["fda"],
    "finra": ["short_interest"], "fdic": ["bank_fdic"],
    "sec13f": ["institutions"], "nhtsa": ["regulatory"],
    "cms": ["health_services"], "clinicaltrials": ["fda"],
    "ofac": ["regulatory"], "ofac_non_sdn": ["regulatory"],
    "doj": ["regulatory"], "epa": ["regulatory"],
}

_EVENT_FIELDS = {
    "sec": ("report_date",), "fda": ("report_date", "letter_date"),
    "fdic": ("report_date",), "sec13f": ("period_of_report", "filing_date"),
    "usaspending": ("award_start", "last_modified_date"),
    "cms": ("ASSOCIATION DATE - OWNER",), "clinicaltrials": ("last_update_posted",),
    "finra": ("settlement_date",), "nhtsa": ("report_received_date",),
    "fred": ("observation_date",), "eia": ("period",),
}
_KNOWLEDGE_FIELDS = (
    "relationship_known_at", "instrument_known_at", "issuer_identity_known_at",
    "financial_name_known_at", "product_relationship_known_at",
)
_TEXT_FIELDS = frozenset({
    "stage", "form", "parser_version", "kind", "series_id", "frequency", "unit",
    "identity_status", "identity_resolution", "issuer_cik", "issuer_sec_cik", "cik",
    "manager_cik", "cusip", "security_class", "information_table_key", "submission_type",
    "recall_number", "product_type", "application_number", "nct_id", "overall_status",
    "uei", "generated_internal_id", "award_id", "contract_award_type", "cert",
    "relationship_type", "issuer_identity_kind", "ownership_attribution_time_basis",
    "issuer_instrument_class", "issuer_instrument_symbol", "market_class", "symbol",
    "campaign", "make", "model", "ENROLLMENT ID", "ASSOCIATE ID - OWNER", "ROLE CODE - OWNER",
})
_NUMBER_FIELDS = frozenset({
    "as_filed_value_usd", "reported_shares", "reported_award_amount_usd", "reported_total_outlays_usd",
    "short_position_shares", "previous_short_position_shares", "days_to_cover_reported", "model_year", "value",
})
_LIST_FIELDS = frozenset({"application_number", "matched_roles", "phases"})
_URL_FIELDS = frozenset({
    "uei_evidence_url", "relationship_evidence_url", "continuity_evidence_url",
    "fdic_evidence_url", "issuer_identity_evidence_url", "report_date_identity_evidence_url",
    "report_date_floor_evidence_url", "financial_name_evidence_url", "product_evidence_url",
    "cusip_evidence_url", "instrument_evidence_url", "ticker_evidence_url",
})
_BOOL_FIELDS = frozenset({
    "issuer_attribution_allowed", "product_attribution_allowed", "historical_association_attribution_allowed",
    "issuer_financial_impact_verified", "issuer_consolidated_values", "top_rows_sample_only",
    "amendments_included", "modification_history_complete", "release_timestamp_known",
    "report_date_is_publication_time", "letter_date_is_publication_time", "period_is_publication_time",
    "last_modified_is_publication_time", "association_date_is_publication_time", "scoring_applied",
    "settlement_date_is_publication_time", "company_purchase_price", "has_results",
})
_DATE_FIELDS = frozenset({field for fields in _EVENT_FIELDS.values() for field in fields} | {
    "relationship_effective_from", "relationship_effective_to", "report_date_eligibility_from",
    "issuer_identity_as_of", "issuer_identity_filing_date",
})
_LIMITATIONS = [
    "Stored publication time is a collector field, not independently verified publisher release time; historical event/report dates never substitute for knowledge time.",
    "Latest retrieval heartbeats and per-identity checks are mutable. Overwritten earlier checks, failures and negative results cannot be reconstructed from today's ScoutStore.",
    "An immutable export preserves the state at export. It cannot prove that a retrospective database dump existed at the requested earlier prediction time.",
    "Dataset-level 13F checks and source-run counters do not establish any individual ticker's complete holdings history or relevant coverage.",
    "USAspending terminal pagination is traversal state, not an immutable publisher snapshot or historical contract-modification completeness.",
    "No observation is distinct from a completed zero-candidate query; neither alone proves absence of an economic event or a relevant identity.",
    "Current canonical 687 tickers are not a historical survivor-free universe. Non-ticker FRED/EIA context is excluded, never attributed to companies.",
    "The export is source evidence only: no labels, matched return outcomes, out-of-sample performance, Windows acceptance or production score activation.",
]


def _utc(value: object) -> str | None:
    try:
        clock = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if clock.tzinfo is None or clock.utcoffset() is None:
            return None
        return clock.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, ValueError, OverflowError):
        return None


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _public_url(value: object) -> str | None:
    try:
        parts = urlsplit(public_https_reference(str(value)))
        # Query and fragment can contain API keys, signed URLs or cursor tokens.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except (ValueError, TypeError):
        return None


def _text(value: object) -> str | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value)
    if len(value) > 300 or re.search(r"(?i)(bearer\s|password|credential|api[_-]?key|access[_-]?token|://|[?&]token=)", value):
        return None
    return value


def _date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date().isoformat()
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _safe_fields(details: dict) -> dict:
    """Explicit projection: never copy errors, excerpts, request state or keys."""
    result = {}
    for key, value in details.items():
        if key in _TEXT_FIELDS and (safe := _text(value)) is not None:
            result[key] = safe
        elif key in _LIST_FIELDS and isinstance(value, list):
            result[key] = [safe for item in value[:100] if (safe := _text(item)) is not None]
        elif key in _NUMBER_FIELDS:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if not isinstance(value, bool) and math.isfinite(number):
                result[key] = number
        elif key in _BOOL_FIELDS and isinstance(value, bool):
            result[key] = value
        elif key in _DATE_FIELDS and (safe := _date(value)) is not None:
            result[key] = safe
        elif key in _KNOWLEDGE_FIELDS and (safe := _utc(value)) is not None:
            result[key] = safe
        elif key in _URL_FIELDS and (safe := _public_url(value)) is not None:
            result[key] = safe
        elif key == "amounts_thousands_usd" and isinstance(value, dict):
            result[key] = {name: number for name, number in sorted(value.items())
                           if re.fullmatch(r"[A-Z][A-Z0-9_]{0,30}", name)
                           and (number is None or (isinstance(number, (int, float))
                                and not isinstance(number, bool) and math.isfinite(number)))}
    evidence = details.get("identity_evidence")
    if isinstance(evidence, dict):
        result["identity_evidence"] = {
            key: safe for key in ("effective_from", "effective_to", "matched_event_date")
            if (safe := _date(evidence.get(key))) is not None
        }
        if (known := _utc(evidence.get("known_at"))) is not None:
            result["identity_evidence"]["known_at"] = known
        result["identity_evidence"]["record_sha256"] = canonical_hash(evidence)
        result["identity_evidence"]["public_references"] = [
            safe for row in evidence.get("evidence", []) if isinstance(row, dict)
            and (safe := _public_url(row.get("url"))) is not None
        ] if isinstance(evidence.get("evidence"), list) else []
    return result


def _observation(row: dict, cutoff: str) -> tuple[dict | None, str | None]:
    try:
        details = json.loads(row["details_json"])
    except (TypeError, ValueError):
        return None, "INVALID_DETAILS_JSON"
    if not isinstance(details, dict):
        return None, "INVALID_DETAILS_JSON"
    stamps = [_utc(row[field]) for field in ("published_at", "available_at", "first_observed_at")]
    if any(stamp is None for stamp in stamps) or not stamps[0] <= stamps[1] <= stamps[2] <= cutoff:
        return None, "INVALID_OR_FUTURE_OBSERVATION_TIME"
    identity_times = {key: details[key] for key in _KNOWLEDGE_FIELDS if details.get(key) is not None}
    identity = details.get("identity_evidence")
    if isinstance(identity, dict) and identity.get("known_at") is not None:
        identity_times["identity_evidence.known_at"] = identity["known_at"]
    normalized = {key: _utc(value) for key, value in identity_times.items()}
    if any(stamp is None or stamp > stamps[2] for stamp in normalized.values()):
        return None, "INVALID_OR_FUTURE_IDENTITY_KNOWLEDGE"
    record = {
        "source": row["source"], "ticker": row["subject_id"],
        "finding_id": row["finding_id"] if re.fullmatch(r"finding:[a-f0-9]{32}", row["finding_id"]) else "sha256:" + _digest(row["finding_id"]),
        "source_object_id_sha256": _digest(row["source_object_id"]),
        "stored_content_hash_sha256": _digest(row["content_hash"]),
        "stored_details_sha256": _digest(row["details_json"]),
        "source_url": _public_url(row["source_url"]),
        "verification_status": row["verification_status"] if row["verification_status"] in {"UNVERIFIED", "SOURCE_VERIFIED", "CLAIM_VERIFIED"} else "UNKNOWN",
        "stored_publication_at": stamps[0], "publisher_publication_time_verified": False,
        "available_at": stamps[1], "first_observed_at": stamps[2],
        "knowledge_at": max(stamps[1], stamps[2], *normalized.values()),
        "identity_knowledge_times": normalized,
        "historical_event_dates": {key: safe for key in _EVENT_FIELDS.get(row["source"], ())
                                   if (safe := _date(details.get(key))) is not None},
        "source_fields": _safe_fields(details),
    }
    record["observation_hash"] = canonical_hash(record)
    # A mutable heartbeat is not part of the immutable observation fingerprint.
    last = _utc(row["retrieved_at"])
    record["latest_retrieved_at_known_at_cutoff"] = last if last and last <= cutoff else None
    return record, None


def _check(source: str, ticker: str | None, key: object, stamp: object, status: str,
           kind: str, **counts: int) -> dict:
    record = {"source": source, "ticker": ticker, "identity_key_sha256": _digest(key),
              "checked_at": _utc(stamp), "status": status, "kind": kind,
              "scope": "DATASET" if ticker is None else "STORED_IDENTITY_QUERY",
              "historical_checks_complete": False, **counts}
    record["check_hash"] = canonical_hash(record)
    return record


def build_specialist_source_archive(
    store: ScoutStore, *, as_of: datetime,
    maximum_observations: int = 10_000, observations_per_source_ticker: int = 20,
    maximum_checks: int = 10_000, source: str | None = None,
    _read_connection=None,
) -> dict:
    """Capture a consistent SQLite read snapshot over the approved 687 tickers.

    The caller passes an existing store. This never initializes a schema, runs
    collectors, writes predictions, adjusts scores or accepts specialists.
    """
    cutoff = _utc(as_of)
    if cutoff is None:
        raise ValueError("Source archive cutoff needs an explicit timezone")
    if source is not None and source not in SOURCES:
        raise ValueError("Source archive requires an approved source")
    sources = (source,) if source is not None else SOURCES
    for value, maximum in ((maximum_observations, 20_000), (observations_per_source_ticker, 100), (maximum_checks, 20_000)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError("Source archive limits must be bounded positive integers")
    if not store.db_path.is_file():
        raise FileNotFoundError("Source archive requires an existing ScoutStore database")
    records = load_canonical_ticker_records()
    subjects = [record["ticker"] for record in records]
    if len(subjects) != CANONICAL_TICKER_COUNT or len(set(subjects)) != CANONICAL_TICKER_COUNT:
        raise ValueError("Source archive requires the exact canonical 687 tickers")
    placeholders = ",".join("?" for _ in subjects)
    source_slots = ",".join("?" for _ in sources)
    scope = f"source IN ({source_slots}) AND subject_id IN ({placeholders})"
    params = (*sources, *subjects, cutoff, cutoff, cutoff)
    predicate = f"{scope} AND archive_utc(published_at)<=? AND archive_utc(available_at)<=? AND archive_utc(first_observed_at)<=?"
    rejected = Counter()
    findings, checks, source_runs = [], [], []
    totals, tables = {}, set()
    check_limits_hit = False
    # The incremental hook supplies its existing read connection so the
    # source state, exact run record and request ledger share one snapshot.
    with (nullcontext(_read_connection) if _read_connection is not None else store._connect()) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.create_function("archive_utc", 1, _utc, deterministic=True)
        if not conn.in_transaction:
            conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "scout_findings" in tables:
            totals = {(row["source"], row["subject_id"]): row["count"] for row in conn.execute(
                f"SELECT source, subject_id, COUNT(*) AS count FROM scout_findings WHERE {predicate} GROUP BY source, subject_id", params)}
            rows = conn.execute(f"""
                SELECT * FROM (SELECT finding_id, source, subject_id, source_object_id,
                    content_hash, source_url, published_at, available_at, first_observed_at,
                    retrieved_at, verification_status,
                    CASE WHEN LENGTH(CAST(details_json AS BLOB))<=? THEN details_json ELSE 'null' END AS details_json,
                    ROW_NUMBER() OVER (
                    PARTITION BY source, subject_id ORDER BY archive_utc(first_observed_at) DESC, finding_id
                ) AS position FROM scout_findings WHERE {predicate})
                WHERE position<=? ORDER BY source, subject_id, position LIMIT ?
            """, (MAX_DETAILS_BYTES, *params, observations_per_source_ticker, maximum_observations)).fetchall()
            for row in rows:
                observation, issue = _observation(dict(row), cutoff)
                if issue:
                    rejected[issue] += 1
                else:
                    findings.append(observation)
        if "scout_specialist_checks" in tables:
            # Fetch one excess row to make truncation explicit, never silent.
            rows = conn.execute(f"""
                SELECT source, subject_id, identity_key, checked_at, candidate_count, truncated
                FROM scout_specialist_checks WHERE source IN ({source_slots})
                  AND (subject_id IN ({placeholders}) OR (source='sec13f' AND subject_id='DATASET'))
                  AND archive_utc(checked_at)<=?
                ORDER BY archive_utc(checked_at) DESC, source, subject_id, identity_key LIMIT ?
            """, (*sources, *subjects, cutoff, maximum_checks + 1)).fetchall()
            check_limits_hit |= len(rows) > maximum_checks
            for row in rows[:maximum_checks]:
                status = "QUERY_PARTIAL" if row["truncated"] else "QUERY_COMPLETE_ZERO_CANDIDATES" if row["candidate_count"] == 0 else "QUERY_COMPLETE_CANDIDATES"
                checks.append(_check(row["source"], None if row["subject_id"] == "DATASET" else row["subject_id"],
                    row["identity_key"], row["checked_at"], status, "LATEST_SPECIALIST_CHECK",
                    candidate_count=row["candidate_count"]))
        remaining = maximum_checks - len(checks)
        if "fdic" in sources and "scout_fdic_requests" in tables:
            rows = conn.execute(f"""
                SELECT * FROM (SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY subject_id, identity_key ORDER BY archive_utc(attempted_at) DESC, request_id
                ) AS position FROM scout_fdic_requests
                WHERE subject_id IN ({placeholders}) AND archive_utc(attempted_at)<=?)
                WHERE position=1 ORDER BY archive_utc(attempted_at) DESC, subject_id, identity_key LIMIT ?
            """, (*subjects, cutoff, remaining + 1)).fetchall()
            check_limits_hit |= len(rows) > remaining
            for row in rows[:remaining]:
                finished = _utc(row["finished_at"])
                known = bool(finished and finished <= cutoff)
                checks.append(_check("fdic", row["subject_id"], row["identity_key"],
                    finished if known else row["attempted_at"], row["outcome"] if known else "RUNNING",
                    "LATEST_FDIC_ATTEMPT", usable_rows=row["usable_rows"] if known else 0,
                    rejected_rows=row["rejected_rows"] if known else 0))
        remaining = maximum_checks - len(checks)
        if "usaspending" in sources and "scout_usaspending_checkpoints" in tables:
            rows = conn.execute(f"""
                SELECT subject_id, identity_key, updated_at,
                    CASE WHEN LENGTH(CAST(state_json AS BLOB))<=? THEN state_json ELSE 'null' END AS state_json
                FROM scout_usaspending_checkpoints
                WHERE kind='awards' AND subject_id IN ({placeholders}) AND archive_utc(updated_at)<=?
                ORDER BY archive_utc(updated_at) DESC, subject_id, identity_key LIMIT ?
            """, (MAX_DETAILS_BYTES, *subjects, cutoff, remaining + 1)).fetchall()
            check_limits_hit |= len(rows) > remaining
            for row in rows[:remaining]:
                try:
                    state = json.loads(row["state_json"])
                    status = "TRAVERSAL_COMPLETE" if state.get("status") == "COMPLETE" and state.get("terminal") is True else "TRAVERSAL_PARTIAL"
                    counts = {key: value for key in ("pages_saved", "usable_rows", "rejected_rows")
                              if isinstance(value := state.get(key), int) and not isinstance(value, bool) and value >= 0}
                except (TypeError, ValueError, AttributeError):
                    rejected["INVALID_USASPENDING_CHECKPOINT_JSON"] += 1
                    continue
                checks.append(_check("usaspending", row["subject_id"], row["identity_key"],
                    row["updated_at"], status, "LATEST_AWARDS_CHECKPOINT", **counts))
        if "scout_source_runs" in tables:
            rows = conn.execute(f"""
                SELECT * FROM (SELECT source, observed_at, status, ROW_NUMBER() OVER (
                    PARTITION BY source ORDER BY archive_utc(observed_at) DESC
                ) AS position FROM scout_source_runs
                WHERE source IN ({source_slots}) AND archive_utc(observed_at)<=?)
                WHERE position=1 ORDER BY source
            """, (*sources, cutoff)).fetchall()
            source_runs = [{"source": row["source"], "observed_at": _utc(row["observed_at"]),
                            "status": row["status"] if re.fullmatch(r"[A-Z_]{2,32}", row["status"]) else "UNKNOWN",
                            "per_ticker_coverage_inferred": False} for row in rows]
    grouped = defaultdict(list)
    for item in findings:
        grouped[item["source"], item["ticker"]].append(item)
    grouped_checks = defaultdict(list)
    for item in checks:
        if item["ticker"] is not None:
            grouped_checks[item["source"], item["ticker"]].append(item)
    matrix = []
    for source in sources:
        for ticker in subjects:
            observed = grouped[source, ticker]
            latest = max(grouped_checks[source, ticker], key=lambda row: (row["checked_at"], row["check_hash"]), default=None)
            count = totals.get((source, ticker), 0)
            missing = []
            if not observed:
                missing.append("NO_EXPORTED_SOURCE_OBSERVATION")
            if latest is None:
                missing.append("NO_STORED_IDENTITY_CHECK_KNOWN_AT_CUTOFF")
            if count > len(observed):
                missing.append("OBSERVATION_EXPORT_LIMIT_OR_REJECTED_RECORD")
            if check_limits_hit:
                missing.append("CHECK_EXPORT_LIMIT_REACHED")
            matrix.append({"source": source, "ticker": ticker,
                "source_observation_status": "OBSERVED" if observed else "MISSING",
                "stored_versions_known_at_cutoff": count, "exported_observation_count": len(observed),
                "observation_export_complete": count == len(observed),
                "latest_check_at": latest["checked_at"] if latest else None,
                "latest_check_status": latest["status"] if latest else "UNKNOWN",
                "latest_check_hash": latest["check_hash"] if latest else None,
                "relevance": "NOT_INFERRED", "relevant_coverage_verified": False,
                "historical_completeness_reconstructible": False, "missingness_reasons": missing})
    result = {
        "schema_version": ARCHIVE_SCHEMA_VERSION, "as_of": cutoff, "analysis_only": True,
        "completion_verified": False, "out_of_sample_evaluation_verified": False,
        "production_scores_changed": False, "prediction_snapshots_changed": False,
        "universe": {"source": CANONICAL_SOURCE_FILE, "csv_sha256": CANONICAL_CSV_SHA256,
                     "ticker_count": len(subjects), "records": records},
        "source_routing": {key: list(SOURCE_SPECIALISTS[key]) for key in sources}, "source_runs": source_runs,
        "observations": findings, "checks": sorted(checks, key=lambda row: row["check_hash"]),
        "source_ticker_missingness": matrix,
        "bounds": {"maximum_observations": maximum_observations,
                   "observations_per_source_ticker": observations_per_source_ticker,
                   "maximum_checks": maximum_checks, "check_export_limit_reached": check_limits_hit,
                   "observation_export_complete": sum(totals.values()) == len(findings)},
        "input": {"known_scoped_finding_versions": sum(totals.values()),
                  "exported_observations": len(findings), "exported_checks": len(checks),
                  "rejected_reasons": dict(sorted(rejected.items())),
                  "available_tables": sorted(tables & {"scout_findings", "scout_specialist_checks", "scout_fdic_requests", "scout_usaspending_checkpoints", "scout_source_runs"})},
        "limitations": list(_LIMITATIONS),
    }
    result["archive_hash"] = canonical_hash(result)
    return result


def export_specialist_source_archive(archive: dict, directory: Path) -> Path:
    """Save once under the archive hash. Never replace an existing artifact."""
    if archive.get("schema_version") != ARCHIVE_SCHEMA_VERSION or archive.get("archive_hash") != canonical_hash({key: value for key, value in archive.items() if key != "archive_hash"}):
        raise ValueError("Source archive schema or content hash is invalid")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"specialist_source_archive_{archive['archive_hash']}.json"
    encoded = (json.dumps(archive, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".source_archive_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # An atomic link publishes the complete file and cannot replace one.
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != encoded:
                raise ValueError("Existing source archive has different content; refusing overwrite")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
