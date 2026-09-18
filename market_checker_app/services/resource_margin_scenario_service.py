"""Point-in-time, disclosure-bound resource and energy margin scenarios.

This service does not forecast commodity prices or margins.  It only converts
an explicitly supplied *what-if* price change into a transparent sensitivity
when cost share, hedging/fixed coverage and pass-through are all disclosed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from urllib.parse import urlsplit

from market_checker_app.config import CommodityEnergySourceConfig, ResourcePricePointConfig


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite_percentage(value: object, label: str, *, signed: bool = False) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(numeric) or (not signed and not 0.0 <= numeric <= 100.0):
        raise ValueError(f"{label} is outside its permitted range")
    return numeric


def _https_url(value: object) -> bool:
    try:
        parsed = urlsplit(str(value or ""))
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _valid_point(point: ResourcePricePointConfig, as_of: datetime) -> bool:
    try:
        value = float(point.value)
    except (TypeError, ValueError):
        return False
    return (
        _utc(point.observed_at) <= _utc(point.available_at) <= as_of
        and math.isfinite(value) and value > 0
        and bool(str(point.unit or "").strip())
        and bool(str(point.currency or "").strip())
        and _https_url(point.source_url)
    )


def _latest_available_price_point(
    points: tuple[ResourcePricePointConfig, ...],
    *,
    as_of: datetime,
) -> tuple[ResourcePricePointConfig | None, int]:
    eligible = [
        point
        for point in points
        if _valid_point(point, as_of)
    ]
    excluded = len(points) - len(eligible)
    if not eligible:
        return None, excluded
    return max(eligible, key=lambda point: (_utc(point.observed_at), _utc(point.available_at))), excluded


def build_resource_margin_scenario(
    source: CommodityEnergySourceConfig,
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Build an auditable sensitivity, or a truthful insufficiency result."""

    cutoff = _utc(as_of)
    latest, future_excluded = _latest_available_price_point(source.price_points, as_of=cutoff)
    cost_share = _finite_percentage(source.disclosed_cost_share_of_revenue_pct, "cost share")
    hedged = _finite_percentage(source.hedged_share_pct, "hedged share")
    fixed = _finite_percentage(source.fixed_price_share_pct, "fixed price share")
    pass_through = _finite_percentage(source.pass_through_pct, "pass-through share")
    scenario_change = _finite_percentage(source.scenario_price_change_pct, "scenario price change", signed=True)
    missing = [
        label
        for label, value in (
            ("COST_SHARE_NOT_DISCLOSED", cost_share),
            ("HEDGED_SHARE_NOT_DISCLOSED", hedged),
            ("FIXED_PRICE_SHARE_NOT_DISCLOSED", fixed),
            ("PASS_THROUGH_NOT_DISCLOSED", pass_through),
            ("SCENARIO_PRICE_CHANGE_NOT_SET", scenario_change),
        )
        if value is None
    ]
    if latest is None:
        missing.insert(0, "PRICE_SERIES_MISSING_OR_NOT_AVAILABLE_AT_CUTOFF")
    if _utc(source.published_at) > cutoff:
        missing.append("DISCLOSURE_NOT_AVAILABLE_AT_CUTOFF")
    if not _https_url(source.url):
        missing.append("DISCLOSURE_SOURCE_URL_INVALID")
    if not str(source.disclosure_period or "").strip() or not str(source.evidence_quote or "").strip():
        missing.append("DISCLOSURE_PERIOD_OR_QUOTE_MISSING")
    if str(getattr(source.exposure_type, "value", source.exposure_type)) == "COMMODITY_OUTPUT":
        missing.append("OUTPUT_REVENUE_SENSITIVITY_NOT_SUPPORTED_BY_COST_FORMULA")
    if scenario_change is not None and scenario_change < -100.0:
        missing.append("PRICE_CHANGE_BELOW_MINUS_100_PCT")
    if hedged is not None and fixed is not None and hedged + fixed > 100.0:
        missing.append("HEDGED_AND_FIXED_SHARE_EXCEED_100_PCT")
    result: dict[str, object] = {
        "status": "READY" if not missing else "INSUFFICIENT_DATA",
        "reason": "" if not missing else ";".join(missing),
        "as_of": cutoff.isoformat(),
        "resource_name": source.resource_name,
        "disclosure_period": source.disclosure_period,
        "price_points_configured": len(source.price_points),
        "price_points_future_excluded": future_excluded,
        "price_series_attached": latest is not None,
        "forecast": False,
        "prediction_input": False,
        "scenario_assumption": {
            "price_change_pct": scenario_change,
            "not_a_price_forecast": True,
        },
    }
    if latest is not None:
        result["latest_price_point"] = {
            "observed_at": _utc(latest.observed_at).isoformat(),
            "available_at": _utc(latest.available_at).isoformat(),
            "value": float(latest.value),
            "unit": latest.unit,
            "currency": latest.currency,
            "source_url": latest.source_url,
        }
    if missing:
        return result
    assert cost_share is not None and hedged is not None and fixed is not None
    assert pass_through is not None and scenario_change is not None
    unprotected = 1.0 - (hedged + fixed) / 100.0
    unpassed = 1.0 - pass_through / 100.0
    margin_impact_pp = -100.0 * (cost_share / 100.0) * (scenario_change / 100.0) * unprotected * unpassed
    result["sensitivity"] = {
        "cost_share_of_revenue_pct": cost_share,
        "hedged_share_pct": hedged,
        "fixed_price_share_pct": fixed,
        "unprotected_share_pct": unprotected * 100.0,
        "pass_through_pct": pass_through,
        "estimated_margin_impact_pp": margin_impact_pp,
        "formula": "-(cost_share_of_revenue * scenario_price_change * unprotected_share * (1-pass_through))",
    }
    return result


__all__ = ["build_resource_margin_scenario"]
