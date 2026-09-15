from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from market_checker_app.release_manifest import (
    ACTIVE_MODEL_ID,
    ACTIVE_MODEL_VERSION,
    ACTIVE_SCORING_VERSION,
    FEATURE_SET_VERSION,
    LEGACY_BASELINE_MODEL_ID,
    LEGACY_BASELINE_MODEL_VERSION,
    build_release_manifest,
)
from market_checker_app.services.price_methodology import (
    PRICE_BASIS,
    PRICE_METHOD_VERSION,
)


SNAPSHOT_SCHEMA_VERSION = "feature_snapshot_v1"
PRIMARY_TARGET_NAME = "5d_excess_return_vs_benchmark"
PRIMARY_TARGET_VERSION = "excess_return_5d_nyse_split_price_v3"
PRIMARY_HORIZON_TRADING_DAYS = 5
DEFAULT_BENCHMARK_TICKER = "SPY"

# New snapshots use the active heuristic contract. The historical v2.1
# baseline identifiers remain exported separately and are never rewritten.
BASELINE_MODEL_ID = ACTIVE_MODEL_ID
BASELINE_MODEL_VERSION = ACTIVE_MODEL_VERSION


@dataclass(frozen=True, slots=True)
class PredictionTargetContract:
    """Machine-readable definition of the first target to validate.

    Target values are decimals, not percentages: 0.02 means two percentage
    points of excess return. A label is resolved only after both the asset and
    benchmark have five future trading-day closes. Prices use a split-adjusted
    price-return basis and deliberately exclude dividend total return.
    """

    name: str = PRIMARY_TARGET_NAME
    version: str = PRIMARY_TARGET_VERSION
    horizon_trading_days: int = PRIMARY_HORIZON_TRADING_DAYS
    return_unit: str = "decimal"
    benchmark_policy: str = "sector_etf_when_available_else_spy"
    price_basis: str = PRICE_BASIS
    price_method_version: str = PRICE_METHOD_VERSION
    dividends_included: bool = False

    def __post_init__(self) -> None:
        if self.horizon_trading_days < 1:
            raise ValueError("horizon_trading_days must be positive")
        if not self.name.strip() or not self.version.strip():
            raise ValueError("target name and version must not be empty")
        if self.return_unit != "decimal":
            raise ValueError("the first target must use decimal returns")
        if self.dividends_included:
            raise ValueError("the primary target is a price-return target, not total return")

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "horizon_trading_days": self.horizon_trading_days,
            "return_unit": self.return_unit,
            "benchmark_policy": self.benchmark_policy,
            "price_basis": self.price_basis,
            "price_method_version": self.price_method_version,
            "dividends_included": self.dividends_included,
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
    """Compute t0 -> t+h price return from a canonical price sequence.

    The sequence must already use ``PRIMARY_PREDICTION_TARGET.price_basis`` and
    must be ordered by trading date, starting at the prediction base session.
    Returning None for incomplete data is intentional: a missing label must
    never be converted to a zero return.
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
    """Compute asset price return minus benchmark price return as a decimal."""

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
    release_manifest: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build an immutable, unlabeled snapshot for later OOS evaluation.

    The returned record deliberately contains no future prices and no target
    value. Its label remains PENDING until a separate resolver observes the
    future horizon. Target price methodology and release identity are embedded
    in provenance so old and new definitions remain auditable without mutating
    historical rows.
    """

    normalized_ticker = str(ticker).strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker must not be empty")
    if int(run_id) < 1:
        raise ValueError("run_id must be positive")
    if not str(benchmark_ticker).strip():
        raise ValueError("benchmark_ticker must not be empty")

    observed_iso = _utc_iso(observed_at)
    target_provenance = dict(provenance)
    target_provenance.setdefault("target_price_basis", target.price_basis)
    target_provenance.setdefault(
        "target_price_method_version",
        target.price_method_version,
    )
    target_provenance.setdefault(
        "target_dividends_included",
        target.dividends_included,
    )
    target_provenance.setdefault("feature_set_version", FEATURE_SET_VERSION)
    manifest = dict(
        release_manifest
        or build_release_manifest(target_version=target.version)
    )
    target_provenance.setdefault("release_manifest", manifest)

    versioned_baseline_output = dict(baseline_output)
    versioned_baseline_output["scoring_version"] = ACTIVE_SCORING_VERSION
    versioned_baseline_output.setdefault("model_version", ACTIVE_MODEL_VERSION)
    versioned_baseline_output.setdefault("feature_set_version", FEATURE_SET_VERSION)
    versioned_baseline_output.setdefault("target_version", target.version)
    versioned_baseline_output.setdefault("code_sha", manifest.get("code_sha"))
    versioned_baseline_output.setdefault("config_hash", manifest.get("config_hash"))
    versioned_baseline_output.setdefault(
        "release_manifest_hash",
        manifest.get("manifest_hash"),
    )

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
        "baseline_output": versioned_baseline_output,
        "provenance": target_provenance,
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


__all__ = [
    "BASELINE_MODEL_ID",
    "BASELINE_MODEL_VERSION",
    "LEGACY_BASELINE_MODEL_ID",
    "LEGACY_BASELINE_MODEL_VERSION",
    "FEATURE_SET_VERSION",
    "PRIMARY_PREDICTION_TARGET",
    "PRIMARY_TARGET_VERSION",
    "build_point_in_time_snapshot",
    "benchmark_for_sector",
    "compute_excess_return_target",
    "compute_forward_return",
    "make_snapshot_id",
    "resolve_excess_return_label",
]
