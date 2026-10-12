"""USAspending traversal state and request accounting, alongside scout findings.

The page cursor is advanced only after every usable row has been persisted.
It is operational state, never evidence that an offset-paginated API provides
an immutable snapshot or that a public issuer has no government contracts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from market_checker_app.storage.scout_store import ScoutStore


def utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("USAspending checkpoint time needs timezone")
    return value.astimezone(timezone.utc).isoformat()


class UsaSpendingCheckpointStore:
    def __init__(self, store: ScoutStore) -> None:
        self.store = store
        with store._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scout_usaspending_checkpoints (
                    kind TEXT NOT NULL CHECK(kind IN ('awards', 'recipients')),
                    identity_key TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY(kind, identity_key)
                );
                CREATE TABLE IF NOT EXISTS scout_usaspending_requests (
                    request_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('awards', 'recipients')),
                    identity_key TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    page INTEGER NOT NULL CHECK(page > 0),
                    attempted_at TEXT NOT NULL,
                    outcome TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scout_usaspending_requests_day
                    ON scout_usaspending_requests(attempted_at);
            """)

    def state(self, kind: str, identity_key: str) -> dict | None:
        with self.store._connect() as conn:
            row = conn.execute("""
                SELECT state_json FROM scout_usaspending_checkpoints
                WHERE kind=? AND identity_key=?
            """, (kind, identity_key)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, kind: str, identity_key: str, subject_id: str,
             state: dict, *, as_of: datetime) -> None:
        clock = utc(as_of)
        with self.store._connect() as conn:
            conn.execute("""
                INSERT INTO scout_usaspending_checkpoints
                    (kind, identity_key, subject_id, updated_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, identity_key) DO UPDATE SET
                    updated_at=excluded.updated_at, state_json=excluded.state_json
                WHERE scout_usaspending_checkpoints.updated_at<=excluded.updated_at
            """, (kind, identity_key, subject_id, clock,
                  json.dumps({**state, "updated_at": clock}, sort_keys=True)))

    def recipients(self, *, as_of: datetime) -> list[dict]:
        """Only an observed, active SEC issuer may seed a name-only candidate."""
        with self.store._connect() as conn:
            rows = conn.execute("""
                SELECT i.subject_id, i.cik, i.company_name, c.checked_at AS legacy_checked_at
                FROM scout_sec_identities AS i
                LEFT JOIN scout_recipient_discovery_checks AS c
                  ON c.subject_id=i.subject_id AND c.company_name=i.company_name
                WHERE i.status='ACTIVE' AND i.first_observed_at<=?
                ORDER BY i.subject_id
            """, (utc(as_of),)).fetchall()
        return [dict(row) for row in rows]

    def reserve(self, kind: str, identity_key: str, subject_id: str, *, page: int,
                as_of: datetime, daily_limit: int, lease_token: str) -> dict:
        clock = utc(as_of)
        start = as_of.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        request_id = uuid4().hex
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute("""
                SELECT 1 FROM scout_provider_leases
                WHERE source='usaspending' AND lease_token=? AND lease_until>?
            """, (lease_token, clock)).fetchone()
            if not lease:
                return {"status": "LEASE_LOST"}
            used = conn.execute("""
                SELECT COUNT(*) FROM scout_usaspending_requests
                WHERE attempted_at>=? AND attempted_at<?
            """, (utc(start), utc(end))).fetchone()[0]
            if used >= daily_limit:
                return {"status": "WAIT_QUOTA"}
            inserted = conn.execute("""
                INSERT INTO scout_usaspending_requests
                    (request_id, kind, identity_key, subject_id, page, attempted_at, outcome)
                VALUES (?, ?, ?, ?, ?, ?, 'RUNNING')
            """, (request_id, kind, identity_key, subject_id, page, clock))
        return {"status": "RESERVED", "request_id": request_id,
                "request_order": inserted.lastrowid}

    def finish(self, request_id: str, *, outcome: str) -> None:
        if outcome not in {"RETURNED", "FAILED"}:
            raise ValueError("Invalid USAspending request outcome")
        with self.store._connect() as conn:
            conn.execute("UPDATE scout_usaspending_requests SET outcome=? WHERE request_id=?",
                         (outcome, request_id))

    def daily_usage(self, *, as_of: datetime) -> int:
        start = as_of.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.store._connect() as conn:
            return int(conn.execute("""
                SELECT COUNT(*) FROM scout_usaspending_requests
                WHERE attempted_at>=? AND attempted_at<?
            """, (utc(start), utc(start + timedelta(days=1)))).fetchone()[0])


def fresh_state(clock: datetime, *, query_start: str | None = None,
                query_end: str | None = None) -> dict:
    return {"scan_started_at": utc(clock), "query_start": query_start,
            "query_end": query_end, "next_page": 1, "terminal": False,
            "pages_saved": 0, "usable_rows": 0, "rejected_rows": 0,
            "resumed": False, "status": "SCANNING", "retry_at": utc(clock),
            "updated_at": utc(clock), "last_attempt_at": None,
            "last_attempt_order": 0}


def state_due(state: dict | None, clock: datetime) -> bool:
    # An operational checkpoint written later cannot satisfy a historical run.
    return state is None or (state["updated_at"] <= utc(clock)
                             and state["retry_at"] <= utc(clock))


def coverage(states: list[dict | None], *, clock: datetime,
             refresh_days: int) -> dict[str, int]:
    cutoff = utc(clock - timedelta(days=refresh_days))
    current = [s for s in states if s and s["updated_at"] <= utc(clock)]
    complete = sum(s["status"] == "COMPLETE" and s["updated_at"] > cutoff for s in current)
    partial = sum(s["status"] != "COMPLETE" and s["updated_at"] > cutoff for s in current)
    return {"coverage_complete": complete, "coverage_partial": partial,
            "coverage_unchecked": len(states) - len(current),
            "coverage_stale": len(current) - complete - partial}
