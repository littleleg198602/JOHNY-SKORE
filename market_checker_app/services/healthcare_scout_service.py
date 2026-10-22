"""Primary healthcare leads with optional dated, object-specific identity links."""
from __future__ import annotations

from datetime import date, datetime, timezone
from copy import deepcopy
import hashlib
import json
import re
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.services.public_source_budget import (
    PublicBudgetExhausted, PublicSourceBudget, bounded_public_source,
)
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, clinical_trial_version, exact_query_names,
)


CMS_DATASET = "029c119f-f79c-49be-9100-344d31d10344"
CMS_URL = f"https://data.cms.gov/data-api/v1/dataset/{CMS_DATASET}/data"
TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"


class _ObservedJson(dict):
    """JSON content with original-byte provenance kept outside the payload."""
    def __init__(self, payload: dict, provenance: dict):
        super().__init__(payload)
        self.provenance = provenance


def _json(url: str, expected_host: str):
    requested = datetime.now(timezone.utc)
    with urlopen(Request(url, headers={"Accept": "application/json",
                                      "User-Agent": "JohnySkoreScout/1.0"}), timeout=20) as response:
        if urlsplit(response.geturl()).hostname != expected_host:
            raise ValueError("Healthcare response left its official host")
        body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError("Healthcare response exceeds byte budget")
        observed = datetime.now(timezone.utc)
        payload = json.loads(body)
        if (expected_host == "clinicaltrials.gov" and isinstance(payload, dict)
                and getattr(response, "status", None) == 200):
            return _ObservedJson(payload, {"source_url": response.geturl(),
                                           "requested_at": requested.isoformat(),
                                           "observed_at": observed.isoformat(),
                                           "body_sha256": hashlib.sha256(body).hexdigest(),
                                           "body_bytes": len(body), "clock_basis": "ACTUAL_HTTP_RETRIEVAL"})
        return payload


class NameClient(Protocol):
    source: str
    source_url: str
    def page(self, name: str, *, size: int, cursor: str | None) -> tuple[list[dict], str | None]: ...


class CmsHospitalOwnerClient:
    source, source_url = "cms", CMS_URL

    def page(self, name: str, *, size: int, cursor: str | None = None):
        if not 1 <= size <= 100 or not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid CMS query")
        offset = 0 if cursor is None else int(cursor)
        if not 0 <= offset <= 1000:
            raise ValueError("CMS offset exceeds budget")
        query = {"size": size, "offset": offset, "filter[ORGANIZATION NAME - OWNER]": name}
        rows = _json(f"{CMS_URL}?{urlencode(query)}", "data.cms.gov")
        if not isinstance(rows, list) or len(rows) > size or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Malformed CMS data page")
        return rows, str(offset + size) if len(rows) == size else None


class ClinicalTrialsClient:
    source, source_url = "clinicaltrials", TRIALS_URL

    def __init__(self, *, study_ids: tuple[str, ...] = ()):
        if (not isinstance(study_ids, tuple) or len(study_ids) > 20
                or any(not isinstance(value, str) or not re.fullmatch(r"NCT\d{8}", value)
                       for value in study_ids)
                or len(set(study_ids)) != len(study_ids)):
            raise ValueError("Clinical trial study scope must be exact and bounded")
        self.study_ids = study_ids
        self.last_page_provenance: dict | None = None

    def page(self, name: str, *, size: int, cursor: str | None = None):
        if not 1 <= size <= 100 or not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid clinical trial query")
        if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 4000):
            raise ValueError("Invalid clinical trial cursor")
        query = {"query.spons": name, "pageSize": size, "format": "json", "countTotal": "true",
                 "fields": "NCTId,BriefTitle,OverallStatus,LeadSponsorName,CollaboratorName,InterventionName,Phase,StudyFirstPostDate,LastUpdatePostDate,LastUpdateSubmitDate,PrimaryCompletionDate,HasResults"}
        if self.study_ids:
            query["filter.ids"] = ",".join(self.study_ids)
        if cursor:
            query["pageToken"] = cursor
        payload = _json(f"{TRIALS_URL}?{urlencode(query)}", "clinicaltrials.gov")
        self.last_page_provenance = deepcopy(getattr(payload, "provenance", None))
        if not isinstance(payload, dict):
            raise ValueError("Malformed clinical trial response")
        rows, token = payload.get("studies"), payload.get("nextPageToken")
        if not isinstance(rows, list) or len(rows) > size or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Malformed clinical trial page")
        if token is not None and (not isinstance(token, str) or not token or len(token) > 4000):
            raise ValueError("Malformed clinical trial cursor")
        total = payload.get("totalCount")
        # The live publisher includes totalCount only on the first page, and
        # may supply another token for a full final page before an empty page.
        if ((cursor is None or total is not None)
                and (not isinstance(total, int) or isinstance(total, bool) or total < len(rows)
                     or (cursor is None and token is None and total != len(rows)))):
            raise ValueError("Inconsistent clinical trial query count")
        if self.study_ids and any(clinical_trial_version(row)["nct_id"]
                                  not in self.study_ids for row in rows):
            raise ValueError("Clinical trial response left the exact study scope")
        return rows, token

    def study(self, nct_id: str) -> dict:
        """Read one full exact object; the caller must reserve its source quota.

        ``last_page_provenance`` carries the actual original-body hash/clock.
        This is a captured current response, never a historical version lookup.
        """
        if not isinstance(nct_id, str) or not re.fullmatch(r"NCT\d{8}", nct_id):
            raise ValueError("Clinical snapshot lookup requires one exact NCT ID")
        payload = _json(f"{TRIALS_URL}/{nct_id}", "clinicaltrials.gov")
        self.last_page_provenance = deepcopy(getattr(payload, "provenance", None))
        if not isinstance(payload, dict) or clinical_trial_version(payload)["nct_id"] != nct_id:
            raise ValueError("Clinical snapshot response left the exact NCT scope")
        return payload


