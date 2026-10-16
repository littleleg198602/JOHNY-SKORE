"""Immutable observed transaction versions, without inferred source revisions.

A source capture is identified by its request, exact body hash and original
capture clocks, not its local manifest filename or replay time. Replaying it
cannot manufacture another publisher observation or backdate local knowledge.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from market_checker_app.storage.scout_store import ScoutStore


def _utc(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Transaction observation needs an explicit timezone")
    return value.astimezone(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


class AwardTransactionStore:
    """A bounded-query audit beside ScoutStore; existing finding hashes stay stable."""

    def __init__(self, store: ScoutStore) -> None:
        self.store = store
        with store._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS scout_award_transaction_observations (
                    observation_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    generated_award_id TEXT NOT NULL,
                    source_object_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL REFERENCES scout_findings(finding_id),
                    award_finding_id TEXT NOT NULL REFERENCES scout_findings(finding_id),
                    source_row_sha256 TEXT NOT NULL,
                    source_origin TEXT NOT NULL CHECK(source_origin IN ('FIXTURE', 'PUBLISHER_CAPTURE')),
                    request_started_at TEXT NOT NULL,
                    response_completed_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    first_ingested_at TEXT NOT NULL,
                    audit_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scout_award_transaction_observations_award
                    ON scout_award_transaction_observations(subject_id, generated_award_id, first_ingested_at);
                CREATE INDEX IF NOT EXISTS scout_award_transaction_observations_revision
                    ON scout_award_transaction_observations(subject_id, source_object_id, source_origin,
                                                           response_completed_at, first_ingested_at);
            """)

    def record_observation(self, *, subject_id: str, generated_award_id: str,
                           source_object_id: str, finding_id: str, award_finding_id: str,
                           source_row_sha256: str, capture_metadata: dict, capture_manifest: str,
                           page: int, row_index: int, action_date: str, modified_clock_audit: dict,
                           available_at: datetime, ingested_at: datetime) -> dict:
        mode = capture_metadata.get("capture_mode")
        if mode not in {"FIXTURE", "LIVE", "ARCHIVE_REPLAY"}:
            raise ValueError("Transaction observation must disclose acquisition mode")
        if mode == "ARCHIVE_REPLAY" and capture_metadata.get("original_capture_mode") != "LIVE":
            raise ValueError("Transaction replay observation needs an original LIVE capture")
        origin = "FIXTURE" if mode == "FIXTURE" else "PUBLISHER_CAPTURE"
        if (not re.fullmatch(r"[a-f0-9]{64}", source_row_sha256)
                or isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 5
                or isinstance(row_index, bool) or not isinstance(row_index, int) or not 0 <= row_index < 100):
            raise ValueError("Malformed transaction observation locator")
        started, completed = (_utc(capture_metadata[key])
                              for key in ("request_started_at", "response_completed_at"))
        available, ingested = _utc(available_at), _utc(ingested_at)
        if not started <= completed <= available <= ingested:
            raise ValueError("Transaction observation has inconsistent capture/knowledge clocks")
        # Keep all original capture metadata, including exact header/timestamp
        # representations. Only the replay wrapper differs from the publisher
        # observation it imports; local filenames cannot create new source data.
        capture_identity = {key: value for key, value in capture_metadata.items()
                            if key not in {"capture_mode", "original_capture_mode", "original_capture_manifest"}}
        capture_identity["capture_mode"] = "FIXTURE" if origin == "FIXTURE" else "LIVE"
        identity = {"finding_id": finding_id, "award_finding_id": award_finding_id,
                    "source_row_sha256": source_row_sha256, "source_origin": origin,
                    "source_capture_identity_sha256": hashlib.sha256(_json(capture_identity).encode()).hexdigest(),
                    "source_request": capture_metadata["request"],
                    "response_sha256": capture_metadata["response_sha256"],
                    "request_started_at": started, "response_completed_at": completed,
                    "page": page, "row_index": row_index}
        observation_id = "transaction-observation:" + hashlib.sha256(_json(identity).encode()).hexdigest()
        audit = {"schema_version": "award_transaction_observation_v1", **identity,
                 "action_date_raw": action_date, "modified_clock_audit": modified_clock_audit,
                 "capture_manifest": capture_manifest, "capture_metadata": capture_metadata,
                 "ingestion_mode": mode, "available_at": available, "first_ingested_at": ingested,
                 "availability_basis": "max(original_response_completed_at, relationship_known_at, award_available_at)",
                 "publisher_publication_time_verified": False, "source_revision_order_verified": False,
                 "historical_revisions_complete": False,
                 "http_headers_are_transaction_revision_timestamps": False}
        encoded = _json(audit)
        if len(encoded.encode()) > 131_072:
            raise ValueError("Transaction observation audit exceeds byte budget")
        with self.store._connect() as connection:
            inserted = connection.execute("""
                INSERT OR IGNORE INTO scout_award_transaction_observations
                    (observation_id, subject_id, generated_award_id, source_object_id, finding_id,
                     award_finding_id, source_row_sha256, source_origin, request_started_at,
                     response_completed_at, available_at, first_ingested_at, audit_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (observation_id, subject_id, generated_award_id, source_object_id, finding_id,
                  award_finding_id, source_row_sha256, origin, started, completed, available, ingested, encoded))
            different_prior = int(connection.execute("""
                SELECT COUNT(DISTINCT source_row_sha256)
                FROM scout_award_transaction_observations
                WHERE subject_id=? AND source_object_id=? AND source_origin=?
                  AND response_completed_at<? AND available_at<=? AND first_ingested_at<=?
                  AND source_row_sha256!=?
            """, (subject_id, source_object_id, origin, completed, ingested, ingested, source_row_sha256)).fetchone()[0])
        return {"observation_id": observation_id, "new_observation": inserted.rowcount == 1,
                "different_rows_in_prior_captures": different_prior,
                "source_revision_order_verified": False, "historical_revisions_complete": False}

    def observations_for_award(self, *, subject_id: str, generated_award_id: str,
                               as_of: datetime, limit: int = 500) -> dict:
        """Actual local ingestion is a gate even for older genuine archived captures."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Transaction observation read needs a one-to-500 row bound")
        cutoff = _utc(as_of)
        with self.store._connect() as connection:
            rows = connection.execute("""
                SELECT * FROM scout_award_transaction_observations
                WHERE subject_id=? AND generated_award_id=? AND available_at<=? AND first_ingested_at<=?
                ORDER BY response_completed_at, observation_id LIMIT ?
            """, (subject_id, generated_award_id, cutoff, cutoff, limit + 1)).fetchall()
        result = []
        for row in rows[:limit]:
            item = dict(row)
            item["audit"] = json.loads(item.pop("audit_json"))
            result.append(item)
        return {"observations": result, "truncated": len(rows) > limit,
                "source_revision_order_verified": False, "historical_revisions_complete": False,
                "capture_order_is_publisher_revision_order": False}
