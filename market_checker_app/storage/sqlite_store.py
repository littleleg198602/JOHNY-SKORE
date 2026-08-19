from __future__ import annotations

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
        ):
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE quality_gate_checks ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
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
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
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
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_observations (
                    orchestration_id TEXT NOT NULL,
                    agent_run_id INTEGER NOT NULL,
                    document_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(orchestration_id, agent_run_id, document_id),
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
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
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(orchestration_id) REFERENCES orchestration_runs(orchestration_id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_quality_gate_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_pipeline ON agent_runs(pipeline_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_observations_entity ON entity_observations(entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_ticker_published ON documents(ticker, published_at)"
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
                    existing_aliases_row = conn.execute(
                        "SELECT aliases_json FROM entities WHERE entity_id = ?",
                        (entity.entity_id,),
                    ).fetchone()
                    existing_aliases: list[str] = []
                    if existing_aliases_row and existing_aliases_row[0]:
                        try:
                            decoded = json.loads(existing_aliases_row[0])
                            if isinstance(decoded, list):
                                existing_aliases = [str(alias) for alias in decoded]
                        except (TypeError, json.JSONDecodeError):
                            existing_aliases = []
                    aliases = list(dict.fromkeys(existing_aliases + entity.aliases))
                    conn.execute(
                        """
                        INSERT INTO entities(
                            entity_id, ticker, yahoo_ticker, name, exchange, cik, isin,
                            lei, sector, industry, aliases_json, source, first_seen_at,
                            last_seen_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(entity_id) DO UPDATE SET
                            ticker = excluded.ticker,
                            yahoo_ticker = COALESCE(excluded.yahoo_ticker, entities.yahoo_ticker),
                            name = COALESCE(excluded.name, entities.name),
                            exchange = COALESCE(excluded.exchange, entities.exchange),
                            cik = COALESCE(excluded.cik, entities.cik),
                            isin = COALESCE(excluded.isin, entities.isin),
                            lei = COALESCE(excluded.lei, entities.lei),
                            sector = COALESCE(excluded.sector, entities.sector),
                            industry = COALESCE(excluded.industry, entities.industry),
                            aliases_json = excluded.aliases_json,
                            source = excluded.source,
                            last_seen_at = excluded.last_seen_at,
                            metadata_json = excluded.metadata_json
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
                        ),
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
                            to_iso(execution.finished_at),
                        ),
                    )

                for document in result.documents:
                    conn.execute(
                        """
                        INSERT INTO documents(
                            document_id, ticker, source, source_type, url, published_at,
                            content_hash, mime_type, raw_path, first_seen_at, last_seen_at,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            metadata_json = excluded.metadata_json
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
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO document_observations(
                            orchestration_id, agent_run_id, document_id, observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            report.orchestration_id,
                            agent_run_id,
                            document.document_id,
                            to_iso(document.observed_at),
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
                            source_agent_name, first_seen_at, last_seen_at,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

                for check in result.quality_checks:
                    conn.execute(
                        """
                        INSERT INTO quality_gate_checks(
                            check_id, orchestration_id, agent_run_id, ticker,
                            gate_name, decision, observed_at, message,
                            related_agent_names_json, signal_ids_json,
                            evidence_ids_json, claim_ids_json,
                            relationship_ids_json, exposure_ids_json,
                            regulatory_event_ids_json, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            self._json_dump(check.metadata),
                        ),
                    )

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
