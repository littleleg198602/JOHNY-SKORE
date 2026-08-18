from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.utils.dates import to_iso


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
        return sqlite3.connect(self.db_path)

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
