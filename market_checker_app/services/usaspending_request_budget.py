"""Shared bounded public HTTP and persistent quotas for USAspending collectors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
import json
import math
import time
from typing import Callable, TypeVar
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.services.public_source_budget import PublicSourceBudget
from market_checker_app.storage.usaspending_checkpoint_store import UsaSpendingCheckpointStore


MAX_RESPONSE_BYTES = 4_000_000
DAILY_REQUEST_LIMIT = 120  # Conservative application budget, not a publisher limit.
T = TypeVar("T")


def bounded_positive(value: object, maximum: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be a positive bounded integer")


def bounded_seconds(value: object, maximum: int = 300) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 1 <= value <= maximum):
        raise ValueError("USAspending time budget must be bounded")


def post_json(url: str, payload: dict, *, timeout: float = 8) -> dict:
    bounded_seconds(timeout, 20)
    _official_url(url)
    deadline = time.monotonic() + timeout
    request = Request(url, data=json.dumps(payload, allow_nan=False).encode(),
                      headers={"Content-Type": "application/json",
                               "User-Agent": "JohnySkoreScout/1.0"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        _official_url(response.geturl())
        expected_size = getattr(response, "length", None)
        if expected_size is not None and expected_size > MAX_RESPONSE_BYTES:
            raise ValueError("USAspending response exceeds byte budget")
        chunks: list[bytes] = []
        size = 0
        read = getattr(response, "read1", response.read)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("USAspending response time budget exhausted")
            # urllib's socket timeout otherwise restarts for every read. Cap
            # each blocking read to the time left in this complete response.
            raw = getattr(getattr(response, "fp", None), "raw", None)
            socket = getattr(raw, "_sock", None)
            if socket is not None:
                socket.settimeout(remaining)
            chunk = read(min(65_536, MAX_RESPONSE_BYTES + 1 - size))
            if time.monotonic() >= deadline:
                raise TimeoutError("USAspending response time budget exhausted")
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("USAspending response exceeds byte budget")
        if expected_size is not None and size != expected_size:
            raise IncompleteRead(b"".join(chunks), expected_size - size)
        def reject_constant(value: str) -> None:
            raise ValueError(f"Non-finite USAspending JSON value: {value}")

        result = json.loads(b"".join(chunks), parse_constant=reject_constant)
        if not isinstance(result, dict):
            raise ValueError("USAspending response must be an object")
        return result


def _official_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "api.usaspending.gov"
            or parsed.port not in {None, 443} or parsed.username or parsed.password):
        raise ValueError("USAspending URL must use official host")


class _OfficialRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Validate the authority before urllib follows any redirected request.
urlopen = build_opener(_OfficialRedirectHandler()).open


def page_rows(payload: dict, *, limit: int) -> list:
    if not isinstance(payload, dict):
        raise ValueError("USAspending page must be an object")
    rows = payload["results"]
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("Malformed USAspending page rows")
    return rows


def page_has_next(payload: dict, *, page: int, rows: list) -> bool:
    meta = payload["page_metadata"]
    if (not isinstance(meta, dict) or not isinstance(meta.get("hasNext"), bool)
            or ("page" in meta and (isinstance(meta["page"], bool)
                                    or not isinstance(meta["page"], int) or meta["page"] != page))
            or (meta["hasNext"] and not rows)):
        raise ValueError("Malformed USAspending page metadata")
    return meta["hasNext"]


class UsaSpendingBudgetExhausted(RuntimeError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(status)


class UsaSpendingRunBudget:
    def __init__(self, checkpoints: UsaSpendingCheckpointStore, *, clock: datetime,
                 max_requests: int, max_run_seconds: float, daily_limit: int) -> None:
        self.checkpoints, self.store = checkpoints, checkpoints.store
        self.clock = clock.astimezone(timezone.utc)
        self.started = time.monotonic()
        self.deadline = self.started + max_run_seconds
        self.max_requests, self.daily_limit = max_requests, daily_limit
        self.requests = 0
        self.last_request_order = 0
        self.token = self.store.claim_provider(
            "usaspending", as_of=self.now(), seconds=math.ceil(max_run_seconds) + 25)

    def now(self) -> datetime:
        return self.clock + timedelta(seconds=max(0.0, time.monotonic() - self.started))

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def blocked_summary(self) -> dict:
        cooldown = self.store.provider_cooldown("usaspending", as_of=self.now())
        return {"status": ("RATE_LIMITED" if cooldown["reason"].endswith("HTTP 429")
                            else "ACCESS_BLOCKED") if cooldown else "BUSY",
                **({"retry_at": cooldown["retry_at"]} if cooldown else {})}

    def call(self, kind: str, identity_key: str, subject_id: str, *, page: int,
             fetch: Callable[[], T]) -> T:
        if not self.token:
            raise RuntimeError("USAspending request requires provider lease")
        if self.remaining() < 1 or self.requests >= self.max_requests:
            raise UsaSpendingBudgetExhausted("RUN_BUDGET")
        reservation = self.checkpoints.reserve(
            kind, identity_key, subject_id, page=page, as_of=self.now(),
            daily_limit=self.daily_limit, lease_token=self.token)
        if reservation["status"] != "RESERVED":
            raise UsaSpendingBudgetExhausted(reservation["status"])
        self.requests += 1
        self.last_request_order = reservation["request_order"]
        try:
            # Acquiring SQLite's write lock can itself consume the deadline.
            # The reserved attempt remains charged, without starting new I/O.
            if self.remaining() < 1:
                raise UsaSpendingBudgetExhausted("RUN_BUDGET")
            result = fetch()
        except BaseException as error:
            self.checkpoints.finish(reservation["request_id"], outcome="FAILED")
            if isinstance(error, HTTPError) and error.code in {401, 403, 429}:
                self.store.defer_provider(
                    "usaspending", self.token, as_of=self.now(),
                    retry_after=PublicSourceBudget._retry_after(error, self.now()),
                    reason=f"usaspending HTTP {error.code}")
            raise
        self.checkpoints.finish(reservation["request_id"], outcome="RETURNED")
        return result

    def close(self) -> None:
        if self.token:
            self.store.release_provider("usaspending", self.token)
