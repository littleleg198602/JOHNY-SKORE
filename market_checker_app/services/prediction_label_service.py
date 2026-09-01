from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

import pandas as pd

from market_checker_app.prediction_contract import (
    resolve_excess_return_label,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


PriceHistoryLoader = Callable[
    [str],
    pd.DataFrame | tuple[pd.DataFrame | None, str | None] | None,
]


class PredictionLabelService:
    """Resolve mature prediction snapshots from later observed closes.

    The loader is injected so production can use Yahoo (or a future licensed
    provider) while tests remain fully deterministic. A snapshot stays PENDING
    when the five-trading-day horizon is not yet observable or a source is
    unavailable. Only a mature, complete window can become RESOLVED; a mature
    window with loaded but unusable prices becomes UNAVAILABLE.
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
    def _price_window(
        history: pd.DataFrame | None,
        *,
        as_of: datetime,
        horizon: int,
    ) -> tuple[list[float], datetime] | None:
        if (
            history is None
            or history.empty
            or "Close" not in history.columns
            or horizon < 1
        ):
            return None
        try:
            timestamps = pd.to_datetime(history.index, utc=True, errors="coerce")
            closes = pd.to_numeric(history["Close"], errors="coerce")
        except (TypeError, ValueError):
            return None
        frame = pd.DataFrame({"timestamp": timestamps, "close": closes})
        frame = frame.dropna(subset=["timestamp", "close"])
        frame = frame[
            frame["close"].map(
                lambda value: math.isfinite(float(value)) and float(value) > 0.0
            )
        ]
        if frame.empty:
            return None
        frame = (
            frame.sort_values("timestamp")
            .drop_duplicates("timestamp", keep="last")
            .reset_index(drop=True)
        )
        cutoff = pd.Timestamp(as_of)
        base = frame[frame["timestamp"] <= cutoff]
        future = frame[frame["timestamp"] > cutoff].head(horizon)
        if base.empty or len(future) < horizon:
            return None
        values = [float(base.iloc[-1]["close"])] + [
            float(value) for value in future["close"].tolist()
        ]
        endpoint = future.iloc[-1]["timestamp"].to_pydatetime()
        return values, endpoint

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
            if snapshot_as_of is None or not ticker or not benchmark or horizon < 1:
                deferred += 1
                continue

            asset_history = load(ticker)
            benchmark_history = load(benchmark)
            asset_window = self._price_window(
                asset_history,
                as_of=snapshot_as_of,
                horizon=horizon,
            )
            benchmark_window = self._price_window(
                benchmark_history,
                as_of=snapshot_as_of,
                horizon=horizon,
            )
            if asset_window is None or benchmark_window is None:
                # Do not turn a not-yet-mature or temporarily unavailable
                # source into a false zero/negative label.
                if (
                    asset_history is None
                    or benchmark_history is None
                    or clock < snapshot_as_of + timedelta(days=self.maturity_grace_days)
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

            target_observed_at = max(asset_window[1], benchmark_window[1])
            labels.append(
                resolve_excess_return_label(
                    snapshot,
                    asset_window[0],
                    benchmark_window[0],
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
