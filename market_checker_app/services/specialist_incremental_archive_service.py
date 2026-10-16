"""Preserve bounded source state immediately after a recorded provider run.

An export's availability is its actual capture time. Provider ``as_of`` clocks
can precede the physical call; neither those clocks nor historical event dates
backdate an archive. These files preserve evidence, never scores or labels.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import islice
import json
import os
from pathlib import Path
import re
import tempfile

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.specialist_source_archive_service import (
    ARCHIVE_SCHEMA_VERSION, MAX_DETAILS_BYTES, SOURCES, _digest, _observation,
    _utc, build_specialist_source_archive,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256, CANONICAL_TICKER_COUNT, load_canonical_ticker_records,
)


RUN_ARCHIVE_SCHEMA_VERSION = "specialist_source_run_archive_v1"
REPLAY_SCHEMA_VERSION = "specialist_source_run_replay_v1"
SUPPORTED_INCREMENTAL_SOURCES = frozenset(SOURCES)
MAX_RUN_ARCHIVE_BYTES = 32_000_000
_ORIGINS = {"RECORDED_SCOUTSTORE_STATE", "CAPTURED_PUBLISHER_PAYLOAD_REPLAY",
            "DETERMINISTIC_TEST_FIXTURE"}
_NO_ATTEMPT_STATUSES = {"NO_DUE", "NO_DUE_WORK", "BUSY", "WAIT_QUOTA",
                        "WAIT_ACCESS", "WAIT_IDENTITY", "NOT_APPLICABLE", "CURRENT"}
_COUNT_FIELDS = frozenset("""
    new_findings new_candidates processed failed scheduled_subjects
    checked_issuers checked_models checked_banks checked_uei checked_names checked_series
    attempted_issuers attempted_requests attempted_banks attempted_uei attempted_names attempted_series
    failed_issuers failed_models failed_banks failed_uei failed_names failed_series incomplete_series usable_rows
    truncated_issuers truncated_names truncated_uei rejected_rows matched_rows saved_rows usable_banks empty_banks
    due_banks deferred_banks due_uei eligible_uei completed_uei partial_uei terminal_uei resumed_uei
    eligible_names due_names completed_names partial_names resumed_names
    coverage_complete_uei coverage_partial_uei coverage_unchecked_uei coverage_stale_uei
    coverage_complete_names coverage_partial_names coverage_unchecked_names coverage_stale_names
    identity_covered_tickers identity_unmapped_tickers identity_requested_tickers identity_mapped_tickers
    reconstructed_amendment_groups unresolved_amendment_groups identity_linked_candidates
    daily_requests daily_issuers daily_series daily_requests_used daily_request_limit daily_issuer_limit daily_series_limit
    quota_deferred_issuers quota_deferred_series search_lookback_days
    alias_rows comments_rows weak_aliases_screened
