from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import sqlite3
from pathlib import Path

import pandas as pd

from market_checker_app.services.price_methodology import (
    PRICE_METHOD_VERSION,
    YAHOO_ADJUSTMENT,
    YAHOO_INTERVAL,
    YAHOO_PROVIDER,
    normalize_split_adjusted_price_frame,
)


_EMPTY_FRAME_JSON = '{"columns":[],"index":[],"data":[]}'
_LEGACY_ADJUSTMENT = "raw_unadjusted_legacy"
_LEGACY_METHOD_VERSION = "legacy_raw_close_v1"


@dataclass(frozen=True)
class YahooOhlcCacheLookup:
    state: str
    frame: pd.DataFrame | None
    fetched_at: datetime | None
    error: str | None
    retry_after: datetime | None = None
    attempt_count: int = 0
    provider: str = YAHOO_PROVIDER
    interval: str = YAHOO_INTERVAL
    adjustment: str = YAHOO_ADJUSTMENT
    methodology_version: str = PRICE_METHOD_VERSION
    first_session: date | None = None
    last_session: date | None = None
    missing_sessions: tuple[date, ...] = ()

    @property
    def usable(self) -> bool:
        return self.state in {"fresh", "stale"} and self.frame is not None

    @property
    def range_complete(self) -> bool:
        return not self.missing_sessions

    def can_retry(self, now: datetime) -> bool:
        current = (
            now.replace(tzinfo=timezone.utc)
            if now.tzinfo is None
            else now.astimezone(timezone.utc)
        )
        return self.retry_after is None or current >= self.retry_after