def _candidate(source: str, row: dict, name: str, clock: datetime) -> tuple[str, dict] | None:
    if source == "cms":
        if str(row.get("ORGANIZATION NAME - OWNER", "")).strip().casefold() != name.strip().casefold():
            return None
        if row.get("TYPE - OWNER") != "O":
            return None
        required = ("ENROLLMENT ID", "ASSOCIATE ID - OWNER", "ORGANIZATION NAME",
                    "ASSOCIATION DATE - OWNER", "ROLE CODE - OWNER")
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in required):
            return None
        try:
            associated = date.fromisoformat(row["ASSOCIATION DATE - OWNER"])
        except ValueError:
            return None
        if associated > clock.date():
            return None
        percentage = row.get("PERCENTAGE OWNERSHIP", "")
        if percentage not in (None, ""):
            try:
                if not 0 <= float(percentage) <= 100:
                    return None
            except (ValueError, TypeError):
                return None
        details = {k: row.get(k) for k in required + (
            "ORGANIZATION NAME - OWNER", "ROLE TEXT - OWNER", "PERCENTAGE OWNERSHIP")}
        object_id = ":".join(row[k] for k in ("ENROLLMENT ID", "ASSOCIATE ID - OWNER",
                                              "ROLE CODE - OWNER", "ASSOCIATION DATE - OWNER"))
        details.update(stage="cms_hospital_owner_name_candidate", dataset_id=CMS_DATASET,
                       association_date_is_publication_time=False,
                       matched_entity_name=row["ORGANIZATION NAME - OWNER"])
        return object_id, details

    protocol = row.get("protocolSection")
    if not isinstance(protocol, dict):
        return None
    identification = protocol.get("identificationModule", {})
    sponsors = protocol.get("sponsorCollaboratorsModule", {})
    status = protocol.get("statusModule", {})
    if not all(isinstance(section, dict) for section in (identification, sponsors, status)):
        return None
    nct = identification.get("nctId", "")
    if not isinstance(nct, str) or not re.fullmatch(r"NCT\d{8}", nct):
        return None
    lead = sponsors.get("leadSponsor", {})
    collaborators = sponsors.get("collaborators", [])
    if not isinstance(lead, dict) or not isinstance(collaborators, list):
        return None
    roles = (["LEAD_SPONSOR"] if str(lead.get("name", "")).strip().casefold() == name.strip().casefold() else [])
    if any(isinstance(c, dict) and str(c.get("name", "")).strip().casefold() == name.strip().casefold()
           for c in collaborators):
        roles.append("COLLABORATOR")
    if not roles or not isinstance(status.get("overallStatus"), str):
        return None
    try:
        version = clinical_trial_version(row)
        posted = version["record_version_date"]
        if date.fromisoformat(posted) > clock.date():
            return None
    except (ValueError, TypeError):
        return None
    details = {"stage": "clinical_trial_sponsor_name_candidate", "nct_id": nct,
               "title": str(identification.get("briefTitle", ""))[:1000],
               "matched_roles": roles, "lead_sponsor": lead.get("name"),
               "matched_entity_name": name,
               "overall_status": status["overallStatus"], "last_update_posted": posted,
               "has_results": row.get("hasResults"),
               "posted_date_is_first_observation": False,
               "publisher_record_version": version,
               "relationship_snapshot_sha256": version["relationship_snapshot_sha256"]}
    design = protocol.get("designModule", {})
    if isinstance(design, dict) and isinstance(design.get("phases"), list):
        details["phases"] = design["phases"]
    details["intervention_names"] = version["intervention_names"]
    return nct, details


