from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from market_checker_app.prediction_contract import PRIMARY_TARGET_VERSION
from market_checker_app.services.us_equity_calendar import target_us_equity_window


@dataclass(frozen=True, slots=True)
class LabelQueueCandidate:
    snapshot_id: str
    ticker: str
    benchmark_ticker: str
    as_of: datetime
    due_at: datetime
    horizon_trading_days: int


@dataclass(frozen=True, slots=True)
class LabelQueueMetrics:
    backlog: int
    due: int
    actionable: int
    deferred_retry: int
    immature: int
    oldest_pending_as_of: datetime | None
    oldest_due_at: datetime | None


class PredictionLabelQueueStore:
    """Persistent fairness/retry state for prediction-label evaluation.

    Prediction snapshots remain the source of truth. This sidecar table stores
    only operational queue state so retry policy can evolve without changing
    immutable snapshot records.
    """

    CURSOR_KEY = "prediction_label_cursor"

    def __init__(
        self,
        db_path: Path,
        *,
        retry_delay: timedelta = timedelta(minutes=30),
    ) -> None:
        self.db_path = Path(db_path)
        self.retry_delay = retry_delay
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _iso(cls, value: datetime) -> str:
        return cls._utc(value).isoformat()

    @classmethod
    def _parse(cls, value: object) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return cls._utc(parsed)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_label_queue_state (
                    snapshot_id TEXT PRIMARY KEY,
                    retry_after TEXT,
                    defer_reason TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_label_queue_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prediction_label_queue_retry "
                "ON prediction_label_queue_state(retry_after)"
            )

    def _pending_rows(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT
                    p.snapshot_id,
                    p.ticker,
                    p.benchmark_ticker,
                    p.as_of,
                    p.horizon_trading_days,
                    q.retry_after,
                    q.defer_reason,
                    q.attempt_count
                FROM prediction_snapshots AS p
                LEFT JOIN prediction_label_queue_state AS q
                  ON q.snapshot_id = p.snapshot_id
                WHERE UPPER(p.label_status) = 'PENDING'
                  AND p.target_version = ?
                ORDER BY p.as_of ASC, p.ticker ASC, p.snapshot_id ASC
                """,
                (PRIMARY_TARGET_VERSION,),
            ).fetchall()

    @staticmethod
    def _candidate_from_row(
        row: sqlite3.Row,
        *,
        clock: datetime,
    ) -> LabelQueueCandidate | None:
        try:
            snapshot_as_of = datetime.fromisoformat(str(row["as_of"]))
            if snapshot_as_of.tzinfo is None or snapshot_as_of.utcoffset() is None:
                snapshot_as_of = snapshot_as_of.replace(tzinfo=timezone.utc)
            snapshot_as_of = snapshot_as_of.astimezone(timezone.utc)
            horizon = int(row["horizon_trading_days"] or 5)
            if horizon < 1:
                return None
            _, future = target_us_equity_window(snapshot_as_of, horizon)
        except (TypeError, ValueError):
            return None
        due_at = future[-1].close_at
        if due_at > clock:
            return None
        return LabelQueueCandidate(
            snapshot_id=str(row["snapshot_id"]),
            ticker=str(row["ticker"] or "").strip().upper(),
            benchmark_ticker=str(row["benchmark_ticker"] or "SPY").strip().upper(),
            as_of=snapshot_as_of,
            due_at=due_at,
            horizon_trading_days=horizon,
        )

    def _cursor(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM prediction_label_queue_meta WHERE key = ?",
                (self.CURSOR_KEY,),
            ).fetchone()
        return str(row["value"]) if row is not None and row["value"] else None

    def _save_cursor(self, snapshot_id: str, *, clock: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prediction_label_queue_meta(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (self.CURSOR_KEY, snapshot_id, self._iso(clock)),
            )

    @staticmethod
    def _rotate_after_cursor(
        candidates: list[LabelQueueCandidate],
        cursor: str | None,
    ) -> list[LabelQueueCandidate]:
        if not candidates or not cursor:
            return candidates
        for index, candidate in enumerate(candidates):
            if candidate.snapshot_id == cursor:
                return candidates[index + 1 :] + candidates[: index + 1]
        return candidates

    def select_candidates(
        self,
        *,
        as_of: datetime,
        limit: int,
    ) -> list[LabelQueueCandidate]:
        if limit < 1:
            raise ValueError("limit must be positive")
        clock = self._utc(as_of)
        candidates: list[LabelQueueCandidate] = []
        for row in self._pending_rows():
            candidate = self._candidate_from_row(row, clock=clock)
            if candidate is None:
                continue
            retry_after = self._parse(row["retry_after"])
            if retry_after is not None and retry_after > clock:
                continue
            candidates.append(candidate)
        ordered = self._rotate_after_cursor(candidates, self._cursor())
        selected = ordered[:limit]
        if selected:
            self._save_cursor(selected[-1].snapshot_id, clock=clock)
        return selected

    def mark_attempted(
        self,
        snapshot_ids: list[str],
        *,
        as_of: datetime,
    ) -> None:
        if not snapshot_ids:
            return
        clock = self._utc(as_of)
        with self._connect() as conn:
            for snapshot_id in snapshot_ids:
                conn.execute(
                    """
                    INSERT INTO prediction_label_queue_state(
                        snapshot_id, retry_after, defer_reason, attempt_count,
                        last_attempt_at, updated_at
                    ) VALUES (?, NULL, NULL, 1, ?, ?)
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                        retry_after=NULL,
                        defer_reason=NULL,
                        attempt_count=prediction_label_queue_state.attempt_count + 1,
                        last_attempt_at=excluded.last_attempt_at,
                        updated_at=excluded.updated_at
                    """,
                    (snapshot_id, self._iso(clock), self._iso(clock)),
                )

    def defer_pending(
        self,
        reasons: dict[str, str],
        *,
        as_of: datetime,
    ) -> None:
        if not reasons:
            return
        clock = self._utc(as_of)
        retry_after = clock + self.retry_delay
        with self._connect() as conn:
            for snapshot_id, reason in reasons.items():
                row = conn.execute(
                    "SELECT label_status FROM prediction_snapshots WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()
                if row is None or str(row["label_status"]).upper() != "PENDING":
                    conn.execute(
                        "DELETE FROM prediction_label_queue_state WHERE snapshot_id = ?",
                        (snapshot_id,),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO prediction_label_queue_state(
                        snapshot_id, retry_after, defer_reason, attempt_count,
                        last_attempt_at, updated_at
                    ) VALUES (?, ?, ?, 1, ?, ?)
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                        retry_after=excluded.retry_after,
                        defer_reason=excluded.defer_reason,
                        last_attempt_at=excluded.last_attempt_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        snapshot_id,
                        self._iso(retry_after),
                        str(reason)[:500],
                        self._iso(clock),
                        self._iso(clock),
                    ),
                )

    def cleanup_finalized(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM prediction_label_queue_state
                WHERE snapshot_id NOT IN (
                    SELECT snapshot_id FROM prediction_snapshots
                    WHERE UPPER(label_status) = 'PENDING'
                )
                """
            )
            return int(cursor.rowcount or 0)

    def metrics(self, *, as_of: datetime) -> LabelQueueMetrics:
        clock = self._utc(as_of)
        backlog = 0
        due = 0
        actionable = 0
        deferred_retry = 0
        immature = 0
        oldest_pending: datetime | None = None
        oldest_due: datetime | None = None
        for row in self._pending_rows():
            backlog += 1
            parsed_as_of = self._parse(row["as_of"])
            if parsed_as_of is not None and (
                oldest_pending is None or parsed_as_of < oldest_pending
            ):
                oldest_pending = parsed_as_of
            candidate = self._candidate_from_row(row, clock=clock)
            if candidate is None:
                immature += 1
                continue
            due += 1
            if oldest_due is None or candidate.due_at < oldest_due:
                oldest_due = candidate.due_at
            retry_after = self._parse(row["retry_after"])
            if retry_after is not None and retry_after > clock:
                deferred_retry += 1
            else:
                actionable += 1
        return LabelQueueMetrics(
            backlog=backlog,
            due=due,
            actionable=actionable,
            deferred_retry=deferred_retry,
            immature=immature,
            oldest_pending_as_of=oldest_pending,
            oldest_due_at=oldest_due,
        )
