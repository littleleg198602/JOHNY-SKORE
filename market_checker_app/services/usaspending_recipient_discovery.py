from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
import json
import re
import sqlite3
from typing import Protocol
from urllib.error import HTTPError

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.usaspending_checkpoint_store import (
    UsaSpendingCheckpointStore, coverage, fresh_state, state_due, utc,
)
from market_checker_app.services.usaspending_request_budget import (
    DAILY_REQUEST_LIMIT, UsaSpendingBudgetExhausted, UsaSpendingRunBudget,
    bounded_positive, bounded_seconds, page_has_next, page_rows, post_json,
)


RECIPIENT_URL = "https://api.usaspending.gov/api/v2/recipient/"


class RecipientClient(Protocol):
    def recipients(self, name: str, *, page: int) -> dict: ...


class UsaSpendingRecipientClient:
    def recipients(self, name: str, *, page: int, timeout: float = 8) -> dict:
        if not isinstance(name, str) or not name.strip() or len(name) > 500:
            raise ValueError("USAspending recipient query requires a bounded name")
        bounded_positive(page, 100_000, "USAspending recipient page")
        return post_json(RECIPIENT_URL, {"keyword": name, "award_type": "contracts",
                                       "page": page, "limit": 50}, timeout=timeout)


class UsaSpendingRecipientDiscovery:
    """Find public recipient candidates; never imply corporate ownership from a name."""

    def __init__(self, store: ScoutStore, *, client: RecipientClient,
                 max_subjects: int = 25, max_pages: int = 2, max_requests: int = 20,
                 max_run_seconds: float = 60, max_failures: int = 3,
                 daily_request_limit: int = DAILY_REQUEST_LIMIT) -> None:
        for value, maximum, label in ((max_subjects, 100, "Recipient subject budget"),
                                      (max_pages, 20, "Recipient page budget"),
                                      (max_requests, 100, "Recipient request budget"),
                                      (max_failures, 10, "Recipient failure budget"),
                                      (daily_request_limit, DAILY_REQUEST_LIMIT, "Recipient daily budget")):
            bounded_positive(value, maximum, label)
        bounded_seconds(max_run_seconds)
        self.store, self.client = store, client
        self.max_subjects, self.max_pages = max_subjects, max_pages
        self.max_requests, self.max_run_seconds = max_requests, max_run_seconds
        self.max_failures, self.daily_request_limit = max_failures, daily_request_limit

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        clock = clock.astimezone(timezone.utc)
        checkpoints = UsaSpendingCheckpointStore(self.store)
        eligible = [row for row in checkpoints.recipients(as_of=clock)
                    if universe is None or row["subject_id"] in universe]
        keyed = [(hashlib.sha256(json.dumps([row["subject_id"], row["cik"], row["company_name"],
                                           "exact-name-v2"]).encode()).hexdigest(), row) for row in eligible]
        states = {key: checkpoints.state("recipients", key) for key, _ in keyed}
        due = [(key, row) for key, row in keyed if state_due(states[key], clock)]
        # Legacy name checks have no page cursor/CIK semantics. Revisit them
        # under the new collector rather than using them as complete evidence.
        due.sort(key=lambda item: ((states[item[0]] or {}).get("last_attempt_order", 0),
                                  (states[item[0]] or {}).get("last_attempt_at") or "", item[1]["subject_id"]))
        created = checked = completed = failed = rejected = resumed = 0
        attempted: set[str] = set()
        exhausted = False
        stop_status: str | None = None
        budget: UsaSpendingRunBudget | None = None

        def summary(status: str | None = None) -> dict:
            measured = coverage([checkpoints.state("recipients", key) for key, _ in keyed],
                                clock=clock, refresh_days=30)
            result = {"status": status or ("PARTIAL" if measured["coverage_complete"] < len(eligible) or failed
                                            or exhausted or len(due) > len(attempted) else "OK"),
                      "checked_names": checked, "new_candidates": created,
                      "eligible_names": len(eligible), "due_names": len(due),
                      "attempted_names": len(attempted), "completed_names": completed,
                      "partial_names": len(attempted) - completed,
                      "truncated_names": len(attempted) - completed,
                      "failed_names": failed, "rejected_rows": rejected, "resumed_names": resumed,
                      "budget_exhausted": exhausted, "attempted_requests": budget.requests if budget else 0,
                      "daily_requests_used": checkpoints.daily_usage(as_of=budget.now() if budget else clock),
                      "daily_request_limit": self.daily_request_limit,
                      "pagination_snapshot_guaranteed": False,
                      "contract_attribution_allowed": False,
                      **{f"{key}_names": value for key, value in measured.items()}}
            if stop_status:
                result["stop_reason"] = stop_status
            if budget:
                cooldown = self.store.provider_cooldown("usaspending", as_of=budget.now())
                if cooldown:
                    result["retry_at"] = cooldown["retry_at"]
            return result

        if not due:
            return summary()
        budget = UsaSpendingRunBudget(checkpoints, clock=clock, max_requests=self.max_requests,
                                     max_run_seconds=self.max_run_seconds, daily_limit=self.daily_request_limit)
        if not budget.token:
            return {**summary(), **budget.blocked_summary()}
        try:
            for key, issuer in due[:self.max_subjects]:
                if failed >= self.max_failures:
                    exhausted, stop_status = True, "FAILURE_BUDGET"
                    break
                name, ticker = issuer["company_name"], issuer["subject_id"]
                state = states[key]
                if state is None or state["terminal"]:
                    state = fresh_state(clock)
                elif state.get("last_attempt_at"):
                    state["resumed"] = True
                successes = 0
                for _ in range(self.max_pages):
                    page = state["next_page"]
                    before_requests = budget.requests
                    try:
                        def fetch_page():
                            kwargs = {"timeout": min(8, budget.remaining())} if isinstance(self.client, UsaSpendingRecipientClient) else {}
                            return self.client.recipients(name, page=page, **kwargs)

                        payload = budget.call("recipients", key, ticker, page=page, fetch=fetch_page)
                        for row in page_rows(payload, limit=50):
                            if budget.remaining() <= 0:
                                raise UsaSpendingBudgetExhausted("RUN_BUDGET")
                            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                                rejected += 1
                                state["rejected_rows"] += 1
                                continue
                            recipient_name = row["name"]
                            if recipient_name.casefold().split() != name.casefold().split():
                                continue  # A loose name-search hit is outside the candidate scope.
                            uei, recipient_id = row.get("uei"), row.get("id")
                            if (not isinstance(uei, str) or not re.fullmatch(r"[A-Z0-9]{12}", uei)
                                    or not isinstance(recipient_id, str) or not recipient_id.strip()):
                                rejected += 1
                                state["rejected_rows"] += 1
                                continue
                            details = {"stage": "recipient_candidate", "uei": uei,
                                       "issuer_sec_name": name, "issuer_sec_cik": issuer["cik"],
                                       "recipient_name": recipient_name, "recipient_id": recipient_id,
                                       "recipient_level": row.get("recipient_level"),
                                       "identity_status": "NAME_ONLY",
                                       "missing_evidence": "dated legal entity/issuer ownership relationship",
                                       "contract_attribution_allowed": False}
                            _, new = self.store.record_finding(
                                source="usaspending", subject_id=ticker,
                                source_object_id=f"recipient:{recipient_id}",
                                content_hash=hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest(),
                                title=f"Kandidát příjemce: {recipient_name} (UEI {uei})",
                                source_url=RECIPIENT_URL, locator=f"recipient_id:{recipient_id}",
                                published_at=clock, available_at=clock, observed_at=clock,
                                verification_status="UNVERIFIED", details=details)
                            created += int(new)
                            state["usable_rows"] += 1
                        if budget.remaining() <= 0:
                            raise UsaSpendingBudgetExhausted("RUN_BUDGET")
                        has_next = page_has_next(payload, page=page, rows=payload["results"])
                        successes += 1
                        state["pages_saved"] += 1
                        state["next_page"] = page + 1
                        state["terminal"] = not has_next
                        state["status"] = "SCANNING" if has_next else "PARTIAL" if state["rejected_rows"] or state["resumed"] else "COMPLETE"
                        state["retry_at"] = utc(clock if has_next else clock + timedelta(days=1 if state["status"] != "COMPLETE" else 30))
                        if state["status"] == "COMPLETE":
                            self.store.record_recipient_discovery_check(
                                subject_id=ticker, company_name=name, as_of=clock,
                                candidate_count=state["usable_rows"], truncated=False)
                            completed += 1
                    except UsaSpendingBudgetExhausted as error:
                        exhausted, stop_status = True, error.status
                        break
                    except (OSError, HTTPException, sqlite3.Error, ValueError, KeyError, TypeError) as error:
                        failed += 1
                        state["status"], state["last_error"] = "FAILED", type(error).__name__
                        state["retry_at"] = utc(clock + timedelta(hours=1))
                        if isinstance(error, HTTPError) and error.code in {401, 403, 429}:
                            stop_status = "RATE_LIMITED" if error.code == 429 else "ACCESS_BLOCKED"
                        break
                    finally:
                        if budget.requests > before_requests:
                            resumed += int(key not in attempted and state["resumed"])
                            attempted.add(key)
                            state["last_attempt_at"] = utc(clock)
                            state["last_attempt_order"] = budget.last_request_order
                            checkpoints.save("recipients", key, ticker, state, as_of=clock)
                    if state["terminal"]:
                        break
                checked += int(successes > 0)
                if exhausted or stop_status in {"RATE_LIMITED", "ACCESS_BLOCKED"}:
                    break
            status = stop_status if stop_status in {"RATE_LIMITED", "ACCESS_BLOCKED", "LEASE_LOST"} else None
            if stop_status == "WAIT_QUOTA" and not attempted:
                status = "WAIT_QUOTA"
            result = summary(status)
            if stop_status == "WAIT_QUOTA":
                result["retry_at"] = utc(budget.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            return result
        finally:
            budget.close()
