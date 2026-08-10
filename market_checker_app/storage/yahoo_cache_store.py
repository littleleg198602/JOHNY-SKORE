from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal


YahooCacheStatus = Literal["fresh", "failed", "unsupported"]
YahooCacheState = Literal[
    "missing",
    "fresh",
    "stale",
    "failed",
    "unsupported",
    "corrupt",
]

_VALID_STATUSES = frozenset({"fresh", "failed", "unsupported"})


@dataclass(frozen=True)
class YahooCacheRecord:
    ticker: str
    yahoo_ticker: str
    status: YahooCacheStatus
    data: dict[str, Any] | None
    fetched_at: datetime
    expires_at: datetime
    error: str | None
    updated_at: datetime


@dataclass(frozen=True)
class YahooCacheLookup:
    state: YahooCacheState
    record: YahooCacheRecord | None

    @property
    def usable(self) -> bool:
        """Whether this lookup contains Yahoo data usable by the scorer.

        Stale data remains usable when a caller explicitly chooses a
        stale-while-revalidate strategy.  The distinct ``state`` prevents it
        from being mistaken for fresh data.
        """

        return self.state in {"fresh", "stale"} and self.record is not None


@dataclass(frozen=True)
class YahooCacheCoverage:
    total: int
    fresh: int
    stale: int
    failed: int
    unsupported: int
    missing: int
    corrupt: int

    @property
    def usable(self) -> int:
        return self.fresh + self.stale

    @property
    def fresh_ratio(self) -> float:
        return self.fresh / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, int | float]:
        return {
            "total": self.total,
            "fresh": self.fresh,
            "stale": self.stale,
            "failed": self.failed,
            "unsupported": self.unsupported,
            "missing": self.missing,
            "corrupt": self.corrupt,
            "usable": self.usable,
            "fresh_ratio": self.fresh_ratio,
        }


