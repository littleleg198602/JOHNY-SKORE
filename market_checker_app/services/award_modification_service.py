from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
import re
from typing import Iterable, Mapping


SOURCE_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
AUDIT_VERSION = "usaspending-observed-obligation-v1"
_SCOPE_FIELDS = (
    "award_id", "award_start", "awarding_agency", "contract_award_type",
    "relationship_effective_from", "relationship_effective_to",
    "relationship_known_at", "uei_evidence_url", "relationship_evidence_url",
    "continuity_evidence_url",
)


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Observation timestamps need an explicit timezone")
    return value.astimezone(timezone.utc)


def _details(record: Mapping) -> dict:
    value = record.get("details_json", record.get("details"))
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Award observation needs details")
    return value


def _amount(value: object) -> Decimal | None:
    # Strings and booleans are not reported monetary numbers. Preserve negative
    # cumulative amounts; de-obligation and corrections must not disappear.
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid reported amount")
    return Decimal(str(value))


def _revision(value: object) -> tuple[datetime, datetime] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", value):
        raise ValueError("Invalid source revision")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        first = datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
        return first, first + timedelta(days=1) - timedelta(microseconds=1)
    point = _utc(value)
    return point, point


@dataclass(frozen=True)
class _Observation:
    record: Mapping
    details: dict
    observed_at: datetime
    available_at: datetime
    known_at: datetime
    obligation: Decimal | None
    outlays: Decimal | None
    currency: str
    revision: tuple[datetime, datetime] | None


