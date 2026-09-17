"""Small point-in-time macro/sector regime reports with vintage protection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math


MACRO_REGIME_REPORT_VERSION = "macro_sector_regime_v1"
GLOBAL_INDICATORS = ("VIX", "US10Y", "T10Y2Y", "DXY", "WTI", "CPI_YOY", "INDPRO_YOY")
SECTOR_RELATIVE_INDICATOR = "SECTOR_RELATIVE_20D"
ALLOWED_INDICATORS = set(GLOBAL_INDICATORS) | {SECTOR_RELATIVE_INDICATOR}


def _utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_id(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_macro_observations(value: str) -> tuple[list[dict[str, object]], list[str]]:
    """Parse a safe, source-attested macro observation manifest.

    ``indicator | scope | reference_period | value | unit | observed_at |
    available_at | vintage_at | HTTPS URL``
    """

    observations: list[dict[str, object]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(str(value or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 9:
            errors.append(f"Makro řádek {line_number}: očekávám 9 polí indicator | scope | reference period | value | unit | observed_at | available_at | vintage_at | HTTPS URL.")
            continue
        indicator, scope, reference_period, raw_value, unit, raw_observed, raw_available, raw_vintage, source_url = parts
        try:
            indicator = indicator.upper()
            if indicator not in ALLOWED_INDICATORS:
                raise ValueError("nepodporovaný indikátor")
            if not scope or not reference_period or not unit:
                raise ValueError("chybí scope, reference period nebo jednotka")
            numeric = float(raw_value.replace(",", "."))
            if not math.isfinite(numeric):
                raise ValueError("hodnota musí být konečná")
            observed_at = _utc(raw_observed)
            available_at = _utc(raw_available)
            vintage_at = _utc(raw_vintage)
            if observed_at is None or available_at is None or vintage_at is None:
                raise ValueError("čas pozorování/dostupnosti/vintage není platný")
            if vintage_at > available_at:
                raise ValueError("vintage_at nesmí být po available_at")
            if not source_url.startswith("https://"):
                raise ValueError("zdroj musí být HTTPS URL")
        except (TypeError, ValueError) as exc:
            errors.append(f"Makro řádek {line_number}: {exc}.")
            continue
        record = {
            "indicator_id": indicator,
            "scope": scope.upper(),
            "reference_period": reference_period,
            "value": numeric,
            "unit": unit,
            "observed_at": observed_at.isoformat(),
            "available_at": available_at.isoformat(),
            "vintage_at": vintage_at.isoformat(),
            "source_url": source_url,
        }
        record["observation_id"] = _stable_id(record)
        if record["observation_id"] not in seen:
            observations.append(record)
            seen.add(record["observation_id"])
    return observations, errors


def _select_vintages(observations: Sequence[Mapping[str, object]], as_of: datetime) -> tuple[dict[tuple[str, str], dict[str, object]], int]:
    candidates: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    future_excluded = 0
    for raw in observations:
        indicator = str(raw.get("indicator_id") or "").upper()
        scope = str(raw.get("scope") or "").upper()
        observed = _utc(raw.get("observed_at"))
        available = _utc(raw.get("available_at"))
        vintage = _utc(raw.get("vintage_at"))
        try:
            numeric = float(raw.get("value"))
        except (TypeError, ValueError):
            continue
        if indicator not in ALLOWED_INDICATORS or not scope or observed is None or available is None or vintage is None or not math.isfinite(numeric):
            continue
        if available > as_of or vintage > as_of:
            future_excluded += 1
            continue
        row = dict(raw)
        row["value"] = numeric
        row["_observed"] = observed
        row["_available"] = available
        row["_vintage"] = vintage
        candidates[(indicator, scope)].append(row)
    selected: dict[tuple[str, str], dict[str, object]] = {}
    for key, rows in candidates.items():
        selected[key] = max(rows, key=lambda row: (row["_observed"], row["_vintage"], row["_available"], str(row.get("observation_id") or "")))
    return selected, future_excluded


def build_macro_regime_report(observations: Sequence[Mapping[str, object]], *, as_of: datetime) -> dict[str, object]:
    """Create a report only; it intentionally does not alter any signal."""

    cutoff = _utc(as_of)
    if cutoff is None:
        raise ValueError("as_of must be a datetime")
    selected, future_excluded = _select_vintages(observations, cutoff)
    globals_by_id = {indicator: selected.get((indicator, "GLOBAL")) for indicator in GLOBAL_INDICATORS}
    missing = [indicator for indicator, row in globals_by_id.items() if row is None]
    sector_rows = [row for (indicator, _), row in selected.items() if indicator == SECTOR_RELATIVE_INDICATOR]
    values = {indicator: float(row["value"]) for indicator, row in globals_by_id.items() if row is not None}
    regime = "INSUFFICIENT_DATA"
    if not missing:
        if values["VIX"] >= 25.0 and (values["T10Y2Y"] < 0.0 or values["INDPRO_YOY"] < 0.0):
            regime = "RISK_OFF"
        elif values["VIX"] >= 25.0:
            regime = "HIGH_VOLATILITY"
        elif values["CPI_YOY"] >= 3.0:
            regime = "INFLATION_PRESSURE"
        else:
            regime = "MIXED"
    selected_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in list(globals_by_id.values()) + sector_rows
        if row is not None
    ]
    report = {
        "report_version": MACRO_REGIME_REPORT_VERSION,
        "as_of": cutoff.isoformat(),
        "status": "READY" if not missing else "INSUFFICIENT_DATA",
        "reason": "" if not missing else "MISSING_REQUIRED_INDICATORS:" + ",".join(missing),
        "regime": regime,
        "required_indicators": list(GLOBAL_INDICATORS),
        "selected_observations": selected_rows,
        "sector_relative_strength": [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in sector_rows
        ],
        "future_or_revised_observation_excluded_count": future_excluded,
        "analysis_only": True,
        "ranking_modified": False,
        "decision_modified": False,
        "activation_allowed": False,
    }
    report["report_id"] = _stable_id(report)
    return report


__all__ = ["MACRO_REGIME_REPORT_VERSION", "build_macro_regime_report", "parse_macro_observations"]
