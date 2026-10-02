"""Bounded EPA ECHO facility-name leads; facility ownership stays unverified."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore


EPA_ECHO_URL = "https://echodata.epa.gov/echo/echo_rest_services.get_facility_info"
MAX_BYTES = 2_000_000


def echo_url(name: str) -> str:
    return f"{EPA_ECHO_URL}?{urlencode({'output':'JSON','p_fn':name,'p_fntype':'EXACT','qcolumns':'1'})}"


class EpaEchoClient(Protocol):
    def facilities(self, name: str) -> tuple[list[dict], int]: ...


class EpaEchoFacilityClient:
    def facilities(self, name: str) -> tuple[list[dict], int]:
        if not isinstance(name, str) or not name.strip() or len(name) > 500:
            raise ValueError("EPA ECHO query must be bounded")
        url = echo_url(name)
        with urlopen(Request(url, headers={"Accept":"application/json",
                                          "User-Agent":"JohnySkoreScout/1.0"}), timeout=20) as response:
            target = urlsplit(response.geturl())
            if (target.scheme != "https" or target.hostname != "echodata.epa.gov"
                    or target.port not in {None, 443}
                    or target.path != "/echo/echo_rest_services.get_facility_info"):
                raise ValueError("EPA ECHO response left official endpoint")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("EPA ECHO response exceeds byte budget")
            payload = json.loads(body)
        results = payload.get("Results") if isinstance(payload, dict) else None
        if not isinstance(results, dict) or results.get("Message") != "Success":
            raise ValueError("Malformed EPA ECHO response")
        rows, count = results.get("Facilities"), results.get("QueryRows")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Malformed EPA ECHO facilities")
        if isinstance(count, bool) or not re.fullmatch(r"\d{1,10}", str(count)):
            raise ValueError("Malformed EPA ECHO count")
        if len(rows) > 100 or int(count) < len(rows):
            raise ValueError("Unbounded or inconsistent EPA ECHO result")
        return rows, int(count)


def _name_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def parse_epa_facility(row: dict, name: str) -> tuple[str, dict] | None:
    if not isinstance(row, dict):
        raise ValueError("Malformed EPA ECHO facility")
    facility, registry = row.get("FacName"), row.get("RegistryID")
    if (not isinstance(facility, str) or not facility.strip()
            or not isinstance(registry, str) or not re.fullmatch(r"\d{9,12}", registry)):
        raise ValueError("Malformed EPA ECHO facility identity")
    if _name_key(facility) != _name_key(name):
        return None
    return registry, {"stage":"epa_echo_exact_facility_name_candidate",
                      "facility_name":facility, "frs_registry_id":registry,
                      "query_issuer_name":name,
                      "query_scope":"EPA ECHO exact facility-name search plus local exact-name filter",
                      "facility_identity_verified":True,
                      "issuer_facility_relationship_verified":False,
                      "complete_facility_group_verified":False,
                      "environmental_liability_verified":False,
                      "scoring_applied":False}


class EpaEchoScoutService:
    def __init__(self, store: ScoutStore, *, client: EpaEchoClient, max_subjects: int = 10,
                 max_run_seconds: float = 60, max_failures: int = 3):
        if (isinstance(max_subjects, bool) or not isinstance(max_subjects, int)
                or not 1 <= max_subjects <= 50 or isinstance(max_failures, bool)
                or not isinstance(max_failures, int) or not 1 <= max_failures <= 10):
            raise ValueError("EPA ECHO request budget must be bounded")
        if (isinstance(max_run_seconds, bool) or not isinstance(max_run_seconds, (int, float))
                or not math.isfinite(max_run_seconds) or not 1 <= max_run_seconds <= 300):
            raise ValueError("EPA ECHO time budget must be bounded")
        self.store, self.client = store, client
        self.max_subjects, self.max_run_seconds, self.max_failures = max_subjects, max_run_seconds, max_failures

    def run(self, *, as_of: datetime | None = None, universe: set[str] | None = None) -> dict:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("EPA ECHO observation needs timezone")
        due = self.store.specialist_due("epa", as_of=clock, limit=900)
        due = [row for row in due if universe is None or row["subject_id"] in universe][:self.max_subjects]
        if not due:
            active = self.store.specialist_coverage("epa", as_of=clock, subjects=universe)["active_identities"]
            return {"status":"OK" if active else "WAIT_IDENTITY", "checked_issuers":0, "new_findings":0}
        deadline = time.monotonic() + self.max_run_seconds
        checked = created = failed = rejected = truncated = 0
        exhausted = False
        for issuer in due:
            if time.monotonic() >= deadline or failed >= self.max_failures:
                exhausted = True
                break
            try:
                rows, total = self.client.facilities(issuer["company_name"])
            except HTTPError as exc:
                failed += 1
                if exc.code in {401,403,429}:
                    return {"status":"RATE_LIMITED" if exc.code == 429 else "ACCESS_BLOCKED",
                            "checked_issuers":checked,"new_findings":created,"failed_issuers":failed}
                continue
            except (OSError, ValueError, TypeError, KeyError):
                failed += 1
                continue
            incomplete = total > len(rows)
            candidates, seen = [], set()
            for row in rows:
                try:
                    candidate = parse_epa_facility(row, issuer["company_name"])
                except (ValueError, TypeError, AttributeError):
                    rejected += 1
                    incomplete = True
                    continue
                if candidate is None:
                    continue
                registry, detail = candidate
                if registry in seen:
                    rejected += 1
                    incomplete = True
                    continue
                seen.add(registry)
                candidates.append((registry, detail))
            for registry, detail in candidates:
                detail["issuer_sec_cik"] = issuer["cik"]
                digest = hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="epa", subject_id=issuer["subject_id"], source_object_id=registry,
                    content_hash=digest, title=f"EPA ECHO facility {detail['facility_name']}",
                    source_url=echo_url(issuer["company_name"]), locator=f"FRS:{registry}",
                    published_at=clock, available_at=clock, observed_at=clock,
                    details=detail, verification_status="UNVERIFIED")
                created += int(fresh)
            self.store.record_specialist_check("epa", subject_id=issuer["subject_id"],
                                               identity_key=f"{issuer['cik']}:{issuer['company_name']}",
                                               as_of=clock, candidate_count=len(candidates), truncated=incomplete)
            checked += 1
            truncated += int(incomplete)
        return {"status":"PARTIAL" if failed or truncated or exhausted else "OK",
                "checked_issuers":checked,"new_findings":created,"failed_issuers":failed,
                "truncated_issuers":truncated,"rejected_rows":rejected,"budget_exhausted":exhausted}