def _observation(record: Mapping) -> _Observation:
    details = _details(record)
    if (record.get("source") != "usaspending" or record.get("source_url") != SOURCE_URL
            or record.get("verification_status") != "SOURCE_VERIFIED"
            or details.get("stage") != "prime_contract"):
        raise ValueError("Wrong source, verification or prime-award scope")
    award, uei, subject = details.get("generated_internal_id"), details.get("uei"), record.get("subject_id")
    if (not isinstance(award, str) or not award.strip()
            or not isinstance(uei, str) or not re.fullmatch(r"[A-Z0-9]{12}", uei)
            or not isinstance(subject, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", subject)
            or record.get("source_object_id") != f"{uei}:{award}"
            or not isinstance(details.get("recipient_name"), str) or not details["recipient_name"].strip()):
        raise ValueError("Missing exact award/recipient identity")
    observed = _utc(record.get("first_observed_at", record.get("observed_at")))
    available = _utc(record.get("available_at"))
    known = _utc(details.get("relationship_known_at"))
    if known > available or available > observed:
        raise ValueError("Identity or source unavailable at observation")
    if record.get("published_at") is not None and _utc(record["published_at"]) > available:
        raise ValueError("Source unavailable before publication")
    start = date.fromisoformat(details["award_start"])
    first = date.fromisoformat(details["relationship_effective_from"])
    last = date.fromisoformat(details["relationship_effective_to"]) if details.get("relationship_effective_to") else None
    if start < first or start > observed.date() or (last and (last < first or start > last)):
        raise ValueError("Award outside documented recipient relationship")
    # Legacy prime-contract findings already name both amount fields in USD.
    # An explicit contradictory currency is rejected, never silently converted.
    currency = details.get("currency", "USD")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Invalid reported currency")
    revision = _revision(details.get("last_modified_date"))
    if revision and revision[0] > observed:
        raise ValueError("Source revision after observation")
    return _Observation(record, details, observed, available, known,
                        _amount(details.get("reported_award_amount_usd")),
                        _amount(details.get("reported_total_outlays_usd")), currency, revision)


def _related(record: Mapping, award_id: str) -> bool:
    # Unrelated findings cannot block an award. An exact generated ID with bad
    # scope or recipient metadata must remain visible as a comparison blocker.
    try:
        return _details(record).get("generated_internal_id") == award_id
    except (ValueError, TypeError):
        return isinstance(record.get("source_object_id"), str) and record["source_object_id"].endswith(":" + award_id)


def _summary(item: _Observation) -> dict:
    return {"finding_id": item.record.get("finding_id"),
            "available_at": item.available_at.isoformat(),
            "observed_at": item.observed_at.isoformat(),
            "source_modified_at": item.details.get("last_modified_date"),
            "cumulative_obligation_usd": float(item.obligation) if item.obligation is not None else None,
            "cumulative_outlays_usd": float(item.outlays) if item.outlays is not None else None}


def _delta(current: Decimal, previous: Decimal) -> tuple[float, str]:
    with localcontext() as context:
        context.prec = max(len(current.as_tuple().digits), len(previous.as_tuple().digits),
                           current.adjusted() - min(current.as_tuple().exponent, previous.as_tuple().exponent) + 2,
                           previous.adjusted() - min(current.as_tuple().exponent, previous.as_tuple().exponent) + 2, 28)
        delta = current - previous
    value = float(delta)
    if not math.isfinite(value):
        raise ValueError("Delta exceeds finite numeric range")
    return value, format(delta, "f")


def audit_award_modification(current: Mapping, observations: Iterable[Mapping], *, as_of: datetime) -> dict:
    """Compare observed cumulative snapshots; never infer an amendment transaction.

    This function performs no I/O and changes neither source observations nor
    scoring. Last Modified Date orders source revisions only: it is not public
    availability, a modification number, or evidence of a new contract win.
    """
    clock = _utc(as_of)
    audit = {"schema_version": 1, "method": AUDIT_VERSION,
             "status": "INVALID_OBSERVATION", "comparable": False,
             "obligation_delta_usd": None, "outlays_delta_usd": None,
             "source_revision_order_confirmed": False,
             "delta_is_transaction": False, "new_award_inferred": False,
             "revenue_inferred": False, "modification_history_complete": False}
    try:
        incoming = _observation(current)
    except (ValueError, KeyError, TypeError, OverflowError, InvalidOperation):
        return audit
    audit.update({"generated_internal_id": incoming.details["generated_internal_id"],
                  "recipient_uei": incoming.details["uei"], "currency": incoming.currency,
                  "current": _summary(incoming)})
    if incoming.available_at > clock or incoming.observed_at > clock:
        return {**audit, "status": "NOT_AVAILABLE_AS_OF"}
    # Auditing an earlier observation today must not consume later knowledge.
    cutoff = min(clock, incoming.observed_at)
    candidates: list[_Observation] = []
    for record in observations:
        if not _related(record, incoming.details["generated_internal_id"]):
            continue
        if current.get("finding_id") and record.get("finding_id") == current["finding_id"]:
            continue
        try:
            observed = _utc(record.get("first_observed_at", record.get("observed_at")))
            available = _utc(record.get("available_at"))
            if observed > cutoff or available > cutoff:
                continue
            candidate = _observation(record)
        except (ValueError, KeyError, TypeError, OverflowError, InvalidOperation):
            return {**audit, "status": "INVALID_PREDECESSOR",
                    "blocking_finding_id": record.get("finding_id")}
        candidates.append(candidate)
    if not candidates:
        return {**audit, "status": "BASELINE_OBSERVATION"}
    latest = max((item.observed_at, item.available_at) for item in candidates)
    peers = [item for item in candidates if (item.observed_at, item.available_at) == latest]
    # Finding IDs or retrieval timestamps cannot manufacture a revision order.
    peer_details = [{key: value for key, value in item.details.items() if key != "modification_audit"}
                    for item in peers]
    if any(details != peer_details[0] for details in peer_details[1:]):
        return {**audit, "status": "AMBIGUOUS_PREDECESSOR",
                "blocking_finding_ids": sorted(str(item.record.get("finding_id", "")) for item in peers)}
    previous = min(peers, key=lambda item: str(item.record.get("finding_id", "")))
    audit["previous"] = _summary(previous)
    if (incoming.record["subject_id"] != previous.record["subject_id"]
            or any(incoming.details.get(key) != previous.details.get(key) for key in _SCOPE_FIELDS)):
        return {**audit, "status": "SCOPE_CHANGED"}
    if (incoming.details["uei"] != previous.details["uei"]
            or incoming.details["recipient_name"].casefold().split() != previous.details["recipient_name"].casefold().split()):
        return {**audit, "status": "RECIPIENT_CHANGED"}
    if incoming.currency != previous.currency:
        return {**audit, "status": "CURRENCY_CHANGED"}
    if incoming.currency != "USD":
        return {**audit, "status": "UNSUPPORTED_CURRENCY"}
    if incoming.revision is None or previous.revision is None:
        return {**audit, "status": "MISSING_SOURCE_REVISION"}
    if incoming.revision[1] < previous.revision[0]:
        return {**audit, "status": "OUT_OF_ORDER_SOURCE_REVISION"}
    if incoming.obligation is None or previous.obligation is None:
        return {**audit, "status": "MISSING_OBLIGATION"}
    same_revision = incoming.revision[0] <= previous.revision[1]
    if incoming.observed_at == previous.observed_at and (
            incoming.obligation != previous.obligation or incoming.outlays != previous.outlays):
        return {**audit, "status": "AMBIGUOUS_OBSERVATION_ORDER"}
    try:
        obligation, exact = _delta(incoming.obligation, previous.obligation)
        outlays = _delta(incoming.outlays, previous.outlays) if incoming.outlays is not None and previous.outlays is not None else None
    except (ValueError, OverflowError, InvalidOperation):
        return {**audit, "status": "DELTA_OUT_OF_RANGE"}
    changed = obligation != 0 or (outlays is not None and outlays[0] != 0)
    return {**audit,
            "status": "OBSERVED_RESTATEMENT" if same_revision and changed else "OBSERVED_REVISION" if changed else "UNCHANGED_TOTALS",
            "comparable": True, "source_revision_order_confirmed": not same_revision,
            "obligation_delta_usd": obligation, "obligation_delta_usd_decimal": exact,
            "obligation_direction": "INCREASE" if obligation > 0 else "DECREASE" if obligation < 0 else "UNCHANGED",
            "outlays_delta_usd": outlays[0] if outlays else None,
            "outlays_delta_usd_decimal": outlays[1] if outlays else None,
            "outlays_comparison_status": "COMPARABLE" if outlays else "MISSING_AMOUNT"}
