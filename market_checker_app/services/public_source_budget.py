"""Persistent application quotas and exclusive I/O for public name collectors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import wraps
import math
import time
from typing import Callable, TypeVar
from urllib.error import HTTPError

from market_checker_app.storage.scout_store import ScoutStore


# Conservative application limits, not statements of publisher entitlements.
# Pages, rejected responses, HTTP failures and interrupted attempts all count.
PUBLIC_DAILY_LIMITS = {
    "fda": (40, 320),  # up to two pages for each of four endpoints per issuer
    "cms": (10, 20),
    "clinicaltrials": (10, 20),
    "doj": (25, 50),
    "epa": (10, 10),
    "eia": (2, 2),  # two shared commodity series, not company identities
}
T = TypeVar("T")


class PublicBudgetExhausted(RuntimeError):
    """The current issuer stays due; previously completed issuers stay saved."""


class PublicSourceBudget:
    def __init__(self, store: ScoutStore, source: str, *, as_of: datetime,
                 max_run_seconds: float):
        if source not in PUBLIC_DAILY_LIMITS:
            raise ValueError("Unsupported public-source budget")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Public-source budget needs timezone")
        self.store, self.source = store, source
        self.clock = as_of.astimezone(timezone.utc)
        self.started = time.perf_counter()
        self.daily_issuer_limit, self.daily_request_limit = PUBLIC_DAILY_LIMITS[source]
        self.lease_seconds = math.ceil(max_run_seconds) + 25
        self.token: str | None = None
        self.attempted_requests = 0
        self.attempted_subjects: set[str] = set()
        self.quota_deferred_issuers = 0
        self.stop_status: str | None = None

    def now(self) -> datetime:
        # Charge an actual request to its UTC day, including runs crossing
        # midnight, while keeping deterministic callers' explicit as-of clock.
        return self.clock + timedelta(seconds=max(0.0, time.perf_counter() - self.started))

    def acquire(self) -> bool:
        self.token = self.store.claim_provider(self.source, as_of=self.now(), seconds=self.lease_seconds)
        return self.token is not None

    def release(self) -> None:
        if self.token is not None:
            self.store.release_provider(self.source, self.token)

    def blocked_summary(self) -> dict:
        cooldown = self.store.provider_cooldown(self.source, as_of=self.now())
        status = ("RATE_LIMITED" if cooldown["reason"].endswith("HTTP 429")
                  else "ACCESS_BLOCKED") if cooldown else "BUSY"
        return self.annotate({"status": status, "checked_issuers": 0, "new_findings": 0,
                              **({"retry_at": cooldown["retry_at"]} if cooldown else {})})

    def select_due(self, rows: list[dict]) -> list[dict]:
        clock = self.now()
        start = clock.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        attempts = self.store.public_subject_attempts(self.source, as_of=clock)
        eligible = [row for row in rows if attempts.get(row["subject_id"], "") < start]
        self.quota_deferred_issuers += len(rows) - len(eligible)
        if rows and not eligible:
            self.stop_status = "WAIT_QUOTA"
        # Failed issuers remain uncompleted, but cannot starve never-attempted
        # issuers on the next day. Keep the original due ordering for ties.
        return sorted(eligible, key=lambda row: attempts.get(row["subject_id"], ""))

    @staticmethod
    def _retry_after(error: HTTPError, clock: datetime) -> timedelta:
        minimum = timedelta(hours=1 if error.code == 429 else 24)
        if error.code != 429 or not error.headers:
            return minimum
        value = error.headers.get("Retry-After", "")
        try:
            if value.strip().isdigit() and len(value.strip()) <= 8:
                delay = timedelta(seconds=int(value.strip()))
            else:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    return minimum
                delay = date.astimezone(timezone.utc) - clock
            return max(minimum, delay)
        except (ValueError, TypeError, OverflowError, AttributeError):
            return minimum

    def call(self, subject_id: str, identity_key: str, fetch: Callable[[], T]) -> T:
        if self.token is None:
            raise RuntimeError("Public-source request requires its provider lease")
        reservation = self.store.reserve_public_request(
            self.source, subject_id=subject_id, identity_key=identity_key,
            as_of=self.now(), daily_issuer_limit=self.daily_issuer_limit,
            daily_request_limit=self.daily_request_limit, lease_token=self.token)
        if reservation["status"] != "RESERVED":
            self.stop_status = reservation["status"]
            raise PublicBudgetExhausted(self.stop_status)
        request_id = reservation["request_id"]
        self.attempted_requests += 1
        self.attempted_subjects.add(subject_id)
        try:
            result = fetch()
        except HTTPError as error:
            self.store.finish_public_request(request_id, outcome="FAILED")
            if error.code in {401, 403, 429}:
                clock = self.now()
                self.store.defer_provider(
                    self.source, self.token, as_of=clock,
                    retry_after=self._retry_after(error, clock),
                    reason=f"{self.source} HTTP {error.code}")
            raise
        except BaseException:
            self.store.finish_public_request(request_id, outcome="FAILED")
            raise
        self.store.finish_public_request(request_id, outcome="RETURNED")
        return result

    def annotate(self, summary: dict) -> dict:
        result = dict(summary)
        clock = self.now()
        if self.stop_status:
            result["budget_exhausted"] = True
            if self.stop_status == "LEASE_LOST":
                result["status"] = "LEASE_LOST"
            elif result["status"] not in {"RATE_LIMITED", "ACCESS_BLOCKED", "ERROR"}:
                result["status"] = "PARTIAL" if self.attempted_requests else "WAIT_QUOTA"
                result["retry_at"] = (clock.replace(hour=0, minute=0, second=0, microsecond=0)
                                      + timedelta(days=1)).isoformat()
        cooldown = self.store.provider_cooldown(self.source, as_of=clock)
        if cooldown:
            result["retry_at"] = cooldown["retry_at"]
        result.update(self.store.public_daily_usage(self.source, as_of=clock))
        result.update(attempted_requests=self.attempted_requests,
                      attempted_issuers=len(self.attempted_subjects),
                      daily_request_limit=self.daily_request_limit,
                      daily_issuer_limit=self.daily_issuer_limit,
                      quota_deferred_issuers=self.quota_deferred_issuers)
        return result


def bounded_public_source(source: str | None = None):
    """Give a collector invocation a local budget without mutable service state."""
    def decorate(run):
        @wraps(run)
        def bounded(self, *, as_of: datetime | None = None, universe: set[str] | None = None):
            budget = PublicSourceBudget(self.store, source or self.client.source,
                                        as_of=as_of or datetime.now(timezone.utc),
                                        max_run_seconds=self.max_run_seconds)
            if not budget.acquire():
                return budget.blocked_summary()
            try:
                return budget.annotate(run(self, as_of=budget.clock, universe=universe, _budget=budget))
            finally:
                budget.release()
        return bounded
    return decorate
