"""Dated, object-specific healthcare relationships, independent of name leads.

The manifest documents identities and roles. It never establishes financial
impact, a drug's efficacy, or ownership of every product in a corporate group.
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


DEFAULT_HEALTHCARE_IDENTITIES = (Path(__file__).resolve().parents[1] / "data"
                                / "verified_healthcare_identities.json")
KINDS = {
    "fda_crl": ("fda", {"application_number", "letter_file_name", "letter_date"},
                {"APPLICATION_HOLDER"}, "EVENT_DATE"),
    "fda_recall_product": ("fda", {"recall_number", "product_type", "application_number", "product_ndc"},
                           {"RECALLING_PRODUCT_ENTITY"}, "EVENT_DATE"),
    "clinical_trial_sponsor": ("clinicaltrials", {"nct_id", "role", "intervention_name", "record_version_date"},
                               {"LEAD_SPONSOR", "COLLABORATOR"}, "EVENT_DATE"),
    "cms_provider_owner": ("cms", {"enrollment_id", "owner_associate_id", "provider_name",
                                  "association_date", "owner_role_code"},
                           {"DIRECT_OWNER", "INDIRECT_OWNER"}, "OBSERVED_SNAPSHOT"),
}


def _aware(value: str | datetime) -> datetime:
    clock = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Healthcare identity needs an explicit timezone")
    return clock.astimezone(timezone.utc)


def normalize_application(value: str) -> str | None:
    """Normalize only documented application syntax; retain Original/S scopes."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(NDA|ANDA|BLA)\s*(\d{6})(?:\s*/\s*(Original\s*\d+|S-?\d{3}))?",
                         value.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    suffix = re.sub(r"\s+|-", "", match[3]).upper() if match[3] else ""
    return match[1].upper() + match[2] + ("/" + suffix if suffix else "")


def application_values(value: object) -> tuple[str, ...]:
    """FDA CRL uses arrays in live responses; older clients may use a string."""
    values = [value] if isinstance(value, str) else value
    if (not isinstance(values, list) or not 1 <= len(values) <= 20
            or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in values)):
        raise ValueError("Malformed FDA application identifiers")
    return tuple(dict.fromkeys(v.strip() for v in values))


def exact_query_names(issuer_name: str, documented_names: tuple[str, ...]) -> tuple[str, ...]:
    """Only trim/case-fold for duplicate queries; do not infer spelling aliases."""
    names: dict[str, str] = {}
    for name in (issuer_name, *documented_names):
        names.setdefault(name.strip().casefold(), name.strip())
    return tuple(names.values())


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError("Healthcare identity requires exact nonempty fields")
    return value.strip()