def _identity_details(registry: HealthcareIdentityRegistry, source: str, detail: dict,
                      issuer: dict, clock: datetime) -> dict:
    common = dict(source=source, ticker=issuer["subject_id"], cik=issuer["cik"],
                  entity_name=detail["matched_entity_name"], as_of=clock)
    if source == "cms":
        identifiers = dict(enrollment_id=detail["ENROLLMENT ID"],
                           owner_associate_id=detail["ASSOCIATE ID - OWNER"],
                           provider_name=detail["ORGANIZATION NAME"],
                           association_date=detail["ASSOCIATION DATE - OWNER"],
                           owner_role_code=detail["ROLE CODE - OWNER"])
        # The publisher supplies a current ownership snapshot, not a historical
        # snapshot of the issuer chain at the (often old) association date.
        result = registry.resolve(kind="cms_provider_owner", identifiers=identifiers,
                                  event_date=clock.date(), **common)
        result["ownership_attribution_time_basis"] = "OBSERVED_SNAPSHOT"
        result["historical_association_attribution_allowed"] = False
        return result
    snapshot = detail.get("source_observation")
    observed_mode = detail.get("trial_attribution_time_basis") == "OBSERVED_SNAPSHOT"
    snapshot_clock = None
    if observed_mode:
        try:
            if not isinstance(snapshot, dict) or snapshot.get("clock_basis") not in {
                    "ACTUAL_HTTP_RETRIEVAL", "ARCHIVED_ORIGINAL_HTTP_RETRIEVAL"}:
                raise ValueError("No exact source observation")
            if (snapshot.get("raw_publication_permitted") is False
                    or str(snapshot.get("status", "")).startswith("WITHHELD")):
                raise ValueError("Source bytes are withheld")
            snapshot_clock = datetime.fromisoformat(snapshot["observed_at"])
            requested = datetime.fromisoformat(snapshot["requested_at"])
            if (snapshot.get("source_url") != f"{TRIALS_URL}/{detail['nct_id']}"
                    or requested.tzinfo is None or requested.utcoffset() is None
                    or snapshot_clock.tzinfo is None or snapshot_clock.utcoffset() is None
                    or not requested <= snapshot_clock <= clock):
                raise ValueError("Unavailable source observation")
        except (ValueError, TypeError, KeyError):
            return {"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False,
                    "identity_resolution": "MISSING_SNAPSHOT_OBSERVATION", "identity_evidence": None,
                    "missing_evidence": "exact captured source body, observation clock and prospective issuer bridge",
                    "trial_attribution_time_basis": "OBSERVED_SNAPSHOT",
                    "historical_trial_sponsor_attribution_allowed": False,
                    "trial_sponsor_identity_is_product_ownership": False}
    resolutions = [registry.resolve(
        kind="clinical_trial_sponsor_snapshot" if observed_mode else "clinical_trial_sponsor", identifiers=dict(
            nct_id=detail["nct_id"], role=role, intervention_name=intervention,
            record_version_date=detail["last_update_posted"],
            relationship_snapshot_sha256=detail["relationship_snapshot_sha256"],
            **({"source_body_sha256": snapshot.get("body_sha256"),
                "source_observed_at": snapshot_clock.astimezone(timezone.utc).isoformat()} if observed_mode else {})),
        event_date=snapshot_clock.astimezone(timezone.utc).date() if observed_mode else date.fromisoformat(detail["last_update_posted"]),
        observed_at=snapshot_clock, **common)
        for role in dict.fromkeys(detail["matched_roles"])
        for intervention in dict.fromkeys(detail["intervention_names"])]
    allowed = [r for r in resolutions if r["issuer_attribution_allowed"]]
    if len(allowed) == 1 and not any(r["identity_resolution"] == "AMBIGUOUS_OBJECT" for r in resolutions):
        result = allowed[0]
        identifiers = result["identity_evidence"]["identifiers"]
        result.update(
            attributed_intervention_names=[identifiers["intervention_name"]],
            unattributed_intervention_names=[name for name in detail["intervention_names"]
                                             if name != identifiers["intervention_name"]],
            relationship_snapshot_pinned="relationship_snapshot_sha256" in identifiers,
            trial_sponsor_identity_is_product_ownership=False)
        return result
    if len(resolutions) == 1:
        return dict(resolutions[0], **({"trial_attribution_time_basis": "OBSERVED_SNAPSHOT",
                                      "historical_trial_sponsor_attribution_allowed": False,
                                      "trial_sponsor_identity_is_product_ownership": False} if observed_mode else {}))
    return {"identity_status": "NAME_ONLY", "issuer_attribution_allowed": False,
            "identity_resolution": "AMBIGUOUS_OBJECT" if allowed else "UNMAPPED_OBJECT",
            "identity_evidence": None, "missing_evidence": "dated trial, intervention, sponsor role and issuer relationship",
            **({"trial_attribution_time_basis": "OBSERVED_SNAPSHOT",
                "historical_trial_sponsor_attribution_allowed": False,
                "trial_sponsor_identity_is_product_ownership": False} if observed_mode else {})}


