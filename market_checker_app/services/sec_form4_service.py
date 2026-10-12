"""Bounded, issuer-checked SEC Form 4 observations, without trade inference."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import re
from xml.etree import ElementTree

from market_checker_app.collectors.sec_edgar_client import SecFiling, SecInsiderTransaction


PARSER_VERSION = "sec-form4-observation-v1"
MAX_FORM4_BYTES = 2_000_000
MAX_FORM4_NODES = 20_000
MAX_FORM4_TRANSACTIONS = 1_000
MAX_FORM4_OWNERS = 32
MAX_FORM4_FOOTNOTES = 512


class Form4ValidationError(ValueError):
    """A primary document cannot safely be attributed or parsed completely."""


def _cik(value: object) -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,10}", raw) or int(raw) == 0:
        raise Form4ValidationError("Form 4 requires a valid nonzero numeric CIK")
    return raw.zfill(10)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Form4ValidationError("Form 4 knowledge time must have a timezone")
    return value.astimezone(timezone.utc)


def _value(node: ElementTree.Element, path: str) -> str | None:
    values = node.findall(path)
    if len(values) > 1:
        raise Form4ValidationError(f"Form 4 duplicate scalar: {path}")
    value = " ".join("".join(values[0].itertext()).split()) if values else ""
    if len(value) > 8_000:
        raise Form4ValidationError(f"Form 4 scalar exceeds limit: {path}")
    return value or None


def _date(value: object, *, field: str, required: bool = True) -> date | None:
    if value is None and not required:
        return None
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(value)):
            raise ValueError("Expected ISO calendar date")
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise Form4ValidationError(f"Form 4 invalid or missing {field}") from exc


def _number(value: str | None) -> float | None:
    if value is None:
        return None  # An omitted/footnoted price is not a zero price.
    try:
        result = float(value.replace(",", ""))
    except ValueError as exc:
        raise Form4ValidationError("Form 4 malformed numeric value") from exc
    if not math.isfinite(result) or result < 0:
        raise Form4ValidationError("Form 4 negative or non-finite numeric value")
    return result


def _boolean(value: str | None) -> bool | None:
    if value is None:
        return None
    if value.lower() in {"1", "true"}:
        return True
    if value.lower() in {"0", "false"}:
        return False
    raise Form4ValidationError("Form 4 malformed boolean value")


def extract_form4_observation(
    document: bytes, *, filing: SecFiling, issuer_cik: str, observed_at: datetime,
) -> dict[str, object] | None:
    """Keep exact issuer, all owners, security titles and as-filed row context.

    Joint reports remain unallocated; one row is never multiplied by the owner
    count. A CIK printed in a filing is not independent legal-person verification.
    Issuer CIK does not identify the watchlist ticker's particular share class.
    Invalid/oversized content raises before the durable job can report success.
    """
    form = filing.form.strip().upper()
    if form.removesuffix("/A") != "4":
        return None
    clock = _utc(observed_at)
    filed = _utc(filing.filed_at)
    if filed > clock:
        raise Form4ValidationError("Form 4 filing is unavailable at knowledge time")
    if len(document) > MAX_FORM4_BYTES or re.search(
        br"<!\s*(?:DOCTYPE|ENTITY)\b", document.replace(b"\x00", b""), re.I,
    ):
        raise Form4ValidationError("Form 4 document exceeds limits or contains a DTD/entity")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise Form4ValidationError("Form 4 primary document is not ownership XML") from exc
    for count, node in enumerate(root.iter(), start=1):
        if count > MAX_FORM4_NODES:
            raise Form4ValidationError("Form 4 XML node limit exceeded")
        node.tag = str(node.tag).rsplit("}", 1)[-1]
    if root.tag != "ownershipDocument" or _value(root, "documentType") != form:
        raise Form4ValidationError("Form 4 XML root or document type conflicts with filing")
    issuers = root.findall("issuer")
    if len(issuers) != 1 or _cik(_value(issuers[0], "issuerCik")) != _cik(issuer_cik):
        raise Form4ValidationError("Form 4 issuer CIK conflicts with SEC index")
    period = _date(_value(root, "periodOfReport"), field="periodOfReport")
    if period > min(filed.date(), clock.date()):
        raise Form4ValidationError("Form 4 report date is in the future")
    original = _date(_value(root, "dateOfOriginalSubmission"),
                     field="dateOfOriginalSubmission", required=False)
    if original is not None and original > filed.date():
        raise Form4ValidationError("Form 4 original submission date is in the future")

    owner_nodes = root.findall("reportingOwner")
    if not 1 <= len(owner_nodes) <= MAX_FORM4_OWNERS:
        raise Form4ValidationError("Form 4 requires a bounded nonempty owner list")
    owners: list[dict[str, object]] = []
    for owner in owner_nodes:
        owner_name = _value(owner, "reportingOwnerId/rptOwnerName")
        owner_cik = _cik(_value(owner, "reportingOwnerId/rptOwnerCik"))
        if not owner_name or any(row["cik"] == owner_cik for row in owners):
            raise Form4ValidationError("Form 4 owner name missing or owner CIK duplicated")
        owners.append({
            "cik": owner_cik, "name": owner_name, "identity_status": "AS_FILED_CIK",
            "identity_independently_verified": False,
            **{role: _boolean(_value(owner, f"reportingOwnerRelationship/{tag}"))
               for role, tag in (("is_director", "isDirector"), ("is_officer", "isOfficer"),
                                 ("is_ten_percent_owner", "isTenPercentOwner"), ("is_other", "isOther"))},
            "officer_title": _value(owner, "reportingOwnerRelationship/officerTitle"),
        })

    footnotes: dict[str, str] = {}
    for footnote in root.findall("footnotes/footnote"):
        key = footnote.get("id", "").strip()
        text = " ".join("".join(footnote.itertext()).split())
        if not key or key in footnotes or len(text) > 8_000 or len(footnotes) >= MAX_FORM4_FOOTNOTES:
            raise Form4ValidationError("Form 4 footnotes are ambiguous or exceed limits")
        footnotes[key] = text
    rows: list[dict[str, object]] = []
    holding_count = 0
    for table_name, transaction_name, derivative in (
        ("nonDerivativeTable", "nonDerivativeTransaction", False),
        ("derivativeTable", "derivativeTransaction", True),
    ):
        tables = root.findall(table_name)
        if len(tables) > 1:
            raise Form4ValidationError("Form 4 duplicate transaction table")
        if not tables:
            continue
        for ordinal, transaction in enumerate(tables[0], start=1):
            if transaction.tag == transaction_name.replace("Transaction", "Holding"):
                holding_count += 1
                continue
            if transaction.tag != transaction_name:
                raise Form4ValidationError("Form 4 unknown transaction-table entry")
            if len(rows) >= MAX_FORM4_TRANSACTIONS:
                raise Form4ValidationError("Form 4 transaction limit exceeded")
            transaction_date = _date(_value(transaction, "transactionDate/value"), field="transactionDate")
            if transaction_date > min(filed.date(), clock.date()):
                raise Form4ValidationError("Form 4 transaction date is in the future")
            refs = sorted({node.get("id", "").strip()
                           for node in transaction.iter() if node.tag == "footnoteId"})
            if any(key not in footnotes for key in refs):
                raise Form4ValidationError("Form 4 transaction references a missing footnote")
            rows.append({
                "row_locator": f"{table_name}/{transaction_name}:{ordinal}",
                "security_title": _value(transaction, "securityTitle/value"),
                "transaction_date": transaction_date.isoformat(),
                "transaction_code": (_value(transaction, "transactionCoding/transactionCode") or "UNKNOWN").upper(),
                "acquired_disposed": _value(transaction, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                "shares": _number(_value(transaction, "transactionAmounts/transactionShares/value")),
                "price_per_share": _number(_value(transaction, "transactionAmounts/transactionPricePerShare/value")),
                "shares_owned_after": _number(_value(transaction, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                "ownership_nature": _value(transaction, "ownershipNature/directOrIndirectOwnership/value"),
                "nature_of_ownership": _value(transaction, "ownershipNature/natureOfOwnership/value"),
                "derivative": derivative,
                "equity_swap_involved": _boolean(_value(transaction, "transactionCoding/equitySwapInvolved")),
                "footnote_ids": refs,
                "owner_attribution": "SINGLE_AS_FILED_OWNER" if len(owners) == 1 else "JOINT_REPORT_NOT_ALLOCATED",
                "instrument_identity_verified": False,
            })
    return {
        "parser_version": PARSER_VERSION, "form": form,
        "issuer_cik": _cik(issuer_cik), "issuer_name": _value(issuers[0], "issuerName"),
        "issuer_trading_symbol": _value(issuers[0], "issuerTradingSymbol"),
        "period_of_report": period.isoformat(),
        "original_submission_date": original.isoformat() if original else None,
        "is_amendment": form.endswith("/A"),
        "rule_10b5_1_checkbox": _boolean(_value(root, "aff10b5One")),
        "rule_10b5_1_scope": "DOCUMENT_CHECKBOX_NOT_PER_TRANSACTION",
        "remarks": _value(root, "remarks"),
        "reporting_owners": owners, "transactions": rows, "footnotes": footnotes,
        "non_transaction_holdings": holding_count,
        "observed_at": clock.isoformat(), "source_url": filing.filing_url,
        "accession_number": filing.accession_number,
        "issuer_identity_checked_against_index": True,
        "reporting_owner_identity_verified": False, "instrument_identity_verified": False,
        "execution_venue_verified": False, "ownership_change_interpreted": False,
        "scoring_applied": False,
    }


@dataclass(frozen=True, slots=True)
class ObservedForm4Transaction(SecInsiderTransaction):
    issuer_cik: str
    security_title: str | None
    row_locator: str
    rule_10b5_1_checkbox: bool | None
    is_amendment: bool
    observed_at: datetime
    footnotes: tuple[tuple[str, str], ...]
    remarks: str | None


def form4_transactions_as_of(
    observation: Mapping[str, object], *, issuer_cik: str, accession_number: str,
    source_url: str, knowledge_at: datetime,
) -> list[ObservedForm4Transaction]:
    """Rehydrate issuer-checked single-owner rows only after first observation."""
    if (observation.get("parser_version") != PARSER_VERSION
            or observation.get("issuer_identity_checked_against_index") is not True
            or observation.get("issuer_cik") != _cik(issuer_cik)
            or observation.get("accession_number") != accession_number
            or observation.get("source_url") != source_url):
        raise Form4ValidationError("Stored Form 4 identity/provenance conflict")
    observed_at = _utc(datetime.fromisoformat(str(observation.get("observed_at"))))
    clock = _utc(knowledge_at)
    if observed_at > clock:
        return []
    owners, rows = observation.get("reporting_owners"), observation.get("transactions")
    if not isinstance(owners, list) or not isinstance(rows, list):
        raise Form4ValidationError("Stored Form 4 rows/owners missing")
    if not 1 <= len(owners) <= MAX_FORM4_OWNERS or len(rows) > MAX_FORM4_TRANSACTIONS:
        raise Form4ValidationError("Stored Form 4 row/owner limits exceeded")
    if len(owners) != 1:
        return []  # The XML does not allocate joint-report rows to one owner.
    owner = owners[0]
    if not isinstance(owner, Mapping):
        raise Form4ValidationError("Stored Form 4 owner invalid")
    footnotes = observation.get("footnotes")
    if not isinstance(footnotes, Mapping):
        raise Form4ValidationError("Stored Form 4 footnotes missing")
    result: list[ObservedForm4Transaction] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("owner_attribution") != "SINGLE_AS_FILED_OWNER":
            raise Form4ValidationError("Stored Form 4 owner attribution conflict")
        transaction_date = datetime.combine(_date(row.get("transaction_date"), field="transactionDate"),
                                            datetime.min.time(), tzinfo=timezone.utc)
        if transaction_date > clock:
            continue
        result.append(ObservedForm4Transaction(
            accession_number=accession_number, owner_cik=_cik(owner.get("cik")),
            owner_name=str(owner["name"]), transaction_date=transaction_date,
            transaction_code=str(row["transaction_code"]), acquired_disposed=row.get("acquired_disposed"),
            shares=row.get("shares"), price_per_share=row.get("price_per_share"),
            shares_owned_after=row.get("shares_owned_after"), ownership_nature=row.get("ownership_nature"),
            derivative=bool(row.get("derivative")), source_url=source_url,
            issuer_cik=_cik(issuer_cik), security_title=row.get("security_title"),
            row_locator=str(row["row_locator"]), rule_10b5_1_checkbox=observation.get("rule_10b5_1_checkbox"),
            is_amendment=bool(observation.get("is_amendment")), observed_at=observed_at,
            footnotes=tuple((key, str(footnotes[key])) for key in row.get("footnote_ids", [])),
            remarks=observation.get("remarks"),
        ))
    return result