def clinical_trial_version(row: dict) -> dict:
    """Validate publisher clocks and pin only the observed sponsor/object scope.

    A posting date is day-precision publisher metadata, not an immutable
    publisher version ID or the time our application first knew the record.
    The digest identifies this sponsor/intervention snapshot; unrelated
    outcomes, addresses and free text are deliberately outside its scope.
    """
    protocol = row.get("protocolSection")
    if not isinstance(protocol, dict):
        raise ValueError("Missing clinical trial protocol")
    identification = protocol.get("identificationModule", {})
    sponsors = protocol.get("sponsorCollaboratorsModule", {})
    status = protocol.get("statusModule", {})
    interventions = protocol.get("armsInterventionsModule", {})
    if not all(isinstance(section, dict) for section in
               (identification, sponsors, status, interventions)):
        raise ValueError("Malformed clinical trial modules")
    nct = identification.get("nctId")
    if not isinstance(nct, str) or not re.fullmatch(r"NCT\d{8}", nct):
        raise ValueError("Invalid clinical trial NCT ID")

    def posted(key: str, *, required: bool) -> tuple[str | None, str | None]:
        value = status.get(key)
        if value is None and not required:
            return None, None
        if not isinstance(value, dict) or not isinstance(value.get("date"), str):
            raise ValueError("Invalid clinical trial publication date")
        parsed = date.fromisoformat(value["date"])
        if parsed.isoformat() != value["date"] or value.get("type") not in {None, "ACTUAL"}:
            raise ValueError("Clinical trial publication date must be actual ISO day")
        return parsed.isoformat(), value.get("type")

    updated, update_type = posted("lastUpdatePostDateStruct", required=True)
    first, first_type = posted("studyFirstPostDateStruct", required=False)
    submitted = status.get("lastUpdateSubmitDate")
    if submitted is not None:
        if not isinstance(submitted, str) or date.fromisoformat(submitted).isoformat() != submitted:
            raise ValueError("Invalid clinical trial last update submission date")
    if (first and first > updated) or (submitted and submitted > updated):
        raise ValueError("Conflicting clinical trial publication clocks")
    lead = sponsors.get("leadSponsor", {})
    collaborators = sponsors.get("collaborators", [])
    values = interventions.get("interventions", [])
    if (not isinstance(lead, dict) or not isinstance(collaborators, list)
            or len(collaborators) > 100 or not isinstance(values, list) or len(values) > 1000):
        raise ValueError("Malformed clinical trial sponsor/intervention scope")
    lead_name = _text(lead.get("name"))
    collaborator_names = [_text(item.get("name")) if isinstance(item, dict)
                          else _text(None) for item in collaborators]
    intervention_names = [_text(item.get("name")) if isinstance(item, dict)
                          else _text(None) for item in values]
    scope = {"nct_id": nct, "lead_sponsor": lead_name,
             "collaborators": sorted(set(collaborator_names)),
             "intervention_names": sorted(set(intervention_names)),
             "record_version_date": updated, "record_version_type": update_type,
             "study_first_posted": first, "study_first_posted_type": first_type,
             "last_update_submitted": submitted}
    # API field projection can omit the ACTUAL labels while retaining the
    # same source day. Do not mistake two projections for two relationships.
    digest_scope = {key: value for key, value in scope.items()
                    if key not in {"record_version_type", "study_first_posted_type"}}
    return dict(scope,
                relationship_snapshot_sha256=hashlib.sha256(json.dumps(
                    digest_scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                publisher_clock_precision="DAY", publisher_version_id_available=False,
                publisher_clocks_are_first_observation=False)


def _validate_record(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Healthcare identity must be an object")
    row = deepcopy(raw)
    kind = row.get("kind")
    if kind not in KINDS:
        raise ValueError("Unsupported healthcare identity kind")
    source, required, relationships, basis = KINDS[kind]
    if (row.get("source") != source or row.get("relationship") not in relationships
            or row.get("time_basis") != basis):
        raise ValueError("Healthcare identity source, role or time basis mismatch")
    for key in ("record_id", "entity_name", "product_or_provider"):
        row[key] = _text(row.get(key))
    if (not isinstance(row.get("ticker"), str)
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", row["ticker"])
            or not isinstance(row.get("issuer_cik"), str)
            or not re.fullmatch(r"\d{10}", row["issuer_cik"])):
        raise ValueError("Healthcare identity needs exact ticker and SEC CIK")
    identifiers = row.get("identifiers")
    optional = {"relationship_snapshot_sha256"} if kind == "clinical_trial_sponsor" else set()
    if (not isinstance(identifiers, dict) or not required <= set(identifiers)
            or not set(identifiers) <= required | optional):
        raise ValueError("Healthcare identity needs object-specific identifiers")
    identifiers = {key: _text(value) for key, value in identifiers.items()}
    row["identifiers"] = identifiers
    if "application_number" in identifiers:
        normalized = normalize_application(identifiers["application_number"])
        if normalized is None or identifiers["application_number"] != normalized:
            raise ValueError("Healthcare application identifier must retain its exact scope")
    if kind == "fda_recall_product":
        if (identifiers["product_type"] not in {"drug", "device", "food"}
                or not re.fullmatch(r"[DFZ]-\d+-\d{4}", identifiers["recall_number"])
                or not re.fullmatch(r"\d{4,5}-\d{3,4}", identifiers["product_ndc"])):
            raise ValueError("Healthcare recall needs exact product identifiers")
    if kind == "clinical_trial_sponsor":
        if (not re.fullmatch(r"NCT\d{8}", identifiers["nct_id"])
                or identifiers["role"] != row["relationship"]
                or ("relationship_snapshot_sha256" in identifiers
                    and not re.fullmatch(r"[a-f0-9]{64}", identifiers["relationship_snapshot_sha256"]))):
            raise ValueError("Healthcare trial ID or sponsor role mismatch")
    if kind == "cms_provider_owner":
        expected_role = "34" if row["relationship"] == "DIRECT_OWNER" else "35"
        if (identifiers["owner_role_code"] != expected_role
                or not re.fullmatch(r"\d{10}", identifiers["owner_associate_id"])):
            raise ValueError("Healthcare CMS role or owner ID mismatch")
    for key in ("letter_date", "record_version_date", "association_date"):
        if key in identifiers:
            date.fromisoformat(identifiers[key])
    start = date.fromisoformat(row["effective_from"])
    end = date.fromisoformat(row["effective_to"]) if row.get("effective_to") else None
    if end is not None and end <= start:
        raise ValueError("Healthcare effective interval must be increasing")
    known = _aware(row["known_at"])
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not 2 <= len(evidence) <= 10:
        raise ValueError("Healthcare identity requires object and issuer evidence")
    purposes: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or item.get("purpose") not in {"OBJECT_RELATIONSHIP", "ISSUER_IDENTITY"}:
            raise ValueError("Healthcare evidence needs a declared purpose")
        item["locator"] = _text(item.get("locator"))
        parsed = urlsplit(public_https_reference(item.get("url")))
        if parsed.port not in {None, 443} or parsed.fragment:
            raise ValueError("Healthcare evidence must use its public official HTTPS endpoint")
        if item["purpose"] == "ISSUER_IDENTITY":
            prefix = f"/Archives/edgar/data/{int(row['issuer_cik'])}/"
            if parsed.hostname != "www.sec.gov" or not parsed.path.startswith(prefix):
                raise ValueError("Healthcare issuer evidence must cite its own SEC CIK")
        else:
            allowed = {
                "fda": {"api.fda.gov", "www.accessdata.fda.gov"},
                "cms": {"data.cms.gov"},
                "clinicaltrials": {"clinicaltrials.gov"},
            }
            if parsed.hostname not in allowed[source]:
                raise ValueError("Healthcare object evidence must cite its primary publisher")
        if _aware(item["observed_at"]) > known:
            raise ValueError("Healthcare identity cannot be known before its evidence")
        purposes.add(item["purpose"])
    if purposes != {"OBJECT_RELATIONSHIP", "ISSUER_IDENTITY"}:
        raise ValueError("Healthcare identity requires both object and issuer evidence")
    return row


class HealthcareIdentityRegistry:
    def __init__(self, records: list[dict] | tuple[dict, ...]):
        if not isinstance(records, (list, tuple)) or len(records) > 10000:
            raise ValueError("Healthcare identity manifest must be a bounded list")
        self._records = tuple(_validate_record(record) for record in records)
        ids = [row["record_id"] for row in self._records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate healthcare identity record ID")

    @classmethod
    def load(cls, path: Path = DEFAULT_HEALTHCARE_IDENTITIES) -> "HealthcareIdentityRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("Unsupported healthcare identity manifest")
        return cls(raw.get("records"))

    def records(self) -> tuple[dict, ...]:
        """Return detached validated relationships for read-only diagnostics.

        A validated record describes one object and its dated issuer evidence;
        its presence does not certify a live source check or issuer-wide scope.
        """
        return deepcopy(self._records)

    def query_names(self, source: str, ticker: str, cik: str, *, as_of: datetime) -> tuple[str, ...]:
        clock = _aware(as_of)
        # Historical object links remain searchable after their event interval.
        # They can never justify attribution of a newer object or event.
        return tuple(dict.fromkeys(row["entity_name"] for row in self._records
                                   if (row["source"], row["ticker"], row["issuer_cik"]) == (source, ticker, cik)
                                   and _aware(row["known_at"]) <= clock
                                   and date.fromisoformat(row["effective_from"]) <= clock.date()))

    def fingerprint(self, source: str, ticker: str, cik: str, *, as_of: datetime) -> str:
        clock = _aware(as_of)
        known = [row for row in self._records if row["source"] == source
                 and _aware(row["known_at"]) <= clock
                 and date.fromisoformat(row["effective_from"]) <= clock.date()]
        own = [row for row in known if (row["ticker"], row["issuer_cik"]) == (ticker, cik)]
        def object_key(row: dict) -> tuple[str, str, str]:
            identifiers = row["identifiers"]
            if row["kind"] == "clinical_trial_sponsor":
                # A date-only competing link can match a pinned lookup too.
                # Group their required object scope together for invalidation;
                # the full records (including pins) still enter the digest.
                identifiers = {key: value for key, value in identifiers.items()
                               if key != "relationship_snapshot_sha256"}
            return (row["kind"], row["entity_name"].casefold(),
                    json.dumps(identifiers, sort_keys=True))
        objects = {object_key(row) for row in own}
        # A newly known competing issuer link can change an exact resolution
        # to ambiguous; it must invalidate this object's previous check too.
        records = sorted((row for row in known if object_key(row) in objects),
                         key=lambda row: row["record_id"])
        payload = {"resolver_version": ("healthcare-trial-snapshot-v2" if source == "clinicaltrials"
                                        else "healthcare-object-v1"), "records": records}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def identity_versions(self, store: ScoutStore, source: str, *, as_of: datetime) -> dict[str, str]:
        """Hash stable active SEC names plus the applicable dated registry scope.

        Observation heartbeats do not alter query scope. New knowledge, an
        effective start date, or an exact object's competing issuer link does.
        """
        if source not in {"fda", "cms", "clinicaltrials"}:
            raise ValueError("Unsupported healthcare identity source")
        clock = _aware(as_of)
        with store._connect() as conn:
            rows = conn.execute("""
                SELECT subject_id, cik, company_name FROM scout_sec_identities
                WHERE status='ACTIVE' AND first_observed_at<=? ORDER BY subject_id
            """, (clock.isoformat(),)).fetchall()
        versions = {}
        for row in rows:
            payload = {"source": source, "sec_identity": dict(row),
                       "dated_records_sha256": self.fingerprint(
                           source, row["subject_id"], row["cik"], as_of=clock)}
            versions[row["subject_id"]] = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return versions

    def resolve(self, *, source: str, kind: str, ticker: str, cik: str,
                entity_name: str, identifiers: dict[str, str], event_date: date,
                as_of: datetime) -> dict:
        clock = _aware(as_of)
        denied = {"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False,
                  "identity_evidence": None, "missing_evidence": "dated object, role and issuer relationship"}
        if (kind not in KINDS or not isinstance(event_date, date) or isinstance(event_date, datetime)
                or event_date > clock.date() or not isinstance(identifiers, dict)):
            return dict(denied, identity_resolution="INVALID_OBJECT")
        candidates = [row for row in self._records
                      if row["source"] == source and row["kind"] == kind
                      and row["entity_name"].casefold() == str(entity_name).strip().casefold()
                      and all(identifiers.get(key) == value for key, value in row["identifiers"].items())]
        if not candidates:
            return dict(denied, identity_resolution="UNMAPPED_OBJECT")
        # Ambiguous attribution is denied globally, even when one possible
        # issuer happens to be the current query subject.
        known = [row for row in candidates if _aware(row["known_at"]) <= clock]
        if not known:
            return dict(denied, identity_resolution="NOT_YET_KNOWN")
        active = [row for row in known if date.fromisoformat(row["effective_from"]) <= event_date
                  and (not row.get("effective_to") or event_date < date.fromisoformat(row["effective_to"]))]
        if not active:
            return dict(denied, identity_resolution="OUTSIDE_EFFECTIVE_PERIOD")
        if len(active) != 1:
            return dict(denied, identity_resolution="AMBIGUOUS_OBJECT")
        row = active[0]
        if (row["ticker"], row["issuer_cik"]) != (ticker, cik):
            return dict(denied, identity_resolution="WRONG_ISSUER")
        details = deepcopy(row)
        details["matched_event_date"] = event_date.isoformat()
        return {"identity_status": "DATED_IDENTITY_VERIFIED", "issuer_attribution_allowed": True,
                "identity_resolution": "EXACT_DATED_OBJECT", "identity_evidence": details,
                "missing_evidence": None}


def resolve_fda_recall(registry: HealthcareIdentityRegistry, *, ticker: str, cik: str,
                       product_type: str, row: dict, reported: date, as_of: datetime) -> dict:
    metadata = row.get("openfda", {})
    identifiers = {"recall_number": row["recall_number"], "product_type": product_type}
    if isinstance(metadata, dict):
        # An API row harmonized to several applications/NDCs is ambiguous.
        # Neither description substrings nor the first metadata value suffice.
        for key in ("application_number", "product_ndc"):
            values = metadata.get(key)
            if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str):
                identifiers[key] = (normalize_application(values[0]) if key == "application_number" else values[0])
    result = registry.resolve(source="fda", kind="fda_recall_product", ticker=ticker, cik=cik,
                              entity_name=row["recalling_firm"], identifiers=identifiers,
                              event_date=reported, as_of=as_of)
    result["product_attribution_allowed"] = result["issuer_attribution_allowed"]
    return result


def resolve_fda_crl(registry: HealthcareIdentityRegistry, *, ticker: str, cik: str,
                    row: dict, signed: date, as_of: datetime) -> dict:
    results = []
    normalized_values = dict.fromkeys(normalize_application(value)
                                     for value in application_values(row["application_number"]))
    for normalized in normalized_values:
        if normalized:
            result = registry.resolve(
                source="fda", kind="fda_crl", ticker=ticker, cik=cik, entity_name=row["company_name"],
                identifiers={"application_number": normalized, "letter_file_name": row["file_name"],
                             "letter_date": signed.isoformat()}, event_date=signed, as_of=as_of)
            results.append(result)
    allowed = [result for result in results if result["issuer_attribution_allowed"]]
    # A letter can list base and scoped applications. More than one documented
    # link stays ambiguous rather than silently choosing a product or Original.
    if len(allowed) == 1 and not any(result["identity_resolution"] == "AMBIGUOUS_OBJECT" for result in results):
        result = allowed[0]
    else:
        result = (results[0] if len(results) == 1 else
                  {"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False,
                   "identity_evidence": None, "identity_resolution": "AMBIGUOUS_APPLICATIONS" if allowed else "UNMAPPED_OBJECT",
                   "missing_evidence": "dated application, sponsor, product and issuer relationship"})
    result["product_attribution_allowed"] = result["issuer_attribution_allowed"]
    return result