class YahooCacheStore:
    """Persistent SQLite cache for Yahoo metadata.

    This store intentionally has no dependency on run-history persistence.
    Callers may therefore keep Yahoo metadata between runs even when saving
    signal history is disabled.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        success_ttl: timedelta = timedelta(hours=24),
        failure_retry_ttl: timedelta = timedelta(minutes=30),
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if success_ttl < timedelta(0):
            raise ValueError("success_ttl must not be negative")
        if failure_retry_ttl < timedelta(0):
            raise ValueError("failure_retry_ttl must not be negative")
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
                CREATE TABLE IF NOT EXISTS yahoo_metadata_cache (
                    ticker TEXT PRIMARY KEY,
                    yahoo_ticker TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('fresh', 'failed', 'unsupported')),
                    data_json TEXT,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_yahoo_metadata_cache_status_expiry
                ON yahoo_metadata_cache(status, expires_at)
                """
            )

    def upsert_success(
        self,
        ticker: str,
        data: Mapping[str, Any],
        *,
        yahoo_ticker: str | None = None,
        fetched_at: datetime | None = None,
        ttl: timedelta | None = None,
    ) -> YahooCacheRecord:
        fetched = self._as_utc(fetched_at or self._now())
        effective_ttl = self.success_ttl if ttl is None else ttl
        if effective_ttl < timedelta(0):
            raise ValueError("ttl must not be negative")
        return self._upsert(
            ticker=ticker,
            yahoo_ticker=yahoo_ticker,
            status="fresh",
            data=data,
            fetched_at=fetched,
            expires_at=fetched + effective_ttl,
            error=None,
        )

    def upsert_failure(
        self,
        ticker: str,
        error: str,
        *,
        yahoo_ticker: str | None = None,
        fetched_at: datetime | None = None,
        retry_ttl: timedelta | None = None,
    ) -> YahooCacheRecord:
        fetched = self._as_utc(fetched_at or self._now())
        effective_ttl = self.failure_retry_ttl if retry_ttl is None else retry_ttl
        if effective_ttl < timedelta(0):
            raise ValueError("retry_ttl must not be negative")
        return self._upsert(
            ticker=ticker,
            yahoo_ticker=yahoo_ticker,
            status="failed",
            data=None,
            fetched_at=fetched,
            expires_at=fetched + effective_ttl,
            error=error or "Yahoo request failed",
        )

    def upsert_unsupported(
        self,
        ticker: str,
        error: str = "Ticker is not supported by Yahoo",
        *,
        yahoo_ticker: str | None = None,
        fetched_at: datetime | None = None,
    ) -> YahooCacheRecord:
        fetched = self._as_utc(fetched_at or self._now())
        return self._upsert(
            ticker=ticker,
            yahoo_ticker=yahoo_ticker,
            status="unsupported",
            data=None,
            fetched_at=fetched,
            expires_at=fetched,
            error=error,
        )

    def _upsert(
        self,
        *,
        ticker: str,
        yahoo_ticker: str | None,
        status: YahooCacheStatus,
        data: Mapping[str, Any] | None,
        fetched_at: datetime,
        expires_at: datetime,
        error: str | None,
    ) -> YahooCacheRecord:
        normalized_ticker = self._normalize_ticker(ticker)
        normalized_yahoo_ticker = (yahoo_ticker or normalized_ticker).strip()
        if not normalized_yahoo_ticker:
            raise ValueError("yahoo_ticker must not be empty")
        if status not in _VALID_STATUSES:
            raise ValueError(f"Unsupported Yahoo cache status: {status}")
        if status == "fresh" and data is None:
            raise ValueError("Fresh Yahoo cache entries require data")

        data_json = self._encode_data(data)
        fetched = self._as_utc(fetched_at)
        expires = self._as_utc(expires_at)
        updated = self._now()

        # One INSERT ... ON CONFLICT statement inside a transaction makes the
        # replacement atomic for this ticker.  JSON serialization happens
        # before opening the transaction, so invalid data cannot destroy an
        # existing valid record.
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO yahoo_metadata_cache(
                    ticker, yahoo_ticker, status, data_json,
                    fetched_at, expires_at, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    yahoo_ticker = excluded.yahoo_ticker,
                    status = excluded.status,
                    data_json = CASE
                        WHEN excluded.status = 'failed'
                             AND yahoo_metadata_cache.data_json IS NOT NULL
                        THEN yahoo_metadata_cache.data_json
                        ELSE excluded.data_json
                    END,
                    fetched_at = CASE
                        WHEN excluded.status = 'failed'
                             AND yahoo_metadata_cache.data_json IS NOT NULL
                        THEN yahoo_metadata_cache.fetched_at
                        ELSE excluded.fetched_at
                    END,
                    expires_at = excluded.expires_at,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_ticker,
                    normalized_yahoo_ticker,
                    status,
                    data_json,
                    self._to_iso(fetched),
                    self._to_iso(expires),
                    error,
                    self._to_iso(updated),
                ),
            )

        lookup = self.get(normalized_ticker, now=updated)
        if lookup.record is None:  # pragma: no cover - defensive DB invariant
            raise RuntimeError(f"Yahoo cache upsert failed for {normalized_ticker}")
        return lookup.record

    def get(self, ticker: str, *, now: datetime | None = None) -> YahooCacheLookup:
        normalized_ticker = self._normalize_ticker(ticker)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM yahoo_metadata_cache WHERE ticker = ?",
                (normalized_ticker,),
            ).fetchone()
        return self._lookup_from_row(row, self._as_utc(now or self._now()))

    def get_fresh(self, ticker: str, *, now: datetime | None = None) -> YahooCacheRecord | None:
        lookup = self.get(ticker, now=now)
        return lookup.record if lookup.state == "fresh" else None

    def get_stale(self, ticker: str, *, now: datetime | None = None) -> YahooCacheRecord | None:
        lookup = self.get(ticker, now=now)
        return lookup.record if lookup.state == "stale" else None

    def get_usable(
        self,
        ticker: str,
        *,
        allow_stale: bool = False,
        now: datetime | None = None,
    ) -> YahooCacheRecord | None:
        lookup = self.get(ticker, now=now)
        if lookup.state == "fresh" or (allow_stale and lookup.state == "stale"):
            return lookup.record
        return None

    def coverage(self, watchlist: Iterable[str], *, now: datetime | None = None) -> YahooCacheCoverage:
        tickers = self._unique_tickers(watchlist)
        states = {state: 0 for state in ("fresh", "stale", "failed", "unsupported", "missing", "corrupt")}
        current = self._as_utc(now or self._now())
        with self._connect() as conn:
            for ticker in tickers:
                row = conn.execute(
                    "SELECT * FROM yahoo_metadata_cache WHERE ticker = ?",
                    (ticker,),
                ).fetchone()
                states[self._lookup_from_row(row, current).state] += 1
        return YahooCacheCoverage(total=len(tickers), **states)

    def list_tickers_needing_refresh(
        self,
        watchlist: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Return unique tickers that may be requested from Yahoo now.

        Missing, stale, and corrupt entries are refreshable immediately.
        Failed entries become refreshable only after their retry TTL.  An
        unsupported ticker is terminal and is deliberately excluded.
        """

        tickers = self._unique_tickers(watchlist)
        current = self._as_utc(now or self._now())
        refresh: list[str] = []
        with self._connect() as conn:
            for ticker in tickers:
                row = conn.execute(
                    "SELECT * FROM yahoo_metadata_cache WHERE ticker = ?",
                    (ticker,),
                ).fetchone()
                lookup = self._lookup_from_row(row, current)
                if lookup.state in {"missing", "corrupt"}:
                    refresh.append(ticker)
                elif lookup.state == "stale" and lookup.record is not None:
                    # A stale record whose last refresh failed keeps serving
                    # its old data, but observes the failure retry deadline.
                    if lookup.record.status != "failed" or lookup.record.expires_at <= current:
                        refresh.append(ticker)
                elif lookup.state == "failed" and lookup.record is not None:
                    if lookup.record.expires_at <= current:
                        refresh.append(ticker)
        return refresh

    def _lookup_from_row(self, row: sqlite3.Row | None, now: datetime) -> YahooCacheLookup:
        if row is None:
            return YahooCacheLookup("missing", None)
        try:
            status = str(row["status"])
            if status not in _VALID_STATUSES:
                return YahooCacheLookup("corrupt", None)
            data = self._decode_data(row["data_json"])
            fetched_at = self._from_iso(str(row["fetched_at"]))
            expires_at = self._from_iso(str(row["expires_at"]))
            updated_at = self._from_iso(str(row["updated_at"]))
            if status == "fresh" and data is None:
                return YahooCacheLookup("corrupt", None)
            record = YahooCacheRecord(
                ticker=str(row["ticker"]),
                yahoo_ticker=str(row["yahoo_ticker"]),
                status=status,  # type: ignore[arg-type]
                data=data,
                fetched_at=fetched_at,
                expires_at=expires_at,
                error=str(row["error"]) if row["error"] is not None else None,
                updated_at=updated_at,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return YahooCacheLookup("corrupt", None)

        if status == "fresh":
            state: YahooCacheState = "fresh" if expires_at > now else "stale"
        elif status == "failed":
            # Preserve the last successful payload when a refresh fails.  It
            # is explicitly stale (and the record carries status/error), but
            # remains available to stale-while-revalidate callers.
            state = "stale" if data is not None else "failed"
        else:
            state = "unsupported"
        return YahooCacheLookup(state, record)

    def _now(self) -> datetime:
        return self._as_utc(self._now_provider())

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        normalized = str(ticker).strip().upper()
        if not normalized:
            raise ValueError("ticker must not be empty")
        return normalized

    @classmethod
    def _unique_tickers(cls, watchlist: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for ticker in watchlist:
            normalized = cls._normalize_ticker(ticker)
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _to_iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @classmethod
    def _from_iso(cls, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return cls._as_utc(parsed)

    @classmethod
    def _encode_data(cls, data: Mapping[str, Any] | None) -> str | None:
        if data is None:
            return None
        safe_data = cls._json_safe(dict(data))
        return json.dumps(safe_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _decode_data(data_json: str | None) -> dict[str, Any] | None:
        if data_json is None:
            return None
        decoded = json.loads(data_json)
        if not isinstance(decoded, dict):
            raise ValueError("Yahoo cache JSON must contain an object")
        return decoded

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, Decimal):
            number = float(value)
            return number if math.isfinite(number) else None
        if isinstance(value, datetime):
            return cls._to_iso(cls._as_utc(value))
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(item) for item in value]
        # NumPy scalar types expose item() and are common in data pipelines.
        item_method = getattr(value, "item", None)
        if callable(item_method):
            return cls._json_safe(item_method())
        raise TypeError(f"Value of type {type(value).__name__} is not JSON serializable")
