from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
import sqlite3
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class YahooOhlcCacheLookup:
    state: str
    frame: pd.DataFrame | None
    fetched_at: datetime | None
    error: str | None

    @property
    def usable(self) -> bool:
        return self.state in {"fresh", "stale"} and self.frame is not None


class YahooOhlcCacheStore:
    """Persistent daily OHLC cache; stale data is explicit, never fabricated."""

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
                    updated_at TEXT NOT NULL
                )
                """
            )

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
    def _parse(cls, value: str) -> datetime:
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
                INSERT INTO yahoo_ohlc_cache(ticker, provider, frame_json, fetched_at, expires_at, last_error, updated_at)
                VALUES (?, 'yfinance', ?, ?, ?, NULL, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                  provider=excluded.provider, frame_json=excluded.frame_json,
                  fetched_at=excluded.fetched_at, expires_at=excluded.expires_at,
                  last_error=NULL, updated_at=excluded.updated_at
                """,
                (self._ticker(ticker), encoded, self._iso(fetched), self._iso(fetched + self.success_ttl), self._iso(self._now())),
            )

    def note_failure(self, ticker: str, error: str, *, fetched_at: datetime | None = None) -> None:
        fetched = self._utc(fetched_at or self._now())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE yahoo_ohlc_cache SET last_error=?, expires_at=?, updated_at=?
                WHERE ticker=?
                """,
                (str(error)[:2000], self._iso(fetched - timedelta(microseconds=1)), self._iso(fetched), self._ticker(ticker)),
            )

    def get(self, ticker: str, *, now: datetime | None = None) -> YahooOhlcCacheLookup:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM yahoo_ohlc_cache WHERE ticker=?", (self._ticker(ticker),)).fetchone()
        if row is None:
            return YahooOhlcCacheLookup("missing", None, None, None)
        try:
            frame = self._validate(pd.read_json(StringIO(row["frame_json"]), orient="split"))
            fetched = self._parse(row["fetched_at"])
            expires = self._parse(row["expires_at"])
        except (TypeError, ValueError, KeyError):
            return YahooOhlcCacheLookup("corrupt", None, None, None)
        current = self._utc(now or self._now())
        return YahooOhlcCacheLookup("fresh" if expires > current else "stale", frame, fetched, row["last_error"])

    def coverage(self, tickers: list[str]) -> dict[str, int]:
        result = {"fresh": 0, "stale": 0, "missing": 0, "corrupt": 0}
        for ticker in dict.fromkeys(self._ticker(t) for t in tickers):
            result[self.get(ticker).state] += 1
        return result