def _finding_digest(detail: dict) -> str:
    content = deepcopy(detail)
    if (content.get("ownership_attribution_time_basis") == "OBSERVED_SNAPSHOT"
            and content.get("issuer_attribution_allowed") is False
            and content.get("observed_snapshot_original_binding_verified") is False
            and content.get("retained_relationship_record_id")
            and content.get("identity_resolution") in {
                "CMS_SNAPSHOT_DATE_MISMATCH", "CMS_SNAPSHOT_ORIGINAL_BINDING_MISSING"}):
        # Both reasons describe the same unbound legacy CMS source lead. A
        # passing observation day changes the diagnostic, not its content.
        # Preserve the actual reason in the finding; normalize only its hash.
        content["identity_resolution"] = "CMS_SNAPSHOT_ORIGINAL_BINDING_MISSING"
    provenance = content.get("source_observation")
    if isinstance(provenance, dict):
        # Identical unlinked content is a heartbeat, not another trial event.
        # Exact identity-record capture clocks remain part of its own evidence.
        for key in ("requested_at", "observed_at", "clock_basis"):
            provenance.pop(key, None)
    evidence = content.get("identity_evidence")
    if isinstance(evidence, dict) and evidence.get("time_basis") == "OBSERVED_SNAPSHOT":
        # A later observation of unchanged CMS content is a replay, not a new
        # ownership event. The store retains first/last observation separately.
        evidence.pop("matched_event_date", None)
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


