from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
import json
import math
from pathlib import Path
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
from market_checker_app.services.award_modification_service import audit_award_modification
from market_checker_app.utils.source_validation import public_https_reference


API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DEFAULT_IDENTITIES = Path(__file__).resolve().parents[1] / "data" / "verified_usaspending_uei.json"
FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "generated_internal_id",
          "Start Date", "Award Amount", "Total Outlays", "Last Modified Date",
          "Awarding Agency", "Contract Award Type"]


class AwardsClient(Protocol):
    def awards(self, uei: str, *, start: date, end: date, page: int) -> dict: ...


class UsaSpendingApiClient:
    def awards(self, uei: str, *, start: date, end: date, page: int,
               timeout: float = 8) -> dict:
        if not isinstance(uei, str) or not re.fullmatch(r"[A-Z0-9]{12}", uei) or start > end:
            raise ValueError("Invalid USAspending UEI or query window")
        bounded_positive(page, 100_000, "USAspending page")
        payload = {"filters": {"award_type_codes": ["A", "B", "C", "D"],
                               "recipient_search_text": [uei],
                               "time_period": [{"start_date": start.isoformat(),
                                                "end_date": end.isoformat()}]},
                   "fields": FIELDS, "limit": 100, "page": page,
                   "sort": "Last Modified Date", "order": "desc"}
        return post_json(API_URL, payload, timeout=timeout)


