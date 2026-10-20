"""Dated, object-specific healthcare relationships, independent of name leads.

The manifest documents identities and roles. It never establishes financial
impact, a drug's efficacy, or ownership of every product in a corporate group.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
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
    "clinical_trial_sponsor_snapshot": (
        "clinicaltrials", {"nct_id", "role", "intervention_name", "record_version_date",
                           "relationship_snapshot_sha256", "source_body_sha256", "source_observed_at"},
        {"LEAD_SPONSOR", "COLLABORATOR"}, "OBSERVED_SNAPSHOT"),
    "cms_provider_owner": ("cms", {"enrollment_id", "owner_associate_id", "provider_name",
                                  "association_date", "owner_role_code"},
                           {"DIRECT_OWNER", "INDIRECT_OWNER"}, "OBSERVED_SNAPSHOT"),
}

CLINICAL_TRIAL_KINDS = {"clinical_trial_sponsor", "clinical_trial_sponsor_snapshot"}


def healthcare_resolver_version(source: str) -> str:
    if source == "cms":
        return "healthcare-cms-original-snapshot-required-v2"
    return "healthcare-trial-observed-snapshot-v3" if source == "clinicaltrials" else "healthcare-object-v1"


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


def _sha256(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Healthcare snapshot needs a full original-body SHA-256")
    return value


def _snapshot_contract(row: dict, *, known: datetime, start: date, end: date | None) -> None:
    """Validate an observed object/issuer cohort, never its publisher event day.

    The closed one-day interval limits the observed cohort. It is not a
    continuous legal interval, and the exact capture clock remains mandatory.
    """
    ids = row["identifiers"]
    observed = _aware(ids["source_observed_at"])
    _sha256(ids["source_body_sha256"])
    if any(row.get(key) is not False for key in (
            "historical_trial_sponsor_attribution_allowed", "trial_sponsor_identity_is_product_ownership",
            "issuer_wide_scope")):
        raise ValueError("Clinical snapshot cannot assert historical, product-ownership or issuer-wide scope")
    if (start != observed.date() or end != start + timedelta(days=1)
            or observed > known):
        raise ValueError("Clinical snapshot must retain its exact observed-day scope")
    scope = row.get("intervention_scope")
    if (not isinstance(scope, dict) or set(scope) != {"type", "intervention_type", "arm_labels"}
            or scope["type"] != "EXPERIMENTAL_ARM_SPONSORSHIP_ONLY"
            or not isinstance(scope["arm_labels"], list) or not 1 <= len(scope["arm_labels"]) <= 100
            or any(not isinstance(label, str) or not label.strip() for label in scope["arm_labels"])
            or len(set(scope["arm_labels"])) != len(scope["arm_labels"])
            or not isinstance(scope["intervention_type"], str) or not scope["intervention_type"].strip()):
        raise ValueError("Clinical snapshot must document the selected experimental-arm scope")
    bridge = row.get("issuer_bridge")
    required = {"schema_version", "type", "source_url", "body_sha256", "observed_at", "known_at",
                "issuer_name", "issuer_cik", "ticker", "entity_name", "snapshot_date"}
    if (not isinstance(bridge, dict) or set(bridge) != required or bridge["schema_version"] != 1
            or bridge["type"] != "SEC_SUBMISSIONS_DIRECT_NAMED_ISSUER"):
        raise ValueError("Clinical snapshot requires an independent dated issuer bridge")
    official = f"https://data.sec.gov/submissions/CIK{row['issuer_cik']}.json"
    if bridge["source_url"] != official:
        raise ValueError("Clinical snapshot issuer bridge must be its own SEC submissions body")
    _sha256(bridge["body_sha256"])
    issuer_observed, issuer_known = _aware(bridge["observed_at"]), _aware(bridge["known_at"])
    if (issuer_observed.date() != observed.date() or bridge["snapshot_date"] != start.isoformat()
            or not issuer_observed <= observed <= known or not issuer_observed <= issuer_known <= known
            or (bridge["issuer_cik"], bridge["ticker"]) != (row["issuer_cik"], row["ticker"])
            or _text(bridge["entity_name"]).casefold() != row["entity_name"].casefold()
            or _text(bridge["issuer_name"]).casefold() != row["entity_name"].casefold()):
        raise ValueError("Clinical snapshot needs an exact named issuer bridge in the observed cohort")
    for purpose, url, body_hash, clock in (
            ("OBJECT_RELATIONSHIP", None, ids["source_body_sha256"], observed),
            ("ISSUER_IDENTITY", official, bridge["body_sha256"], issuer_observed)):
        matches = [item for item in row["evidence"] if item["purpose"] == purpose
                   and (url is None or item["url"] == url)
                   and item.get("body_sha256") == body_hash
                   and _aware(item["observed_at"]) == clock]
        if len(matches) != 1:
            raise ValueError("Clinical snapshot evidence must pin both original source bodies and clocks")
    # The object URL is checked separately, not inferred from a publisher name.
    object_evidence = next(item for item in row["evidence"] if item["purpose"] == "OBJECT_RELATIONSHIP")
    parsed = urlsplit(object_evidence["url"])
    if (parsed.hostname != "clinicaltrials.gov"
            or parsed.path != f"/api/v2/studies/{ids['nct_id']}" or parsed.query):
        raise ValueError("Clinical snapshot evidence must identify the exact captured NCT object")


def build_clinical_trial_snapshot_record(*, ticker: str, cik: str, intervention: str, role: str,
                                         trial_metadata: dict, trial_body: bytes,
                                         issuer_metadata: dict, issuer_body: bytes,
                                         reviewed_at: datetime) -> dict:
    """Build a proposal only from original bytes and a direct same-day issuer.

    This performs no network/store/registry mutation. Differently spelled
    issuer names need their own independently dated bridge; no '& Co' alias or
    old annual-report period is inferred. Withheld/truncated bytes cannot enter.
    """
    known = _aware(reviewed_at)

    def original(metadata: dict, body: bytes, *, host: str) -> tuple[dict, datetime]:
        if (not isinstance(metadata, dict) or not isinstance(body, bytes)
                or metadata.get("raw_publication_permitted") is False
                or str(metadata.get("status", "")).startswith("WITHHELD")
                or metadata.get("http_status") != 200
                or metadata.get("body_complete_within_byte_budget") is not True):
            raise ValueError("WITHHELD_OR_INCOMPLETE_PRIMARY_CAPTURE")
        if (len(body) != metadata.get("body_bytes") or len(body) > 4_000_000
                or len(body) > metadata.get("byte_budget", 0)
                or hashlib.sha256(body).hexdigest() != _sha256(metadata.get("body_sha256"))):
            raise ValueError("PRIMARY_CAPTURE_INTEGRITY_FAILED")
        for key in ("request_url", "response_url"):
            parsed = urlsplit(public_https_reference(metadata.get(key)))
            if parsed.hostname != host or parsed.fragment or parsed.port not in {None, 443}:
                raise ValueError("PRIMARY_CAPTURE_PUBLISHER_MISMATCH")
        if metadata["request_url"] != metadata["response_url"]:
            raise ValueError("PRIMARY_CAPTURE_OBJECT_REDIRECT")
        requested, observed = _aware(metadata.get("requested_at")), _aware(metadata.get("observed_at"))
        if not requested <= observed <= known:
            raise ValueError("PRIMARY_CAPTURE_NOT_AVAILABLE_AT_REVIEW")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("PRIMARY_CAPTURE_NOT_AN_OBJECT")
        return payload, observed

    trial, observed = original(trial_metadata, trial_body, host="clinicaltrials.gov")
    issuer, issuer_observed = original(issuer_metadata, issuer_body, host="data.sec.gov")
    version = clinical_trial_version(trial)
    if observed.date() != issuer_observed.date():
        raise ValueError("ISSUER_BRIDGE_OUTSIDE_SOURCE_OBSERVED_COHORT")
    issuer_cik = issuer.get("cik")
    if (isinstance(issuer_cik, bool) or not isinstance(issuer_cik, (str, int))
            or not str(issuer_cik).isdigit() or str(issuer_cik).zfill(10) != cik):
        raise ValueError("ISSUER_BRIDGE_CIK_MISMATCH")
    issuer_name = _text(issuer.get("name"))
    if role == "LEAD_SPONSOR":
        entity_name = version["lead_sponsor"]
    elif role == "COLLABORATOR":
        matches = [name for name in version["collaborators"] if name.casefold() == issuer_name.casefold()]
        if len(matches) != 1:
            raise ValueError("NO_EXACT_COLLABORATOR_ISSUER_BRIDGE")
        entity_name = matches[0]
    else:
        raise ValueError("INVALID_SNAPSHOT_SPONSOR_ROLE")
    if entity_name.casefold() != issuer_name.casefold():
        raise ValueError("EXACT_SPONSOR_ISSUER_NAME_BRIDGE_MISSING")
    if issuer_observed > observed:
        raise ValueError("ISSUER_EVIDENCE_NOT_AVAILABLE_AT_SOURCE_OBSERVATION")
    tickers = issuer.get("tickers")
    if (not isinstance(tickers, list) or not 1 <= len(tickers) <= 100
            or any(not isinstance(value, str) for value in tickers) or tickers.count(ticker) != 1):
        raise ValueError("ISSUER_BRIDGE_TICKER_MISMATCH")
    if intervention not in version["intervention_names"]:
        raise ValueError("SNAPSHOT_INTERVENTION_NOT_CAPTURED")
    arms_module = trial["protocolSection"].get("armsInterventionsModule", {})
    selected = [item for item in arms_module.get("interventions", []) if item["name"] == intervention]
    groups = arms_module.get("armGroups")
    if (len(selected) != 1 or not isinstance(groups, list) or not 1 <= len(groups) <= 100
            or any(not isinstance(group, dict) for group in groups)):
        raise ValueError("EXPERIMENTAL_ARM_SCOPE_NOT_CAPTURED")
    selected = selected[0]
    intervention_type = _text(selected.get("type"))
    reference = f"{intervention_type.replace('_', ' ').title()}: {intervention}"
    matched = [group for group in groups if isinstance(group.get("interventionNames"), list)
               and reference in group["interventionNames"]]
    labels = selected.get("armGroupLabels")
    if (not matched or any(group.get("type") != "EXPERIMENTAL" for group in matched)
            or not isinstance(labels, list) or not labels
            or any(not isinstance(label, str) or not label.strip() for label in labels)
            or set(labels) != {_text(group.get("label")) for group in matched}):
        raise ValueError("COMPARATOR_OR_UNDOCUMENTED_INTERVENTION_SCOPE")
    ids = {"nct_id": version["nct_id"], "role": role, "intervention_name": intervention,
           "record_version_date": version["record_version_date"],
           "relationship_snapshot_sha256": version["relationship_snapshot_sha256"],
           "source_body_sha256": trial_metadata["body_sha256"], "source_observed_at": observed.isoformat()}
    scope_id = hashlib.sha256(json.dumps(ids, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    record = {"record_id": f"{ticker.lower()}-trial-{version['nct_id'].lower()}-observed-{scope_id}",
              "source": "clinicaltrials", "kind": "clinical_trial_sponsor_snapshot", "ticker": ticker,
              "issuer_cik": cik, "entity_name": entity_name, "product_or_provider": intervention,
              "relationship": role, "time_basis": "OBSERVED_SNAPSHOT", "identifiers": ids,
              "effective_from": observed.date().isoformat(),
              "effective_to": (observed.date() + timedelta(days=1)).isoformat(), "known_at": known.isoformat(),
              "issuer_bridge": {"schema_version": 1, "type": "SEC_SUBMISSIONS_DIRECT_NAMED_ISSUER",
                                "source_url": issuer_metadata["response_url"],
                                "body_sha256": issuer_metadata["body_sha256"],
                                "observed_at": issuer_observed.isoformat(), "known_at": known.isoformat(),
                                "issuer_name": issuer_name, "issuer_cik": cik, "ticker": ticker,
                                "entity_name": entity_name, "snapshot_date": observed.date().isoformat()},
              "intervention_scope": {"type": "EXPERIMENTAL_ARM_SPONSORSHIP_ONLY",
                                     "intervention_type": intervention_type, "arm_labels": sorted(set(labels))},
              "evidence": [{"url": trial_metadata["response_url"], "locator": "exact captured NCT, role, intervention and snapshot digest",
                            "purpose": "OBJECT_RELATIONSHIP", "observed_at": observed.isoformat(),
                            "body_sha256": trial_metadata["body_sha256"]},
                           {"url": issuer_metadata["response_url"], "locator": "same-day SEC submissions exact name, CIK and ticker; no name alias",
                            "purpose": "ISSUER_IDENTITY", "observed_at": issuer_observed.isoformat(),
                            "body_sha256": issuer_metadata["body_sha256"]}],
              "historical_trial_sponsor_attribution_allowed": False, "trial_sponsor_identity_is_product_ownership": False,
              "issuer_wide_scope": False}
    return _validate_record(record)


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
    if kind in CLINICAL_TRIAL_KINDS:
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
            snapshot_issuer = (kind == "clinical_trial_sponsor_snapshot" and parsed.hostname == "data.sec.gov"
                               and parsed.path == f"/submissions/CIK{row['issuer_cik']}.json" and not parsed.query)
            if not snapshot_issuer and (parsed.hostname != "www.sec.gov" or not parsed.path.startswith(prefix)):
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
    if kind == "clinical_trial_sponsor_snapshot":
        _snapshot_contract(row, known=known, start=start, end=end)
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
        payload = {"resolver_version": healthcare_resolver_version(source), "records": records}
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
                as_of: datetime, observed_at: datetime | None = None) -> dict:
        clock = _aware(as_of)
        denied = {"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False,
                  "identity_evidence": None, "missing_evidence": "dated object, role and issuer relationship"}
        if (kind not in KINDS or not isinstance(event_date, date) or isinstance(event_date, datetime)
                or event_date > clock.date() or not isinstance(identifiers, dict)):
            return dict(denied, identity_resolution="INVALID_OBJECT")
        if kind == "clinical_trial_sponsor_snapshot":
            try:
                observed = _aware(observed_at)
                pinned = _aware(identifiers["source_observed_at"])
                _sha256(identifiers.get("source_body_sha256"))
                if observed != pinned or event_date != observed.date() or observed > clock:
                    return dict(denied, identity_resolution="INVALID_SNAPSHOT_OBSERVATION")
            except (ValueError, TypeError, KeyError):
                return dict(denied, identity_resolution="MISSING_SNAPSHOT_OBSERVATION")
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
        if kind == "cms_provider_owner":
            # This legacy kind retains projected owner/enrollment fields, not
            # the original CMS body, exact provider PAC/CCN join and captured
            # observation. An open-ended manifest interval cannot turn that
            # source lead into a verified snapshot on another day; the same
            # date alone cannot supply the missing original-object binding.
            # Keep the record available for queries and independent review.
            different_day = event_date != date.fromisoformat(row["effective_from"])
            return dict(denied,
                        identity_resolution=("CMS_SNAPSHOT_DATE_MISMATCH" if different_day
                                             else "CMS_SNAPSHOT_ORIGINAL_BINDING_MISSING"),
                        missing_evidence="original CMS owner/provider bodies, CCN/PAC join and exact observed snapshot issuer bridge",
                        retained_relationship_record_id=row["record_id"],
                        ownership_attribution_time_basis="OBSERVED_SNAPSHOT",
                        historical_association_attribution_allowed=False,
                        observed_snapshot_original_binding_verified=False)
        details = deepcopy(row)
        if kind == "clinical_trial_sponsor_snapshot":
            details["matched_observed_at"] = observed.isoformat()
            return {"identity_status": "OBSERVED_SNAPSHOT_IDENTITY_VERIFIED", "issuer_attribution_allowed": True,
                    "identity_resolution": "EXACT_OBSERVED_SNAPSHOT", "identity_evidence": details,
                    "missing_evidence": None, "trial_attribution_time_basis": "OBSERVED_SNAPSHOT",
                    "historical_trial_sponsor_attribution_allowed": False}
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
