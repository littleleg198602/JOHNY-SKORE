from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
import sqlite3
from pathlib import Path

import pandas as pd


_EMPTY_FRAME_JSON = '{"columns":[],"index":[],"data":[]}'


@dataclass(frozen=True)
class YahooOhlcCacheLookup:
    state: str
    frame: pd.DataFrame | None
    fetched_at: datetime | None
    error: str | None
    retry_after: datetime | None = None
    attempt_count: int = 0

    @property
    def usable(self) -> bool:
        return self.state in {"fresh", "stale"} and self.frame is not None

    def can_retry(self, now: datetime) -> bool:
        current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
        return self.retry_after is None or current >= self.retry_after


class YahooOhlcCacheStore:
    """Persistent daily OHLC cache with explicit retry checkpoints.

    Successful frames remain reusable after a temporary provider failure.  A
    failure without a previous frame is also persisted, preventing restarts
    from immediately repeating the same failed request.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        success_ttl: timedelta = timedelta(hours=30),
        failure_retry_ttl: timedelta = timedelta(minutes=30),
        now_provider=None,
    ) -> None:
        self.db_path = Path(db_path)
        self.success_ttl = success_ttl
        self.failure_retry_ttl = failure_retry_ttl
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(yahoo_ohlc_cache)").fetchall()
        }
        if name not in columns:
            conn.execute(f"ALTER TABLE yahoo_ohlc_cache ADD COLUMN {name} {definition}")

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS yahoo_ohlc_cache (
                    ticker TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    frame_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_error TEXT,
                    retry_after TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Existing local histories pre-date the retry checkpoint fields.
            self._ensure_column(conn, "retry_after", "TEXT")
            self._ensure_column(conn, "attempt_count", "INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _ticker(value: str) -> str:
        result = str(value or "").strip().upper()
        if not result:
            raise ValueError("ticker must not be empty")
        return result

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @classmethod
    def _iso(cls, value: datetime) -> str:
        return cls._utc(value).isoformat().replace("+00:00", "Z")

    @classmethod
    def _parse(cls, value: str | None) -> datetime | None:
        if not value:
            return None
        return cls._utc(datetime.fromisoformat(value.replace("Z", "+00:00")))

    def _now(self) -> datetime:
        return self._utc(self._now_provider())

    @staticmethod
    def _validate(frame: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame) or frame.empty or "Close" not in frame.columns:
            raise ValueError("OHLC cache requires a non-empty frame with Close")
        result = frame.copy()
        close = pd.to_numeric(result["Close"], errors="coerce")
        result = result.loc[close.notna() & (close > 0)]
        if result.empty:
            raise ValueError("OHLC cache requires at least one positive numeric Close")
        return result

    def upsert_success(self, ticker: str, frame: pd.DataFrame, *, fetched_at: datetime | None = None) -> None:
        checked = self._validate(frame)
        fetched = self._utc(fetched_at or self._now())
        encoded = checked.to_json(orient="split", date_format="iso")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO yahoo_ohlc_cache(
                    ticker, provider, frame_json, fetched_at, expires_at, last_error,
                    retry_after, attempt_count, updated_at
                )
                VALUES (?, 'yfinance', ?, ?, ?, NULL, NULL, 0, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                  provider=excluded.provider, frame_json=excluded.frame_json,
                  fetched_at=excluded.fetched_at, expires_at=excluded.expires_at,
                  last_error=NULL, retry_after=NULL, attempt_count=0,
                  updated_at=excluded.updated_at
                """,
                (
                    self._ticker(ticker),
                    encoded,
                    self._iso(fetched),
                    self._iso(fetched + self.success_ttl),
                    self._iso(self._now()),
                ),
            )

    def note_failure(self, ticker: str, error: str, *, fetched_at: datetime | None = None) -> None:
        attempted = self._utc(fetched_at or self._now())
        retry_after = attempted + self.failure_retry_ttl
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO yahoo_ohlc_cache(
                    ticker, provider, frame_json, fetched_at, expires_at, last_error,
                    retry_after, attempt_count, updated_at
                )
                VALUES (?, 'yfinance', ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                  expires_at=excluded.expires_at, last_error=excluded.last_error,
                  retry_after=excluded.retry_after,
                  attempt_count=COALESCE(yahoo_ohlc_cache.attempt_count, 0) + 1,
                  updated_at=excluded.updated_at
                """,
                (
                    self._ticker(ticker),
                    _EMPTY_FRAME_JSON,
                    self._iso(attempted),
                    self._iso(attempted - timedelta(microseconds=1)),
                    str(error)[:2000],
                    self._iso(retry_after),
                    self._iso(attempted),
                ),
            )

    def get(self, ticker: str, *, now: datetime | None = None) -> YahooOhlcCacheLookup:
        current = self._utc(now or self._now())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM yahoo_ohlc_cache WHERE ticker=?",
                (self._ticker(ticker),),
            ).fetchone()
        if row is None:
            return YahooOhlcCacheLookup("missing", None, None, None)

        fetched = self._parse(row["fetched_at"])
        retry_after = self._parse(row["retry_after"])
        attempts = int(row["attempt_count"] or 0)
        if row["frame_json"] == _EMPTY_FRAME_JSON:
            return YahooOhlcCacheLookup(
                "failed",
                None,
                fetched,
                row["last_error"],
                retry_after,
                attempts,
            )
        try:
            frame = self._validate(pd.read_json(StringIO(row["frame_json"]), orient="split"))
            expires = self._parse(row["expires_at"])
            if fetched is None or expires is None:
                raise ValueError("missing cache timestamps")
        except (TypeError, ValueError, KeyError):
            return YahooOhlcCacheLookup(
                "corrupt",
                None,
                fetched,
                row["last_error"],
                retry_after,
                attempts,
            )
        return YahooOhlcCacheLookup(
            "fresh" if expires > current else "stale",
            frame,
            fetched,
            row["last_error"],
            retry_after,
            attempts,
        )

    def coverage(self, tickers: list[str]) -> dict[str, int]:
        result = {"fresh": 0, "stale": 0, "failed": 0, "missing": 0, "corrupt": 0}
        for ticker in dict.fromkeys(self._ticker(t) for t in tickers):
            result[self.get(ticker).state] += 1
        return result
