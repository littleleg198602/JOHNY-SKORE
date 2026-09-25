from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Scout timestamps require an explicit timezone")
    return value.astimezone(timezone.utc).isoformat()


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ScoutJob:
    job_id: str
    source: str
    subject_id: str
    reason: str
    cursor: str | None
    attempts: int
    failure_count: int
    lease_token: str


class ScoutStore:
    """Durable, independently leased work and immutable source observations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scout_jobs (
                    job_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    due_at TEXT NOT NULL,
                    cursor TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'READY',
                    lease_token TEXT,
                    lease_until TEXT,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scout_jobs_ready
                    ON scout_jobs(status, due_at, priority);
                CREATE TABLE IF NOT EXISTS scout_provider_leases (
                    source TEXT PRIMARY KEY,
                    lease_token TEXT NOT NULL,
                    lease_until TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scout_provider_cooldowns (
                    source TEXT PRIMARY KEY,
                    retry_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scout_attempts (
                    job_id TEXT NOT NULL REFERENCES scout_jobs(job_id),
                    attempt_no INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    outcome TEXT,
                    error TEXT,
                    PRIMARY KEY(job_id, attempt_no)
                );
                CREATE TABLE IF NOT EXISTS scout_findings (
                    finding_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    source_object_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    UNIQUE(source, subject_id, source_object_id, content_hash)
                );
                CREATE INDEX IF NOT EXISTS scout_findings_asof
                    ON scout_findings(subject_id, available_at, first_observed_at);
                CREATE TABLE IF NOT EXISTS scout_leads (
                    lead_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL REFERENCES scout_findings(finding_id),
                    parent_lead_id TEXT REFERENCES scout_leads(lead_id),
                    question TEXT NOT NULL,
                    depth INTEGER NOT NULL CHECK(depth BETWEEN 0 AND 2),
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(finding_id, question)
                );
                CREATE TABLE IF NOT EXISTS scout_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scout_analysis_snapshots (
                    orchestration_id TEXT PRIMARY KEY,
                    as_of TEXT NOT NULL,
                    finding_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scout_lead_transitions (
                    transition_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL REFERENCES scout_leads(lead_id),
                    status TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_for_json TEXT NOT NULL,
                    evidence_against_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scout_lead_transitions_asof
                    ON scout_lead_transitions(lead_id, changed_at);
            """)
            conn.execute(
                "INSERT OR IGNORE INTO scout_schema_migrations(version, applied_at) "
                "VALUES(1, ?)", (_utc(datetime.now(timezone.utc)),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO scout_schema_migrations(version, applied_at) "
                "VALUES(2, ?)", (_utc(datetime.now(timezone.utc)),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO scout_schema_migrations(version, applied_at) "
                "VALUES(3, ?)", (_utc(datetime.now(timezone.utc)),),
            )

    def enqueue(
        self, *, source: str, subject_id: str, reason: str,
        due_at: datetime, priority: int = 0, cursor: str | None = None,
    ) -> str:
        due = _utc(due_at)
        dedupe = _key(source, subject_id, reason)
        job_id = f"scout:{dedupe[:24]}"
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO scout_jobs(job_id, dedupe_key, source, subject_id,
                    reason, priority, due_at, cursor, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    due_at=CASE WHEN scout_jobs.status='DONE' THEN excluded.due_at
                        WHEN excluded.due_at < scout_jobs.due_at THEN excluded.due_at
                        ELSE scout_jobs.due_at END,
                    status=CASE WHEN scout_jobs.status='DONE' THEN 'READY'
                        ELSE scout_jobs.status END,
                    priority=MAX(scout_jobs.priority, excluded.priority),
                    updated_at=excluded.updated_at
            """, (job_id, dedupe, source, subject_id, reason, priority, due, cursor, due))
        return job_id

    def claim_provider(
        self, source: str, *, as_of: datetime, seconds: int = 1800,
    ) -> str | None:
        clock = _utc(as_of)
        until = _utc(as_of + timedelta(seconds=seconds))
        token = uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cooldown = conn.execute(
                "SELECT retry_at FROM scout_provider_cooldowns WHERE source=?",
                (source,),
            ).fetchone()
            if cooldown is not None and cooldown[0] > clock:
                return None
            row = conn.execute(
                "SELECT lease_until FROM scout_provider_leases WHERE source=?",
                (source,),
            ).fetchone()
            if row is not None and row[0] > clock:
                return None
            conn.execute("""
                INSERT INTO scout_provider_leases(source, lease_token, lease_until)
                VALUES(?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    lease_token=excluded.lease_token,
                    lease_until=excluded.lease_until
            """, (source, token, until))
        return token

    def defer_provider(
        self, source: str, token: str, *, as_of: datetime,
        retry_after: timedelta, reason: str,
    ) -> bool:
        until = _utc(as_of + retry_after)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT lease_token FROM scout_provider_leases WHERE source=?",
                (source,),
            ).fetchone()
            if row is None or row[0] != token:
                return False
            conn.execute("""
                INSERT INTO scout_provider_cooldowns(source, retry_at, reason)
                VALUES(?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    retry_at=MAX(retry_at, excluded.retry_at),
                    reason=excluded.reason
            """, (source, until, reason[:200]))
        return True

    def provider_retry_at(self, source: str, *, as_of: datetime) -> str | None:
        cooldown = self.provider_cooldown(source, as_of=as_of)
        return cooldown["retry_at"] if cooldown else None

    def provider_cooldown(
        self, source: str, *, as_of: datetime,
    ) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT retry_at, reason FROM scout_provider_cooldowns "
                "WHERE source=? AND retry_at>?", (source, _utc(as_of)),
            ).fetchone()
        return {"retry_at": str(row[0]), "reason": str(row[1])} if row else None

    def release_provider(self, source: str, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM scout_provider_leases WHERE source=? AND lease_token=?",
                (source, token),
            )

    def renew_provider(
        self, source: str, token: str, *, as_of: datetime, seconds: int = 1800,
    ) -> bool:
        if seconds < 1:
            raise ValueError("Provider lease duration must be positive")
        clock = _utc(as_of)
        until = _utc(as_of + timedelta(seconds=seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute("""
                UPDATE scout_provider_leases SET lease_until=?
                WHERE source=? AND lease_token=? AND lease_until>?
            """, (until, source, token, clock))
        return updated.rowcount == 1

    def lease(
        self, *, as_of: datetime, seconds: int = 300, source: str | None = None,
    ) -> ScoutJob | None:
        if seconds < 1:
            raise ValueError("lease duration must be positive")
        clock = _utc(as_of)
        until = _utc(as_of + timedelta(seconds=seconds))
        token = uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM scout_jobs
                WHERE (? IS NULL OR source=?) AND
                    ((status='READY' AND due_at<=?)
                    OR (status='LEASED' AND lease_until<=?))
                ORDER BY priority DESC, due_at, updated_at, job_id LIMIT 1
            """, (source, source, clock, clock)).fetchone()
            if row is None:
                return None
            attempt = int(row["attempts"]) + 1
            conn.execute("""
                UPDATE scout_jobs SET status='LEASED', lease_token=?,
                    lease_until=?, attempts=?, updated_at=? WHERE job_id=?
            """, (token, until, attempt, clock, row["job_id"]))
            conn.execute("""
                INSERT INTO scout_attempts(job_id, attempt_no, started_at)
                VALUES(?, ?, ?)
            """, (row["job_id"], attempt, clock))
            return ScoutJob(row["job_id"], row["source"], row["subject_id"],
                            row["reason"], row["cursor"], attempt,
                            int(row["failure_count"]), token)

    def finish(
        self, job: ScoutJob, *, as_of: datetime, error: str | None = None,
        retry_after: timedelta = timedelta(minutes=30), max_attempts: int = 5,
        cursor: str | None = None,
    ) -> bool:
        clock = _utc(as_of)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT lease_token FROM scout_jobs WHERE job_id=?", (job.job_id,)
            ).fetchone()
            if row is None or row["lease_token"] != job.lease_token:
                return False
            failures = job.failure_count + 1 if error else 0
            status = "DONE" if error is None else (
                "DEAD" if failures >= max_attempts else "READY"
            )
            due = clock if error is None else _utc(as_of + retry_after)
            conn.execute("""
                UPDATE scout_jobs SET status=?, due_at=?, cursor=?, failure_count=?,
                    lease_token=NULL, lease_until=NULL, last_error=?, updated_at=?
                WHERE job_id=?
            """, (status, due, cursor if cursor is not None else job.cursor, failures,
                  str(error)[:1000] if error else None, clock, job.job_id))
            conn.execute("""
                UPDATE scout_attempts SET finished_at=?, outcome=?, error=?
                WHERE job_id=? AND attempt_no=?
            """, (clock, status, str(error)[:1000] if error else None,
                  job.job_id, job.attempts))
            return True

    def record_finding(
        self, *, source: str, subject_id: str, source_object_id: str,
        content_hash: str, title: str, source_url: str, locator: str,
        published_at: datetime, available_at: datetime, observed_at: datetime,
        details: dict[str, object], verification_status: str = "SOURCE_VERIFIED",
    ) -> tuple[str, bool]:
        published = _utc(published_at)
        available = _utc(available_at)
        observed = _utc(observed_at)
        if available > observed:
            raise ValueError("Source cannot be observed before it is available")
        if verification_status not in {"SOURCE_VERIFIED", "CLAIM_VERIFIED", "UNVERIFIED"}:
            raise ValueError("Unknown scout verification status")
        if verification_status == "CLAIM_VERIFIED" and not (
            details.get("claim_text") and details.get("verification_method")
        ):
            raise ValueError("Claim verification needs the precise claim and method")
        finding_id = f"finding:{_key(source, subject_id, source_object_id, content_hash)[:32]}"
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT OR IGNORE INTO scout_findings(finding_id, source, subject_id,
                    source_object_id, content_hash, title, source_url, locator,
                    published_at, available_at, first_observed_at, retrieved_at,
                    verification_status, details_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (finding_id, source, subject_id, source_object_id, content_hash,
                  title, source_url, locator, published, available, observed,
                  observed, verification_status, json.dumps(details, sort_keys=True)))
            newly_seen = cur.rowcount == 1
            if not newly_seen:
                conn.execute(
                    "UPDATE scout_findings SET retrieved_at=MAX(retrieved_at, ?) "
                    "WHERE finding_id=?",
                    (observed, finding_id),
                )
        return finding_id, newly_seen

    def add_lead(
        self, *, subject_id: str, finding_id: str, question: str,
        as_of: datetime, parent_lead_id: str | None = None,
    ) -> str:
        clock = _utc(as_of)
        lead_id = f"lead:{_key(finding_id, question)[:32]}"
        with self._connect() as conn:
            finding = conn.execute("""
                SELECT 1 FROM scout_findings
                WHERE finding_id=? AND subject_id=? AND available_at<=?
                  AND first_observed_at<=?
            """, (finding_id, subject_id, clock, clock)).fetchone()
            if finding is None:
                raise ValueError("Lead source is unavailable or belongs to another subject")
            parent_depth = 0
            if parent_lead_id:
                parent = conn.execute(
                    "SELECT depth, subject_id, created_at FROM scout_leads WHERE lead_id=?",
                    (parent_lead_id,),
                ).fetchone()
                if parent is None:
                    raise ValueError("Unknown parent lead")
                if parent["subject_id"] != subject_id:
                    raise ValueError("Parent lead belongs to a different subject")
                if parent["created_at"] > clock:
                    raise ValueError("Child lead predates its parent")
                parent_depth = int(parent[0]) + 1
            if parent_depth > 2:
                raise ValueError("Maximum scout lead depth exceeded")
            conn.execute("""
                INSERT OR IGNORE INTO scout_leads(lead_id, subject_id, finding_id,
                    parent_lead_id, question, depth, due_at, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """, (lead_id, subject_id, finding_id, parent_lead_id,
                  question, parent_depth, clock, clock))
        return lead_id

    def advance_lead(
        self, lead_id: str, *, status: str, as_of: datetime, reason: str,
        evidence_for: tuple[str, ...] = (), evidence_against: tuple[str, ...] = (),
    ) -> None:
        """Transition a case with dated evidence; never rewrite its history."""
        allowed = {
            "OPEN": {"INVESTIGATING", "INSUFFICIENT_DATA", "EXPIRED"},
            "INVESTIGATING": {"VERIFIED", "CONTRADICTED", "INSUFFICIENT_DATA", "EXPIRED"},
        }
        clock = _utc(as_of)
        if not reason.strip():
            raise ValueError("A lead transition needs an audit reason")
        for_ids = sorted(set(evidence_for))
        against_ids = sorted(set(evidence_against))
        if set(for_ids) & set(against_ids):
            raise ValueError("An observation cannot support and oppose the same lead")
        if status == "VERIFIED" and not for_ids:
            raise ValueError("Verified conclusion requires supporting evidence")
        if status == "CONTRADICTED" and not against_ids:
            raise ValueError("Contradicted conclusion requires contrary evidence")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lead = conn.execute(
                "SELECT status, subject_id, created_at FROM scout_leads WHERE lead_id=?",
                (lead_id,),
            ).fetchone()
            if lead is None or clock < lead["created_at"]:
                raise ValueError("Unknown lead or transition before creation")
            last_change = conn.execute(
                "SELECT MAX(changed_at) FROM scout_lead_transitions WHERE lead_id=?",
                (lead_id,),
            ).fetchone()[0]
            if last_change is not None and clock < last_change:
                raise ValueError("Lead history cannot be backdated")
            if status not in allowed.get(lead["status"], set()):
                raise ValueError(f"Invalid lead transition: {lead['status']} → {status}")
            for identifier in for_ids + against_ids:
                required_status = (
                    "CLAIM_VERIFIED" if status in {"VERIFIED", "CONTRADICTED"}
                    else "SOURCE_VERIFIED"
                )
                finding = conn.execute("""
                    SELECT 1 FROM scout_findings
                    WHERE finding_id=? AND subject_id=?
                      AND first_observed_at<=? AND available_at<=?
                      AND verification_status=?
                """, (identifier, lead["subject_id"], clock, clock,
                      required_status)).fetchone()
                if finding is None:
                    raise ValueError("Transition cites unavailable or foreign evidence")
            conn.execute("""
                INSERT INTO scout_lead_transitions(
                    transition_id, lead_id, status, changed_at, reason,
                    evidence_for_json, evidence_against_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """, (uuid4().hex, lead_id, status, clock, reason,
                  json.dumps(for_ids), json.dumps(against_ids)))
            conn.execute(
                "UPDATE scout_leads SET status=? WHERE lead_id=?", (status, lead_id),
            )

    def findings_as_of(
        self, subject_id: str, *, as_of: datetime,
        actual_observation: bool = True,
    ) -> list[dict[str, object]]:
        cutoff = _utc(as_of)
        predicate = "AND first_observed_at<=?" if actual_observation else ""
        params = (subject_id, cutoff, cutoff) if actual_observation else (subject_id, cutoff)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT * FROM scout_findings
                WHERE subject_id=? AND available_at<=? {predicate}
                ORDER BY available_at DESC, finding_id
            """, params).fetchall()
        return [dict(row) for row in rows]

    def finding_by_id(self, finding_id: str, *, subject_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scout_findings WHERE finding_id=? AND subject_id=?",
                (finding_id, subject_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def record_analysis_snapshot(
        self, orchestration_id: str, *, as_of: datetime,
        finding_ids: list[str],
    ) -> None:
        """Freeze exactly which scout observations an analytical run consumed."""
        if not orchestration_id:
            raise ValueError("Missing orchestration ID")
        cutoff = _utc(as_of)
        identifiers = sorted(set(finding_ids))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for start in range(0, len(identifiers), 800):
                chunk = identifiers[start:start + 800]
                placeholders = ", ".join("?" for _ in chunk)
                count = conn.execute(f"""
                    SELECT COUNT(*) FROM scout_findings
                    WHERE finding_id IN ({placeholders})
                      AND available_at<=? AND first_observed_at<=?
                      AND verification_status='SOURCE_VERIFIED'
                """, (*chunk, cutoff, cutoff)).fetchone()[0]
                if count != len(chunk):
                    raise ValueError("Snapshot contains unavailable scout evidence")
            payload = json.dumps(identifiers)
            existing = conn.execute(
                "SELECT as_of, finding_ids_json FROM scout_analysis_snapshots "
                "WHERE orchestration_id=?", (orchestration_id,),
            ).fetchone()
            if existing:
                if existing[0] != cutoff or existing[1] != payload:
                    raise ValueError("Cannot rewrite an existing scout analysis snapshot")
                return
            conn.execute("""
                INSERT INTO scout_analysis_snapshots(
                    orchestration_id, as_of, finding_ids_json, created_at
                ) VALUES(?, ?, ?, ?)
            """, (orchestration_id, cutoff, payload,
                  _utc(datetime.now(timezone.utc))))

    def analysis_snapshot(self, orchestration_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT as_of, finding_ids_json FROM scout_analysis_snapshots "
                "WHERE orchestration_id=?", (orchestration_id,),
            ).fetchone()
        return ({"as_of": row[0], "finding_ids": json.loads(row[1])}
                if row else None)

    def metrics(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM scout_jobs GROUP BY status"
            ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def open_leads(
        self, subjects: list[str], *, as_of: datetime, limit: int = 50,
    ) -> list[dict[str, object]]:
        if not subjects or limit < 1:
            return []
        bounded = list(dict.fromkeys(subjects))[:900]
        placeholders = ", ".join("?" for _ in bounded)
        cutoff = _utc(as_of)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT * FROM (
                    SELECT l.subject_id, l.question,
                           COALESCE((
                               SELECT t.status FROM scout_lead_transitions AS t
                               WHERE t.lead_id=l.lead_id AND t.changed_at<=?
                               ORDER BY t.changed_at DESC, t.rowid DESC LIMIT 1
                           ), 'OPEN') AS status,
                           l.depth, l.created_at, f.source_url, f.locator,
                           f.verification_status
                    FROM scout_leads AS l
                    JOIN scout_findings AS f ON f.finding_id=l.finding_id
                    WHERE l.subject_id IN ({placeholders}) AND l.created_at<=?
                      AND f.available_at<=? AND f.first_observed_at<=?
                ) WHERE status IN ('OPEN', 'INVESTIGATING')
                ORDER BY created_at DESC, question DESC LIMIT ?
            """, (cutoff, *bounded, cutoff, cutoff, cutoff, limit)).fetchall()
        return [dict(row) for row in rows]

    def closed_leads(
        self, subjects: list[str], *, as_of: datetime, limit: int = 50,
    ) -> list[dict[str, object]]:
        if not subjects or limit < 1:
            return []
        bounded = list(dict.fromkeys(subjects))[:900]
        placeholders = ", ".join("?" for _ in bounded)
        cutoff = _utc(as_of)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT l.subject_id, l.question, t.status, t.changed_at,
                       t.reason, t.evidence_for_json, t.evidence_against_json,
                       f.source_url, f.locator
                FROM scout_leads AS l
                JOIN scout_findings AS f ON f.finding_id=l.finding_id
                JOIN scout_lead_transitions AS t ON t.transition_id=(
                    SELECT historical.transition_id
                    FROM scout_lead_transitions AS historical
                    WHERE historical.lead_id=l.lead_id AND historical.changed_at<=?
                    ORDER BY historical.changed_at DESC, historical.rowid DESC LIMIT 1
                )
                WHERE l.subject_id IN ({placeholders}) AND l.created_at<=?
                  AND f.available_at<=? AND f.first_observed_at<=?
                  AND t.status IN ('VERIFIED', 'CONTRADICTED',
                                   'INSUFFICIENT_DATA', 'EXPIRED')
                ORDER BY t.changed_at DESC, l.lead_id DESC LIMIT ?
            """, (cutoff, *bounded, cutoff, cutoff, cutoff, limit)).fetchall()
        return [dict(row) for row in rows]

    def recent_failures(self, subjects: list[str], *, limit: int = 20) -> list[dict[str, object]]:
        if not subjects or limit < 1:
            return []
        bounded = list(dict.fromkeys(subjects))[:900]
        placeholders = ", ".join("?" for _ in bounded)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT subject_id, source, status, failure_count,
                       due_at, last_error
                FROM scout_jobs
                WHERE subject_id IN ({placeholders}) AND last_error IS NOT NULL
                ORDER BY updated_at DESC, job_id DESC LIMIT ?
            """, (*bounded, limit)).fetchall()
        return [dict(row) for row in rows]

    def latest_findings(
        self, subjects: list[str], *, as_of: datetime, limit: int = 100,
        source: str | None = None,
    ) -> list[dict[str, object]]:
        if not subjects or limit < 1:
            return []
        # The universe is bounded; bind each ticker rather than interpolating it.
        bounded = list(dict.fromkeys(subjects))[:900]
        placeholders = ", ".join("?" for _ in bounded)
        cutoff = _utc(as_of)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT subject_id, title, source_url, locator, available_at,
                       first_observed_at, verification_status, finding_id,
                       details_json
                FROM scout_findings
                WHERE subject_id IN ({placeholders}) AND (? IS NULL OR source=?)
                    AND available_at<=?
                    AND first_observed_at<=?
                ORDER BY first_observed_at DESC, finding_id DESC LIMIT ?
            """, (*bounded, source, source, cutoff, cutoff, limit)).fetchall()
        output: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            details = json.loads(str(item.pop("details_json")))
            item["stage"] = details.get("stage", "unclassified")
            item["item_locators"] = ", ".join(
                str(section.get("locator", ""))
                for section in details.get("item_excerpts", [])
            )
            output.append(item)
        return output

    def findings_for_watchlist(
        self, subjects: list[str], *, as_of: datetime, per_subject: int = 2,
    ) -> list[dict[str, object]]:
        if not subjects or per_subject < 1:
            return []
        bounded = list(dict.fromkeys(subjects))[:900]
        placeholders = ", ".join("?" for _ in bounded)
        cutoff = _utc(as_of)
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT * FROM (
                    SELECT f.*, ROW_NUMBER() OVER (
                        PARTITION BY subject_id
                        ORDER BY available_at DESC, finding_id DESC
                    ) AS position
                    FROM scout_findings AS f
                    WHERE subject_id IN ({placeholders})
                      AND available_at<=? AND first_observed_at<=?
                ) WHERE position<=?
                ORDER BY subject_id, available_at DESC, finding_id
            """, (*bounded, cutoff, cutoff, per_subject)).fetchall()
        return [dict(row) for row in rows]

    def has_findings(self, subjects: list[str], *, as_of: datetime) -> bool:
        if not subjects:
            return False
        bounded = list(dict.fromkeys(subjects))[:900]
        placeholders = ", ".join("?" for _ in bounded)
        cutoff = _utc(as_of)
        with self._connect() as conn:
            row = conn.execute(f"""
                SELECT 1 FROM scout_findings
                WHERE subject_id IN ({placeholders})
                  AND available_at<=? AND first_observed_at<=?
                  AND verification_status='SOURCE_VERIFIED'
                LIMIT 1
            """, (*bounded, cutoff, cutoff)).fetchone()
        return row is not None
