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
                    news_weighted_48h REAL,
                    news_volume_48h INTEGER,
                    news_score REAL,
                    tech_score REAL,
                    yahoo_score REAL,
                    total_score REAL,
                    signal TEXT,
                    tech_status TEXT,
                    yahoo_status TEXT,
                    last_week_change_pct REAL,
                    last_1m_change_pct REAL,
                    last_3m_change_pct REAL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )

    def insert_run(self, metadata: RunMetadata) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO runs(started_at, finished_at, watchlist_size, processed_symbols, warnings_count, errors_count, excel_path)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
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
                row.news_weighted_48h,
                row.news_volume_48h,
                row.news_score,
                row.tech_score,
                row.yahoo_score,
                row.total_score,
                row.signal,
                row.tech_status,
                row.yahoo_status,
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
                    news_weighted_48h, news_volume_48h, news_score, tech_score, yahoo_score,
                    total_score, signal, tech_status, yahoo_status,
                    last_week_change_pct, last_1m_change_pct, last_3m_change_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    def get_last_run_id(self) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(run_id) FROM runs").fetchone()
        return int(row[0]) if row and row[0] else None

    def get_previous_run_id(self, current_run_id: int) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM runs WHERE run_id < ? ORDER BY run_id DESC LIMIT 1", (current_run_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def update_run_excel_path(self, run_id: int, excel_path: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE runs SET excel_path = ? WHERE run_id = ?", (excel_path, run_id))

    def get_run_excel_path(self, run_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT excel_path FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        value = row[0]
        return str(value) if value else None

    def read_signals_for_run(self, run_id: int) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query("SELECT * FROM signal_history WHERE run_id = ?", conn, params=(run_id,))

    def read_global_history(self) -> pd.DataFrame:
        query = """
            SELECT r.run_id, r.finished_at, s.ticker, s.total_score, s.news_score, s.tech_score, s.yahoo_score, s.signal
            FROM runs r
            JOIN signal_history s ON s.run_id = r.run_id
            ORDER BY r.run_id ASC
        """
        with self._connect() as conn:
            return pd.read_sql_query(query, conn)


    def list_tickers(self) -> list[str]:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT DISTINCT ticker FROM signal_history ORDER BY ticker ASC").fetchall()
            return [str(r[0]) for r in rows if r and r[0]]
        except sqlite3.Error:
            return []

    def read_ticker_history(self, ticker: str) -> pd.DataFrame:
        query = """
            SELECT
                r.run_id,
                r.finished_at,
                s.id,
                s.ticker,
                s.updated_at,
                s.market_cap_usd,
                s.rank_market_cap,
                s.news_weighted_48h,
                s.news_volume_48h,
                s.news_score,
                s.tech_score,
                s.yahoo_score,
                s.total_score,
                s.signal,
                s.tech_status,
                s.yahoo_status,
                s.last_week_change_pct,
                s.last_1m_change_pct,
                s.last_3m_change_pct
            FROM signal_history s
            JOIN runs r ON r.run_id = s.run_id
            WHERE s.ticker = ?
            ORDER BY r.run_id ASC
        """
        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=(ticker,))