class YahooOhlcCacheStore:
    """Versioned persistent OHLC cache with range and retry semantics.

    Cache identity is ``ticker + provider + interval + adjustment + methodology``.
    The default variant is the legacy/raw pipeline cache for backwards
    compatibility. Consumers that require the split-adjusted target methodology
    must request that variant explicitly; it refuses frames that omit corporate
    action columns so raw bulk data cannot silently contaminate target labels.

    Successful refreshes merge by timestamp so a short update cannot erase
    older history required by a pending label.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        success_ttl: timedelta = timedelta(hours=30),
        failure_retry_ttl: timedelta = timedelta(minutes=30),
        provider: str = YAHOO_PROVIDER,
        interval: str = YAHOO_INTERVAL,
        adjustment: str = _LEGACY_ADJUSTMENT,
        methodology_version: str = _LEGACY_METHOD_VERSION,
        now_provider=None,
    ) -> None:
        self.db_path = Path(db_path)
        self.success_ttl = success_ttl
        self.failure_retry_ttl = failure_retry_ttl
        self.provider = str(provider).strip().lower() or YAHOO_PROVIDER
        self.interval = str(interval).strip().lower() or YAHOO_INTERVAL
        self.adjustment = str(adjustment).strip().lower() or _LEGACY_ADJUSTMENT
        self.methodology_version = (
            str(methodology_version).strip() or _LEGACY_METHOD_VERSION
        )
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        name: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            # Keep the original table readable for existing local databases.
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
            self._ensure_column(conn, "yahoo_ohlc_cache", "retry_after", "TEXT")
            self._ensure_column(
                conn,
                "yahoo_ohlc_cache",
                "attempt_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS yahoo_ohlc_cache_v2 (
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    adjustment TEXT NOT NULL,
                    methodology_version TEXT NOT NULL,
                    frame_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_error TEXT,
                    retry_after TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(
                        ticker, provider, interval, adjustment, methodology_version
                    )
                )
                """
            )
            # Preserve legacy data under an explicitly incompatible identity.
            conn.execute(
                """
                INSERT OR IGNORE INTO yahoo_ohlc_cache_v2(
                    ticker, provider, interval, adjustment, methodology_version,
                    frame_json, fetched_at, expires_at, last_error, retry_after,
                    attempt_count, updated_at
                )
                SELECT
                    ticker,
                    LOWER(COALESCE(provider, 'yfinance')),
                    '1d',
                    ?,
                    ?,
                    frame_json,
                    fetched_at,
                    expires_at,
                    last_error,
                    retry_after,
                    COALESCE(attempt_count, 0),
                    updated_at
                FROM yahoo_ohlc_cache
                """,
                (_LEGACY_ADJUSTMENT, _LEGACY_METHOD_VERSION),
            )

    @staticmethod
    def _ticker(value: str) -> str:
        result = str(value or "").strip().upper()
        if not result:
            raise ValueError("ticker must not be empty")
        return result

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

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

    def _normalise_for_methodology(self, frame: pd.DataFrame) -> pd.DataFrame:
        if (
            self.provider == YAHOO_PROVIDER
            and self.interval == YAHOO_INTERVAL
            and self.adjustment == YAHOO_ADJUSTMENT
            and self.methodology_version == PRICE_METHOD_VERSION
        ):
            if "Stock Splits" not in frame.columns:
                raise ValueError(
                    "split-adjusted target cache requires Stock Splits corporate-action data"
                )
            return normalize_split_adjusted_price_frame(frame)
        return self._validate(frame)

    @staticmethod
    def _validate(frame: pd.DataFrame) -> pd.DataFrame:
        if (
            not isinstance(frame, pd.DataFrame)
            or frame.empty
            or "Close" not in frame.columns
        ):
            raise ValueError("OHLC cache requires a non-empty frame with Close")
        result = frame.copy().sort_index(kind="stable")
        close = pd.to_numeric(result["Close"], errors="coerce")
        finite = close.map(
            lambda value: pd.notna(value)
            and float("-inf") < float(value) < float("inf")
            and float(value) > 0.0
        )
        result = result.loc[finite]
        if result.empty:
            raise ValueError("OHLC cache requires at least one positive numeric Close")
        return result

    @staticmethod
    def _session_date(value: object) -> date | None:
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError):
            return None
        if pd.isna(parsed):
            return None
        return parsed.date()

    @classmethod
    def _session_set(cls, frame: pd.DataFrame) -> set[date]:
        return {
            session
            for session in (cls._session_date(value) for value in frame.index)
            if session is not None
        }

    @classmethod
    def _required_sessions(
        cls,
        values: Iterable[date | datetime | str] | None,
    ) -> tuple[date, ...]:
        if values is None:
            return ()
        sessions = {
            session
            for session in (cls._session_date(value) for value in values)
            if session is not None
        }
        return tuple(sorted(sessions))

    def _identity(self, ticker: str) -> tuple[str, str, str, str, str]:
        return (
            self._ticker(ticker),
            self.provider,
            self.interval,
            self.adjustment,
            self.methodology_version,
        )

    def _row(self, conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM yahoo_ohlc_cache_v2
            WHERE ticker=? AND provider=? AND interval=?
              AND adjustment=? AND methodology_version=?
            """,
            self._identity(ticker),
        ).fetchone()

    def _decode_frame(self, row: sqlite3.Row) -> pd.DataFrame | None:
        if row["frame_json"] == _EMPTY_FRAME_JSON:
            return None
        return self._validate(
            pd.read_json(StringIO(row["frame_json"]), orient="split")
        )

    def upsert_success(
        self,
        ticker: str,
        frame: pd.DataFrame,
        *,
        fetched_at: datetime | None = None,
    ) -> pd.DataFrame:
        checked = self._normalise_for_methodology(frame)
        fetched = self._utc(fetched_at or self._now())
        with self._connect() as conn:
            existing = self._row(conn, ticker)
            if existing is not None:
                try:
                    previous = self._decode_frame(existing)
                except (TypeError, ValueError, KeyError):
                    previous = None
                if previous is not None:
                    checked = pd.concat([previous, checked], axis=0)
                    checked = checked[~checked.index.duplicated(keep="last")]
                    checked = checked.sort_index(kind="stable")
            encoded = checked.to_json(orient="split", date_format="iso")
            conn.execute(
                """
                INSERT INTO yahoo_ohlc_cache_v2(
                    ticker, provider, interval, adjustment, methodology_version,
                    frame_json, fetched_at, expires_at, last_error, retry_after,
                    attempt_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?)
                ON CONFLICT(
                    ticker, provider, interval, adjustment, methodology_version
                ) DO UPDATE SET
                    frame_json=excluded.frame_json,
                    fetched_at=excluded.fetched_at,
                    expires_at=excluded.expires_at,
                    last_error=NULL,
                    retry_after=NULL,
                    attempt_count=0,
                    updated_at=excluded.updated_at
                """,
                (
                    *self._identity(ticker),
                    encoded,
                    self._iso(fetched),
                    self._iso(fetched + self.success_ttl),
                    self._iso(self._now()),
                ),
            )
        return checked

    def note_failure(
        self,
        ticker: str,
        error: str,
        *,
        fetched_at: datetime | None = None,
    ) -> None:
        attempted = self._utc(fetched_at or self._now())
        retry_after = attempted + self.failure_retry_ttl
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO yahoo_ohlc_cache_v2(
                    ticker, provider, interval, adjustment, methodology_version,
                    frame_json, fetched_at, expires_at, last_error, retry_after,
                    attempt_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(
                    ticker, provider, interval, adjustment, methodology_version
                ) DO UPDATE SET
                    expires_at=excluded.expires_at,
                    last_error=excluded.last_error,
                    retry_after=excluded.retry_after,
                    attempt_count=COALESCE(yahoo_ohlc_cache_v2.attempt_count, 0) + 1,
                    updated_at=excluded.updated_at
                """,
                (
                    *self._identity(ticker),
                    _EMPTY_FRAME_JSON,
                    self._iso(attempted),
                    self._iso(attempted - timedelta(microseconds=1)),
                    str(error)[:2000],
                    self._iso(retry_after),
                    self._iso(attempted),
                ),
            )

    def get(
        self,
        ticker: str,
        *,
        now: datetime | None = None,
        required_sessions: Iterable[date | datetime | str] | None = None,
    ) -> YahooOhlcCacheLookup:
        current = self._utc(now or self._now())
        required = self._required_sessions(required_sessions)
        with self._connect() as conn:
            row = self._row(conn, ticker)
        if row is None:
            return YahooOhlcCacheLookup(
                "missing",
                None,
                None,
                None,
                provider=self.provider,
                interval=self.interval,
                adjustment=self.adjustment,
                methodology_version=self.methodology_version,
                missing_sessions=required,
            )

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
                self.provider,
                self.interval,
                self.adjustment,
                self.methodology_version,
                missing_sessions=required,
            )
        try:
            frame = self._decode_frame(row)
            expires = self._parse(row["expires_at"])
            if frame is None or fetched is None or expires is None:
                raise ValueError("missing cache payload or timestamps")
        except (TypeError, ValueError, KeyError):
            return YahooOhlcCacheLookup(
                "corrupt",
                None,
                fetched,
                row["last_error"],
                retry_after,
                attempts,
                self.provider,
                self.interval,
                self.adjustment,
                self.methodology_version,
                missing_sessions=required,
            )

        sessions = self._session_set(frame)
        ordered_sessions = tuple(sorted(sessions))
        missing = tuple(day for day in required if day not in sessions)
        state = "fresh" if expires > current else "stale"
        if missing:
            state = "incomplete"
        return YahooOhlcCacheLookup(
            state,
            frame,
            fetched,
            row["last_error"],
            retry_after,
            attempts,
            self.provider,
            self.interval,
            self.adjustment,
            self.methodology_version,
            ordered_sessions[0] if ordered_sessions else None,
            ordered_sessions[-1] if ordered_sessions else None,
            missing,
        )

    def coverage(self, tickers: list[str]) -> dict[str, int]:
        result = {
            "fresh": 0,
            "stale": 0,
            "incomplete": 0,
            "failed": 0,
            "missing": 0,
            "corrupt": 0,
        }
        for ticker in dict.fromkeys(self._ticker(t) for t in tickers):
            result[self.get(ticker).state] += 1
        return result
