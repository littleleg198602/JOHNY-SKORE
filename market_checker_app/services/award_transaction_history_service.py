"""Bounded USAspending transaction rows for one already verified prime award.

This reads the publisher's transaction-history endpoint. Award-total changes
are never inputs. Terminal pagination describes this traversal only; it cannot
prove preservation of every historical source revision, deletion or correction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
from http.client import HTTPException, IncompleteRead
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from market_checker_app.services.usaspending_request_budget import (
    DAILY_REQUEST_LIMIT, MAX_RESPONSE_BYTES, UsaSpendingBudgetExhausted,
    UsaSpendingRunBudget, bounded_positive, bounded_seconds, page_has_next,
    page_rows,
)
from market_checker_app.services.usaspending_scout_service import (
    API_URL as AWARD_SEARCH_URL, _validate_identities, load_verified_identities,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import UsaSpendingCheckpointStore


SOURCE_URL = "https://api.usaspending.gov/api/v2/transactions/"
DOCUMENTATION_URL = (
    "https://github.com/fedspendingtransparency/usaspending-api/blob/master/"
    "usaspending_api/api_contracts/contracts/v2/transactions.md"
)
CAPTURE_VERSION = "award_transaction_capture_v1"
COLLECTOR_VERSION = "award_transaction_history_v1"
MAX_MANIFEST_BYTES = 32_768
MAX_ROW_BYTES = 65_536
MODIFIED_FIELDS = frozenset({"last_modified_date", "last_modified", "modified_at",
                             "updated_at", "update_date"})


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Transaction capture times need an explicit timezone")
    return value.astimezone(timezone.utc)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite USAspending JSON value: {value}")


def _request(award_id: str, page: int, limit: int) -> dict:
    if not isinstance(award_id, str) or not re.fullmatch(r"CONT_AWD_[A-Z0-9_-]{1,490}", award_id):
        raise ValueError("Transaction history needs a generated prime-contract award ID")
    bounded_positive(page, 100_000, "Transaction page")
    bounded_positive(limit, 100, "Transaction page size")
    return {"award_id": award_id, "page": page, "limit": limit,
            "sort": "action_date", "order": "asc"}


class _TransactionRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != SOURCE_URL:
            raise ValueError("Transaction redirects must stay on the documented endpoint")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


urlopen = build_opener(_TransactionRedirectHandler()).open


@dataclass(frozen=True)
class CapturedTransactionPage:
    body: bytes
    metadata: dict


class TransactionsClient(Protocol):
    capture_mode: str

    def transactions(self, award_id: str, *, page: int, limit: int,
                     timeout: float) -> CapturedTransactionPage: ...


def _read_response(response, *, deadline: float, maximum_bytes: int) -> bytes:
    expected = getattr(response, "length", None)
    if expected is not None and expected > maximum_bytes:
        raise ValueError("Transaction response exceeds byte budget")
    chunks, size = [], 0
    read = getattr(response, "read1", response.read)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Transaction response time budget exhausted")
            raw = getattr(getattr(response, "fp", None), "raw", None)
            socket = getattr(raw, "_sock", None)
            if socket is not None:
                socket.settimeout(remaining)
            chunk = read(min(65_536, maximum_bytes + 1 - size))
            if chunk:
                chunks.append(chunk)
                size += len(chunk)
            if size > maximum_bytes:
                raise ValueError("Transaction response exceeds byte budget")
            if time.monotonic() >= deadline:
                raise TimeoutError("Transaction response time budget exhausted")
            if not chunk:
                break
    except (OSError, HTTPException, ValueError) as error:
        # Keep bounded partial bytes as a failed capture, never usable JSON.
        partial = getattr(error, "partial", b"")
        error.partial = (b"".join(chunks) + (partial if isinstance(partial, bytes) else b""))[:maximum_bytes]
        raise
    body = b"".join(chunks)
    if expected is not None and size != expected:
        raise IncompleteRead(body, expected - size)
    return body


class UsaSpendingTransactionsClient:
    """Unauthenticated public HTTP; no fallback hosts or retries."""

    capture_mode = "LIVE"

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        bounded_positive(max_response_bytes, MAX_RESPONSE_BYTES, "Transaction byte budget")
        self.max_response_bytes = max_response_bytes

    def transactions(self, award_id: str, *, page: int, limit: int,
                     timeout: float = 8) -> CapturedTransactionPage:
        bounded_seconds(timeout, 20)
        payload = _request(award_id, page, limit)
        metadata = {"schema_version": CAPTURE_VERSION, "capture_mode": self.capture_mode,
                    "source_url": SOURCE_URL, "method": "POST", "request": payload,
                    "request_started_at": datetime.now(timezone.utc).isoformat(),
                    "timeout_seconds": timeout, "byte_limit": self.max_response_bytes,
                    "http_status": None, "response_headers": {}, "complete_response": False}
        deadline = time.monotonic() + timeout
        request = Request(SOURCE_URL, data=_json(payload), method="POST",
                          headers={"Content-Type": "application/json", "User-Agent": "JohnySkoreScout/1.0"})
        body = b""
        try:
            try:
                response = urlopen(request, timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                if response.geturl() != SOURCE_URL:
                    raise ValueError("Transaction response must use the documented endpoint")
                metadata["source_url_final"] = response.geturl()
                metadata["http_status"] = response.status
                metadata["response_headers"] = {
                    key: value for key, value in response.headers.items()
                    if key.lower() in {"date", "content-type", "content-length", "last-modified", "etag", "retry-after"}
                }
                body = _read_response(response, deadline=deadline, maximum_bytes=self.max_response_bytes)
                metadata["complete_response"] = True
                if response.status != 200:
                    metadata["error_type"] = "HTTP_ERROR"
        except (OSError, HTTPException, ValueError) as error:
            metadata["error_type"] = type(error).__name__
            partial = getattr(error, "partial", b"")
            if isinstance(partial, bytes):
                body = partial[:self.max_response_bytes]
        metadata["response_completed_at"] = datetime.now(timezone.utc).isoformat()
        metadata["response_bytes"] = len(body)
        metadata["response_sha256"] = hashlib.sha256(body).hexdigest()
        return CapturedTransactionPage(body, metadata)


def _read_file(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("Archived transaction file exceeds byte budget")
    return body


def _validate_capture(capture: CapturedTransactionPage, *, request: dict, mode: str) -> None:
    if not isinstance(capture, CapturedTransactionPage) or mode not in {"LIVE", "ARCHIVE_REPLAY", "FIXTURE"}:
        raise ValueError("Invalid transaction capture type or acquisition mode")
    meta = capture.metadata
    if (not isinstance(capture.body, bytes) or len(capture.body) > MAX_RESPONSE_BYTES
            or not isinstance(meta, dict) or meta.get("schema_version") != CAPTURE_VERSION
            or meta.get("capture_mode") != mode or meta.get("source_url") != SOURCE_URL
            or meta.get("method") != "POST" or meta.get("request") != request
            or meta.get("response_sha256") != hashlib.sha256(capture.body).hexdigest()
            or isinstance(meta.get("response_bytes"), bool) or meta.get("response_bytes") != len(capture.body)
            or not isinstance(meta.get("complete_response"), bool)
            or (meta.get("http_status") is not None and
                (isinstance(meta["http_status"], bool) or not isinstance(meta["http_status"], int)
                 or not 100 <= meta["http_status"] <= 599))
            or (meta.get("source_url_final") is not None and meta["source_url_final"] != SOURCE_URL)):
        raise ValueError("Invalid transaction source capture")
    if _utc(meta["request_started_at"]) > _utc(meta["response_completed_at"]):
        raise ValueError("Transaction capture has reversed observation times")
    if len(_json(meta)) > MAX_MANIFEST_BYTES:
        raise ValueError("Transaction capture manifest exceeds byte budget")


def _write_immutable(path: Path, body: bytes, *, maximum: int) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="capture-write-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
        try:
            # Publishing a fully written inode prevents crashed writes from
            # occupying the content-addressed name with a truncated object.
            os.link(temporary, path)
        except FileExistsError:
            if _read_file(path, maximum) != body:
                raise ValueError("Archived transaction object collision or corruption")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def archive_transaction_capture(capture: CapturedTransactionPage, directory: Path) -> Path:
    """One immutable body per byte hash; each observation keeps its own times."""
    meta = capture.metadata
    _validate_capture(capture, request=meta["request"], mode=meta["capture_mode"])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"body-{meta['response_sha256']}.response"
    body_path = directory / name
    _write_immutable(body_path, capture.body, maximum=MAX_RESPONSE_BYTES)
    manifest = directory / f"capture-{uuid4().hex}.json"
    _write_immutable(manifest, _json({**meta, "body_file": name}), maximum=MAX_MANIFEST_BYTES)
    return manifest


class ArchivedTransactionsClient:
    """Replay verified bytes of explicit LIVE captures, without network access.

    Local archive integrity is checked; the manifest is not a publisher-signed
    proof of origin. A replay is always labelled ARCHIVE_REPLAY, never LIVE.
    """

    capture_mode = "ARCHIVE_REPLAY"

    def __init__(self, manifests: list[Path]) -> None:
        if not isinstance(manifests, list) or not 1 <= len(manifests) <= 5:
            raise ValueError("Transaction replay needs one to five capture manifests")
        self.manifests = [Path(path) for path in manifests]

    def transactions(self, award_id: str, *, page: int, limit: int,
                     timeout: float = 8) -> CapturedTransactionPage:
        bounded_seconds(timeout, 20)
        request = _request(award_id, page, limit)
        for path in self.manifests:
            meta = json.loads(_read_file(path, MAX_MANIFEST_BYTES), parse_constant=_reject_constant)
            if not isinstance(meta, dict):
                raise ValueError("Transaction replay manifest must be an object")
            if meta.get("request") != request:
                continue
            name = meta.get("body_file")
            if not isinstance(name, str) or not re.fullmatch(r"body-[a-f0-9]{64}\.response", name):
                raise ValueError("Invalid archived transaction body reference")
            body = _read_file(path.parent / name, MAX_RESPONSE_BYTES)
            original = CapturedTransactionPage(body, {key: value for key, value in meta.items() if key != "body_file"})
            _validate_capture(original, request=request, mode="LIVE")
            return CapturedTransactionPage(body, {**original.metadata, "capture_mode": self.capture_mode,
                                                  "original_capture_mode": "LIVE",
                                                  "original_capture_manifest": path.name})
        raise ValueError("No archived source capture for the requested transaction page")


def _verified_target(store: ScoutStore, identities: list[dict], *, subject_id: str,
                     finding_id: str, clock: datetime) -> tuple[dict, dict, dict]:
    row = store.finding_by_id(finding_id, subject_id=subject_id)
    if row is None or row.get("source") != "usaspending" or row.get("source_url") != AWARD_SEARCH_URL:
        raise ValueError("Transaction target needs an existing official USAspending award observation")
    details = json.loads(row["details_json"])
    if not isinstance(details, dict) or details.get("stage") != "prime_contract" or row.get("verification_status") != "SOURCE_VERIFIED":
        raise ValueError("Transaction target needs a source-verified prime award")
    published, available, observed = (_utc(row[key]) for key in ("published_at", "available_at", "first_observed_at"))
    if not published <= available <= observed <= clock:
        raise ValueError("Transaction target was not observed as of the requested time")
    entries = [entry for entry in identities if entry["ticker"] == subject_id and entry["uei"] == details.get("uei")]
    if len(entries) != 1:
        raise ValueError("Transaction target needs exactly one verified ticker/UEI relationship")
    entry = entries[0]
    if (not isinstance(details.get("recipient_name"), str)
            or details["recipient_name"].casefold().split() != entry["recipient_name"].casefold().split()
            or row.get("source_object_id") != f"{entry['uei']}:{details.get('generated_internal_id')}"
            or any(details.get(key) != entry.get(manifest_key) for key, manifest_key in (
                ("uei_evidence_url", "uei_evidence_url"), ("relationship_evidence_url", "relationship_evidence_url"),
                ("continuity_evidence_url", "continuity_evidence_url"), ("relationship_effective_from", "effective_from"),
                ("relationship_effective_to", "effective_to"), ("relationship_known_at", "known_at")))):
        raise ValueError("Transaction target does not match the verified recipient and dated relationship")
    if _utc(entry["known_at"]) > available:
        raise ValueError("Transaction target predates relationship knowledge")
    start = date.fromisoformat(details["award_start"])
    if (start < date.fromisoformat(entry["effective_from"]) or start > clock.date()
            or (entry.get("effective_to") and start > date.fromisoformat(entry["effective_to"]))):
        raise ValueError("Transaction target starts outside the documented recipient relationship")
    _request(details["generated_internal_id"], 1, 1)
    return row, details, entry


def _transaction(row: object, *, captured_at: datetime) -> dict:
    if not isinstance(row, dict) or len(_json(row)) > MAX_ROW_BYTES:
        raise ValueError("Malformed transaction row")
    if (not isinstance(row.get("id"), str) or not re.fullmatch(r"CONT_TX_[A-Za-z0-9_-]{1,990}", row["id"])
            or row.get("type") not in {"A", "B", "C", "D"}
            or not isinstance(row.get("action_date"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["action_date"])):
        raise ValueError("Transaction row needs source contract ID, type and action date")
    if date.fromisoformat(row["action_date"]) > captured_at.date():
        raise ValueError("Transaction action date is after source capture")
    for field in ("action_type", "action_type_description", "modification_number", "description", "type_description"):
        if field not in row or (row[field] is not None and not isinstance(row[field], str)):
            raise ValueError("Malformed transaction descriptive fields")
    for field in ("federal_action_obligation", "face_value_loan_guarantee", "original_loan_subsidy_cost"):
        value = row[field]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("Malformed transaction monetary fields")
    return row


class _ReplayBudget:
    """Bound offline reads without pretending they were source HTTP attempts."""

    def __init__(self, *, clock: datetime, maximum_requests: int, maximum_seconds: float):
        self.clock, self.maximum_requests = clock, maximum_requests
        self.started = time.monotonic()
        self.deadline = self.started + maximum_seconds
        self.requests = 0
        self.token = True

    def now(self) -> datetime:
        return self.clock + timedelta(seconds=max(0, time.monotonic() - self.started))

    def remaining(self) -> float:
        return max(0, self.deadline - time.monotonic())

    def call(self, kind, identity_key, subject_id, *, page, fetch):
        if self.remaining() < 1 or self.requests >= self.maximum_requests:
            raise UsaSpendingBudgetExhausted("RUN_BUDGET")
        self.requests += 1
        return fetch()

    def close(self):
        pass


class AwardTransactionHistoryService:
    """Run for an exact existing ScoutStore award finding, with immutable rows.

    Application hook: run(subject_id="LMT", award_finding_id=existing_id).
    LIVE requests share the existing USAspending lease and daily request ledger.
    No award coverage checkpoint is written by this transaction-history lane.
    """

    def __init__(self, store: ScoutStore, *, archive_directory: Path,
                 client: TransactionsClient | None = None, identities: list[dict] | None = None,
                 max_pages: int = 2, page_size: int = 100, max_requests: int = 2,
                 max_run_seconds: float = 30, daily_request_limit: int = DAILY_REQUEST_LIMIT) -> None:
        for value, maximum, label in ((max_pages, 5, "Transaction page budget"),
                                      (page_size, 100, "Transaction page size"),
                                      (max_requests, 5, "Transaction request budget"),
                                      (daily_request_limit, DAILY_REQUEST_LIMIT, "Transaction daily request budget")):
            bounded_positive(value, maximum, label)
        bounded_seconds(max_run_seconds, 60)
        self.store, self.archive_directory = store, Path(archive_directory)
        self.client = client if client is not None else UsaSpendingTransactionsClient()
        if self.client.capture_mode not in {"LIVE", "ARCHIVE_REPLAY", "FIXTURE"}:
            raise ValueError("Transaction client must disclose LIVE, ARCHIVE_REPLAY or FIXTURE acquisition")
        self.identities = _validate_identities(identities if identities is not None else load_verified_identities())
        self.max_pages, self.page_size, self.max_requests = max_pages, page_size, max_requests
        self.max_run_seconds, self.daily_request_limit = max_run_seconds, daily_request_limit

    def run(self, *, subject_id: str, award_finding_id: str, as_of: datetime | None = None) -> dict:
        clock = _utc(as_of or datetime.now(timezone.utc))
        result = {"status": "ERROR", "collector_version": COLLECTOR_VERSION,
                  "source_url": SOURCE_URL, "documentation_url": DOCUMENTATION_URL,
                  "acquisition_mode": self.client.capture_mode, "subject_id": subject_id,
                  "award_finding_id": award_finding_id, "new_findings": 0,
                  "attempted_requests": 0, "io_attempts": 0, "captured_pages": 0,
                  "accepted_rows": 0, "rejected_rows": 0, "duplicate_rows": 0,
                  "conflicting_transaction_ids": 0, "attributable_rows": 0,
                  "traversal_complete": False, "pagination_snapshot_guaranteed": False,
                  "modification_history_complete": False, "issuer_coverage_complete": False,
                  "source_revision_order_verified": False, "publisher_publication_time_verified": False,
                  "snapshot_totals_differenced": False, "revenue_inferred": False, "company_exposure_inferred": False,
                  "source_order_consistent": True, "source_transactions": [], "capture_manifests": [],
                  "coverage_scope": "Reported transaction rows for one generated award ID in this bounded traversal"}
        try:
            target, award, identity = _verified_target(self.store, self.identities, subject_id=subject_id,
                                                      finding_id=award_finding_id, clock=clock)
        except (ValueError, KeyError, TypeError) as error:
            return {**result, "status": "INVALID_TARGET", "error": str(error)}
        result.update(generated_internal_id=award["generated_internal_id"], uei=identity["uei"],
                      recipient_name=identity["recipient_name"])
        key = "transaction-history:" + hashlib.sha256(_json({"identity": identity,
                                                            "award": award["generated_internal_id"]})).hexdigest()
        live = self.client.capture_mode == "LIVE"
        checkpoints = UsaSpendingCheckpointStore(self.store) if live else None
        budget = (UsaSpendingRunBudget(checkpoints, clock=clock, max_requests=self.max_requests,
                                      max_run_seconds=self.max_run_seconds, daily_limit=self.daily_request_limit)
                  if live else _ReplayBudget(clock=clock, maximum_requests=self.max_requests, maximum_seconds=self.max_run_seconds))
        if not budget.token:
            return {**result, **budget.blocked_summary()}
        seen: dict[str, str] = {}
        previous_action: str | None = None
        try:
            for page in range(1, self.max_pages + 1):
                expected = _request(award["generated_internal_id"], page, self.page_size)
                try:
                    def fetch():
                        capture = self.client.transactions(award["generated_internal_id"], page=page,
                                                           limit=self.page_size, timeout=min(8, budget.remaining()))
                        _validate_capture(capture, request=expected, mode=self.client.capture_mode)
                        manifest = archive_transaction_capture(capture, self.archive_directory)
                        result["capture_manifests"].append(str(manifest))
                        meta = capture.metadata
                        if meta.get("http_status") != 200:
                            if meta.get("http_status") is not None:
                                raise HTTPError(SOURCE_URL, meta["http_status"], "USAspending transaction response", meta["response_headers"], None)
                            raise OSError("Transaction source capture failed: " + str(meta.get("error_type", "UNKNOWN")))
                        if not meta["complete_response"] or meta.get("error_type"):
                            raise OSError("Transaction source response was incomplete")
                        return capture, manifest

                    # The existing shared ledger calls contract requests 'awards'.
                    # A separate identity namespace prevents any coverage collision;
                    # these requests never save ordinary award traversal checkpoints.
                    capture, manifest = budget.call("awards", key, subject_id, page=page, fetch=fetch)
                    captured_at = _utc(capture.metadata["response_completed_at"])
                    if not live and captured_at > budget.now():
                        raise ValueError("Archived transaction response was not available as of this run")
                    payload = json.loads(capture.body, parse_constant=_reject_constant)
                    rows = page_rows(payload, limit=self.page_size)
                    has_next = page_has_next(payload, page=page, rows=rows)
                    meta = payload["page_metadata"]
                    if (meta.get("next") != (page + 1 if has_next else None)
                            or meta.get("previous") != (page - 1 if page > 1 else None)
                            or meta.get("hasPrevious") is not (page > 1)):
                        raise ValueError("Malformed transaction pagination links")
                    observed = max(budget.now(), captured_at)
                    available = max(captured_at, _utc(identity["known_at"]), _utc(target["available_at"]))
                    observed = max(observed, available)
                    for index, raw in enumerate(rows):
                        if budget.remaining() <= 0:
                            raise UsaSpendingBudgetExhausted("RUN_BUDGET")
                        try:
                            row = _transaction(raw, captured_at=captured_at)
                        except (ValueError, KeyError, TypeError, OverflowError):
                            result["rejected_rows"] += 1
                            continue
                        source_hash = hashlib.sha256(_json(row)).hexdigest()
                        transaction_id = row["id"]
                        if transaction_id in seen:
                            if seen[transaction_id] == source_hash:
                                result["duplicate_rows"] += 1
                            else:
                                result["conflicting_transaction_ids"] += 1
                        seen[transaction_id] = source_hash
                        if previous_action is not None and row["action_date"] < previous_action:
                            result["source_order_consistent"] = False
                        previous_action = row["action_date"]
                        action = date.fromisoformat(row["action_date"])
                        attributable = (action >= date.fromisoformat(identity["effective_from"])
                                        and (not identity.get("effective_to") or action <= date.fromisoformat(identity["effective_to"])))
                        modified = {field: row[field] for field in MODIFIED_FIELDS if field in row}
                        stable = {"collector_version": COLLECTOR_VERSION, "source_transaction": row,
                                  "source_origin": "FIXTURE" if self.client.capture_mode == "FIXTURE" else "PUBLISHER_CAPTURE",
                                  "generated_internal_id": award["generated_internal_id"], "verified_identity": identity}
                        details = {"stage": "award_transaction", "transaction_id": transaction_id,
                                   "generated_internal_id": award["generated_internal_id"], "award_id": award.get("award_id"),
                                   "uei": identity["uei"], "recipient_name": identity["recipient_name"],
                                   "action_date": row["action_date"], "modification_number": row["modification_number"],
                                   "reported_transaction_obligation_usd": row["federal_action_obligation"],
                                   "source_modified_fields": modified, "source_transaction": row,
                                   "source_order": {"page": page, "row_index": index, "sort": "action_date", "order": "asc"},
                                   "within_action_date_order_verified": False,
                                   "source_response_sha256": capture.metadata["response_sha256"],
                                   "source_capture_manifest": manifest.name, "source_acquisition_mode": self.client.capture_mode,
                                   "source_request_started_at": capture.metadata["request_started_at"],
                                   "source_response_completed_at": capture.metadata["response_completed_at"],
                                   "collector_version": COLLECTOR_VERSION, "award_finding_id": award_finding_id,
                                   "issuer_attribution_allowed": attributable,
                                   "relationship_effective_from": identity["effective_from"],
                                   "relationship_effective_to": identity.get("effective_to"), "relationship_known_at": identity["known_at"],
                                   "uei_evidence_url": identity["uei_evidence_url"], "relationship_evidence_url": identity["relationship_evidence_url"],
                                   "continuity_evidence_url": identity.get("continuity_evidence_url"),
                                   "action_date_is_publication_time": False, "last_modified_is_publication_time": False,
                                   "publisher_publication_time_verified": False,
                                   "stored_publication_time_basis": "source_response_completed_at",
                                   "source_revision_order_verified": False, "pagination_snapshot_guaranteed": False,
                                   "modification_history_complete": False, "snapshot_totals_differenced": False,
                                   "revenue_inferred": False, "company_exposure_inferred": False, "scoring_applied": False}
                        finding_id, new = self.store.record_finding(
                            source="usaspending", subject_id=subject_id,
                            source_object_id=f"{identity['uei']}:{award['generated_internal_id']}:transaction:{transaction_id}",
                            content_hash=hashlib.sha256(_json(stable)).hexdigest(),
                            title=f"USAspending transaction {transaction_id}", source_url=SOURCE_URL,
                            locator=f"transaction_id:{transaction_id}", published_at=captured_at,
                            available_at=available, observed_at=observed, details=details,
                            verification_status="UNVERIFIED" if self.client.capture_mode == "FIXTURE" else "SOURCE_VERIFIED")
                        result["new_findings"] += int(new)
                        result["accepted_rows"] += 1
                        result["attributable_rows"] += int(attributable)
                        result["source_transactions"].append({"transaction_id": transaction_id, "action_date": row["action_date"],
                                                               "modification_number": row["modification_number"],
                                                               "federal_action_obligation": row["federal_action_obligation"],
                                                               "source_modified_fields": modified, "page": page, "row_index": index,
                                                               "finding_id": finding_id, "source_row_sha256": source_hash})
                    if budget.remaining() <= 0:
                        raise UsaSpendingBudgetExhausted("RUN_BUDGET")
                    result["captured_pages"] += 1
                    if not has_next:
                        result["traversal_complete"] = True
                        break
                except UsaSpendingBudgetExhausted as error:
                    result["stop_reason"] = error.status
                    break
                except (OSError, HTTPException, sqlite3.Error, ValueError, KeyError, TypeError) as error:
                    result["error"] = type(error).__name__
                    result["stop_reason"] = ("RATE_LIMITED" if error.code == 429 else "ACCESS_BLOCKED" if error.code in {401, 403} else "HTTP_ERROR") if isinstance(error, HTTPError) else "SOURCE_ERROR"
                    break
            defects = (result["rejected_rows"] or result["duplicate_rows"] or result["conflicting_transaction_ids"]
                       or not result["source_order_consistent"] or result.get("stop_reason"))
            if result["traversal_complete"] and not defects:
                result["status"] = "COMPLETE" if result["accepted_rows"] else "NO_MATCH"
            else:
                result["status"] = "PARTIAL" if result["captured_pages"] or result["accepted_rows"] else "ERROR"
            if not result["traversal_complete"] and not result.get("stop_reason"):
                result["stop_reason"] = "PAGE_BUDGET"
            result["io_attempts"] = budget.requests
            result["attempted_requests"] = budget.requests if live else 0
            if checkpoints is not None:
                result["daily_requests_used"] = checkpoints.daily_usage(as_of=budget.now())
                result["daily_request_limit"] = self.daily_request_limit
                cooldown = self.store.provider_cooldown("usaspending", as_of=budget.now())
                if cooldown:
                    result["retry_at"] = cooldown["retry_at"]
            return result
        finally:
            budget.close()