""".split())
_BOOLEAN_FIELDS = frozenset({"budget_exhausted", "issuer_coverage_complete",
    "pagination_snapshot_guaranteed", "contract_attribution_allowed",
    "publisher_remarks_complete", "weak_alias_extraction_complete", "company_exposure_verified"})
_HASH_FIELDS = frozenset({"snapshot_sha256", "alias_snapshot_sha256", "comments_snapshot_sha256"})
_QUERY_UNITS = {
    "sec": ("SEC_JOB", ("processed",), ("failed",)),
    "fdic": ("BANK_CERT", ("attempted_banks", "checked_banks"), ("failed_banks",)),
    "usaspending": ("UEI_TRAVERSAL", ("attempted_uei", "checked_uei"), ("failed_uei",)),
    "nhtsa": ("VEHICLE_MODEL", ("checked_models",), ("failed_models",)),
    "eia": ("SHARED_SERIES", ("attempted_series", "checked_series"), ("failed_series",)),
    "fred": ("SHARED_SERIES", (), ()),
    "sec13f": ("DATASET", (), ()),
}
_SHARED_SUBJECTS = {
    "fred": ("MACRO:US",),
    "eia": ("COMMODITY:WTI", "COMMODITY:JET_FUEL_GULF"),
}
_LIMITATIONS = [
    "Preservation starts with captured files. Prior overwritten checks, missing captures and collector failures before recording cannot be reconstructed.",
    "Provider as_of/check/attempt clocks can be logical clocks earlier than physical call start. Clock proximity does not verify association with this run.",
    "A captured current check may belong to an earlier run. A NO_DUE/BUSY/WAIT_QUOTA record is not a performed empty query.",
    "Request RETURNED means the client returned; it does not establish usable payload, complete traversal or issuer relevance. Some ledgers lack outcome timestamps.",
    "Shared FRED/EIA observations and dataset-level 13F checks never establish individual issuer coverage.",
    "A retrospective capture becomes available at capture time, never at its earlier provider or event time. Hashes verify file consistency, not publisher authenticity.",
    "Bounded capture and replay may omit evidence. The current 687-ticker universe is not survivor-free historical coverage.",
    "No labels, return outcomes, out-of-sample lift, specialist acceptance or production scoring are established by these archives.",
]


def specialist_run_archive_directory(store: ScoutStore) -> Path:
    return store.db_path.parent / "specialist_source_runs"


def _capture_clock() -> datetime:
    return datetime.now(timezone.utc)


def _bounded(value: int, maximum: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be a bounded positive integer")


def _summary(value: dict) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("status"), str) or not re.fullmatch(r"[A-Z_]{2,32}", value["status"]):
        raise ValueError("Run archive requires an explicit source status")
    result = {"status": value["status"]}
    for key, item in value.items():
        if key in _COUNT_FIELDS and isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 2**63 - 1:
            result[key] = item
        elif key in _BOOLEAN_FIELDS and isinstance(item, bool):
            result[key] = item
        elif key in _HASH_FIELDS and isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item):
            result[key] = item
        elif key == "retry_at" and (stamp := _utc(item)) is not None:
            result[key] = stamp
        elif key in {"stop_reason", "remarks_supplement_status"} and isinstance(item, str) and re.fullmatch(r"[A-Z_]{2,32}", item):
            result[key] = item
    return result


def _attempt_semantics(source: str, summary: dict) -> dict:
    unit, primary, failures = _QUERY_UNITS.get(source, ("IDENTITY_QUERY", ("attempted_issuers", "checked_issuers"), ("failed_issuers",)))
    count = next((summary[key] for key in primary if key in summary), None)
    # checked_* excludes failures in these collectors. Explicit attempted_*
    # and SEC processed are already attempts, so never count failures twice.
    chosen = next((key for key in primary if key in summary), None)
    if chosen and chosen.startswith("checked_"):
        count += sum(summary.get(key, 0) for key in failures)
    requests = summary.get("attempted_requests")
    performed = (count is not None and count > 0) or (requests is not None and requests > 0)
    state = "PERFORMED_REPORTED" if performed else "NOT_PERFORMED_REPORTED" if (summary["status"] in _NO_ATTEMPT_STATUSES or count == 0 or requests == 0) else "UNREPORTED"
    return {"state": state, "query_unit": unit, "reported_query_units": count,
            "reported_requests": requests, "reason_status": summary["status"],
            "per_ticker_coverage_inferred": False, "relevant_coverage_verified": False,
            "empty_result_inferred_from_zero_new_findings": False}


def _time_basis(stamp: str, started_at: str | None, provider_as_of: str | None, observed_at: str) -> str:
    if started_at and started_at <= stamp <= observed_at:
        return "PHYSICAL_CALL_WINDOW"
    if provider_as_of and provider_as_of <= stamp <= observed_at:
        return "PROVIDER_LOGICAL_WINDOW"
    return "PRIOR_STORED_STATE" if started_at or provider_as_of else "RUN_ASSOCIATION_UNKNOWN"


def _request_evidence(conn, source: str, subjects: list[str], cutoff: str, maximum: int) -> tuple[list[dict], bool]:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    allowed = [*subjects, *_SHARED_SUBJECTS.get(source, ())]
    slots = ",".join("?" for _ in allowed)
    common = f"subject_id IN ({slots}) AND archive_utc(attempted_at)<=?"
    queries = []
    if "scout_public_requests" in tables:
        queries.append(("PUBLIC_REQUEST", f"SELECT request_id, subject_id, identity_key, attempted_at, NULL AS finished_at, outcome FROM scout_public_requests WHERE source=? AND {common} ORDER BY archive_utc(attempted_at) DESC, request_id LIMIT ?", (source, *allowed, cutoff, maximum + 1)))
    if source == "fdic" and "scout_fdic_requests" in tables:
        queries.append(("FDIC_REQUEST", f"SELECT request_id, subject_id, identity_key, attempted_at, finished_at, outcome FROM scout_fdic_requests WHERE {common} ORDER BY archive_utc(attempted_at) DESC, request_id LIMIT ?", (*allowed, cutoff, maximum + 1)))
    if source == "usaspending" and "scout_usaspending_requests" in tables:
        queries.append(("AWARDS_PAGE_REQUEST", f"SELECT request_id, subject_id, identity_key, attempted_at, NULL AS finished_at, outcome FROM scout_usaspending_requests WHERE kind='awards' AND {common} ORDER BY archive_utc(attempted_at) DESC, request_id LIMIT ?", (*allowed, cutoff, maximum + 1)))
    if source == "sec" and {"scout_jobs", "scout_attempts"} <= tables:
        queries.append(("SEC_JOB_ATTEMPT", f"SELECT a.job_id || ':' || a.attempt_no AS request_id, j.subject_id, j.reason AS identity_key, a.started_at AS attempted_at, a.finished_at, a.outcome FROM scout_attempts a JOIN scout_jobs j ON a.job_id=j.job_id WHERE j.source='sec' AND j.subject_id IN ({slots}) AND archive_utc(a.started_at)<=? ORDER BY archive_utc(a.started_at) DESC, request_id LIMIT ?", (*allowed, cutoff, maximum + 1)))
    result, truncated = [], False
    for kind, query, params in queries:
        rows = conn.execute(query, params).fetchall()
        remaining = maximum - len(result)
        truncated |= len(rows) > remaining
        for row in rows[:remaining]:
            finished = _utc(row["finished_at"])
            timestamped = kind in {"FDIC_REQUEST", "SEC_JOB_ATTEMPT"}
            known_finish = bool(finished and finished <= cutoff)
            outcome = row["outcome"] if known_finish or not timestamped else "RUNNING"
            record = {"source": source, "ticker": row["subject_id"] if row["subject_id"] in subjects else None,
                      "scope": "STORED_IDENTITY_REQUEST" if row["subject_id"] in subjects else "SHARED_CONTEXT",
                      "shared_subject_id": row["subject_id"] if row["subject_id"] not in subjects else None,
                      "request_id_sha256": _digest(row["request_id"]), "identity_key_sha256": _digest(row["identity_key"]),
                      "kind": kind, "attempted_at": _utc(row["attempted_at"]),
                      "finished_at": finished if known_finish else None,
                      "outcome": outcome if isinstance(outcome, str) and re.fullmatch(r"[A-Z_]{2,32}", outcome) else "UNKNOWN",
                      "outcome_timestamp_known": known_finish, "run_association_verified": False,
                      "usable_check_inferred": False}
            record["attempt_hash"] = canonical_hash(record)
            result.append(record)
    return sorted(result, key=lambda row: row["attempt_hash"]), truncated


def _shared_observations(conn, source: str, cutoff: str, maximum: int) -> tuple[list[dict], bool]:
    shared = _SHARED_SUBJECTS.get(source, ())
    if not shared or conn.execute("SELECT 1 FROM sqlite_master WHERE name='scout_findings'").fetchone() is None:
        return [], False
    slots = ",".join("?" for _ in shared)
    rows = conn.execute(f"""SELECT finding_id, source, subject_id, source_object_id, content_hash,
        source_url, published_at, available_at, first_observed_at, retrieved_at, verification_status,
        CASE WHEN LENGTH(CAST(details_json AS BLOB))<=? THEN details_json ELSE 'null' END AS details_json
        FROM scout_findings WHERE source=? AND subject_id IN ({slots})
        AND archive_utc(published_at)<=? AND archive_utc(available_at)<=? AND archive_utc(first_observed_at)<=?
        ORDER BY archive_utc(first_observed_at) DESC, finding_id LIMIT ?""",
        (MAX_DETAILS_BYTES, source, *shared, cutoff, cutoff, cutoff, maximum + 1)).fetchall()
    result = []
    rejected = 0
    for row in rows[:maximum]:
        item, issue = _observation(dict(row), cutoff)
        if issue:
            rejected += 1
            continue
        item.update(ticker=None, shared_subject_id=row["subject_id"], scope="SHARED_CONTEXT")
        item["observation_hash"] = canonical_hash({key: value for key, value in item.items()
            if key not in {"observation_hash", "latest_retrieved_at_known_at_cutoff"}})
        result.append(item)
    return result, len(rows) > maximum or rejected > 0


def _run_id(source: str, observed_at: str) -> str:
    return canonical_hash({"source": source, "observed_at": observed_at})


def _publish(snapshot: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_RUN_ARCHIVE_BYTES:
        raise ValueError("Run archive exceeds the byte bound")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".source_run_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # The immutable identity index makes simultaneous retries choose the
        # first complete capture. Both names link to the same complete bytes.
        # A retry also repairs a crash between publishing these two names.
        marker = directory / f"source_run_identity_{snapshot['source_run_id']}.json"
        try:
            os.link(temporary, marker)
        except FileExistsError:
            preserved = load_specialist_source_run_archive(marker, _require_content_filename=False)
            for key in ("source", "source_run_id", "observed_at", "started_at", "provider_as_of", "evidence_origin", "run_summary"):
                if preserved[key] != snapshot[key]:
                    raise ValueError("Existing source run differs; refusing archive rewrite")
            snapshot = preserved
            encoded = marker.read_bytes()
        path = directory / f"specialist_source_run_{snapshot['source_run_id']}_{snapshot['snapshot_hash']}.json"
        try:
            os.link(marker, path)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError("Existing run archive differs; refusing overwrite")
        if os.name != "nt":
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _recorded_summary(conn, source: str, cutoff: str) -> dict:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='scout_source_runs'").fetchone() is None:
        raise ValueError("Source run must be recorded before capture")
    row = conn.execute("""SELECT status, CASE WHEN LENGTH(CAST(summary_json AS BLOB))<=?
        THEN summary_json ELSE 'null' END AS summary_json FROM scout_source_runs
        WHERE source=? AND archive_utc(observed_at)=?""", (MAX_DETAILS_BYTES, source, cutoff)).fetchone()
    if row is None:
        raise ValueError("Exact source run must be recorded before capture")
    try:
        recorded = _summary(json.loads(row["summary_json"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Recorded source run summary is invalid") from exc
    if recorded["status"] != row["status"]:
        raise ValueError("Recorded source run status differs from its summary")
    return recorded


def capture_specialist_source_run(
    store: ScoutStore, *, source: str, observed_at: datetime,
    summary: dict | None = None, started_at: datetime | None = None,
    provider_as_of: datetime | None = None, directory: Path | None = None,
    maximum_observations: int = 2_000, observations_per_source_ticker: int = 20,
    maximum_checks: int = 2_000, maximum_attempts: int = 2_000,
    maximum_shared_observations: int = 20,
    evidence_origin: str = "RECORDED_SCOUTSTORE_STATE",
) -> Path:
    """Hook to call immediately after ``store.record_source_run``.

    ``observed_at`` must be that exact record's completion clock;
    ``started_at`` is physical call start and ``provider_as_of`` is the clock
    passed to the collector. Read one SQLite snapshot; write only archive files.
    Unsupported auxiliary routes (e.g. recipient_discovery) are not silently
    credited to a supported source. Failures should be reported by the caller.
    """
    cutoff = _utc(observed_at)
    start = _utc(started_at) if started_at is not None else None
    provider = _utc(provider_as_of) if provider_as_of is not None else None
    if source not in SUPPORTED_INCREMENTAL_SOURCES or evidence_origin not in _ORIGINS:
        raise ValueError("Unsupported source or evidence origin")
    if cutoff is None or (started_at is not None and start is None) or (provider_as_of is not None and provider is None):
        raise ValueError("Run archive clocks need explicit timezones")
    if (start and start > cutoff) or (provider and provider > cutoff):
        raise ValueError("Run start and provider clocks cannot follow completion")
    for value, maximum, name in ((maximum_observations, 20_000, "maximum_observations"),
        (observations_per_source_ticker, 100, "observations_per_source_ticker"),
        (maximum_checks, 20_000, "maximum_checks"), (maximum_attempts, 20_000, "maximum_attempts"),
        (maximum_shared_observations, 100, "maximum_shared_observations")):
        _bounded(value, maximum, name)
    supplied = _summary(summary) if summary is not None else None
    directory = Path(directory) if directory is not None else specialist_run_archive_directory(store)
    leaf = directory / source
    identity = _run_id(source, cutoff)
    if not store.db_path.is_file():
        raise FileNotFoundError("Run capture requires an existing ScoutStore database")
    with store._connect() as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.create_function("archive_utc", 1, _utc, deterministic=True)
        conn.execute("BEGIN")
        recorded = _recorded_summary(conn, source, cutoff)
        if supplied is not None and any(supplied.get(key) != value for key, value in recorded.items()):
            raise ValueError("Supplied summary differs from the recorded source run")
        run_summary = supplied if supplied is not None else recorded
        marker = leaf / f"source_run_identity_{identity}.json"
        if marker.is_file():
            saved = load_specialist_source_run_archive(marker, _require_content_filename=False)
            if saved["run_summary"] != run_summary or saved["recorded_summary_hash"] != canonical_hash(recorded):
                raise ValueError("Recorded source run was changed; refusing archive rewrite")
            if (saved["started_at"], saved["provider_as_of"], saved["evidence_origin"]) != (start, provider, evidence_origin):
                raise ValueError("Source run clocks or evidence origin differ from preserved capture")
            return _publish(saved, leaf)
        archive = build_specialist_source_archive(store, as_of=observed_at, source=source,
            maximum_observations=maximum_observations,
            observations_per_source_ticker=observations_per_source_ticker,
            maximum_checks=maximum_checks, _read_connection=conn)
        subjects = [item["ticker"] for item in archive["universe"]["records"]]
        attempts, attempt_limit = _request_evidence(conn, source, subjects, cutoff, maximum_attempts)
        shared, shared_limit = _shared_observations(conn, source, cutoff, maximum_shared_observations)
    captured = _utc(_capture_clock())
    if captured is None or captured < cutoff:
        raise ValueError("Actual capture time cannot precede source run completion")
    snapshot = {
        "schema_version": RUN_ARCHIVE_SCHEMA_VERSION, "source": source, "source_run_id": identity,
        "observed_at": cutoff, "started_at": start, "provider_as_of": provider,
        "available_at": captured, "captured_at": captured, "evidence_origin": evidence_origin,
        "run_summary": run_summary, "recorded_summary_hash": canonical_hash(recorded),
        "attempt_semantics": _attempt_semantics(source, run_summary),
        "check_time_basis": [{"check_hash": item["check_hash"],
            "time_basis": _time_basis(item["checked_at"], start, provider, cutoff),
            "run_association_verified": False} for item in archive["checks"]],
        "attempts": attempts, "shared_context_observations": shared,
        "source_archive": archive,
        "bounds": {"maximum_attempts": maximum_attempts, "attempt_export_limit_reached": attempt_limit,
                   "maximum_shared_observations": maximum_shared_observations, "shared_export_limit_or_rejection": shared_limit},
        "historical_checks_complete": False, "relevant_coverage_verified": False,
        "live_provider_access_independently_verified": False, "out_of_sample_evaluation_verified": False,
        "production_scores_changed": False, "prediction_snapshots_changed": False,
        "limitations": list(_LIMITATIONS),
    }
    snapshot["snapshot_hash"] = canonical_hash(snapshot)
    return _publish(snapshot, leaf)


def load_specialist_source_run_archive(path: Path, *, _require_content_filename: bool = True) -> dict:
    """Read an immutable run, checking content hashes, scope and clock order."""
    path = Path(path)
    with path.open("rb") as handle:
        encoded = handle.read(MAX_RUN_ARCHIVE_BYTES + 1)
    if len(encoded) > MAX_RUN_ARCHIVE_BYTES:
        raise ValueError("Run archive exceeds the byte bound")
    try:
        item = json.loads(encoded)
        source = item["source"]
        archive = item["source_archive"]
        if (item["schema_version"] != RUN_ARCHIVE_SCHEMA_VERSION or source not in SOURCES
                or item["snapshot_hash"] != canonical_hash({key: value for key, value in item.items() if key != "snapshot_hash"})
                or item["source_run_id"] != _run_id(source, item["observed_at"])
                or (_require_content_filename and path.name != f"specialist_source_run_{item['source_run_id']}_{item['snapshot_hash']}.json")
                or archive["schema_version"] != ARCHIVE_SCHEMA_VERSION
                or archive["archive_hash"] != canonical_hash({key: value for key, value in archive.items() if key != "archive_hash"})):
            raise ValueError("Run archive schema, filename or content hash is invalid")
        for field in ("observed_at", "available_at", "captured_at"):
            if _utc(item[field]) != item[field]:
                raise ValueError("Run archive clock is invalid")
        if not item["observed_at"] <= item["available_at"] == item["captured_at"] or archive["as_of"] != item["observed_at"]:
            raise ValueError("Run archive availability cannot precede observation")
        for field in ("started_at", "provider_as_of"):
            if item[field] is not None and (_utc(item[field]) != item[field] or item[field] > item["observed_at"]):
                raise ValueError("Run archive start or provider clock is invalid")
        canonical = load_canonical_ticker_records()
        universe = archive["universe"]
        if universe["records"] != canonical or universe["csv_sha256"] != CANONICAL_CSV_SHA256 or universe["ticker_count"] != CANONICAL_TICKER_COUNT or set(archive["source_routing"]) != {source}:
            raise ValueError("Run archive requires the exact canonical 687 ticker scope")
        subjects = {row["ticker"] for row in canonical}
        cells = archive["source_ticker_missingness"]
        if len(cells) != CANONICAL_TICKER_COUNT or {(cell["source"], cell["ticker"]) for cell in cells} != {(source, ticker) for ticker in subjects}:
            raise ValueError("Run archive source/ticker scope is invalid")
        if item["evidence_origin"] not in _ORIGINS or _summary(item["run_summary"]) != item["run_summary"] or item["attempt_semantics"] != _attempt_semantics(source, item["run_summary"]):
            raise ValueError("Run archive source semantics are invalid")
        for value, maximum, name in ((archive["bounds"]["maximum_observations"], 20_000, "maximum_observations"),
            (archive["bounds"]["observations_per_source_ticker"], 100, "observations_per_source_ticker"),
            (archive["bounds"]["maximum_checks"], 20_000, "maximum_checks"),
            (item["bounds"]["maximum_attempts"], 20_000, "maximum_attempts"),
            (item["bounds"]["maximum_shared_observations"], 100, "maximum_shared_observations")):
            _bounded(value, maximum, name)
        if (len(archive["observations"]) > archive["bounds"]["maximum_observations"]
            or len(archive["checks"]) > archive["bounds"]["maximum_checks"]
            or len(item["attempts"]) > item["bounds"]["maximum_attempts"]
            or len(item["shared_context_observations"]) > item["bounds"]["maximum_shared_observations"]):
            raise ValueError("Run archive evidence exceeds its declared bounds")
        for flag in ("historical_checks_complete", "relevant_coverage_verified", "live_provider_access_independently_verified", "out_of_sample_evaluation_verified", "production_scores_changed", "prediction_snapshots_changed"):
            if item[flag] is not False:
                raise ValueError("Run archive cannot assert acceptance, performance or complete coverage")
        if archive["analysis_only"] is not True or any(archive[flag] is not False for flag in ("completion_verified", "out_of_sample_evaluation_verified", "production_scores_changed", "prediction_snapshots_changed")) or any(cell[flag] is not False for cell in cells for flag in ("relevant_coverage_verified", "historical_completeness_reconstructible")):
            raise ValueError("Source state cannot assert acceptance, performance or complete coverage")
        if archive["source_runs"] != [{"source": source, "observed_at": item["observed_at"],
            "status": item["run_summary"]["status"], "per_ticker_coverage_inferred": False}]:
            raise ValueError("Source state must contain the exact recorded source run")
        for row in [*archive["observations"], *item["shared_context_observations"]]:
            if row["source"] != source or (row["ticker"] is not None and row["ticker"] not in subjects) or (row["ticker"] is None and row.get("shared_subject_id") not in _SHARED_SUBJECTS.get(source, ())):
                raise ValueError("Observation leaves the permitted source scope")
            if row["observation_hash"] != canonical_hash({key: value for key, value in row.items() if key not in {"observation_hash", "latest_retrieved_at_known_at_cutoff"}}):
                raise ValueError("Observation hash is invalid")
            if any(_utc(row[field]) != row[field] for field in ("stored_publication_at", "available_at", "first_observed_at", "knowledge_at")) or not row["stored_publication_at"] <= row["available_at"] <= row["first_observed_at"] <= row["knowledge_at"] <= item["observed_at"]:
                raise ValueError("Observation clock order is invalid")
            times = list(row["identity_knowledge_times"].values())
            if any(_utc(stamp) != stamp or stamp > row["first_observed_at"] for stamp in times) or row["knowledge_at"] != max(row["available_at"], row["first_observed_at"], *times):
                raise ValueError("Observation identity knowledge clock is invalid")
            heartbeat = row["latest_retrieved_at_known_at_cutoff"]
            if heartbeat is not None and (_utc(heartbeat) != heartbeat or heartbeat > item["observed_at"]):
                raise ValueError("Observation heartbeat clock is invalid")
        for kind, rows, hash_key, clock in (("check", archive["checks"], "check_hash", "checked_at"), ("attempt", item["attempts"], "attempt_hash", "attempted_at")):
            for row in rows:
                if row["source"] != source or (row["ticker"] is not None and row["ticker"] not in subjects) or row[hash_key] != canonical_hash({key: value for key, value in row.items() if key != hash_key}) or _utc(row[clock]) != row[clock] or row[clock] > item["observed_at"]:
                    raise ValueError(f"Run archive {kind} hash, clock or scope is invalid")
                if row["ticker"] is None and ((kind == "check" and (source != "sec13f" or row["scope"] != "DATASET")) or (kind == "attempt" and row.get("shared_subject_id") not in _SHARED_SUBJECTS.get(source, ()))):
                    raise ValueError(f"Run archive {kind} cannot imply shared issuer coverage")
                if kind == "attempt" and row["finished_at"] is not None and (_utc(row["finished_at"]) != row["finished_at"] or not row[clock] <= row["finished_at"] <= item["observed_at"]):
                    raise ValueError("Run archive request finish clock is invalid")
        basis = [{"check_hash": row["check_hash"], "time_basis": _time_basis(row["checked_at"], item["started_at"], item["provider_as_of"], item["observed_at"]), "run_association_verified": False} for row in archive["checks"]]
        if basis != item["check_time_basis"]:
            raise ValueError("Run archive check time basis is invalid")
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("Run archive structure is invalid") from exc
    return item


def replay_specialist_source_history(
    directory: Path, *, as_of: datetime, source: str | None = None,
    maximum_archives: int = 200, maximum_observations: int = 20_000,
    maximum_checks: int = 20_000, maximum_attempts: int = 20_000,
) -> dict:
    """Replay only captures actually available at the requested cutoff.

    Scanning, bytes per file and returned evidence counts are bounded. If the
    directory exceeds the scan limit, the inspected subset is explicitly
    incomplete; it must not be used to assert latest or complete history.
    """
    cutoff = _utc(as_of)
    if cutoff is None or (source is not None and source not in SOURCES):
        raise ValueError("Replay requires an aware cutoff and an approved source")
    for value, maximum, name in ((maximum_archives, 2_000, "maximum_archives"),
        (maximum_observations, 20_000, "maximum_observations"), (maximum_checks, 20_000, "maximum_checks"),
        (maximum_attempts, 20_000, "maximum_attempts")):
        _bounded(value, maximum, name)
    directory = Path(directory)
    sources = (source,) if source is not None else SOURCES
    maximum_directory_entries = maximum_archives * 3
    directory_entries_scanned = 0
    directory_scan_limited = False

    def paths():
        nonlocal directory_entries_scanned, directory_scan_limited
        for selected in sources:
            leaf = directory / selected
            if leaf.is_dir():
                with os.scandir(leaf) as entries:
                    for entry in entries:
                        if directory_entries_scanned >= maximum_directory_entries:
                            directory_scan_limited = True
                            return
                        directory_entries_scanned += 1
                        if entry.is_file(follow_symlinks=False) and entry.name.startswith("specialist_source_run_") and entry.name.endswith(".json"):
                            yield Path(entry.path)

    inspected = list(islice(paths(), maximum_archives + 1))
    limits = {"archive_scan_limit_reached": len(inspected) > maximum_archives or directory_scan_limited,
              "directory_entry_scan_limit_reached": directory_scan_limited,
              "capture_export_incomplete": False,
              "observation_history_limit_reached": False, "check_history_limit_reached": False,
              "attempt_history_limit_reached": False}
    runs, excluded = [], 0
    buckets = {"observations": {}, "checks": {}, "attempts": {}}
    selected_limits = {"observations": maximum_observations, "checks": maximum_checks, "attempts": maximum_attempts}
    for path in sorted(inspected[:maximum_archives]):
        item = load_specialist_source_run_archive(path)
        if item["source"] not in sources or path.parent.name != item["source"]:
            raise ValueError("Archive directory differs from its recorded source")
        if item["available_at"] > cutoff:
            excluded += 1
            continue
        archive = item["source_archive"]
        limits["capture_export_incomplete"] |= (not archive["bounds"]["observation_export_complete"]
            or archive["bounds"]["check_export_limit_reached"]
            or bool(archive["input"]["rejected_reasons"])
            or item["bounds"]["attempt_export_limit_reached"] or item["bounds"]["shared_export_limit_or_rejection"])
        runs.append({key: item[key] for key in ("source", "source_run_id", "observed_at", "started_at", "provider_as_of", "available_at", "snapshot_hash", "evidence_origin", "run_summary", "attempt_semantics")})
        for name, rows, hash_key in (("observations", [*archive["observations"], *item["shared_context_observations"]], "observation_hash"), ("checks", archive["checks"], "check_hash"), ("attempts", item["attempts"], "attempt_hash")):
            for row in rows:
                key = row[hash_key]
                previous = buckets[name].get(key)
                if previous is not None:
                    if item["available_at"] < previous["archive_available_at"]:
                        previous.update(record=row, archive_available_at=item["available_at"], source_run_id=item["source_run_id"])
                    continue
                if len(buckets[name]) >= selected_limits[name]:
                    limits[{"observations": "observation_history_limit_reached", "checks": "check_history_limit_reached", "attempts": "attempt_history_limit_reached"}[name]] = True
                    continue
                buckets[name][key] = {"record": row, "archive_available_at": item["available_at"], "source_run_id": item["source_run_id"]}
    checked, observed = defaultdict(list), defaultdict(list)
    for item in buckets["checks"].values():
        row = item["record"]
        if row["ticker"] is not None:
            checked[row["source"], row["ticker"]].append(item)
    for item in buckets["observations"].values():
        row = item["record"]
        if row["ticker"] is not None:
            observed[row["source"], row["ticker"]].append(item)
    records = load_canonical_ticker_records()
    matrix = []
    for selected in sources:
        for ticker in (row["ticker"] for row in records):
            history = checked[selected, ticker]
            latest = max(history, key=lambda row: (row["record"]["checked_at"], row["archive_available_at"], row["record"]["check_hash"]), default=None)
            matrix.append({"source": selected, "ticker": ticker, "preserved_check_versions": len(history),
                "preserved_observation_versions": len(observed[selected, ticker]),
                "latest_preserved_check_status": latest["record"]["status"] if latest else "UNKNOWN",
                "latest_preserved_check_at": latest["record"]["checked_at"] if latest else None,
                "latest_preserved_check_archive_available_at": latest["archive_available_at"] if latest else None,
                "relevant_coverage_verified": False, "historical_checks_complete": False})
    result = {"schema_version": REPLAY_SCHEMA_VERSION, "as_of": cutoff,
        "universe": {"ticker_count": len(records), "csv_sha256": CANONICAL_CSV_SHA256, "records": records},
        "source_runs": sorted(runs, key=lambda row: (row["available_at"], row["source_run_id"])),
        **{key: [value for _, value in sorted(bucket.items())] for key, bucket in buckets.items()},
        "source_ticker_history": matrix,
        "bounds": {"maximum_archives": maximum_archives, "maximum_observations": maximum_observations,
                   "maximum_checks": maximum_checks, "maximum_attempts": maximum_attempts,
                   "maximum_directory_entries": maximum_directory_entries, **limits},
        "input": {"inspected_archives": min(len(inspected), maximum_archives), "replayed_archives": len(runs),
                  "directory_entries_scanned": directory_entries_scanned, "excluded_not_yet_available": excluded},
        "historical_checks_complete": False, "relevant_coverage_verified": False,
        "out_of_sample_evaluation_verified": False, "production_scores_changed": False,
        "prediction_snapshots_changed": False, "limitations": list(_LIMITATIONS)}
    result["replay_hash"] = canonical_hash(result)
    return result
