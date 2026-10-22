"""Bounded, source-only NHTSA model-year case reviews and immutable audit history.

A successful empty response is a model-year negative, never issuer-wide absence.
LIVE I/O uses the application's existing durable public request quota and lease.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.services.nhtsa_recall_scout_service import (
    MAX_RESPONSE_BYTES, SUCCESS_MESSAGE, _unique_json_object, _validated_rows,
    recall_url, validate_nhtsa_response_scope,
)
from market_checker_app.services.public_source_budget import (
    PUBLIC_DAILY_LIMITS, PublicBudgetExhausted, PublicSourceBudget,
)
from market_checker_app.storage.scout_store import ScoutStore


def _utc(clock: datetime) -> datetime:
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("NHTSA audit clock requires timezone")
    return clock.astimezone(timezone.utc)


def _hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def read_nhtsa_scope_reviews(store: ScoutStore, *, as_of: datetime, limit: int = 8) -> dict:
    """Read a bounded known-at window without schema, quota or HTTP side effects.

    The physical history column controls availability. Stored case fields are
    returned unchanged; this read does not reclassify an HTTP400 empty response,
    prove issuer-wide absence, or turn an offline case into LIVE acceptance.
    Counts describe only the returned review window, not source coverage.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("NHTSA report limit must be bounded")
    clock = _utc(as_of).isoformat()
    result = {"as_of": clock, "limit": limit, "status": "NO_RECORDED_REVIEWS",
        "reason": "NO_REVIEWS_KNOWN_AT_CUTOFF", "reviews": [],
        "counts_scope": "RETURNED_REVIEW_WINDOW", "read_only": True,
        "complete_negative_model_years": 0, "live_complete_negative_model_years": 0,
        "scope_completeness": "EXACT_MODEL_YEAR_ONLY",
        "issuer_wide_absence_verified": False, "issuer_attribution_verified": False,
        "manufacturer_ownership_inferred": False,
        "historical_availability_reconstructed": False, "scoring_applied": False,
        "prediction_input": False}
    path = Path(store.db_path).resolve()
    if not path.is_file():
        return {**result, "reason": "DATABASE_NOT_FOUND"}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            exists = connection.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='scout_nhtsa_scope_reviews'""").fetchone()
            if not exists:
                return {**result, "reason": "REVIEW_TABLE_ABSENT"}
            rows = connection.execute("""SELECT review_json FROM scout_nhtsa_scope_reviews
                WHERE known_at<=? ORDER BY known_at DESC, review_id LIMIT ?""", (clock, limit)).fetchall()
    except sqlite3.Error:
        return {**result, "status": "REVIEW_READ_ERROR", "reason": "DATABASE_READ_ERROR"}
    try:
        reviews = [json.loads(row[0]) for row in rows]
        if any(not isinstance(review, dict) for review in reviews):
            raise ValueError("Stored NHTSA review must be an object")
    except (ValueError, TypeError):
        return {**result, "status": "REVIEW_READ_ERROR", "reason": "INVALID_REVIEW_JSON"}
    if not reviews:
        return result
    return {**result, "status": "RECORDED_REVIEWS", "reason": "", "reviews": reviews,
        "complete_negative_model_years": sum(row.get("complete_negative") is True for row in reviews),
        "live_complete_negative_model_years": sum(row.get("live_complete_negative_verified") is True
                                                   for row in reviews)}


@dataclass(frozen=True, slots=True)
class NhtsaModelYearScope:
    make: str
    model: str
    model_year: int
    known_at: datetime
    catalog_evidence: dict

    @property
    def key(self) -> str:
        return _hash(json.dumps({"make": self.make, "model": self.model,
            "model_year": self.model_year, "known_at": self.known_at.isoformat(),
            "catalog_sha256": self.catalog_evidence["sha256"]}, sort_keys=True).encode())

    def record(self) -> dict:
        return {"make": self.make, "model": self.model, "model_year": self.model_year,
                "known_at": self.known_at.isoformat(), "catalog_evidence": dict(self.catalog_evidence)}


def documented_model_scope(*, make: str, model: str, model_year: int,
                           catalog_body: bytes, catalog_metadata: dict) -> NhtsaModelYearScope:
    """Validate the exact independently captured official model-year entry."""
    if (any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9., -]{2,80}", name)
            for name in (make, model)) or isinstance(model_year, bool)
            or not isinstance(model_year, int) or not 1990 <= model_year <= 2100):
        raise ValueError("NHTSA review requires exact make/model/year")
    metadata = dict(catalog_metadata)
    if (metadata.get("http_status") != 200 or metadata.get("archive_path") is None
            or metadata.get("public_raw_available") is False
            or len(catalog_body) > MAX_RESPONSE_BYTES
            or metadata.get("raw_response_sha256", metadata.get("archive_sha256")) != _hash(catalog_body)):
        raise ValueError("Model catalog requires successful available exact bytes")
    requested = urlsplit(metadata["requested_url"])
    path = re.fullmatch(r"/api/vehicles/GetModelsForMakeYear/make/([^/]+)/modelyear/(\d{4})", requested.path)
    if (requested.scheme != "https" or requested.hostname != "vpic.nhtsa.dot.gov"
            or requested.port not in {None, 443} or requested.username or requested.password
            or requested.fragment or requested.query != "format=json" or path is None
            or unquote(path[1]).casefold() != make.casefold() or int(path[2]) != model_year
            or metadata.get("final_url", metadata["requested_url"]) != metadata["requested_url"]):
        raise ValueError("Model catalog query is not the exact official make/year")
    payload = json.loads(catalog_body, object_pairs_hook=_unique_json_object)
    rows, count = payload["Results"], payload["Count"]
    if (not isinstance(rows, list) or isinstance(count, bool) or not isinstance(count, int)
            or count != len(rows) or not 1 <= count <= 1000 or payload.get("Message") != SUCCESS_MESSAGE
            or payload.get("SearchCriteria") != f"Make:{unquote(path[1])} | ModelYear:{model_year}"):
        raise ValueError("Incomplete or unsuccessful official model catalog")
    matches = [row for row in rows if isinstance(row, dict)
               and str(row.get("Make_Name", "")).casefold() == make.casefold()
               and str(row.get("Model_Name", "")).casefold() == model.casefold()]
    identities = {(row.get("Make_ID"), row.get("Model_ID")) for row in matches}
    if (len(identities) != 1 or not matches or any(isinstance(v, bool) or not isinstance(v, int) or v < 1
                                                 for v in next(iter(identities)))):
        raise ValueError("Exact model not independently documented or ambiguous")
    known = _utc(datetime.fromisoformat(metadata["observed_at"].replace("Z", "+00:00")))
    started = _utc(datetime.fromisoformat(metadata["started_at"].replace("Z", "+00:00")))
    if started > known:
        raise ValueError("Model catalog observation precedes request")
    make_id, model_id = next(iter(identities))
    evidence = {"source_url": metadata["requested_url"], "archive_path": metadata["archive_path"],
                "sha256": _hash(catalog_body), "observed_at": known.isoformat(),
                "model_id": model_id, "make_id": make_id, "publisher_rows": count,
                "scope": "EXACT_OFFICIAL_MODEL_YEAR_SNAPSHOT"}
    return NhtsaModelYearScope(make, model, model_year, known, evidence)


@dataclass(frozen=True, slots=True)
class NhtsaResponseCapture:
    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    body: bytes
    started_at: datetime
    observed_at: datetime
    origin: str = "ARCHIVED_RESPONSE_REPLAY"
    retry_after: str | None = None


class NhtsaCaptureClient(Protocol):
    def capture(self, scope: NhtsaModelYearScope, *, timeout_seconds: float) -> NhtsaResponseCapture: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Each reservation performs exactly one API request. Redirects remain
        # HTTP-error captures, not an uncounted second network operation.
        return None


class NhtsaRawRecallClient:
    is_live = True

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirect())

    def capture(self, scope: NhtsaModelYearScope, *, timeout_seconds: float = 20) -> NhtsaResponseCapture:
        if (isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 20):
            raise ValueError("NHTSA HTTP time budget must be bounded")
        url = recall_url(scope.make, scope.model, scope.model_year)
        started = datetime.now(timezone.utc)
        try:
            response = self._opener.open(Request(url, headers={"Accept": "application/json",
                "User-Agent": "JohnySkoreScout/1.0"}), timeout=timeout_seconds)
        except HTTPError as error:
            response = error
        with response:
            final = response.geturl()
            validate_nhtsa_response_scope(final, url)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("NHTSA response exceeds byte bound")
            return NhtsaResponseCapture(url, final, response.getcode(),
                response.headers.get("Content-Type", ""), body, started, datetime.now(timezone.utc),
                origin="LIVE_PUBLIC_HTTP", retry_after=response.headers.get("Retry-After"))


_SECRET_FIELD = re.compile(rb'["\'](?:azure_map_key|azureMapsKey|subscriptionKey|api[_-]?key|accessToken|client_secret)["\']\s*:', re.I)


def review_capture(scope: NhtsaModelYearScope, capture: NhtsaResponseCapture, *, reviewed_at: datetime) -> dict:
    """Strict bounded case contract; no network, issuer attribution or scores."""
    reviewed = _utc(reviewed_at)
    started, observed = _utc(capture.started_at), _utc(capture.observed_at)
    url = recall_url(scope.make, scope.model, scope.model_year)
    validate_nhtsa_response_scope(capture.requested_url, url)
    validate_nhtsa_response_scope(capture.final_url, url)
    if (len(capture.body) > MAX_RESPONSE_BYTES or started > observed
            or scope.known_at > started or observed > reviewed
            or isinstance(capture.http_status, bool) or not isinstance(capture.http_status, int)):
        raise ValueError("NHTSA capture violates scope, byte or UTC knowledge bounds")
    content_type = capture.content_type.split(";", 1)[0].strip().casefold()
    withheld = ("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE" if _SECRET_FIELD.search(capture.body)
                else "WITHHELD_UNEXPECTED_CONTENT_TYPE" if content_type != "application/json" else None)
    result = {"schema_version": 1, "scope": scope.record(), "requested_url": url,
        "final_url": capture.final_url, "http_status": capture.http_status,
        "content_type": capture.content_type, "raw_response_bytes": len(capture.body),
        "raw_response_sha256": _hash(capture.body), "started_at": started.isoformat(),
        "source_observed_at": observed.isoformat(), "reviewed_at": reviewed.isoformat(),
        "known_at": max(observed, reviewed, scope.known_at).isoformat(),
        "capture_origin": capture.origin, "publication_status": withheld or "PUBLIC_EXACT_RAW",
        "public_raw_available": withheld is None, "status": "PARTIAL", "reason": "",
        "publisher_count": None, "matched_rows": 0, "rejected_rows": 0,
        "complete_negative": False, "live_complete_negative_verified": False,
        "scope_completeness": "EXACT_MODEL_YEAR_ONLY", "issuer_wide_absence_verified": False,
        "issuer_attribution_verified": False, "manufacturer_ownership_inferred": False,
        "historical_availability_reconstructed": False, "scoring_applied": False, "prediction_input": False}
    if withheld:
        result["reason"] = withheld
        return result
    if capture.http_status != 200:
        result["reason"] = f"HTTP_{capture.http_status}_IS_NOT_COMPLETE_NEGATIVE"
        return result
    try:
        payload = json.loads(capture.body, object_pairs_hook=_unique_json_object)
        rows = _validated_rows(payload)
        if payload.get("Message") != SUCCESS_MESSAGE:
            raise ValueError("Complete review requires publisher success message")
    except (ValueError, TypeError, KeyError):
        result["reason"] = "INVALID_OR_INCOMPLETE_PUBLISHER_ENVELOPE"
        return result
    result["publisher_count"] = len(rows)
    if not rows:
        result.update(status="COMPLETE_NEGATIVE_MODEL_YEAR", complete_negative=True,
            live_complete_negative_verified=capture.origin == "LIVE_PUBLIC_HTTP",
            reason="HTTP200_SUCCESS_EMPTY_EXACT_DOCUMENTED_MODEL_YEAR")
        return result
    for row in rows:
        try:
            valid = (isinstance(row, dict) and str(row.get("Make", "")).casefold() == scope.make.casefold()
                     and str(row.get("Model", "")).casefold() == scope.model.casefold()
                     and str(row.get("ModelYear", "")) == str(scope.model_year)
                     and isinstance(row.get("Manufacturer"), str) and bool(row["Manufacturer"].strip())
                     and re.fullmatch(r"\d{2}[A-Z]\d{6}", str(row.get("NHTSACampaignNumber", ""))) is not None
                     and datetime.strptime(row["ReportReceivedDate"], "%d/%m/%Y").date() <= observed.date())
        except (ValueError, TypeError, KeyError):
            valid = False
        result["matched_rows" if valid else "rejected_rows"] += 1
    result.update(status="POSITIVE_EXACT_MODEL_YEAR" if not result["rejected_rows"] else "PARTIAL",
        reason="EXACT_MODEL_YEAR_CAMPAIGNS" if not result["rejected_rows"] else "REJECTED_OR_MISMATCHED_ROWS")
    return result


class NhtsaNegativeReviewService:
    def __init__(self, store: ScoutStore, *, client: NhtsaCaptureClient,
                 scopes: list[NhtsaModelYearScope], archive_root: Path,
                 max_requests: int = 8, max_run_seconds: float = 180) -> None:
        if (isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 8
                or isinstance(max_run_seconds, bool) or not math.isfinite(max_run_seconds)
                or not 1 <= max_run_seconds <= 180 or len(scopes) > 8
                or len({scope.key for scope in scopes}) != len(scopes)):
            raise ValueError("NHTSA case review requires bounded unique scopes")
        self.store, self.client, self.scopes = store, client, list(scopes)
        self.archive_root = Path(archive_root)
        self.max_requests, self.max_run_seconds = max_requests, max_run_seconds
        with self.store._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS scout_nhtsa_scope_reviews (
                review_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                known_at TEXT NOT NULL, review_json TEXT NOT NULL)""")

    def _persist(self, scope: NhtsaModelYearScope, capture: NhtsaResponseCapture, *, reviewed_at: datetime) -> dict:
        result = review_capture(scope, capture, reviewed_at=reviewed_at)
        review_id = _hash(json.dumps(result, sort_keys=True).encode())
        folder = self.archive_root / review_id
        folder.mkdir(parents=True, exist_ok=True)
        result["review_id"] = review_id
        result["archive_relative_path"] = f"{review_id}/response.json" if result["public_raw_available"] else None
        if result["public_raw_available"]:
            raw_path = folder / "response.json"
            try:
                with raw_path.open("xb") as output:
                    output.write(capture.body)
            except FileExistsError:
                if raw_path.read_bytes() != capture.body:
                    raise ValueError("Immutable NHTSA response archive collision")
        metadata_path = folder / "review.json"
        serialized = json.dumps(result, sort_keys=True, indent=2) + "\n"
        try:
            with metadata_path.open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialized)
        except FileExistsError:
            if metadata_path.read_text(encoding="utf-8") != serialized:
                raise ValueError("Immutable NHTSA review archive collision")
        with self.store._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO scout_nhtsa_scope_reviews VALUES (?, ?, ?, ?)",
                (review_id, scope.key, result["known_at"], serialized))
        return result

    def ingest_capture(self, scope: NhtsaModelYearScope, capture: NhtsaResponseCapture, *, as_of: datetime) -> dict:
        if capture.origin == "LIVE_PUBLIC_HTTP":
            raise ValueError("LIVE capture must enter through reserved runtime I/O")
        return self._persist(scope, capture, reviewed_at=as_of)

    def report(self, *, as_of: datetime, limit: int = 8) -> dict:
        return read_nhtsa_scope_reviews(self.store, as_of=as_of, limit=limit)

    def run(self, *, as_of: datetime | None = None) -> dict:
        clock = _utc(as_of or datetime.now(timezone.utc))
        if getattr(self.client, "is_live", False):
            actual = datetime.now(timezone.utc)
            if abs((clock - actual).total_seconds()) > 1:
                raise ValueError("LIVE NHTSA I/O cannot be backdated or future-dated")
            clock = actual
        if "nhtsa" not in PUBLIC_DAILY_LIMITS:
            return {"status": "WAIT_SHARED_BUDGET", "attempted_requests": 0, "reviews": []}
        budget = PublicSourceBudget(self.store, "nhtsa", as_of=clock, max_run_seconds=self.max_run_seconds)
        if not budget.acquire():
            return {**budget.blocked_summary(), "reviews": [], "budget_subject_type": "VEHICLE_MAKE_ONLY"}
        results = []
        stop = None
        started = time.perf_counter()
        try:
            eligible = [{"subject_id": "MAKE:" + scope.make.upper(), "scope": scope}
                        for scope in self.scopes if scope.known_at <= clock]
            due = budget.select_due(eligible)
            for row in due:
                remaining = self.max_run_seconds - (time.perf_counter() - started)
                if budget.attempted_requests >= self.max_requests or remaining < 1:
                    stop = "RUN_BUDGET"
                    break
                scope = row["scope"]
                def fetch():
                    capture = self.client.capture(scope, timeout_seconds=min(20, remaining))
                    review_clock = (datetime.now(timezone.utc) if capture.origin == "LIVE_PUBLIC_HTTP"
                                    else budget.now())
                    review = self._persist(scope, capture, reviewed_at=review_clock)
                    results.append(review)
                    if capture.http_status != 200:
                        error = HTTPError(capture.requested_url, capture.http_status, "Captured NHTSA HTTP error",
                            {"Retry-After": capture.retry_after} if capture.retry_after else {}, None)
                        raise error
                    return review
                try:
                    budget.call(row["subject_id"], scope.key, fetch)
                except PublicBudgetExhausted:
                    stop = budget.stop_status
                    break
                except HTTPError as error:
                    if error.code in {401, 403, 429}:
                        stop = "RATE_LIMITED" if error.code == 429 else "ACCESS_BLOCKED"
                        break
                except (OSError, ValueError, TypeError, KeyError) as error:
                    results.append({"scope": scope.record(), "status": "PARTIAL",
                        "reason": "TRANSPORT_OR_CAPTURE_ERROR", "error_type": type(error).__name__,
                        "complete_negative": False, "live_complete_negative_verified": False,
                        "issuer_wide_absence_verified": False, "scoring_applied": False})
            status = ("PARTIAL" if stop == "RUN_BUDGET" else stop) or (
                "PARTIAL" if any(row["status"] == "PARTIAL" for row in results) else "OK")
            if not eligible:
                status = "WAIT_IDENTITY"
            summary = budget.annotate({"status": status, "reviews": results,
                "complete_negative_model_years": sum(row["complete_negative"] for row in results),
                "live_complete_negative_model_years": sum(row["live_complete_negative_verified"] for row in results),
                "max_run_requests": self.max_requests, "budget_subject_type": "VEHICLE_MAKE_ONLY",
                "stop_reason": stop,
                "issuer_wide_absence_verified": False, "issuer_attribution_verified": False,
                "historical_availability_reconstructed": False, "scoring_applied": False})
            if stop == "WAIT_QUOTA":
                summary["status"] = "WAIT_QUOTA"
            return summary
        finally:
            budget.release()
