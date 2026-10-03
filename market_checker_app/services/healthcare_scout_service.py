"""Primary healthcare name candidates, separate from verified issuer exposure."""
from __future__ import annotations

from datetime import date, datetime, timezone
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


CMS_DATASET = "029c119f-f79c-49be-9100-344d31d10344"
CMS_URL = f"https://data.cms.gov/data-api/v1/dataset/{CMS_DATASET}/data"
TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"


def _json(url: str, expected_host: str):
    with urlopen(Request(url, headers={"Accept": "application/json",
                                      "User-Agent": "JohnySkoreScout/1.0"}), timeout=20) as response:
        if urlsplit(response.geturl()).hostname != expected_host:
            raise ValueError("Healthcare response left its official host")
        body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError("Healthcare response exceeds byte budget")
        return json.loads(body)


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

    def page(self, name: str, *, size: int, cursor: str | None = None):
        if not 1 <= size <= 100 or not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid clinical trial query")
        if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 4000):
            raise ValueError("Invalid clinical trial cursor")
        query = {"query.spons": name, "pageSize": size, "format": "json",
                 "fields": "NCTId,BriefTitle,OverallStatus,LeadSponsorName,CollaboratorName,Phase,StudyFirstPostDate,LastUpdatePostDate,PrimaryCompletionDate,HasResults"}
        if cursor:
            query["pageToken"] = cursor
        payload = _json(f"{TRIALS_URL}?{urlencode(query)}", "clinicaltrials.gov")
        if not isinstance(payload, dict):
            raise ValueError("Malformed clinical trial response")
        rows, token = payload.get("studies"), payload.get("nextPageToken")
        if not isinstance(rows, list) or len(rows) > size or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Malformed clinical trial page")
        if token is not None and (not isinstance(token, str) or not token or len(token) > 4000):
            raise ValueError("Malformed clinical trial cursor")
        return rows, token


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
                       association_date_is_publication_time=False)
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
    posted = status.get("lastUpdatePostDateStruct", {}).get("date")
    try:
        if not isinstance(posted, str) or date.fromisoformat(posted) > clock.date():
            return None
    except ValueError:
        return None
    details = {"stage": "clinical_trial_sponsor_name_candidate", "nct_id": nct,
               "title": str(identification.get("briefTitle", ""))[:1000],
               "matched_roles": roles, "lead_sponsor": lead.get("name"),
               "overall_status": status["overallStatus"], "last_update_posted": posted,
               "has_results": row.get("hasResults"),
               "posted_date_is_first_observation": False}
    design = protocol.get("designModule", {})
    if isinstance(design, dict) and isinstance(design.get("phases"), list):
        details["phases"] = design["phases"]
    return nct, details


class HealthcareNameScoutService:
    def __init__(self, store: ScoutStore, *, client: NameClient,
                 max_subjects: int = 10, page_size: int = 100, max_pages: int = 2,
                 max_run_seconds: float = 60, max_failures: int = 3):
        if client.source not in {"cms", "clinicaltrials"}:
            raise ValueError("Unknown healthcare source")
        if not 1 <= max_subjects <= 50 or not 1 <= page_size <= 100 or not 1 <= max_pages <= 10:
            raise ValueError("Healthcare query budget must be bounded")
        if not 1 <= max_run_seconds <= 300 or not 1 <= max_failures <= 10:
            raise ValueError("Healthcare time and failure budgets must be bounded")
        self.store, self.client = store, client
        self.max_subjects, self.page_size, self.max_pages = max_subjects, page_size, max_pages
        self.max_run_seconds, self.max_failures = max_run_seconds, max_failures

    def run(self, *, as_of: datetime | None = None, universe: set[str] | None = None):
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
        due = [row for row in self.store.specialist_due(source, as_of=clock, limit=900)
               if row["subject_id"] in applicable][:self.max_subjects]
        coverage = self.store.specialist_coverage(source, as_of=clock, subjects=applicable)
        if not due:
            return {"status": "WAIT_IDENTITY" if coverage["active_identities"] == 0 else "OK",
                    "checked_issuers": 0, "new_findings": 0}
        checked = created = failed = truncated_issuers = rejected = 0
        deadline = time.monotonic() + self.max_run_seconds
        budget_exhausted = False
        for issuer in due:
            if failed >= self.max_failures or time.monotonic() >= deadline:
                budget_exhausted = True
                break
            name = issuer["company_name"]
            cursor = None
            seen_cursors: set[str] = set()
            seen_rows: set[str] = set()
            candidates = []
            incomplete = False
            try:
                for _ in range(self.max_pages):
                    if time.monotonic() >= deadline:
                        budget_exhausted = True
                        # Keep this issuer due; a timeout between pages is
                        # not a complete negative query and commits no rows.
                        raise TimeoutError("Healthcare time budget exhausted")
                    rows, next_cursor = self.client.page(name, size=self.page_size, cursor=cursor)
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
                        candidate = _candidate(source, row, name, clock)
                        if candidate is None:
                            rejected += 1
                            incomplete = True
                        else:
                            candidates.append(candidate)
                    if next_cursor is None:
                        break
                    if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                        raise ValueError("Repeated healthcare cursor")
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
                else:
                    incomplete = True
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
                              issuer_attribution_allowed=False, scoring_applied=False)
                digest = hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source=source, subject_id=issuer["subject_id"], source_object_id=object_id,
                    content_hash=digest, title=f"{source}: {object_id}", source_url=self.client.source_url,
                    locator=object_id, published_at=clock, available_at=clock, observed_at=clock,
                    details=detail, verification_status="UNVERIFIED")
                created += int(fresh)
            self.store.record_specialist_check(source, subject_id=issuer["subject_id"],
                                               identity_key=f"{issuer['cik']}:{name}", as_of=clock,
                                               candidate_count=len(candidates), truncated=incomplete)
            checked += 1
            truncated_issuers += int(incomplete)
        return {"status": "PARTIAL" if failed or truncated_issuers or budget_exhausted else "OK",
                "checked_issuers": checked, "new_findings": created,
                "failed_issuers": failed, "truncated_issuers": truncated_issuers,
                "rejected_rows": rejected, "budget_exhausted": budget_exhausted}
