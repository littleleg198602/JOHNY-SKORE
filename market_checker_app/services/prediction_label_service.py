from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
import json
import math

import pandas as pd

from market_checker_app.prediction_contract import (
    PRIMARY_TARGET_VERSION,
    resolve_excess_return_label,
)
from market_checker_app.services.us_equity_calendar import (
    target_us_equity_window,
    us_equity_session,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


PriceHistoryLoader = Callable[
    [str],
    pd.DataFrame | tuple[pd.DataFrame | None, str | None] | None,
]


class PredictionLabelService:
    """Resolve mature prediction snapshots from later observed closes.

    Target windows are defined by the exchange calendar, not by "the next N
    rows" returned by a provider. Missing expected sessions therefore cannot
    silently shift a five-session horizon forward. A label is also forbidden
    from using a close that was not yet available at the evaluation clock.
    """

    def __init__(
        self,
        *,
        maturity_grace_days: int = 14,
    ) -> None:
        if maturity_grace_days < 1:
            raise ValueError("maturity_grace_days must be positive")
        self.maturity_grace_days = maturity_grace_days

    @staticmethod
    def _history_from_result(
        result: pd.DataFrame | tuple[pd.DataFrame | None, str | None] | None,
    ) -> pd.DataFrame | None:
        if isinstance(result, tuple):
            return result[0]
        return result

    @staticmethod
    def _as_of(value: object) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

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
    def _price_lookup(
        cls,
        history: pd.DataFrame | None,
    ) -> dict[date, float]:
        if history is None or history.empty or "Close" not in history.columns:
            return {}
        closes = pd.to_numeric(history["Close"], errors="coerce")
        lookup: dict[date, float] = {}
        for raw_timestamp, raw_close in zip(history.index, closes):
            session_date = cls._session_date(raw_timestamp)
            if session_date is None or us_equity_session(session_date) is None:
                continue
            try:
                close = float(raw_close)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(close) or close <= 0.0:
                continue
            # Provider duplicates are deterministic: the final row for a
            # session wins, while the expected date itself is never skipped.
            lookup[session_date] = close
        return lookup

    @classmethod
    def _price_window(
        cls,
        history: pd.DataFrame | None,
        *,
        as_of: datetime,
        horizon: int,
        evaluation_as_of: datetime | None = None,
    ) -> tuple[list[float], datetime] | None:
        if horizon < 1:
            return None
        base, future = target_us_equity_window(as_of, horizon)
        endpoint = future[-1]
        if evaluation_as_of is not None:
            clock = cls._as_of(evaluation_as_of)
            if clock is None or endpoint.close_at > clock:
                return None
        lookup = cls._price_lookup(history)
        required = [base.session_date, *(session.session_date for session in future)]
        if any(day not in lookup for day in required):
            return None
        return [lookup[day] for day in required], endpoint.close_at

    @classmethod
    def _common_price_windows(
        cls,
        asset_history: pd.DataFrame | None,
        benchmark_history: pd.DataFrame | None,
        *,
        as_of: datetime,
        horizon: int,
        evaluation_as_of: datetime | None = None,
    ) -> tuple[list[float], list[float], datetime] | None:
        """Return values only for the exact calendar-defined target sessions."""

        if horizon < 1:
            return None
        base, future = target_us_equity_window(as_of, horizon)
        endpoint = future[-1]
        if evaluation_as_of is not None:
            clock = cls._as_of(evaluation_as_of)
            if clock is None or endpoint.close_at > clock:
                return None

        asset_lookup = cls._price_lookup(asset_history)
        benchmark_lookup = cls._price_lookup(benchmark_history)
        required = [base.session_date, *(session.session_date for session in future)]
        if any(
            day not in asset_lookup or day not in benchmark_lookup
            for day in required
        ):
            return None
        return (
            [asset_lookup[day] for day in required],
            [benchmark_lookup[day] for day in required],
            endpoint.close_at,
        )

    @staticmethod
    def _snapshot_mapping(row: Mapping[str, object]) -> dict[str, object]:
        snapshot = dict(row)
        for column, fallback in (
            ("feature_payload_json", {}),
            ("baseline_output_json", {}),
            ("provenance_json", {}),
        ):
            target = column.removesuffix("_json")
            raw = snapshot.get(column)
            if isinstance(raw, str):
                try:
                    snapshot[target] = json.loads(raw)
                except json.JSONDecodeError:
                    snapshot[target] = fallback
            elif raw is not None:
                snapshot[target] = raw
            snapshot.pop(column, None)
        return snapshot

    def resolve_pending_snapshots(
        self,
        *,
        store: SQLiteStore,
        price_loader: PriceHistoryLoader,
        as_of: datetime | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        """Resolve currently mature PENDING rows and persist labels idempotently."""

        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            clock = clock.replace(tzinfo=timezone.utc)
        clock = clock.astimezone(timezone.utc)

        pending_frame = store.read_prediction_snapshots()
        if pending_frame.empty or "label_status" not in pending_frame.columns:
            return {
                "pending_before": 0,
                "resolved": 0,
                "unavailable": 0,
                "deferred": 0,
                "source_failures": 0,
            }
        pending_frame = pending_frame[
            pending_frame["label_status"].astype(str).str.upper() == "PENDING"
        ]
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive when provided")
            pending_frame = pending_frame.head(limit)

        history_cache: dict[str, pd.DataFrame | None] = {}
        source_failures = 0

        def load(ticker: str) -> pd.DataFrame | None:
            nonlocal source_failures
            key = str(ticker).strip().upper()
            if key not in history_cache:
                try:
                    raw = price_loader(key)
                    history_cache[key] = self._history_from_result(raw)
                    if history_cache[key] is None:
                        source_failures += 1
                except Exception:
                    history_cache[key] = None
                    source_failures += 1
            return history_cache[key]

        labels: list[dict[str, object]] = []
        deferred = 0
        for raw_row in pending_frame.to_dict(orient="records"):
            snapshot = self._snapshot_mapping(raw_row)
            snapshot_as_of = self._as_of(snapshot.get("as_of"))
            ticker = str(snapshot.get("ticker") or "").strip().upper()
            benchmark = str(snapshot.get("benchmark_ticker") or "SPY").strip().upper()
            horizon = int(snapshot.get("horizon_trading_days") or 5)
            target_version = str(snapshot.get("target_version") or "")
            if (
                snapshot_as_of is None
                or not ticker
                or not benchmark
                or horizon < 1
                or target_version != PRIMARY_TARGET_VERSION
            ):
                deferred += 1
                continue

            _, future_sessions = target_us_equity_window(snapshot_as_of, horizon)
            target_due_at = future_sessions[-1].close_at
            if clock < target_due_at:
                # Even if a buggy/provider fixture returns future rows, replay
                # may never consume information that was unavailable at clock.
                deferred += 1
                continue

            asset_history = load(ticker)
            benchmark_history = load(benchmark)
            common_windows = self._common_price_windows(
                asset_history,
                benchmark_history,
                as_of=snapshot_as_of,
                horizon=horizon,
                evaluation_as_of=clock,
            )
            if common_windows is None:
                # After the target session has closed, allow a grace period for
                # provider delays. Only then classify loaded-but-incomplete data
                # as unavailable; a transport failure remains pending.
                if (
                    asset_history is None
                    or benchmark_history is None
                    or clock < target_due_at + timedelta(days=self.maturity_grace_days)
                ):
                    deferred += 1
                    continue
                labeled = resolve_excess_return_label(
                    snapshot,
                    [],
                    [],
                )
                labels.append(labeled)
                continue

            asset_values, benchmark_values, target_observed_at = common_windows
            if target_observed_at > clock:
                deferred += 1
                continue
            labels.append(
                resolve_excess_return_label(
                    snapshot,
                    asset_values,
                    benchmark_values,
                    target_observed_at=target_observed_at,
                )
            )

        updated = store.update_prediction_snapshot_labels(labels)
        resolved = sum(
            str(label.get("label_status")) == "RESOLVED" for label in labels
        )
        unavailable = sum(
            str(label.get("label_status")) == "UNAVAILABLE" for label in labels
        )
        if updated != len(labels):
            raise RuntimeError(
                "počet uložených labelů neodpovídá počtu připravených labelů"
            )
        return {
            "pending_before": int(len(pending_frame)),
            "resolved": int(resolved),
            "unavailable": int(unavailable),
            "deferred": int(deferred),
            "source_failures": int(source_failures),
        }
