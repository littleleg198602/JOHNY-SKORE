from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.utils.dates import to_iso

if TYPE_CHECKING:
    from market_checker_app.agents.contracts import OrchestrationReport


class SQLiteStore:
    SIGNAL_HISTORY_INSERT = """
        INSERT INTO signal_history(
            run_id, ticker, updated_at, market_cap_usd, current_price, current_price_source,
            scoring_version, legacy_total_score, legacy_signal, tech_source_used,
            rank_market_cap, news_count_48h, news_score, tech_score, yahoo_score, behavioral_score, risk_score,
            raw_total_score, quality_adjusted_score, risk_adjusted_score, final_total_score, final_confidence,
            news_confidence, tech_confidence, yahoo_confidence, behavioral_confidence, data_quality_score,
            module_confidence, decision_confidence, panic_score,
            bull_score, bear_score, bull_bear_spread,
            bullish_module_count, bearish_module_count, neutral_module_count, downgrade_count,
            blocked_reasons, module_breakdown,
            decision_signal, forecast, action, action_reasons,
            signal, signal_strength, rank_in_watchlist, percentile_in_watchlist, regime,
            reasons, warnings, risk_flags, key_drivers, overall_summary,
            last_week_change_pct, last_14d_change_pct, last_1m_change_pct, last_3m_change_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _json_default(value: Any) -> object:
        if isinstance(value, datetime):
            return to_iso(value)
        if hasattr(value, "item"):
            return value.item()
        return str(value)

    @classmethod
    def _json_dump(cls, value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=cls._json_default,
        )

    @classmethod
    def _persist_entity_identity_version(
        cls,
        conn: sqlite3.Connection,
        *,
        entity_id: str,
        orchestration_id: str,
        agent_run_id: int,
        observed_at: str,
    ) -> None:
        identity_columns = (
            "ticker",
            "yahoo_ticker",
            "name",
            "exchange",
            "cik",
            "isin",
            "lei",
            "legal_entity_id",
            "issuer_id",
            "instrument_id",
            "parent_entity_id",
            "security_type",
            "share_class",
            "mic",
            "country_code",
            "aliases_json",
            "valid_from",
            "valid_to",
        )
        selected_columns = identity_columns + (
            "source",
            "source_url",
            "confidence",
            "metadata_json",
        )
        row = conn.execute(
            f"SELECT {', '.join(selected_columns)} FROM entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError(
                f"Entity {entity_id!r} disappeared before identity versioning"
            )
        current = dict(zip(selected_columns, row))
        snapshot = {
            column: current[column]
            for column in identity_columns
            + ("source", "source_url", "confidence")
        }
        snapshot_hash = hashlib.sha256(
            cls._json_dump(snapshot).encode("utf-8")
        ).hexdigest()
        active = conn.execute(
            """
            SELECT version_id, snapshot_hash
            FROM entity_identity_versions
            WHERE entity_id = ? AND superseded_at IS NULL
            ORDER BY observed_at DESC, version_id DESC
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if active is not None and active[1] == snapshot_hash:
            return
        if active is not None:
            conn.execute(
                """
                UPDATE entity_identity_versions
                SET superseded_at = ?
                WHERE version_id = ? AND superseded_at IS NULL
                """,
                (observed_at, active[0]),
            )
        version_id = hashlib.sha256(
            "|".join(
                (
                    entity_id,
                    snapshot_hash,
                    observed_at,
                    orchestration_id,
                    str(agent_run_id),
                )
            ).encode("utf-8")
        ).hexdigest()
        conn.execute(
            """
            INSERT INTO entity_identity_versions(
                version_id, entity_id, snapshot_hash, ticker, yahoo_ticker,
                name, exchange, cik, isin, lei, legal_entity_id, issuer_id,
                instrument_id, parent_entity_id, security_type, share_class,
                mic, country_code, aliases_json, effective_from, effective_to,
                observed_at, superseded_at, source, source_url, confidence,
                orchestration_id, agent_run_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                entity_id,
                snapshot_hash,
                current["ticker"],
                current["yahoo_ticker"],
                current["name"],
                current["exchange"],
                current["cik"],
                current["isin"],
                current["lei"],
                current["legal_entity_id"],
                current["issuer_id"],
                current["instrument_id"],
                current["parent_entity_id"],
                current["security_type"],
                current["share_class"],
                current["mic"],
                current["country_code"],
                current["aliases_json"],
                current["valid_from"] or observed_at,
                current["valid_to"],
                observed_at,
                current["source"],
                current["source_url"],
                float(current["confidence"] or 0.0),
                orchestration_id,
                agent_run_id,
                current["metadata_json"],
            ),
        )

    def _ensure_signal_history_columns(self, conn: sqlite3.Connection) -> None:
        expected: dict[str, str] = {
            "behavioral_score": "REAL",
            "risk_score": "REAL",
            "quality_adjusted_score": "REAL",
            "risk_adjusted_score": "REAL",
            "behavioral_confidence": "REAL",
            "rank_in_watchlist": "INTEGER",
            "percentile_in_watchlist": "REAL",
            "risk_flags": "TEXT",
            "key_drivers": "TEXT",
            "overall_summary": "TEXT",
            "regime": "TEXT",
            "current_price": "REAL",
            "current_price_source": "TEXT",
            "scoring_version": "TEXT",
            "legacy_total_score": "REAL",
            "legacy_signal": "TEXT",
            "tech_source_used": "TEXT",
            "last_14d_change_pct": "REAL",
            "decision_signal": "TEXT",
            "forecast": "TEXT",
            "action": "TEXT",
            "action_reasons": "TEXT",
            "module_confidence": "REAL",
            "decision_confidence": "REAL",
            "panic_score": "REAL",
            "bull_score": "REAL",
            "bear_score": "REAL",
            "bull_bear_spread": "REAL",
            "bullish_module_count": "INTEGER",
            "bearish_module_count": "INTEGER",
            "neutral_module_count": "INTEGER",
            "downgrade_count": "INTEGER",
            "blocked_reasons": "TEXT",
            "module_breakdown": "TEXT",
        }
        existing = {row[1] for row in conn.execute("PRAGMA table_info(signal_history)").fetchall()}
        for column, ctype in expected.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE signal_history ADD COLUMN {column} {ctype}")

    @staticmethod
    def _ensure_entity_columns(conn: sqlite3.Connection) -> None:
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()
        }
        expected = {
            "legal_entity_id": "TEXT",
            "issuer_id": "TEXT",
            "instrument_id": "TEXT",
            "parent_entity_id": "TEXT",
            "security_type": "TEXT",
            "share_class": "TEXT",
            "mic": "TEXT",
            "country_code": "TEXT",
            "valid_from": "TEXT",
            "valid_to": "TEXT",
            "source_url": "TEXT",
            "confidence": "REAL NOT NULL DEFAULT 0.0",
        }
        for column, column_type in expected.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE entities ADD COLUMN {column} {column_type}"
                )

    @staticmethod
    def _ensure_document_columns(conn: sqlite3.Connection) -> None:
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        expected = {
            "source_priority": "INTEGER NOT NULL DEFAULT 0",
            "source_authority": "TEXT",
            "legal_entity_id": "TEXT",
            "issuer_id": "TEXT",
            "instrument_id": "TEXT",
            "reporting_period_end": "TEXT",
            "is_audited": "INTEGER NOT NULL DEFAULT 0",
            "language": "TEXT",
            "canonical_event_key": "TEXT",
        }
        for column, column_type in expected.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE documents ADD COLUMN {column} {column_type}"
                )

    @staticmethod
    def _ensure_quality_gate_columns(conn: sqlite3.Connection) -> None:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(quality_gate_checks)").fetchall()
        }
        if "claim_ids_json" not in existing:
            conn.execute(
                "ALTER TABLE quality_gate_checks ADD COLUMN claim_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
        for column in (
            "relationship_ids_json",
            "exposure_ids_json",
            "regulatory_event_ids_json",
            "decision_ids_json",
            "evaluation_ids_json",
            "activation_ids_json",
            "identity_conflict_ids_json",
            "governance_event_ids_json",
        ):
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE quality_gate_checks ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
                )

    @staticmethod
    def _ensure_document_observation_columns(conn: sqlite3.Connection) -> None:
        existing = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(document_observations)"
            ).fetchall()
        }
        expected = {
            "source_url": "TEXT",
            "content_hash": "TEXT",
            "mime_type": "TEXT",
            "source_type": "TEXT",
            "source_priority": "INTEGER NOT NULL DEFAULT 0",
            "source_authority": "TEXT",
            "legal_entity_id": "TEXT",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "canonical_event_key": "TEXT",
        }
        for column, column_type in expected.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE document_observations ADD COLUMN {column} {column_type}"
                )

    @staticmethod
    def _ensure_regulatory_contract_event_columns(
        conn: sqlite3.Connection,
    ) -> None:
        existing = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(regulatory_contract_events)"
            ).fetchall()
        }
        if "legal_entity_id" not in existing:
            conn.execute(
                "ALTER TABLE regulatory_contract_events ADD COLUMN legal_entity_id TEXT"
            )

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    watchlist_size INTEGER NOT NULL,
                    processed_symbols INTEGER NOT NULL,
                    warnings_count INTEGER NOT NULL,
                    errors_count INTEGER NOT NULL,
                    excel_path TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    market_cap_usd REAL,
                    current_price REAL,
                    current_price_source TEXT,
                    scoring_version TEXT,
                    legacy_total_score REAL,
                    legacy_signal TEXT,
                    tech_source_used TEXT,
                    rank_market_cap INTEGER,
                    news_count_48h INTEGER,
                    news_score REAL,
                    tech_score REAL,
                    yahoo_score REAL,
                    behavioral_score REAL,
                    risk_score REAL,
                    raw_total_score REAL,
                    quality_adjusted_score REAL,
                    risk_adjusted_score REAL,
                    final_total_score REAL,
                    final_confidence REAL,
                    news_confidence REAL,
                    tech_confidence REAL,
                    yahoo_confidence REAL,
                    behavioral_confidence REAL,
                    data_quality_score REAL,
                    module_confidence REAL,
                    decision_confidence REAL,
                    panic_score REAL,
                    bull_score REAL,
                    bear_score REAL,
                    bull_bear_spread REAL,
                    bullish_module_count INTEGER,
                    bearish_module_count INTEGER,
                    neutral_module_count INTEGER,
                    downgrade_count INTEGER,
                    blocked_reasons TEXT,
                    module_breakdown TEXT,
                    decision_signal TEXT,
                    forecast TEXT,
                    action TEXT,
                    action_reasons TEXT,
                    signal TEXT,
                    signal_strength TEXT,
                    rank_in_watchlist INTEGER,
                    percentile_in_watchlist REAL,
                    regime TEXT,
                    reasons TEXT,
                    warnings TEXT,
                    risk_flags TEXT,
                    key_drivers TEXT,
                    overall_summary TEXT,
                    last_week_change_pct REAL,
                    last_14d_change_pct REAL,
                    last_1m_change_pct REAL,
                    last_3m_change_pct REAL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )
            self._ensure_signal_history_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                    orchestration_id TEXT PRIMARY KEY,
                    pipeline_run_id INTEGER,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    shadow_mode INTEGER NOT NULL,
                    watchlist_size INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(pipeline_run_id) REFERENCES runs(run_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    agent_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    orchestration_id TEXT NOT NULL,
                    pipeline_run_id INTEGER,
                    agent_name TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    required INTEGER NOT NULL,
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    input_count INTEGER NOT NULL,
                    output_count INTEGER NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(orchestration_id, agent_name),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(pipeline_run_id) REFERENCES runs(run_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL UNIQUE,
                    yahoo_ticker TEXT,
                    name TEXT,
                    exchange TEXT,
                    cik TEXT,
                    isin TEXT,
                    lei TEXT,
                    sector TEXT,
                    industry TEXT,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    legal_entity_id TEXT,
                    issuer_id TEXT,
                    instrument_id TEXT,
                    parent_entity_id TEXT,
                    security_type TEXT,
                    share_class TEXT,
                    mic TEXT,
                    country_code TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    source_url TEXT,
                    confidence REAL NOT NULL DEFAULT 0.0
                )
                """
            )
            self._ensure_entity_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    entity_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(orchestration_id, agent_run_id, entity_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_identity_versions (
                    version_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    yahoo_ticker TEXT,
                    name TEXT,
                    exchange TEXT,
                    cik TEXT,
                    isin TEXT,
                    lei TEXT,
                    legal_entity_id TEXT,
                    issuer_id TEXT,
                    instrument_id TEXT,
                    parent_entity_id TEXT,
                    security_type TEXT,
                    share_class TEXT,
                    mic TEXT,
                    country_code TEXT,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    observed_at TEXT NOT NULL,
                    superseded_at TEXT,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    confidence REAL NOT NULL,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(entity_id) REFERENCES entities(entity_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_identity_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    legal_entity_id TEXT,
                    field_name TEXT NOT NULL,
                    existing_value TEXT NOT NULL,
                    candidate_value TEXT NOT NULL,
                    existing_source TEXT NOT NULL,
                    candidate_source TEXT NOT NULL,
                    existing_source_url TEXT,
                    candidate_source_url TEXT,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_identity_conflict_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    conflict_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(orchestration_id, agent_run_id, conflict_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(conflict_id) REFERENCES entity_identity_conflicts(conflict_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    url TEXT,
                    published_at TEXT,
                    content_hash TEXT,
                    mime_type TEXT,
                    raw_path TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    source_priority INTEGER NOT NULL DEFAULT 0,
                    source_authority TEXT,
                    legal_entity_id TEXT,
                    issuer_id TEXT,
                    instrument_id TEXT,
                    reporting_period_end TEXT,
                    is_audited INTEGER NOT NULL DEFAULT 0,
                    language TEXT,
                    canonical_event_key TEXT
                )
                """
            )
            self._ensure_document_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    document_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_url TEXT,
                    content_hash TEXT,
                    mime_type TEXT,
                    source_type TEXT,
                    source_priority INTEGER NOT NULL DEFAULT 0,
                    source_authority TEXT,
                    legal_entity_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    canonical_event_key TEXT,
                    PRIMARY KEY(orchestration_id, agent_run_id, document_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            self._ensure_document_observation_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_source_resolutions (
                    resolution_id TEXT PRIMARY KEY,
                    canonical_event_key TEXT NOT NULL UNIQUE,
                    ticker TEXT NOT NULL,
                    legal_entity_id TEXT,
                    preferred_document_id TEXT NOT NULL,
                    retained_document_ids_json TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(preferred_document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_source_resolution_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    resolution_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    previous_preferred_document_id TEXT,
                    preferred_document_id TEXT NOT NULL,
                    retained_document_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(orchestration_id, agent_run_id, resolution_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(resolution_id) REFERENCES document_source_resolutions(resolution_id),
                    FOREIGN KEY(preferred_document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS governance_events (
                    event_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    legal_entity_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_agent_name TEXT NOT NULL,
                    actor TEXT,
                    transaction_type TEXT,
                    shares REAL,
                    price_per_share REAL,
                    event_value REAL,
                    currency TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS governance_event_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(orchestration_id, agent_run_id, event_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES governance_events(event_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamental_facts (
                    fact_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    cik TEXT NOT NULL,
                    taxonomy TEXT NOT NULL,
                    concept TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    value REAL NOT NULL,
                    period_start TEXT,
                    period_end TEXT,
                    filed_at TEXT NOT NULL,
                    form TEXT NOT NULL,
                    fiscal_year INTEGER,
                    fiscal_period TEXT,
                    accession_number TEXT NOT NULL,
                    frame TEXT,
                    source_url TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamental_fact_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    fact_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(orchestration_id, agent_run_id, fact_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(fact_id) REFERENCES fundamental_facts(fact_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_claims (
                    claim_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    report_document_id TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    published_at TEXT NOT NULL,
                    source_agent_name TEXT NOT NULL,
                    verification_agent_name TEXT,
                    verification_summary TEXT NOT NULL DEFAULT '',
                    evidence_document_ids_json TEXT NOT NULL DEFAULT '[]',
                    source_urls_json TEXT NOT NULL DEFAULT '[]',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(report_document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_claim_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    claim_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    verification_agent_name TEXT,
                    verification_summary TEXT NOT NULL DEFAULT '',
                    evidence_document_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(orchestration_id, agent_run_id, claim_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(claim_id) REFERENCES research_claims(claim_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_relationships (
                    relationship_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    counterparty TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    dependency_pct REAL,
                    confidence REAL NOT NULL,
                    published_at TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_agent_name TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_relationship_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    relationship_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(orchestration_id, agent_run_id, relationship_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(relationship_id) REFERENCES company_relationships(relationship_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_exposures (
                    exposure_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    resource_name TEXT NOT NULL,
                    exposure_type TEXT NOT NULL,
                    dependency_pct REAL,
                    confidence REAL NOT NULL,
                    published_at TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_agent_name TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_exposure_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    exposure_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(orchestration_id, agent_run_id, exposure_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(exposure_id) REFERENCES resource_exposures(exposure_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS regulatory_contract_events (
                    event_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    authority_or_counterparty TEXT NOT NULL,
                    event_value REAL,
                    currency TEXT,
                    confidence REAL NOT NULL,
                    published_at TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_agent_name TEXT NOT NULL,
                    legal_entity_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS regulatory_contract_event_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(orchestration_id, agent_run_id, event_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES regulatory_contract_events(event_id)
                )
                """
            )
            self._ensure_regulatory_contract_event_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    direction REAL NOT NULL,
                    risk_score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    hard_veto INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    document_ids_json TEXT NOT NULL DEFAULT '[]',
                    source_urls_json TEXT NOT NULL DEFAULT '[]',
                    valid_until TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_signals (
                    signal_id TEXT PRIMARY KEY,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    forecast TEXT NOT NULL,
                    direction REAL NOT NULL,
                    risk_score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    hard_veto INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_gate_checks (
                    check_id TEXT PRIMARY KEY,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    ticker TEXT,
                    gate_name TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    message TEXT NOT NULL,
                    related_agent_names_json TEXT NOT NULL DEFAULT '[]',
                    signal_ids_json TEXT NOT NULL DEFAULT '[]',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    claim_ids_json TEXT NOT NULL DEFAULT '[]',
                    relationship_ids_json TEXT NOT NULL DEFAULT '[]',
                    exposure_ids_json TEXT NOT NULL DEFAULT '[]',
                    regulatory_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    decision_ids_json TEXT NOT NULL DEFAULT '[]',
                    evaluation_ids_json TEXT NOT NULL DEFAULT '[]',
                    activation_ids_json TEXT NOT NULL DEFAULT '[]',
                    identity_conflict_ids_json TEXT NOT NULL DEFAULT '[]',
                    governance_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_records (
                    decision_id TEXT PRIMARY KEY,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    pipeline_run_id INTEGER,
                    ticker TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    baseline_signal_id TEXT NOT NULL,
                    baseline_action TEXT NOT NULL,
                    baseline_forecast TEXT NOT NULL,
                    proposed_action TEXT NOT NULL,
                    proposed_forecast TEXT NOT NULL,
                    baseline_p_up REAL NOT NULL,
                    baseline_p_flat REAL NOT NULL,
                    baseline_p_down REAL NOT NULL,
                    p_up REAL NOT NULL,
                    p_flat REAL NOT NULL,
                    p_down REAL NOT NULL,
                    confidence REAL NOT NULL,
                    hard_veto INTEGER NOT NULL,
                    activation_state TEXT NOT NULL,
                    applied_to_prediction INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    conflicts_json TEXT NOT NULL DEFAULT '[]',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    claim_ids_json TEXT NOT NULL DEFAULT '[]',
                    regulatory_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(pipeline_run_id) REFERENCES runs(run_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    pipeline_run_id INTEGER,
                    policy_name TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    evaluated_through TEXT,
                    sample_count INTEGER NOT NULL,
                    distinct_weeks INTEGER NOT NULL,
                    baseline_accuracy_pct REAL NOT NULL,
                    candidate_accuracy_pct REAL NOT NULL,
                    lift_pct_points REAL NOT NULL,
                    lift_lower_bound_pct_points REAL NOT NULL,
                    baseline_false_positive_rate_pct REAL NOT NULL,
                    candidate_false_positive_rate_pct REAL NOT NULL,
                    coverage_pct REAL NOT NULL,
                    baseline_brier_score REAL NOT NULL,
                    candidate_brier_score REAL NOT NULL,
                    baseline_calibration_error REAL NOT NULL,
                    candidate_calibration_error REAL NOT NULL,
                    gate_passed INTEGER NOT NULL,
                    gate_results_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(pipeline_run_id) REFERENCES runs(run_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_activation_decisions (
                    activation_id TEXT PRIMARY KEY,
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    pipeline_run_id INTEGER,
                    policy_name TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    evaluated_through TEXT,
                    state TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    distinct_weeks INTEGER NOT NULL,
                    consecutive_passes INTEGER NOT NULL,
                    gate_passed INTEGER NOT NULL,
                    live_application_authorized INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(pipeline_run_id) REFERENCES runs(run_id),
                    FOREIGN KEY(evaluation_id) REFERENCES policy_evaluations(evaluation_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    snapshot_schema_version TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    target_version TEXT NOT NULL,
                    horizon_trading_days INTEGER NOT NULL,
                    benchmark_ticker TEXT NOT NULL,
                    benchmark_selection TEXT NOT NULL,
                    label_status TEXT NOT NULL,
                    target_value REAL,
                    target_observed_at TEXT,
                    baseline_model_id TEXT NOT NULL,
                    baseline_model_version TEXT NOT NULL,
                    feature_payload_json TEXT NOT NULL,
                    baseline_output_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, ticker, target_version),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )
            self._ensure_quality_gate_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_run_ticker ON prediction_snapshots(run_id, ticker)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_pipeline ON agent_runs(pipeline_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_observations_entity ON entity_observations(entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_legal_entity ON entities(legal_entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_issuer ON entities(issuer_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_instrument ON entities(instrument_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_identity_versions_time ON entity_identity_versions(entity_id, observed_at, superseded_at)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_identity_versions_active ON entity_identity_versions(entity_id) WHERE superseded_at IS NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_identity_conflicts_ticker ON entity_identity_conflicts(ticker, status, last_seen_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_ticker_published ON documents(ticker, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_source_priority ON documents(ticker, source_priority, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_canonical_event ON documents(canonical_event_key, source_priority)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_document_source_resolution_observations_run ON document_source_resolution_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_governance_events_ticker_type ON governance_events(ticker, event_type, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_governance_event_observations_run ON governance_event_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_ticker_concept ON fundamental_facts(ticker, concept, period_end)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fundamental_facts_accession ON fundamental_facts(accession_number)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fundamental_fact_observations_run ON fundamental_fact_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_claims_ticker_status ON research_claims(ticker, status, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_claim_observations_run ON research_claim_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_company_relationships_ticker_type ON company_relationships(ticker, relationship_type, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_company_relationship_observations_run ON company_relationship_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_exposures_ticker_type ON resource_exposures(ticker, exposure_type, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_exposure_observations_run ON resource_exposure_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_regulatory_contract_events_ticker_type ON regulatory_contract_events(ticker, event_type, published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_regulatory_contract_event_observations_run ON regulatory_contract_event_observations(orchestration_id, agent_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_ticker_observed ON evidence(ticker, observed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_orchestration ON evidence(orchestration_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_signals_ticker_observed ON agent_signals(ticker, observed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_signals_orchestration ON agent_signals(orchestration_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quality_gate_ticker ON quality_gate_checks(ticker, decision)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quality_gate_orchestration ON quality_gate_checks(orchestration_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_records_policy_run ON decision_records(policy_name, pipeline_run_id, ticker)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_policy_evaluations_policy_time ON policy_evaluations(policy_name, evaluated_through)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activation_policy_time ON signal_activation_decisions(policy_name, observed_at)"
            )

    def insert_run(self, metadata: RunMetadata) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs(started_at, finished_at, watchlist_size, processed_symbols, warnings_count, errors_count, excel_path) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (to_iso(metadata.started_at), to_iso(metadata.finished_at), metadata.watchlist_size, metadata.processed_symbols, metadata.warnings_count, metadata.errors_count, metadata.excel_path),
            )
            return int(cur.lastrowid)

    @staticmethod
    def _build_signal_payload(run_id: int, signals: pd.DataFrame, updated_at: str) -> list[tuple[object, ...]]:
        if signals.empty:
            return []
        return [
            (
                run_id,
                row.ticker,
                updated_at,
                row.market_cap_usd,
                row.current_price if hasattr(row, "current_price") else None,
                row.current_price_source if hasattr(row, "current_price_source") else None,
                row.scoring_version if hasattr(row, "scoring_version") else None,
                row.legacy_total_score if hasattr(row, "legacy_total_score") else None,
                row.legacy_signal if hasattr(row, "legacy_signal") else None,
                row.tech_source_used if hasattr(row, "tech_source_used") else None,
                row.rank_market_cap if hasattr(row, "rank_market_cap") else None,
                row.news_count_48h,
                row.news_score,
                row.tech_score,
                row.yahoo_score,
                row.behavioral_score,
                row.risk_score,
                row.raw_total_score,
                row.quality_adjusted_score,
                row.risk_adjusted_score,
                row.final_total_score,
                row.final_confidence,
                row.news_confidence,
                row.tech_confidence,
                row.yahoo_confidence,
                row.behavioral_confidence,
                row.data_quality_score,
                getattr(row, "module_confidence", None),
                getattr(row, "decision_confidence", None),
                getattr(row, "panic_score", None),
                getattr(row, "bull_score", None),
                getattr(row, "bear_score", None),
                getattr(row, "bull_bear_spread", None),
                getattr(row, "bullish_module_count", None),
                getattr(row, "bearish_module_count", None),
                getattr(row, "neutral_module_count", None),
                getattr(row, "downgrade_count", None),
                getattr(row, "blocked_reasons", None),
                getattr(row, "module_breakdown", None),
                row.decision_signal if hasattr(row, "decision_signal") else row.signal,
                row.forecast if hasattr(row, "forecast") else None,
                row.action if hasattr(row, "action") else row.signal,
                row.action_reasons if hasattr(row, "action_reasons") else None,
                row.signal,
                row.signal_strength,
                row.rank_in_watchlist,
                row.percentile_in_watchlist,
                row.regime,
                row.reasons,
                row.warnings,
                row.risk_flags,
                row.key_drivers,
                row.overall_summary,
                row.last_week_change_pct,
                row.last_14d_change_pct if hasattr(row, "last_14d_change_pct") else None,
                row.last_1m_change_pct,
                row.last_3m_change_pct,
            )
            for row in signals.itertuples(index=False)
        ]

    def insert_signal_history(self, run_id: int, signals: pd.DataFrame, updated_at: str) -> None:
        payload = self._build_signal_payload(run_id, signals, updated_at)
        if not payload:
            return
        with self._connect() as conn:
            conn.executemany(self.SIGNAL_HISTORY_INSERT, payload)

    def save_run(self, metadata: RunMetadata, signals: pd.DataFrame, updated_at: str) -> int:
        """Persist a run and all signals atomically.

        If signal insertion fails, the run row is rolled back as well, avoiding
        orphan runs that make History and Delta appear empty.
        """
        self.ensure_schema()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs(started_at, finished_at, watchlist_size, processed_symbols, warnings_count, errors_count, excel_path) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    to_iso(metadata.started_at),
                    to_iso(metadata.finished_at),
                    metadata.watchlist_size,
                    metadata.processed_symbols,
                    metadata.warnings_count,
                    metadata.errors_count,
                    metadata.excel_path,
                ),
            )
            run_id = int(cur.lastrowid)
            payload = self._build_signal_payload(run_id, signals, updated_at)
            if payload:
                conn.executemany(self.SIGNAL_HISTORY_INSERT, payload)
            return run_id

    def save_orchestration_report(
        self,
        report: OrchestrationReport,
        pipeline_run_id: int | None = None,
    ) -> None:
        """Persist one complete agent orchestration as an atomic audit record."""

        self.ensure_schema()
        linked_run_id = pipeline_run_id if pipeline_run_id is not None else report.pipeline_run_id
        report.pipeline_run_id = linked_run_id

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orchestration_runs(
                    orchestration_id, pipeline_run_id, started_at, finished_at,
                    status, shadow_mode, watchlist_size, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.orchestration_id,
                    linked_run_id,
                    to_iso(report.started_at),
                    to_iso(report.finished_at),
                    report.status.value,
                    int(report.shadow_mode),
                    report.watchlist_size,
                    self._json_dump(report.metadata),
                ),
            )

            for execution in report.executions:
                result = execution.result
                cursor = conn.execute(
                    """
                    INSERT INTO agent_runs(
                        orchestration_id, pipeline_run_id, agent_name, agent_version,
                        required, dependencies_json, status, started_at, finished_at,
                        elapsed_ms, input_count, output_count, warnings_json, error,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report.orchestration_id,
                        linked_run_id,
                        execution.agent_name,
                        execution.agent_version,
                        int(execution.required),
                        self._json_dump(execution.dependencies),
                        execution.status.value,
                        to_iso(execution.started_at),
                        to_iso(execution.finished_at),
                        execution.elapsed_ms,
                        execution.input_count,
                        result.output_count,
                        self._json_dump(result.warnings),
                        result.error,
                        self._json_dump(result.metadata),
                    ),
                )
                agent_run_id = int(cursor.lastrowid)

                for entity in result.entities:
                    existing_columns = (
                        "ticker",
                        "aliases_json",
                        "cik",
                        "isin",
                        "lei",
                        "confidence",
                    )
                    existing_entity_row = conn.execute(
                        f"SELECT {', '.join(existing_columns)} FROM entities WHERE entity_id = ?",
                        (entity.entity_id,),
                    ).fetchone()
                    existing_entity = (
                        dict(zip(existing_columns, existing_entity_row))
                        if existing_entity_row is not None
                        else None
                    )
                    if (
                        existing_entity is not None
                        and entity.confidence
                        >= float(existing_entity["confidence"] or 0.0)
                    ):
                        for identifier_name in ("cik", "isin", "lei"):
                            old_value = existing_entity[identifier_name]
                            new_value = getattr(entity, identifier_name)
                            if (
                                old_value is not None
                                and new_value is not None
                                and old_value != new_value
                            ):
                                raise sqlite3.IntegrityError(
                                    f"Conflicting {identifier_name.upper()} for "
                                    f"{entity.entity_id}: {old_value} != {new_value}"
                                )
                    existing_aliases: list[str] = []
                    if existing_entity and existing_entity["aliases_json"]:
                        try:
                            decoded = json.loads(existing_entity["aliases_json"])
                            if isinstance(decoded, list):
                                existing_aliases = [str(alias) for alias in decoded]
                        except (TypeError, json.JSONDecodeError):
                            existing_aliases = []
                    if (
                        existing_entity
                        and existing_entity["ticker"]
                        and existing_entity["ticker"] != entity.ticker
                    ):
                        existing_aliases.append(str(existing_entity["ticker"]))
                    aliases = list(dict.fromkeys(existing_aliases + entity.aliases))
                    conn.execute(
                        """
                        INSERT INTO entities(
                            entity_id, ticker, yahoo_ticker, name, exchange, cik, isin,
                            lei, sector, industry, aliases_json, source, first_seen_at,
                            last_seen_at, metadata_json, legal_entity_id, issuer_id,
                            instrument_id, parent_entity_id, security_type, share_class,
                            mic, country_code, valid_from, valid_to, source_url, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(entity_id) DO UPDATE SET
                            ticker = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN excluded.ticker ELSE entities.ticker END,
                            yahoo_ticker = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.yahoo_ticker, entities.yahoo_ticker)
                                ELSE entities.yahoo_ticker END,
                            name = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.name, entities.name)
                                ELSE entities.name END,
                            exchange = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.exchange, entities.exchange)
                                ELSE entities.exchange END,
                            cik = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.cik, entities.cik)
                                ELSE entities.cik END,
                            isin = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.isin, entities.isin)
                                ELSE entities.isin END,
                            lei = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.lei, entities.lei)
                                ELSE entities.lei END,
                            sector = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.sector, entities.sector)
                                ELSE entities.sector END,
                            industry = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.industry, entities.industry)
                                ELSE entities.industry END,
                            aliases_json = excluded.aliases_json,
                            source = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN excluded.source ELSE entities.source END,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN excluded.metadata_json ELSE entities.metadata_json END,
                            legal_entity_id = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.legal_entity_id, entities.legal_entity_id)
                                ELSE entities.legal_entity_id END,
                            issuer_id = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.issuer_id, entities.issuer_id)
                                ELSE entities.issuer_id END,
                            instrument_id = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.instrument_id, entities.instrument_id)
                                ELSE entities.instrument_id END,
                            parent_entity_id = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.parent_entity_id, entities.parent_entity_id)
                                ELSE entities.parent_entity_id END,
                            security_type = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.security_type, entities.security_type)
                                ELSE entities.security_type END,
                            share_class = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.share_class, entities.share_class)
                                ELSE entities.share_class END,
                            mic = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.mic, entities.mic)
                                ELSE entities.mic END,
                            country_code = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.country_code, entities.country_code)
                                ELSE entities.country_code END,
                            valid_from = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.valid_from, entities.valid_from)
                                ELSE entities.valid_from END,
                            valid_to = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.valid_to, entities.valid_to)
                                ELSE entities.valid_to END,
                            source_url = CASE
                                WHEN excluded.confidence >= entities.confidence
                                THEN COALESCE(excluded.source_url, entities.source_url)
                                ELSE entities.source_url END,
                            confidence = MAX(excluded.confidence, entities.confidence)
                        """,
                        (
                            entity.entity_id,
                            entity.ticker,
                            entity.yahoo_ticker,
                            entity.name,
                            entity.exchange,
                            entity.cik,
                            entity.isin,
                            entity.lei,
                            entity.sector,
                            entity.industry,
                            self._json_dump(aliases),
                            entity.source,
                            to_iso(report.started_at),
                            to_iso(execution.finished_at),
                            self._json_dump(entity.metadata),
                            entity.legal_entity_id,
                            entity.issuer_id,
                            entity.instrument_id,
                            entity.parent_entity_id,
                            entity.security_type,
                            entity.share_class,
                            entity.mic,
                            entity.country_code,
                            to_iso(entity.valid_from) if entity.valid_from else None,
                            to_iso(entity.valid_to) if entity.valid_to else None,
                            entity.source_url,
                            entity.confidence,
                        ),
                    )
                    observed_at = to_iso(execution.finished_at)
                    self._persist_entity_identity_version(
                        conn,
                        entity_id=entity.entity_id,
                        orchestration_id=report.orchestration_id,
                        agent_run_id=agent_run_id,
                        observed_at=observed_at,
                    )
                    conn.execute(
                        """
                        INSERT INTO entity_observations(
                            orchestration_id, agent_run_id, entity_id, observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            entity.entity_id,
                            observed_at,
                        ),
                    )

                for conflict in result.identity_conflicts:
                    conn.execute(
                        """
                        INSERT INTO entity_identity_conflicts(
                            conflict_id, ticker, entity_id, legal_entity_id,
                            field_name, existing_value, candidate_value,
                            existing_source, candidate_source, existing_source_url,
                            candidate_source_url, status, reason, first_seen_at,
                            last_seen_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(conflict_id) DO UPDATE SET
                            status = excluded.status,
                            reason = excluded.reason,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            conflict.conflict_id,
                            conflict.ticker,
                            conflict.entity_id,
                            conflict.legal_entity_id,
                            conflict.field_name,
                            conflict.existing_value,
                            conflict.candidate_value,
                            conflict.existing_source,
                            conflict.candidate_source,
                            conflict.existing_source_url,
                            conflict.candidate_source_url,
                            conflict.status.value,
                            conflict.reason,
                            to_iso(conflict.observed_at),
                            to_iso(conflict.observed_at),
                            self._json_dump(conflict.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO entity_identity_conflict_observations(
                            orchestration_id, agent_run_id, conflict_id,
                            observed_at, status, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            conflict.conflict_id,
                            to_iso(conflict.observed_at),
                            conflict.status.value,
                            self._json_dump(conflict.metadata),
                        ),
                    )

                for document in result.documents:
                    conn.execute(
                        """
                        INSERT INTO documents(
                            document_id, ticker, source, source_type, url, published_at,
                            content_hash, mime_type, raw_path, first_seen_at, last_seen_at,
                            metadata_json, source_priority, source_authority,
                            legal_entity_id, issuer_id, instrument_id,
                            reporting_period_end, is_audited, language,
                            canonical_event_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(document_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            source = excluded.source,
                            source_type = excluded.source_type,
                            url = COALESCE(excluded.url, documents.url),
                            published_at = COALESCE(excluded.published_at, documents.published_at),
                            content_hash = COALESCE(excluded.content_hash, documents.content_hash),
                            mime_type = COALESCE(excluded.mime_type, documents.mime_type),
                            raw_path = COALESCE(excluded.raw_path, documents.raw_path),
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json,
                            source_priority = excluded.source_priority,
                            source_authority = COALESCE(excluded.source_authority, documents.source_authority),
                            legal_entity_id = COALESCE(excluded.legal_entity_id, documents.legal_entity_id),
                            issuer_id = COALESCE(excluded.issuer_id, documents.issuer_id),
                            instrument_id = COALESCE(excluded.instrument_id, documents.instrument_id),
                            reporting_period_end = COALESCE(excluded.reporting_period_end, documents.reporting_period_end),
                            is_audited = excluded.is_audited,
                            language = COALESCE(excluded.language, documents.language),
                            canonical_event_key = COALESCE(excluded.canonical_event_key, documents.canonical_event_key)
                        """,
                        (
                            document.document_id,
                            document.ticker,
                            document.source,
                            document.source_type,
                            document.url,
                            to_iso(document.published_at) if document.published_at else None,
                            document.content_hash,
                            document.mime_type,
                            document.raw_path,
                            to_iso(document.observed_at),
                            to_iso(document.observed_at),
                            self._json_dump(document.metadata),
                            int(document.source_priority or 0),
                            document.source_authority,
                            document.legal_entity_id,
                            document.issuer_id,
                            document.instrument_id,
                            (
                                to_iso(document.reporting_period_end)
                                if document.reporting_period_end
                                else None
                            ),
                            int(document.is_audited),
                            document.language,
                            document.canonical_event_key,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO document_observations(
                            orchestration_id, agent_run_id, document_id, observed_at,
                            source_url, content_hash, mime_type, source_type,
                            source_priority, source_authority, legal_entity_id,
                            metadata_json, canonical_event_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            document.document_id,
                            to_iso(document.observed_at),
                            document.url,
                            document.content_hash,
                            document.mime_type,
                            document.source_type,
                            int(document.source_priority or 0),
                            document.source_authority,
                            document.legal_entity_id,
                            self._json_dump(document.metadata),
                            document.canonical_event_key,
                        ),
                    )

                for resolution in result.document_source_resolutions:
                    existing_resolution = conn.execute(
                        """
                        SELECT preferred_document_id
                        FROM document_source_resolutions
                        WHERE resolution_id = ?
                        """,
                        (resolution.resolution_id,),
                    ).fetchone()
                    previous_preferred = (
                        str(existing_resolution[0])
                        if existing_resolution is not None
                        else None
                    )
                    conn.execute(
                        """
                        INSERT INTO document_source_resolutions(
                            resolution_id, canonical_event_key, ticker,
                            legal_entity_id, preferred_document_id,
                            retained_document_ids_json, policy_version,
                            first_seen_at, last_seen_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(resolution_id) DO UPDATE SET
                            canonical_event_key = excluded.canonical_event_key,
                            ticker = excluded.ticker,
                            legal_entity_id = COALESCE(excluded.legal_entity_id, document_source_resolutions.legal_entity_id),
                            preferred_document_id = excluded.preferred_document_id,
                            retained_document_ids_json = excluded.retained_document_ids_json,
                            policy_version = excluded.policy_version,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            resolution.resolution_id,
                            resolution.canonical_event_key,
                            resolution.ticker,
                            resolution.legal_entity_id,
                            resolution.preferred_document_id,
                            self._json_dump(resolution.retained_document_ids),
                            resolution.policy_version,
                            to_iso(resolution.observed_at),
                            to_iso(resolution.observed_at),
                            self._json_dump(resolution.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO document_source_resolution_observations(
                            orchestration_id, agent_run_id, resolution_id,
                            observed_at, previous_preferred_document_id,
                            preferred_document_id, retained_document_ids_json,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            resolution.resolution_id,
                            to_iso(resolution.observed_at),
                            previous_preferred,
                            resolution.preferred_document_id,
                            self._json_dump(resolution.retained_document_ids),
                            self._json_dump(resolution.metadata),
                        ),
                    )

                for event in result.governance_events:
                    conn.execute(
                        """
                        INSERT INTO governance_events(
                            event_id, ticker, event_type, status, title,
                            published_at, document_id, source_url, legal_entity_id,
                            confidence, source_agent_name, actor, transaction_type,
                            shares, price_per_share, event_value, currency,
                            first_seen_at, last_seen_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id) DO UPDATE SET
                            status = excluded.status,
                            title = excluded.title,
                            confidence = excluded.confidence,
                            actor = COALESCE(excluded.actor, governance_events.actor),
                            transaction_type = COALESCE(excluded.transaction_type, governance_events.transaction_type),
                            shares = COALESCE(excluded.shares, governance_events.shares),
                            price_per_share = COALESCE(excluded.price_per_share, governance_events.price_per_share),
                            event_value = COALESCE(excluded.event_value, governance_events.event_value),
                            currency = COALESCE(excluded.currency, governance_events.currency),
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            event.event_id,
                            event.ticker,
                            event.event_type.value,
                            event.status.value,
                            event.title,
                            to_iso(event.published_at),
                            event.document_id,
                            event.source_url,
                            event.legal_entity_id,
                            event.confidence,
                            event.source_agent_name,
                            event.actor,
                            event.transaction_type,
                            event.shares,
                            event.price_per_share,
                            event.event_value,
                            event.currency,
                            to_iso(event.observed_at),
                            to_iso(event.observed_at),
                            self._json_dump(event.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO governance_event_observations(
                            orchestration_id, agent_run_id, event_id, observed_at,
                            status, confidence, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            event.event_id,
                            to_iso(event.observed_at),
                            event.status.value,
                            event.confidence,
                            self._json_dump(event.metadata),
                        ),
                    )

                for fact in result.fundamental_facts:
                    conn.execute(
                        """
                        INSERT INTO fundamental_facts(
                            fact_id, ticker, cik, taxonomy, concept, label,
                            description, unit, value, period_start, period_end,
                            filed_at, form, fiscal_year, fiscal_period,
                            accession_number, frame, source_url, document_id,
                            first_seen_at, last_seen_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(fact_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            cik = excluded.cik,
                            taxonomy = excluded.taxonomy,
                            concept = excluded.concept,
                            label = excluded.label,
                            description = excluded.description,
                            unit = excluded.unit,
                            value = excluded.value,
                            period_start = excluded.period_start,
                            period_end = excluded.period_end,
                            filed_at = excluded.filed_at,
                            form = excluded.form,
                            fiscal_year = excluded.fiscal_year,
                            fiscal_period = excluded.fiscal_period,
                            accession_number = excluded.accession_number,
                            frame = excluded.frame,
                            source_url = excluded.source_url,
                            document_id = excluded.document_id,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            fact.fact_id,
                            fact.ticker,
                            fact.cik,
                            fact.taxonomy,
                            fact.concept,
                            fact.label,
                            fact.description,
                            fact.unit,
                            fact.value,
                            to_iso(fact.period_start) if fact.period_start else None,
                            to_iso(fact.period_end) if fact.period_end else None,
                            to_iso(fact.filed_at),
                            fact.form,
                            fact.fiscal_year,
                            fact.fiscal_period,
                            fact.accession_number,
                            fact.frame,
                            fact.source_url,
                            fact.document_id,
                            to_iso(fact.observed_at),
                            to_iso(fact.observed_at),
                            self._json_dump(fact.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO fundamental_fact_observations(
                            orchestration_id, agent_run_id, fact_id, observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            fact.fact_id,
                            to_iso(fact.observed_at),
                        ),
                    )

                for claim in result.claims:
                    conn.execute(
                        """
                        INSERT INTO research_claims(
                            claim_id, ticker, report_document_id, claim_type,
                            statement, status, confidence, published_at,
                            source_agent_name, verification_agent_name,
                            verification_summary, evidence_document_ids_json,
                            source_urls_json, first_seen_at, last_seen_at,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(claim_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            report_document_id = excluded.report_document_id,
                            claim_type = excluded.claim_type,
                            statement = excluded.statement,
                            status = excluded.status,
                            confidence = excluded.confidence,
                            published_at = excluded.published_at,
                            source_agent_name = excluded.source_agent_name,
                            verification_agent_name = excluded.verification_agent_name,
                            verification_summary = excluded.verification_summary,
                            evidence_document_ids_json = excluded.evidence_document_ids_json,
                            source_urls_json = excluded.source_urls_json,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            claim.claim_id,
                            claim.ticker,
                            claim.report_document_id,
                            claim.claim_type,
                            claim.statement,
                            claim.status.value,
                            claim.confidence,
                            to_iso(claim.published_at),
                            claim.source_agent_name,
                            claim.verification_agent_name,
                            claim.verification_summary,
                            self._json_dump(claim.evidence_document_ids),
                            self._json_dump(claim.source_urls),
                            to_iso(claim.observed_at),
                            to_iso(claim.observed_at),
                            self._json_dump(claim.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO research_claim_observations(
                            orchestration_id, agent_run_id, claim_id, observed_at,
                            status, confidence, verification_agent_name,
                            verification_summary, evidence_document_ids_json,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            claim.claim_id,
                            to_iso(claim.observed_at),
                            claim.status.value,
                            claim.confidence,
                            claim.verification_agent_name,
                            claim.verification_summary,
                            self._json_dump(claim.evidence_document_ids),
                            self._json_dump(claim.metadata),
                        ),
                    )

                for relationship in result.company_relationships:
                    conn.execute(
                        """
                        INSERT INTO company_relationships(
                            relationship_id, ticker, counterparty,
                            relationship_type, dependency_pct, confidence,
                            published_at, document_id, source_url,
                            source_agent_name, first_seen_at, last_seen_at,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(relationship_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            counterparty = excluded.counterparty,
                            relationship_type = excluded.relationship_type,
                            dependency_pct = excluded.dependency_pct,
                            confidence = excluded.confidence,
                            published_at = excluded.published_at,
                            document_id = excluded.document_id,
                            source_url = excluded.source_url,
                            source_agent_name = excluded.source_agent_name,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            relationship.relationship_id,
                            relationship.ticker,
                            relationship.counterparty,
                            relationship.relationship_type.value,
                            relationship.dependency_pct,
                            relationship.confidence,
                            to_iso(relationship.published_at),
                            relationship.document_id,
                            relationship.source_url,
                            relationship.source_agent_name,
                            to_iso(relationship.observed_at),
                            to_iso(relationship.observed_at),
                            self._json_dump(relationship.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO company_relationship_observations(
                            orchestration_id, agent_run_id, relationship_id,
                            observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            relationship.relationship_id,
                            to_iso(relationship.observed_at),
                        ),
                    )

                for exposure in result.resource_exposures:
                    conn.execute(
                        """
                        INSERT INTO resource_exposures(
                            exposure_id, ticker, resource_name, exposure_type,
                            dependency_pct, confidence, published_at,
                            document_id, source_url, source_agent_name,
                            first_seen_at, last_seen_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(exposure_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            resource_name = excluded.resource_name,
                            exposure_type = excluded.exposure_type,
                            dependency_pct = excluded.dependency_pct,
                            confidence = excluded.confidence,
                            published_at = excluded.published_at,
                            document_id = excluded.document_id,
                            source_url = excluded.source_url,
                            source_agent_name = excluded.source_agent_name,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            exposure.exposure_id,
                            exposure.ticker,
                            exposure.resource_name,
                            exposure.exposure_type.value,
                            exposure.dependency_pct,
                            exposure.confidence,
                            to_iso(exposure.published_at),
                            exposure.document_id,
                            exposure.source_url,
                            exposure.source_agent_name,
                            to_iso(exposure.observed_at),
                            to_iso(exposure.observed_at),
                            self._json_dump(exposure.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO resource_exposure_observations(
                            orchestration_id, agent_run_id, exposure_id,
                            observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            exposure.exposure_id,
                            to_iso(exposure.observed_at),
                        ),
                    )

                for event in result.regulatory_contract_events:
                    conn.execute(
                        """
                        INSERT INTO regulatory_contract_events(
                            event_id, ticker, event_type, status, title,
                            authority_or_counterparty, event_value, currency,
                            confidence, published_at, document_id, source_url,
                            source_agent_name, legal_entity_id, first_seen_at, last_seen_at,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            event_type = excluded.event_type,
                            status = excluded.status,
                            title = excluded.title,
                            authority_or_counterparty = excluded.authority_or_counterparty,
                            event_value = excluded.event_value,
                            currency = excluded.currency,
                            confidence = excluded.confidence,
                            published_at = excluded.published_at,
                            document_id = excluded.document_id,
                            source_url = excluded.source_url,
                            source_agent_name = excluded.source_agent_name,
                            legal_entity_id = COALESCE(excluded.legal_entity_id, regulatory_contract_events.legal_entity_id),
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            event.event_id,
                            event.ticker,
                            event.event_type.value,
                            event.status.value,
                            event.title,
                            event.authority_or_counterparty,
                            event.event_value,
                            event.currency,
                            event.confidence,
                            to_iso(event.published_at),
                            event.document_id,
                            event.source_url,
                            event.source_agent_name,
                            event.legal_entity_id,
                            to_iso(event.observed_at),
                            to_iso(event.observed_at),
                            self._json_dump(event.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO regulatory_contract_event_observations(
                            orchestration_id, agent_run_id, event_id, observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            event.event_id,
                            to_iso(event.observed_at),
                        ),
                    )

                for evidence in result.evidence:
                    conn.execute(
                        """
                        INSERT INTO evidence(
                            evidence_id, orchestration_id, agent_run_id, ticker,
                            agent_name, event_type, observed_at, summary, direction,
                            risk_score, confidence, hard_veto, reasons_json,
                            document_ids_json, source_urls_json, valid_until, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evidence.evidence_id,
                            report.orchestration_id,
                            agent_run_id,
                            evidence.ticker,
                            evidence.agent_name,
                            evidence.event_type,
                            to_iso(evidence.observed_at),
                            evidence.summary,
                            evidence.direction,
                            evidence.risk_score,
                            evidence.confidence,
                            int(evidence.hard_veto),
                            self._json_dump(evidence.reasons),
                            self._json_dump(evidence.document_ids),
                            self._json_dump(evidence.source_urls),
                            to_iso(evidence.valid_until) if evidence.valid_until else None,
                            self._json_dump(evidence.metadata),
                        ),
                    )

                for signal in result.signals:
                    conn.execute(
                        """
                        INSERT INTO agent_signals(
                            signal_id, orchestration_id, agent_run_id, ticker,
                            agent_name, agent_version, event_type, observed_at, action,
                            forecast, direction, risk_score, confidence, hard_veto,
                            reasons_json, evidence_ids_json, expires_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            signal.signal_id,
                            report.orchestration_id,
                            agent_run_id,
                            signal.ticker,
                            signal.agent_name,
                            signal.agent_version,
                            signal.event_type,
                            to_iso(signal.observed_at),
                            signal.action,
                            signal.forecast,
                            signal.direction,
                            signal.risk_score,
                            signal.confidence,
                            int(signal.hard_veto),
                            self._json_dump(signal.reasons),
                            self._json_dump(signal.evidence_ids),
                            to_iso(signal.expires_at) if signal.expires_at else None,
                            self._json_dump(signal.metadata),
                        ),
                    )

                for decision in result.decisions:
                    conn.execute(
                        """
                        INSERT INTO decision_records(
                            decision_id, orchestration_id, agent_run_id,
                            pipeline_run_id, ticker, policy_name, policy_version,
                            observed_at, baseline_signal_id, baseline_action,
                            baseline_forecast, proposed_action, proposed_forecast,
                            baseline_p_up, baseline_p_flat, baseline_p_down,
                            p_up, p_flat, p_down, confidence, hard_veto,
                            activation_state, applied_to_prediction, reasons_json,
                            conflicts_json, evidence_ids_json, claim_ids_json,
                            regulatory_event_ids_json, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            decision.decision_id,
                            report.orchestration_id,
                            agent_run_id,
                            linked_run_id,
                            decision.ticker,
                            decision.policy_name,
                            decision.policy_version,
                            to_iso(decision.observed_at),
                            decision.baseline_signal_id,
                            decision.baseline_action,
                            decision.baseline_forecast,
                            decision.proposed_action,
                            decision.proposed_forecast,
                            decision.baseline_p_up,
                            decision.baseline_p_flat,
                            decision.baseline_p_down,
                            decision.p_up,
                            decision.p_flat,
                            decision.p_down,
                            decision.confidence,
                            int(decision.hard_veto),
                            decision.activation_state.value,
                            int(decision.applied_to_prediction),
                            self._json_dump(decision.reasons),
                            self._json_dump(decision.conflicts),
                            self._json_dump(decision.evidence_ids),
                            self._json_dump(decision.claim_ids),
                            self._json_dump(decision.regulatory_event_ids),
                            self._json_dump(decision.metadata),
                        ),
                    )

                for evaluation in result.policy_evaluations:
                    conn.execute(
                        """
                        INSERT INTO policy_evaluations(
                            evaluation_id, orchestration_id, agent_run_id,
                            pipeline_run_id, policy_name, policy_version,
                            observed_at, evaluated_through, sample_count,
                            distinct_weeks, baseline_accuracy_pct,
                            candidate_accuracy_pct, lift_pct_points,
                            lift_lower_bound_pct_points,
                            baseline_false_positive_rate_pct,
                            candidate_false_positive_rate_pct, coverage_pct,
                            baseline_brier_score, candidate_brier_score,
                            baseline_calibration_error,
                            candidate_calibration_error, gate_passed,
                            gate_results_json,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evaluation.evaluation_id,
                            report.orchestration_id,
                            agent_run_id,
                            linked_run_id,
                            evaluation.policy_name,
                            evaluation.policy_version,
                            to_iso(evaluation.observed_at),
                            (
                                to_iso(evaluation.evaluated_through)
                                if evaluation.evaluated_through
                                else None
                            ),
                            evaluation.sample_count,
                            evaluation.distinct_weeks,
                            evaluation.baseline_accuracy_pct,
                            evaluation.candidate_accuracy_pct,
                            evaluation.lift_pct_points,
                            evaluation.lift_lower_bound_pct_points,
                            evaluation.baseline_false_positive_rate_pct,
                            evaluation.candidate_false_positive_rate_pct,
                            evaluation.coverage_pct,
                            evaluation.baseline_brier_score,
                            evaluation.candidate_brier_score,
                            evaluation.baseline_calibration_error,
                            evaluation.candidate_calibration_error,
                            int(evaluation.gate_passed),
                            self._json_dump(evaluation.gate_results),
                            self._json_dump(evaluation.metadata),
                        ),
                    )

                for activation in result.activation_decisions:
                    conn.execute(
                        """
                        INSERT INTO signal_activation_decisions(
                            activation_id, orchestration_id, agent_run_id,
                            pipeline_run_id, policy_name, policy_version,
                            observed_at, evaluated_through, state, evaluation_id,
                            sample_count, distinct_weeks, consecutive_passes,
                            gate_passed, live_application_authorized,
                            reasons_json, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            activation.activation_id,
                            report.orchestration_id,
                            agent_run_id,
                            linked_run_id,
                            activation.policy_name,
                            activation.policy_version,
                            to_iso(activation.observed_at),
                            (
                                to_iso(activation.evaluated_through)
                                if activation.evaluated_through
                                else None
                            ),
                            activation.state.value,
                            activation.evaluation_id,
                            activation.sample_count,
                            activation.distinct_weeks,
                            activation.consecutive_passes,
                            int(activation.gate_passed),
                            0,
                            self._json_dump(activation.reasons),
                            self._json_dump(activation.metadata),
                        ),
                    )

                for check in result.quality_checks:
                    conn.execute(
                        """
                        INSERT INTO quality_gate_checks(
                            check_id, orchestration_id, agent_run_id, ticker,
                            gate_name, decision, observed_at, message,
                            related_agent_names_json, signal_ids_json,
                            evidence_ids_json, claim_ids_json,
                            relationship_ids_json, exposure_ids_json,
                            regulatory_event_ids_json, decision_ids_json,
                            evaluation_ids_json, activation_ids_json,
                            identity_conflict_ids_json, governance_event_ids_json,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            check.check_id,
                            report.orchestration_id,
                            agent_run_id,
                            check.ticker,
                            check.gate_name,
                            check.decision.value,
                            to_iso(check.observed_at),
                            check.message,
                            self._json_dump(check.related_agent_names),
                            self._json_dump(check.signal_ids),
                            self._json_dump(check.evidence_ids),
                            self._json_dump(check.claim_ids),
                            self._json_dump(check.relationship_ids),
                            self._json_dump(check.exposure_ids),
                            self._json_dump(check.regulatory_event_ids),
                            self._json_dump(check.decision_ids),
                            self._json_dump(check.evaluation_ids),
                            self._json_dump(check.activation_ids),
                            self._json_dump(check.identity_conflict_ids),
                            self._json_dump(check.governance_event_ids),
                            self._json_dump(check.metadata),
                        ),
                    )

    def save_prediction_snapshots(
        self,
        snapshots: list[dict[str, object]],
    ) -> int:
        """Persist immutable point-in-time snapshots.

        A snapshot is write-once. Re-running the same write for the same
        run/ticker/target cannot overwrite the original baseline or provenance.
        Future labels are intentionally not written by this method.
        """

        if not snapshots:
            return 0
        self.ensure_schema()
        inserted = 0
        with self._connect() as conn:
            for snapshot in snapshots:
                snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
                ticker = str(snapshot.get("ticker") or "").strip().upper()
                run_id = int(snapshot.get("run_id") or 0)
                if not snapshot_id or not ticker or run_id < 1:
                    raise ValueError("prediction snapshot identity is incomplete")
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO prediction_snapshots(
                        snapshot_id, run_id, ticker, snapshot_schema_version,
                        as_of, observed_at, target_name, target_version,
                        horizon_trading_days, benchmark_ticker,
                        benchmark_selection, label_status, target_value,
                        target_observed_at, baseline_model_id,
                        baseline_model_version, feature_payload_json,
                        baseline_output_json, provenance_json, snapshot_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        run_id,
                        ticker,
                        str(snapshot.get("snapshot_schema_version") or ""),
                        str(snapshot.get("as_of") or ""),
                        str(snapshot.get("observed_at") or ""),
                        str(snapshot.get("target_name") or ""),
                        str(snapshot.get("target_version") or ""),
                        int(snapshot.get("horizon_trading_days") or 0),
                        str(snapshot.get("benchmark_ticker") or ""),
                        str(snapshot.get("benchmark_selection") or ""),
                        str(snapshot.get("label_status") or "PENDING"),
                        snapshot.get("target_value"),
                        snapshot.get("target_observed_at"),
                        str(snapshot.get("baseline_model_id") or ""),
                        str(snapshot.get("baseline_model_version") or ""),
                        self._json_dump(snapshot.get("feature_payload") or {}),
                        self._json_dump(snapshot.get("baseline_output") or {}),
                        self._json_dump(snapshot.get("provenance") or {}),
                        str(snapshot.get("snapshot_hash") or ""),
                        str(snapshot.get("created_at") or snapshot.get("observed_at") or ""),
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
        return inserted

    def read_prediction_snapshots(
        self,
        run_id: int | None = None,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        clauses: list[str] = []
        params: list[object] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(int(run_id))
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(str(ticker).strip().upper())
        query = "SELECT * FROM prediction_snapshots"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY as_of ASC, ticker ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=tuple(params))

    def update_run_counts(self, run_id: int, warnings_count: int, errors_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET warnings_count = ?, errors_count = ? WHERE run_id = ?",
                (warnings_count, errors_count, run_id),
            )

    def read_orchestration_runs(self) -> pd.DataFrame:
        self.ensure_schema()
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM orchestration_runs ORDER BY started_at ASC",
                conn,
            )

    def read_agent_runs(self, orchestration_id: str | None = None) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM agent_runs"
        params: tuple[object, ...] = ()
        if orchestration_id is not None:
            query += " WHERE orchestration_id = ?"
            params = (orchestration_id,)
        query += " ORDER BY agent_run_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_entities(self) -> pd.DataFrame:
        self.ensure_schema()
        with self._connect() as conn:
            return pd.read_sql_query("SELECT * FROM entities ORDER BY ticker ASC", conn)

    def read_entity_identity_versions(
        self,
        entity_id: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM entity_identity_versions"
        params: tuple[object, ...] = ()
        if entity_id is not None:
            query += " WHERE entity_id = ?"
            params = (entity_id,)
        query += " ORDER BY entity_id ASC, observed_at ASC, version_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_entity_identity_conflicts(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM entity_identity_conflicts"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY last_seen_at DESC, conflict_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_entity_identity_as_of(
        self,
        as_of: datetime,
        *,
        ticker: str | None = None,
        effective_at: datetime | None = None,
    ) -> pd.DataFrame:
        """Return only identity versions known at the requested decision time."""

        self.ensure_schema()
        observed_cutoff = to_iso(as_of)
        clauses = [
            "observed_at <= ?",
            "(superseded_at IS NULL OR superseded_at > ?)",
        ]
        params: list[object] = [observed_cutoff, observed_cutoff]
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if effective_at is not None:
            effective_cutoff = to_iso(effective_at)
            clauses.extend(
                [
                    "effective_from <= ?",
                    "(effective_to IS NULL OR effective_to > ?)",
                ]
            )
            params.extend([effective_cutoff, effective_cutoff])
        query = (
            "SELECT * FROM entity_identity_versions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY entity_id ASC, observed_at DESC, version_id DESC"
        )
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=tuple(params))

    def read_documents(self, ticker: str | None = None) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM documents"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY ticker ASC, published_at DESC, document_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_document_source_resolutions(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM document_source_resolutions"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY ticker ASC, canonical_event_key ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_document_source_resolution_observations(
        self,
        resolution_id: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM document_source_resolution_observations"
        params: tuple[object, ...] = ()
        if resolution_id is not None:
            query += " WHERE resolution_id = ?"
            params = (resolution_id,)
        query += " ORDER BY observed_at ASC, agent_run_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_evidence(self, orchestration_id: str | None = None) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM evidence"
        params: tuple[object, ...] = ()
        if orchestration_id is not None:
            query += " WHERE orchestration_id = ?"
            params = (orchestration_id,)
        query += " ORDER BY observed_at ASC, evidence_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_fundamental_facts(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM fundamental_facts"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY ticker ASC, concept ASC, filed_at DESC, fact_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_governance_events(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM governance_events"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY published_at DESC, ticker ASC, event_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_research_claims(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM research_claims"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY published_at DESC, ticker ASC, claim_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_company_relationships(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM company_relationships"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY ticker ASC, published_at DESC, relationship_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_resource_exposures(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM resource_exposures"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY ticker ASC, published_at DESC, exposure_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_regulatory_contract_events(
        self,
        ticker: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM regulatory_contract_events"
        params: tuple[object, ...] = ()
        if ticker is not None:
            query += " WHERE ticker = ?"
            params = (ticker,)
        query += " ORDER BY ticker ASC, published_at DESC, event_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_agent_signals(self, orchestration_id: str | None = None) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM agent_signals"
        params: tuple[object, ...] = ()
        if orchestration_id is not None:
            query += " WHERE orchestration_id = ?"
            params = (orchestration_id,)
        query += " ORDER BY observed_at ASC, signal_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_decision_records(
        self,
        *,
        policy_name: str | None = None,
        orchestration_id: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        clauses: list[str] = []
        params: list[object] = []
        if policy_name is not None:
            clauses.append("policy_name = ?")
            params.append(policy_name)
        if orchestration_id is not None:
            clauses.append("orchestration_id = ?")
            params.append(orchestration_id)
        query = "SELECT * FROM decision_records"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at ASC, decision_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=tuple(params))

    def read_policy_evaluations(
        self,
        policy_name: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM policy_evaluations"
        params: tuple[object, ...] = ()
        if policy_name is not None:
            query += " WHERE policy_name = ?"
            params = (policy_name,)
        query += " ORDER BY observed_at ASC, evaluation_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_signal_activation_decisions(
        self,
        policy_name: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM signal_activation_decisions"
        params: tuple[object, ...] = ()
        if policy_name is not None:
            query += " WHERE policy_name = ?"
            params = (policy_name,)
        query += " ORDER BY observed_at ASC, activation_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def read_quality_gate_checks(
        self,
        orchestration_id: str | None = None,
    ) -> pd.DataFrame:
        self.ensure_schema()
        query = "SELECT * FROM quality_gate_checks"
        params: tuple[object, ...] = ()
        if orchestration_id is not None:
            query += " WHERE orchestration_id = ?"
            params = (orchestration_id,)
        query += " ORDER BY observed_at ASC, check_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_last_run_id(self) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(run_id) FROM runs").fetchone()
        return int(row[0]) if row and row[0] else None

    def get_previous_run_id(self, current_run_id: int) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT run_id FROM runs WHERE run_id < ? ORDER BY run_id DESC LIMIT 1", (current_run_id,)).fetchone()
        return int(row[0]) if row else None

    def update_run_excel_path(self, run_id: int, excel_path: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE runs SET excel_path = ? WHERE run_id = ?", (excel_path, run_id))

    def list_tickers(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT ticker FROM signal_history ORDER BY ticker ASC").fetchall()
        return [str(r[0]) for r in rows if r and r[0]]

    def read_signals_for_run(self, run_id: int) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query("SELECT * FROM signal_history WHERE run_id = ?", conn, params=(run_id,))

    def read_global_history(self) -> pd.DataFrame:
        self.ensure_schema()
        q = "SELECT r.run_id, r.finished_at, s.ticker, s.current_price, s.current_price_source, s.scoring_version, s.legacy_total_score, s.legacy_signal, s.final_total_score, s.raw_total_score, s.news_score, s.tech_score, s.yahoo_score, s.behavioral_score, s.risk_score, s.rank_in_watchlist, s.percentile_in_watchlist, s.decision_signal, s.forecast, s.action, s.action_reasons, s.signal, s.signal_strength, s.final_confidence, s.module_confidence, s.decision_confidence, s.panic_score, s.bull_score, s.bear_score, s.bull_bear_spread, s.bullish_module_count, s.bearish_module_count, s.neutral_module_count, s.downgrade_count, s.blocked_reasons, s.module_breakdown, s.tech_source_used, s.risk_flags FROM runs r JOIN signal_history s ON s.run_id = r.run_id ORDER BY r.run_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(q, conn)

    def read_ticker_history(self, ticker: str) -> pd.DataFrame:
        self.ensure_schema()
        with self._connect() as conn:
            return pd.read_sql_query("SELECT r.run_id, r.finished_at, s.* FROM signal_history s JOIN runs r ON r.run_id=s.run_id WHERE s.ticker=? ORDER BY r.run_id ASC", conn, params=(ticker,))
