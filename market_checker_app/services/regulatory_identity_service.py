"""Reviewed, dated EPA FRS facility ownership; never issuer legal liability.

This optional input is deliberately empty by default. A facility name or an
EPA enforcement field cannot supply the missing facility-to-issuer link.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference


RESOLVER_VERSION = "epa-dated-facility-owner-v1"
MAX_MANIFEST_BYTES = 5_000_000
EVIDENCE_PURPOSES = {"FACILITY_IDENTITY", "OWNERSHIP_RELATIONSHIP", "ISSUER_IDENTITY"}


def _aware(value: str | datetime) -> datetime:
    clock = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Regulatory identity needs an explicit timezone")
    return clock.astimezone(timezone.utc)


def _text(value: object, *, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Regulatory identity requires exact nonempty fields")
    return value.strip()


def _day(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Regulatory identity dates must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _effective(row: dict, on_date: date) -> bool:
    return (_day(row["effective_from"]) <= on_date
            and (not row.get("effective_to") or on_date < _day(row["effective_to"])))


def _validate_record(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Regulatory identity must be an object")
    row = deepcopy(raw)
    if (row.get("source") != "epa" or row.get("kind") != "epa_frs_facility_owner"
            or row.get("relationship") not in {"DIRECT_OWNER", "INDIRECT_OWNER"}
            or row.get("time_basis") != "OBSERVED_SNAPSHOT"
            or row.get("verification_status") not in {"VERIFIED", "UNVERIFIED"}):
        raise ValueError("Regulatory identity source, role, time basis or review mismatch")
    for key in ("record_id", "owner_entity_name"):
        row[key] = _text(row.get(key))
    row["facility_name"] = _text(row.get("facility_name"), limit=500)
    if (not isinstance(row.get("ticker"), str)
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", row["ticker"])
            or not isinstance(row.get("issuer_cik"), str)
            or not re.fullmatch(r"\d{10}", row["issuer_cik"])
            or not isinstance(row.get("frs_registry_id"), str)
            or not re.fullmatch(r"\d{12}", row["frs_registry_id"])):
        raise ValueError("Regulatory identity needs exact ticker, SEC CIK and 12-digit FRS ID")
    start = _day(row.get("effective_from"))
    end = _day(row["effective_to"]) if row.get("effective_to") is not None else None
    if end is not None and end <= start:
        raise ValueError("Regulatory effective interval must be increasing")
    known = _aware(row.get("known_at"))
    verified = row["verification_status"] == "VERIFIED"
    reviewed = None
    if verified:
        row["reviewer"] = _text(row.get("reviewer"))
        reviewed = _aware(row.get("reviewed_at"))
        if reviewed > known:
            raise ValueError("Regulatory identity cannot be known before review")
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not 0 <= len(evidence) <= 10:
        raise ValueError("Regulatory identity evidence must be bounded")
    purposes = set()
    for item in evidence:
        if not isinstance(item, dict) or item.get("purpose") not in EVIDENCE_PURPOSES:
            raise ValueError("Regulatory evidence needs a declared purpose")
        item["locator"] = _text(item.get("locator"))
        item["supports"] = _text(item.get("supports"), limit=3000)
        parsed = urlsplit(public_https_reference(item.get("url")))
        if parsed.port not in {None, 443} or parsed.fragment:
            raise ValueError("Regulatory evidence must use its public official HTTPS endpoint")
        own_sec = (parsed.hostname == "www.sec.gov"
                   and parsed.path.startswith(f"/Archives/edgar/data/{int(row['issuer_cik'])}/"))
        official_epa = parsed.hostname in {"echo.epa.gov", "echodata.epa.gov", "www.epa.gov", "enviro.epa.gov"}
        if item["purpose"] == "ISSUER_IDENTITY" and not own_sec:
            raise ValueError("Regulatory issuer evidence must cite its own SEC CIK")
        if item["purpose"] == "FACILITY_IDENTITY":
            if not official_epa or row["frs_registry_id"] not in item["locator"]:
                raise ValueError("Regulatory facility evidence must cite EPA and the exact FRS ID")
        if item["purpose"] == "OWNERSHIP_RELATIONSHIP" and not (own_sec or official_epa):
            raise ValueError("Regulatory ownership evidence must cite EPA or its own SEC filing")
        observed = _aware(item.get("observed_at"))
        if observed > known:
            raise ValueError("Regulatory identity cannot be known before its evidence")
        if reviewed is not None and observed > reviewed:
            raise ValueError("Regulatory review cannot precede its evidence")
        purposes.add(item["purpose"])
    if verified and purposes != EVIDENCE_PURPOSES:
        raise ValueError("Reviewed ownership needs facility, ownership and issuer evidence")
    return row


class RegulatoryIdentityRegistry:
    """Optional reviewed input, independent of name leads and score weights."""

    def __init__(self, records: list[dict] | tuple[dict, ...] = ()):
        if not isinstance(records, (list, tuple)) or len(records) > 10000:
            raise ValueError("Regulatory identity manifest must be a bounded list")
        self._records = tuple(_validate_record(record) for record in records)
        ids = [row["record_id"] for row in self._records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate regulatory identity record ID")

    @classmethod
    def load(cls, path: Path) -> "RegulatoryIdentityRegistry":
        with Path(path).open("rb") as stream:
            body = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(body) > MAX_MANIFEST_BYTES:
            raise ValueError("Regulatory identity manifest exceeds byte budget")
        raw = json.loads(body)
        if (not isinstance(raw, dict) or isinstance(raw.get("schema_version"), bool)
                or raw.get("schema_version") != 1):
            raise ValueError("Unsupported regulatory identity manifest")
        return cls(raw.get("records"))

    def _known(self, as_of: datetime) -> tuple[dict, ...]:
        clock = _aware(as_of)
        return tuple(row for row in self._records if _aware(row["known_at"]) <= clock)

    def _own_current(self, ticker: str, cik: str, *, as_of: datetime) -> tuple[dict, ...]:
        clock = _aware(as_of)
        return tuple(row for row in self._known(clock)
                     if (row["ticker"], row["issuer_cik"]) == (ticker, cik)
                     and _effective(row, clock.date()))

    def query_names(self, ticker: str, cik: str, *, as_of: datetime) -> tuple[str, ...]:
        # Unreviewed records are searchable leads, never ownership evidence.
        rows = sorted(self._own_current(ticker, cik, as_of=as_of), key=lambda row: row["record_id"])
        return tuple(dict.fromkeys(row["facility_name"] for row in rows))

    def reviewed_facility_ids(self, ticker: str, cik: str, *, as_of: datetime) -> frozenset[str]:
        return frozenset(row["frs_registry_id"] for row in self._own_current(ticker, cik, as_of=as_of)
                         if row["verification_status"] == "VERIFIED")

    def fingerprint(self, ticker: str, cik: str, *, as_of: datetime) -> str:
        clock = _aware(as_of)
        known = [row for row in self._known(clock) if _day(row["effective_from"]) <= clock.date()]
        own_ids = {row["frs_registry_id"] for row in known
                   if (row["ticker"], row["issuer_cik"]) == (ticker, cik)}
        # A competing owner affects this issuer's old resolution too. Expiry
        # changes the hash once; observation heartbeats and unrelated links do not.
        records = [{"record": row, "effective_at_observation": _effective(row, clock.date())}
                   for row in sorted(known, key=lambda row: row["record_id"])
                   if row["frs_registry_id"] in own_ids]
        payload = {"resolver_version": RESOLVER_VERSION, "records": records}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def identity_versions(self, store: ScoutStore, *, as_of: datetime) -> dict[str, str]:
        clock = _aware(as_of)
        with store._connect() as conn:
            rows = conn.execute("""
                SELECT subject_id, cik, company_name FROM scout_sec_identities
                WHERE status='ACTIVE' AND first_observed_at<=? ORDER BY subject_id
            """, (clock.isoformat(),)).fetchall()
        versions = {}
        for row in rows:
            payload = {"source": "epa", "sec_identity": dict(row),
                       "dated_records_sha256": self.fingerprint(row["subject_id"], row["cik"], as_of=clock)}
            versions[row["subject_id"]] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return versions

    def resolve(self, *, ticker: str, cik: str, frs_registry_id: str,
                facility_name: str, observation_date: date, as_of: datetime) -> dict:
        clock = _aware(as_of)
        denied = {"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False,
                  "issuer_facility_relationship_verified": False, "identity_evidence": None,
                  "missing_evidence": "reviewed dated exact-FRS facility ownership to the issuer",
                  "facility_regulatory_action_attribution_allowed": False,
                  "issuer_conviction_verified": False, "issuer_fine_verified": False,
                  "environmental_liability_verified": False, "complete_facility_group_verified": False}
        if (not isinstance(observation_date, date) or isinstance(observation_date, datetime)
                or observation_date > clock.date() or not isinstance(frs_registry_id, str)
                or not re.fullmatch(r"\d{12}", frs_registry_id)
                or not isinstance(facility_name, str) or not facility_name.strip()):
            return dict(denied, identity_resolution="INVALID_FACILITY")
        candidates = [row for row in self._records if row["frs_registry_id"] == frs_registry_id]
        if not candidates:
            return dict(denied, identity_resolution="UNMAPPED_FACILITY")
        known = [row for row in candidates if _aware(row["known_at"]) <= clock]
        if not known:
            return dict(denied, identity_resolution="NOT_YET_KNOWN")
        active = [row for row in known if _effective(row, observation_date)]
        if not active:
            return dict(denied, identity_resolution="OUTSIDE_EFFECTIVE_PERIOD")
        if len(active) != 1:
            return dict(denied, identity_resolution="AMBIGUOUS_FACILITY")
        row = active[0]
        if (row["ticker"], row["issuer_cik"]) != (ticker, cik):
            return dict(denied, identity_resolution="WRONG_ISSUER")
        if row["facility_name"].casefold() != facility_name.strip().casefold():
            return dict(denied, identity_resolution="FACILITY_NAME_MISMATCH")
        if row["verification_status"] != "VERIFIED":
            return dict(denied, identity_resolution="UNREVIEWED_OWNERSHIP")
        evidence = deepcopy(row)
        evidence["matched_observation_date"] = observation_date.isoformat()
        return dict(denied, identity_status="DATED_FACILITY_OWNERSHIP_VERIFIED",
                    issuer_attribution_allowed=True, issuer_facility_relationship_verified=True,
                    identity_resolution="EXACT_DATED_FACILITY", identity_evidence=evidence,
                    missing_evidence=None)
