from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from market_checker_app.models import RunMetadata
from market_checker_app.utils.dates import to_iso


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def _ensure_signal_history_columns(self, conn: sqlite3.Connection) -> None:
        expected: dict[str, str] = {
            "news_count_48h": "INTEGER",
            "news_score": "REAL",
            "tech_score": "REAL",
            "yahoo_score": "REAL",
            "raw_total_score": "REAL",
            "final_total_score": "REAL",
            "final_confidence": "REAL",
            "news_confidence": "REAL",
            "tech_confidence": "REAL",
            "yahoo_confidence": "REAL",
            "data_quality_score": "REAL",
            "signal_strength": "TEXT",
            "reasons": "TEXT",
            "warnings": "TEXT",
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
                    rank_market_cap INTEGER,
                    news_count_48h INTEGER,
                    news_score REAL,
                    tech_score REAL,
                    yahoo_score REAL,
                    raw_total_score REAL,
                    final_total_score REAL,
                    final_confidence REAL,
                    news_confidence REAL,
                    tech_confidence REAL,
                    yahoo_confidence REAL,
                    data_quality_score REAL,
                    signal TEXT,
                    signal_strength TEXT,
                    reasons TEXT,
                    warnings TEXT,
                    last_week_change_pct REAL,
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

    def insert_signal_history(self, run_id: int, signals: pd.DataFrame, updated_at: str) -> None:
        if signals.empty:
            return
        payload = [
            (
                run_id,
                row.ticker,
                updated_at,
                row.market_cap_usd,
                row.rank_market_cap,
                row.news_count_48h,
                row.news_score,
                row.tech_score,
                row.yahoo_score,
                row.raw_total_score,
                row.final_total_score,
                row.final_confidence,
                row.news_confidence,
                row.tech_confidence,
                row.yahoo_confidence,
                row.data_quality_score,
                row.signal,
                row.signal_strength,
                row.reasons,
                row.warnings,
                row.last_week_change_pct,
                row.last_1m_change_pct,
                row.last_3m_change_pct,
            )
            for row in signals.itertuples(index=False)
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO signal_history(
                    run_id, ticker, updated_at, market_cap_usd, rank_market_cap,
                    news_count_48h, news_score, tech_score, yahoo_score,
                    raw_total_score, final_total_score, final_confidence,
                    news_confidence, tech_confidence, yahoo_confidence, data_quality_score,
                    signal, signal_strength, reasons, warnings,
                    last_week_change_pct, last_1m_change_pct, last_3m_change_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

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
        q = "SELECT r.run_id, r.finished_at, s.ticker, s.final_total_score, s.news_score, s.tech_score, s.yahoo_score, s.final_confidence, s.signal FROM runs r JOIN signal_history s ON s.run_id = r.run_id ORDER BY r.run_id ASC"
        with self._connect() as conn:
            return pd.read_sql_query(q, conn)

    def read_ticker_history(self, ticker: str) -> pd.DataFrame:
        q = """
        SELECT
            r.run_id,
            r.finished_at,
            s.id,
            s.ticker,
            s.updated_at,
            s.market_cap_usd,
            s.rank_market_cap,
            s.news_count_48h,
            s.news_score,
            s.tech_score,
            s.yahoo_score,
            s.raw_total_score,
            s.final_total_score,
            s.final_confidence,
            s.news_confidence,
            s.tech_confidence,
            s.yahoo_confidence,
            s.data_quality_score,
            s.signal,
            s.signal_strength,
            s.reasons,
            s.warnings,
            s.last_week_change_pct,
            s.last_1m_change_pct,
            s.last_3m_change_pct
        FROM signal_history s
        JOIN runs r ON r.run_id = s.run_id
        WHERE s.ticker = ?
        ORDER BY r.run_id ASC
        """
        with self._connect() as conn:
            return pd.read_sql_query(q, conn, params=(ticker,))