class HealthcareNameScoutService:
    def __init__(self, store: ScoutStore, *, client: NameClient,
                 max_subjects: int = 10, page_size: int = 100, max_pages: int = 2,
                 max_run_seconds: float = 60, max_failures: int = 3,
                 identities: HealthcareIdentityRegistry | None = None):
        if client.source not in {"cms", "clinicaltrials"}:
            raise ValueError("Unknown healthcare source")
        if not 1 <= max_subjects <= 50 or not 1 <= page_size <= 100 or not 1 <= max_pages <= 10:
            raise ValueError("Healthcare query budget must be bounded")
        if not 1 <= max_run_seconds <= 300 or not 1 <= max_failures <= 10:
            raise ValueError("Healthcare time and failure budgets must be bounded")
        self.store, self.client = store, client
        self.max_subjects, self.page_size, self.max_pages = max_subjects, page_size, max_pages
        self.max_run_seconds, self.max_failures = max_run_seconds, max_failures
        self.identities = identities if identities is not None else HealthcareIdentityRegistry.load()

    @bounded_public_source()
    def run(self, *, as_of: datetime | None = None, universe: set[str] | None = None,
            _budget: PublicSourceBudget):
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Healthcare observation needs timezone")
        source = self.client.source
        profiles = load_research_profiles()
        applicable_codes = {"HEALTH_SERVICES"} if source == "cms" else {"PHARMA", "MEDTECH"}
        applicable = {t for t, p in profiles.by_ticker.items() if p.code in applicable_codes}
        if universe is not None:
            applicable &= universe
        if not applicable:
            return {"status": "NOT_APPLICABLE", "checked_issuers": 0, "new_findings": 0}
        identity_versions = self.identities.identity_versions(self.store, source, as_of=clock)
        due = _budget.select_due([
            row for row in self.store.specialist_due(source, as_of=clock, limit=900,
                                                    identity_versions=identity_versions)
            if row["subject_id"] in applicable])[:self.max_subjects]
        coverage = self.store.specialist_coverage(source, as_of=clock, subjects=applicable,
                                                  identity_versions=identity_versions)
        if not due:
            return {"status": "WAIT_IDENTITY" if coverage["active_identities"] == 0 else "OK",
                    "checked_issuers": 0, "new_findings": 0}
        checked = created = failed = truncated_issuers = rejected = identity_linked = 0
        deadline = time.monotonic() + self.max_run_seconds
        budget_exhausted = False
        for issuer in due:
            if failed >= self.max_failures or time.monotonic() >= deadline:
                budget_exhausted = True
                break
            name = issuer["company_name"]
            candidates = []
            query_names = exact_query_names(name, self.identities.query_names(
                source, issuer["subject_id"], issuer["cik"], as_of=clock))
            # A completed exact-study lookup must never complete the broader
            # issuer-name source check, including a zero-row lookup.
            incomplete = len(query_names) > 5 or bool(getattr(self.client, "study_ids", ()))
            try:
                for query_name in query_names[:5]:
                    cursor = None
                    seen_cursors: set[str] = set()
                    seen_rows: set[str] = set()
                    for _ in range(self.max_pages):
                        if time.monotonic() >= deadline:
                            budget_exhausted = True
                            # Keep this issuer due; a timeout between pages is
                            # not a complete negative query and commits no rows.
                            raise TimeoutError("Healthcare time budget exhausted")
                        rows, next_cursor = _budget.call(
                            issuer["subject_id"], f"{issuer['cik']}:{name}",
                            lambda: self.client.page(query_name, size=self.page_size, cursor=cursor))
                        provenance = deepcopy(getattr(self.client, "last_page_provenance", None)) if source == "clinicaltrials" else None
                        candidate_clock = _budget.now() if provenance else clock
                        if not isinstance(rows, list) or len(rows) > self.page_size:
                            raise ValueError("Malformed healthcare page")
                        for row in rows:
                            if not isinstance(row, dict):
                                raise ValueError("Malformed healthcare row")
                            digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                            if digest in seen_rows:
                                incomplete = True
                                continue
                            seen_rows.add(digest)
                            candidate = _candidate(source, row, query_name, candidate_clock)
                            if candidate is None:
                                rejected += 1
                                incomplete = True
                            else:
                                if provenance:
                                    candidate[1].update(source_observation=provenance,
                                                        trial_attribution_time_basis="OBSERVED_SNAPSHOT")
                                candidates.append(candidate)
                        if next_cursor is None:
                            break
                        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                            raise ValueError("Repeated healthcare cursor")
                        seen_cursors.add(next_cursor)
                        cursor = next_cursor
                    else:
                        incomplete = True
            except PublicBudgetExhausted:
                budget_exhausted = True
                break
            except HTTPError as exc:
                failed += 1
                if exc.code in {401, 403, 429}:
                    return {"status": "RATE_LIMITED" if exc.code == 429 else "ACCESS_BLOCKED",
                            "checked_issuers": checked, "new_findings": created,
                            "failed_issuers": failed, "truncated_issuers": truncated_issuers,
                            "rejected_rows": rejected, "budget_exhausted": budget_exhausted}
                continue
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                failed += 1
                continue
            for object_id, detail in candidates:
                detail.update(query_issuer_name=name, issuer_cik=issuer["cik"],
                              scoring_applied=False, issuer_financial_impact_verified=False,
                              restricted_study_ids=list(getattr(self.client, "study_ids", ())),
                              source_query_is_issuer_wide=False)
                finding_clock = _budget.now() if detail.get("source_observation") else clock
                detail.update(_identity_details(self.identities, source, detail, issuer, finding_clock))
                identity_linked += int(detail["issuer_attribution_allowed"])
                digest = _finding_digest(detail)
                _, fresh = self.store.record_finding(
                    source=source, subject_id=issuer["subject_id"], source_object_id=object_id,
                    content_hash=digest, title=f"{source}: {object_id}", source_url=self.client.source_url,
                    locator=object_id, published_at=finding_clock, available_at=finding_clock, observed_at=finding_clock,
                    details=detail, verification_status="UNVERIFIED")
                created += int(fresh)
            self.store.record_specialist_check(source, subject_id=issuer["subject_id"],
                                               identity_key=f"{issuer['cik']}:{name}", as_of=clock,
                                               candidate_count=len(candidates), truncated=incomplete,
                                               identity_version=identity_versions[issuer["subject_id"]])
            checked += 1
            truncated_issuers += int(incomplete)
        return {"status": "PARTIAL" if failed or truncated_issuers or budget_exhausted else "OK",
                "checked_issuers": checked, "new_findings": created,
                "failed_issuers": failed, "truncated_issuers": truncated_issuers,
                "rejected_rows": rejected, "budget_exhausted": budget_exhausted,
                "identity_linked_candidates": identity_linked,
                "query_scope": "documented exact names; not issuer-wide healthcare coverage",
                "restricted_study_ids": list(getattr(self.client, "study_ids", ())),
                "exact_study_lookup_completes_source_scope": False}
