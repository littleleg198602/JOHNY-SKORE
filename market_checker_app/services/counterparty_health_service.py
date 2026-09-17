"""Evidence-only health views for already identified counterparties.

This deliberately does not infer an identity, retrieve paid data, calculate a
credit score, or influence a company ranking.  A public counterparty can only
be linked to an SEC point-in-time snapshot through an explicit manifest entry.
Private or incomplete public disclosures remain limited assessments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json


COUNTERPARTY_HEALTH_REPORT_VERSION = "counterparty_health_v1"
PUBLIC_DOCUMENT_STATUSES = {
    "PUBLIC_FILING_AVAILABLE",
    "PUBLIC_DOCUMENT_LIMITED",
    "NO_PUBLIC_DOCUMENT",
    "UNVERIFIED",
}
REQUIRED_METRICS = (
    "cash_and_equivalents",
    "total_debt",
    "operating_cash_flow",
    "free_cash_flow",
)


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
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _json(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def parse_counterparty_health_sources(value: str) -> tuple[list[dict[str, object]], list[str]]:
    """Parse an explicit identity/document manifest.

    ``counterparty | PUBLIC/PRIVATE/UNKNOWN | public ticker/- | document status |
    publisher | published_at | HTTPS URL | disclosure scope``
    """

    records: list[dict[str, object]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(str(value or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 8:
            errors.append(f"Protistrana řádek {line_number}: očekávám 8 polí.")
            continue
        name, entity_type, public_ticker, document_status, publisher, raw_date, url, scope = parts
        entity_type = entity_type.upper()
        document_status = document_status.upper()
        ticker = public_ticker.upper() if public_ticker not in {"", "-"} else None
        published = _utc(raw_date)
        try:
            if not name:
                raise ValueError("chybí název protistrany")
            if entity_type not in {"PUBLIC", "PRIVATE", "UNKNOWN"}:
                raise ValueError("entity type musí být PUBLIC, PRIVATE nebo UNKNOWN")
            if document_status not in PUBLIC_DOCUMENT_STATUSES:
                raise ValueError("neplatný document status")
            if not publisher or not scope or published is None:
                raise ValueError("chybí vydavatel, datum nebo rozsah zveřejnění")
            if not url.startswith("https://"):
                raise ValueError("zdroj musí být HTTPS URL")
            if entity_type == "PUBLIC" and not ticker:
                raise ValueError("veřejná protistrana vyžaduje ověřený ticker")
            if entity_type != "PUBLIC" and ticker:
                raise ValueError("soukromá/neznámá protistrana nesmí mít veřejný ticker")
        except ValueError as exc:
            errors.append(f"Protistrana řádek {line_number}: {exc}.")
            continue
        key = _norm(name)
        if key in seen:
            errors.append(f"Protistrana řádek {line_number}: duplicitní identita {name!r}.")
            continue
        seen.add(key)
        record = {
            "counterparty": name,
            "entity_type": entity_type,
            "public_ticker": ticker,
            "document_status": document_status,
            "publisher": publisher,
            "published_at": published.isoformat(),
            "source_url": url,
            "disclosure_scope": scope,
        }
        record["source_id"] = _stable_id(record)
        records.append(record)
    return records, errors


def _select_snapshot(rows: Sequence[Mapping[str, object]], ticker: str, as_of: datetime) -> dict[str, object] | None:
    eligible: list[dict[str, object]] = []
    for raw in rows:
        if str(raw.get("ticker") or "").upper() != ticker:
            continue
        available = _utc(raw.get("availability_at"))
        snapshot_as_of = _utc(raw.get("as_of"))
        if available is None or snapshot_as_of is None or available > as_of or snapshot_as_of > as_of:
            continue
        row = dict(raw)
        row["_available"] = available
        row["_as_of"] = snapshot_as_of
        eligible.append(row)
    return max(eligible, key=lambda row: (row["_as_of"], row["_available"], str(row.get("snapshot_id") or ""))) if eligible else None


def _public_entry(
    relationship: Mapping[str, object], source: Mapping[str, object], snapshots: Sequence[Mapping[str, object]], as_of: datetime
) -> dict[str, object]:
    ticker = str(source["public_ticker"])
    snapshot = _select_snapshot(snapshots, ticker, as_of)
    entry: dict[str, object] = {
        "relationship_id": str(relationship.get("relationship_id") or ""),
        "company_ticker": str(relationship.get("ticker") or ""),
        "counterparty": str(relationship.get("counterparty") or ""),
        "entity_type": "PUBLIC",
        "public_ticker": ticker,
        "identity_source": dict(source),
        "health_assessment": "NOT_RATED",
        "analysis_only": True,
        "ranking_modified": False,
        "decision_modified": False,
    }
    if source.get("document_status") != "PUBLIC_FILING_AVAILABLE":
        entry.update(
            {
                "status": "LIMITED_PUBLIC_DOCUMENT",
                "reason": "DECLARED_DOCUMENT_STATUS:" + str(source.get("document_status")),
            }
        )
        return entry
    if snapshot is None:
        entry.update({"status": "PUBLIC_FILING_NOT_INGESTED", "reason": "NO_PIT_SEC_SNAPSHOT"})
        return entry
    values = _json(snapshot.get("values_json"))
    missing = _json(snapshot.get("missing_reasons_json"))
    metrics = {metric: values.get(metric) for metric in REQUIRED_METRICS if metric in values}
    metrics["debt_to_cash_ratio"] = values.get("debt_to_cash_ratio")
    missing_metrics = [metric for metric in REQUIRED_METRICS if metric not in metrics]
    entry.update(
        {
            "status": "PUBLIC_FILING_EVIDENCE_AVAILABLE" if not missing_metrics else "LIMITED_PUBLIC_EVIDENCE",
            "reason": "" if not missing_metrics else "MISSING_METRICS:" + ",".join(missing_metrics),
            "metrics": metrics,
            "missing_reasons": {metric: missing.get(metric, "MISSING_FROM_SNAPSHOT") for metric in missing_metrics},
            "snapshot_id": snapshot.get("snapshot_id"),
            "period_basis": snapshot.get("period_basis"),
            "period_start": snapshot.get("period_start"),
            "period_end": snapshot.get("period_end"),
            "availability_at": snapshot.get("availability_at"),
            "source_accessions": _json(snapshot.get("source_accessions_json")),
            "source_urls": _json(snapshot.get("source_urls_json")),
        }
    )
    return entry


def build_counterparty_health_report(
    relationships: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]],
    sources: Sequence[Mapping[str, object]],
    *,
    as_of: datetime,
) -> dict[str, object]:
    """Build a conservative, point-in-time evidence view for relationships."""

    cutoff = _utc(as_of)
    if cutoff is None:
        raise ValueError("as_of must be a datetime")
    source_by_name = {_norm(row.get("counterparty")): row for row in sources}
    entries: list[dict[str, object]] = []
    for relationship in relationships:
        metadata = _json(relationship.get("metadata_json"))
        base = {
            "relationship_id": str(relationship.get("relationship_id") or ""),
            "company_ticker": str(relationship.get("ticker") or ""),
            "counterparty": str(relationship.get("counterparty") or ""),
            "health_assessment": "NOT_RATED",
            "analysis_only": True,
            "ranking_modified": False,
            "decision_modified": False,
        }
        if metadata.get("counterparty_identity_status") != "IDENTIFIED":
            entries.append({**base, "status": "COUNTERPARTY_NOT_IDENTIFIED", "reason": "NO_VERIFIED_IDENTITY"})
            continue
        source = source_by_name.get(_norm(base["counterparty"]))
        if source is None:
            entries.append({**base, "status": "IDENTITY_NOT_MAPPED", "reason": "NO_EXPLICIT_COUNTERPARTY_MANIFEST"})
        elif source.get("entity_type") == "PUBLIC":
            entries.append(_public_entry(relationship, source, snapshots, cutoff))
        else:
            entries.append(
                {
                    **base,
                    "entity_type": source.get("entity_type"),
                    "identity_source": dict(source),
                    "status": "LIMITED_PUBLIC_DOCUMENT",
                    "reason": "PRIVATE_OR_UNVERIFIED_COUNTERPARTY_NO_HEALTH_RATING",
                }
            )
    status_counts: dict[str, int] = {}
    for entry in entries:
        status = str(entry["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    report = {
        "report_version": COUNTERPARTY_HEALTH_REPORT_VERSION,
        "as_of": cutoff.isoformat(),
        "status": "READY" if entries else "INSUFFICIENT_DATA",
        "reason": "" if entries else "NO_SUPPLY_CHAIN_RELATIONSHIPS",
        "entries": entries,
        "status_counts": dict(sorted(status_counts.items())),
        "analysis_only": True,
        "ranking_modified": False,
        "decision_modified": False,
        "activation_allowed": False,
    }
    report["report_id"] = _stable_id(report)
    return report


__all__ = [
    "COUNTERPARTY_HEALTH_REPORT_VERSION",
    "build_counterparty_health_report",
    "parse_counterparty_health_sources",
]
