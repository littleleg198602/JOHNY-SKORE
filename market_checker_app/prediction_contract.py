from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any


SNAPSHOT_SCHEMA_VERSION = "feature_snapshot_v1"
PRIMARY_TARGET_NAME = "5d_excess_return_vs_benchmark"
PRIMARY_TARGET_VERSION = "excess_return_5d_v1"
PRIMARY_HORIZON_TRADING_DAYS = 5
DEFAULT_BENCHMARK_TICKER = "SPY"
BASELINE_MODEL_ID = "legacy_v2.1_heuristic"
BASELINE_MODEL_VERSION = "v2.1_guarded_consensus"


@dataclass(frozen=True, slots=True)
class PredictionTargetContract:
    """Machine-readable definition of the first target to validate.

    Target values are decimals, not percentages: 0.02 means two percentage
    points of excess return. A label is resolved only after both the asset and
    benchmark have five future trading-day closes.
    """

    name: str = PRIMARY_TARGET_NAME
    version: str = PRIMARY_TARGET_VERSION
    horizon_trading_days: int = PRIMARY_HORIZON_TRADING_DAYS
    return_unit: str = "decimal"
    benchmark_policy: str = "sector_etf_when_available_else_spy"

    def __post_init__(self) -> None:
        if self.horizon_trading_days < 1:
            raise ValueError("horizon_trading_days must be positive")
        if not self.name.strip() or not self.version.strip():
            raise ValueError("target name and version must not be empty")
        if self.return_unit != "decimal":
            raise ValueError("the first target must use decimal returns")

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "horizon_trading_days": self.horizon_trading_days,
            "return_unit": self.return_unit,
            "benchmark_policy": self.benchmark_policy,
        }


PRIMARY_PREDICTION_TARGET = PredictionTargetContract()


_SECTOR_BENCHMARKS: tuple[tuple[str, str], ...] = (
    ("communication", "XLC"),
    ("consumer cyclical", "XLY"),
    ("consumer defensive", "XLP"),
    ("energy", "XLE"),
    ("financial", "XLF"),
    ("healthcare", "XLV"),
    ("industrials", "XLI"),
    ("real estate", "XLRE"),
    ("technology", "XLK"),
    ("basic materials", "XLB"),
    ("utilities", "XLU"),
)


def _normalise_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def benchmark_for_sector(sector: object | None) -> tuple[str, str]:
    """Return (ticker, selection_reason) without inventing sector data."""

    normalised = _normalise_text(sector)
    if normalised:
        for label, ticker in _SECTOR_BENCHMARKS:
            if label in normalised:
                return ticker, "sector_etf"
    return DEFAULT_BENCHMARK_TICKER, "default_fallback"


def _numeric_prices(prices: Sequence[object]) -> list[float]:
    values: list[float] = []
    for raw in prices:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            values.append(value)
    return values


def compute_forward_return(
    prices: Sequence[object],
    horizon_trading_days: int = PRIMARY_HORIZON_TRADING_DAYS,
) -> float | None:
    """Compute t0 -> t+h return from a price sequence.

    The sequence must be ordered by trading date and start at the prediction
    timestamp. Returning None for incomplete data is intentional: a missing
    label must never be converted to a zero return.
    """

    if horizon_trading_days < 1:
        raise ValueError("horizon_trading_days must be positive")
    values = _numeric_prices(prices)
    if len(values) <= horizon_trading_days:
        return None
    base = values[0]
    future = values[horizon_trading_days]
    if base <= 0.0:
        return None
    return (future / base) - 1.0


def compute_excess_return_target(
    asset_prices: Sequence[object],
    benchmark_prices: Sequence[object],
    horizon_trading_days: int = PRIMARY_HORIZON_TRADING_DAYS,
) -> float | None:
    """Compute asset return minus benchmark return as a decimal value."""

    asset_return = compute_forward_return(asset_prices, horizon_trading_days)
    benchmark_return = compute_forward_return(benchmark_prices, horizon_trading_days)
    if asset_return is None or benchmark_return is None:
        return None
    return asset_return - benchmark_return


def _json_default(value: Any) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return str(value)


def canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_snapshot_id(run_id: int, ticker: str, target_version: str) -> str:
    """Create a stable identifier for one immutable run/ticker snapshot."""

    identity = f"{int(run_id)}|{str(ticker).strip().upper()}|{target_version}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def build_point_in_time_snapshot(
    *,
    run_id: int,
    ticker: str,
    observed_at: datetime,
    feature_payload: Mapping[str, object],
    baseline_output: Mapping[str, object],
    provenance: Mapping[str, object],
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER,
    benchmark_selection: str = "default_fallback",
    target: PredictionTargetContract = PRIMARY_PREDICTION_TARGET,
) -> dict[str, object]:
    """Build an immutable, unlabeled snapshot for later OOS evaluation.

    The returned record deliberately contains no future prices and no target
    value. Its label remains PENDING until a separate resolver observes the
    future horizon.
    """

    normalized_ticker = str(ticker).strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker must not be empty")
    if int(run_id) < 1:
        raise ValueError("run_id must be positive")
    if not str(benchmark_ticker).strip():
        raise ValueError("benchmark_ticker must not be empty")

    observed_iso = _utc_iso(observed_at)
    body: dict[str, object] = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": make_snapshot_id(
            int(run_id), normalized_ticker, target.version
        ),
        "run_id": int(run_id),
        "ticker": normalized_ticker,
        "as_of": observed_iso,
        "observed_at": observed_iso,
        "target_name": target.name,
        "target_version": target.version,
        "horizon_trading_days": target.horizon_trading_days,
        "benchmark_ticker": str(benchmark_ticker).strip().upper(),
        "benchmark_selection": str(benchmark_selection).strip() or "unknown",
        "label_status": "PENDING",
        "target_value": None,
        "target_observed_at": None,
        "baseline_model_id": BASELINE_MODEL_ID,
        "baseline_model_version": BASELINE_MODEL_VERSION,
        "feature_payload": dict(feature_payload),
        "baseline_output": dict(baseline_output),
        "provenance": dict(provenance),
    }
    body["snapshot_hash"] = canonical_hash(body)
    return body


def resolve_excess_return_label(
    snapshot: Mapping[str, object],
    asset_prices: Sequence[object],
    benchmark_prices: Sequence[object],
    *,
    target_observed_at: datetime | None = None,
) -> dict[str, object]:
    """Return a labeled copy once the complete future horizon is available."""

    horizon = int(
        snapshot.get("horizon_trading_days") or PRIMARY_HORIZON_TRADING_DAYS
    )
    target_value = compute_excess_return_target(
        asset_prices,
        benchmark_prices,
        horizon,
    )
    labeled = dict(snapshot)
    if target_value is None:
        labeled["label_status"] = "UNAVAILABLE"
        labeled["target_value"] = None
        labeled["target_observed_at"] = None
    else:
        labeled["label_status"] = "RESOLVED"
        labeled["target_value"] = target_value
        labeled["target_observed_at"] = (
            _utc_iso(target_observed_at)
            if target_observed_at is not None
            else None
        )
    labeled["snapshot_hash"] = canonical_hash(
        {key: value for key, value in labeled.items() if key != "snapshot_hash"}
    )
    return labeled