def load_verified_identities(path: Path = DEFAULT_IDENTITIES) -> list[dict]:
    """A UEI alone cannot establish which public issuer owns its recipient."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    return _validate_identities(entries)


def _validate_identities(entries: list[dict]) -> list[dict]:
    if not isinstance(entries, list):
        raise ValueError("USAspending identity manifest must be a list")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("USAspending identity entry must be an object")
        ticker, uei = entry.get("ticker"), entry.get("uei")
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", ticker):
            raise ValueError("USAspending identity needs a canonical ticker")
        if not isinstance(uei, str) or not re.fullmatch(r"[A-Z0-9]{12}", uei):
            raise ValueError("USAspending identity needs a twelve-character UEI")
        if (ticker, uei) in seen:
            raise ValueError("Duplicate USAspending ticker/UEI")
        seen.add((ticker, uei))
        for key in ("recipient_name", "uei_evidence_url", "relationship_evidence_url"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"USAspending identity needs {key}")
        for key in ("uei_evidence_url", "relationship_evidence_url"):
            public_https_reference(entry[key])
        if entry.get("continuity_evidence_url"):
            public_https_reference(entry["continuity_evidence_url"])
        first = date.fromisoformat(entry["effective_from"])
        last = date.fromisoformat(entry["effective_to"]) if entry.get("effective_to") else None
        known = datetime.fromisoformat(entry["known_at"])
        if (last and last < first) or known.tzinfo is None or known.utcoffset() is None:
            raise ValueError("USAspending identity needs valid dated relationship and knowledge")
    return [dict(entry) for entry in entries]


def _identity_key(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def _source_date(value: object) -> date:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", value):
        raise ValueError("USAspending date needs an ISO date")
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _award_details(row: object, entry: dict, *, clock: datetime, state: dict) -> dict | None:
    if not isinstance(row, dict) or row.get("Recipient UEI") != entry["uei"]:
        return None
    name, award_id = row.get("Recipient Name"), row.get("generated_internal_id")
    if (not isinstance(name, str) or name.casefold().split() != entry["recipient_name"].casefold().split()
            or not isinstance(award_id, str) or not award_id.strip()):
        return None
    try:
        award_start = _source_date(row["Start Date"])
        modified = row.get("Last Modified Date")
        if modified is not None and _source_date(modified) > clock.date():
            return None
        # The API search window bounds retrieval cost. A returned long-running
        # contract can start earlier; attribution follows the cited ownership
        # dates, while the pinned query end prevents future award starts.
        if not date.fromisoformat(entry["effective_from"]) <= award_start <= date.fromisoformat(state["query_end"]):
            return None
        amounts = (row.get("Award Amount"), row.get("Total Outlays"))
        if any(isinstance(x, bool) or (x is not None and
               (not isinstance(x, (int, float)) or not math.isfinite(x))) for x in amounts):
            return None
    except (ValueError, KeyError, TypeError, OverflowError):
        return None
    return {"stage": "prime_contract", "uei": entry["uei"],
            "recipient_name": name, "award_id": row.get("Award ID"),
            "generated_internal_id": award_id, "award_start": award_start.isoformat(),
            "last_modified_date": modified,
            "reported_award_amount_usd": float(amounts[0]) if amounts[0] is not None else None,
            "reported_total_outlays_usd": float(amounts[1]) if amounts[1] is not None else None,
            "awarding_agency": row.get("Awarding Agency"),
            "contract_award_type": row.get("Contract Award Type"),
            "uei_evidence_url": entry["uei_evidence_url"],
            "relationship_evidence_url": entry["relationship_evidence_url"],
            "continuity_evidence_url": entry.get("continuity_evidence_url"),
            "relationship_effective_from": entry["effective_from"],
            "relationship_effective_to": entry.get("effective_to"),
            "relationship_known_at": entry["known_at"],
            "revenue_inferred": False, "last_modified_is_publication_time": False,
            "modification_history_complete": False}


class UsaSpendingScoutService:
    """Observe current prime contracts only for independently documented ticker/UEI links."""

    def __init__(self, store: ScoutStore, *, client: AwardsClient,
                 identities: list[dict], max_pages: int = 3, max_uei: int = 5,
                 max_requests: int = 10, max_run_seconds: float = 60,
                 max_failures: int = 3, daily_request_limit: int = DAILY_REQUEST_LIMIT,
                 refresh_days: int = 1) -> None:
        for value, maximum, label in ((max_pages, 20, "USAspending page budget"),
                                      (max_uei, 100, "USAspending UEI budget"),
                                      (max_requests, 100, "USAspending request budget"),
                                      (max_failures, 10, "USAspending failure budget"),
                                      (daily_request_limit, DAILY_REQUEST_LIMIT, "USAspending daily request budget"),
                                      (refresh_days, 30, "USAspending refresh interval")):
            bounded_positive(value, maximum, label)
        bounded_seconds(max_run_seconds)
        self.store, self.client = store, client
        self.identities = _validate_identities(identities)
        self.max_pages, self.max_uei, self.max_requests = max_pages, max_uei, max_requests
        self.max_run_seconds, self.max_failures = max_run_seconds, max_failures
        self.daily_request_limit, self.refresh_days = daily_request_limit, refresh_days

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        clock = clock.astimezone(timezone.utc)
        start = clock.date() - timedelta(days=730)
        eligible = [e for e in self.identities
                    if (universe is None or e["ticker"] in universe)
                    and datetime.fromisoformat(e["known_at"]) <= clock
                    and date.fromisoformat(e["effective_from"]) <= clock.date()
                    and (not e.get("effective_to") or date.fromisoformat(e["effective_to"]) >= start)]
        checkpoints = UsaSpendingCheckpointStore(self.store)
        keyed = [(_identity_key(e), e) for e in eligible]
        states = {key: checkpoints.state("awards", key) for key, _ in keyed}
        due = [(key, entry) for key, entry in keyed if state_due(states[key], clock)]
        due.sort(key=lambda item: ((states[item[0]] or {}).get("last_attempt_order", 0),
                                  (states[item[0]] or {}).get("last_attempt_at") or "", item[0]))
        created = checked = completed = failed = rejected = terminal = resumed = 0
        attempted: set[str] = set()
        exhausted = False
        stop_status: str | None = None
        budget: UsaSpendingRunBudget | None = None
        histories: dict[str, dict[str, list[dict]]] = {}

        def award_history(subject: str, award_id: str) -> list[dict]:
            if subject not in histories:
                grouped: dict[str, list[dict]] = {}
                for finding in self.store.findings_as_of(subject, as_of=clock):
                    if finding["source"] != "usaspending":
                        continue
                    stored = json.loads(finding["details_json"])
                    internal_id = stored.get("generated_internal_id")
                    if isinstance(internal_id, str):
                        grouped.setdefault(internal_id, []).append(finding)
                histories[subject] = grouped
            return histories[subject].setdefault(award_id, [])

        def summary(status: str | None = None) -> dict:
            measured = coverage([checkpoints.state("awards", key) for key, _ in keyed],
                                clock=clock, refresh_days=self.refresh_days)
            result = {"status": status or ("PARTIAL" if measured["coverage_complete"] < len(eligible) or failed
                                            or exhausted or len(due) > len(attempted) else "OK"),
                      "new_findings": created, "eligible_uei": len(eligible), "due_uei": len(due),
                      "checked_uei": checked, "attempted_uei": len(attempted),
                      "completed_uei": completed, "partial_uei": len(attempted) - completed,
                      "truncated_uei": len(attempted) - completed, "failed_uei": failed,
                      "terminal_uei": terminal, "resumed_uei": resumed,
                      "rejected_rows": rejected, "budget_exhausted": exhausted,
                      "attempted_requests": budget.requests if budget else 0,
                      "daily_requests_used": checkpoints.daily_usage(as_of=budget.now() if budget else clock),
                      "daily_request_limit": self.daily_request_limit,
                      "identity_covered_tickers": len({e["ticker"] for e in eligible}),
                      "identity_unmapped_tickers": len(universe - {e["ticker"] for e in eligible}) if universe is not None else 0,
                      "search_lookback_days": 730, "issuer_coverage_complete": False,
                      "pagination_snapshot_guaranteed": False,
                      **{f"{key}_uei": value for key, value in measured.items()}}
            if stop_status:
                result["stop_reason"] = stop_status
            if budget:
                cooldown = self.store.provider_cooldown("usaspending", as_of=budget.now())
                if cooldown:
                    result["retry_at"] = cooldown["retry_at"]
            return result

        if not eligible:
            return summary("WAIT_IDENTITY")
        if not due:
            return summary()
        budget = UsaSpendingRunBudget(checkpoints, clock=clock, max_requests=self.max_requests,
                                     max_run_seconds=self.max_run_seconds, daily_limit=self.daily_request_limit)
        if not budget.token:
            return {**summary(), **budget.blocked_summary()}
        try:
            for key, entry in due[:self.max_uei]:
                if failed >= self.max_failures:
                    exhausted, stop_status = True, "FAILURE_BUDGET"
                    break
                state = states[key]
                if state is None or state["terminal"]:
                    state = fresh_state(clock, query_start=max(start, date.fromisoformat(entry["effective_from"])).isoformat(),
                                        query_end=min(clock.date(), date.fromisoformat(entry["effective_to"])).isoformat()
                                        if entry.get("effective_to") else clock.date().isoformat())
                elif state.get("last_attempt_at"):
                    # Replayed first pages also follow an earlier observation;
                    # saved rows or a failed response cannot prove a snapshot.
                    state["resumed"] = True
                successes = 0
                for _ in range(self.max_pages):
                    page = state["next_page"]
                    before_requests = budget.requests
                    try:
                        def fetch_page():
                            kwargs = {"timeout": min(8, budget.remaining())} if isinstance(self.client, UsaSpendingApiClient) else {}
                            return self.client.awards(entry["uei"], start=date.fromisoformat(state["query_start"]),
                                                      end=date.fromisoformat(state["query_end"]), page=page, **kwargs)

                        payload = budget.call("awards", key, entry["ticker"], page=page, fetch=fetch_page)
                        for row in page_rows(payload, limit=100):
                            if budget.remaining() <= 0:
                                raise UsaSpendingBudgetExhausted("RUN_BUDGET")
                            details = _award_details(row, entry, clock=clock, state=state)
                            if details is None:
                                rejected += 1
                                state["rejected_rows"] += 1
                                continue
                            award_id = details["generated_internal_id"]
                            # The audit is derived from immutable source versions.
                            # Excluding it from the source-content hash preserves
                            # the existing deduplication of identical API rows.
                            content_hash = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                            prior = award_history(entry["ticker"], award_id)
                            observation = {"source": "usaspending", "subject_id": entry["ticker"],
                                           "source_object_id": f"{entry['uei']}:{award_id}",
                                           "source_url": API_URL, "verification_status": "SOURCE_VERIFIED",
                                           "content_hash": content_hash, "available_at": utc(clock),
                                           "first_observed_at": utc(clock), "details": details}
                            details["modification_audit"] = audit_award_modification(
                                observation, prior, as_of=clock)
                            finding_id, new = self.store.record_finding(
                                source="usaspending", subject_id=entry["ticker"],
                                source_object_id=f"{entry['uei']}:{award_id}",
                                content_hash=content_hash,
                                title=f"USAspending {details['award_id'] or award_id} – {entry['recipient_name']}",
                                source_url=API_URL, locator=f"generated_internal_id:{award_id}",
                                published_at=clock, available_at=clock, observed_at=clock, details=details)
                            if new:
                                prior.append({**observation, "finding_id": finding_id,
                                              "details_json": json.dumps(details, sort_keys=True)})
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
                        state["retry_at"] = utc(clock if has_next else clock + timedelta(days=self.refresh_days))
                        if not has_next:
                            terminal += 1
                            completed += int(state["status"] == "COMPLETE")
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
                            checkpoints.save("awards", key, entry["ticker"], state, as_of=clock)
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
