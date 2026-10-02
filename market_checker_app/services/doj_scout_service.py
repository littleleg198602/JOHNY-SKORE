"""Bounded DOJ title-search leads; legal liability and issuer identity stay unverified."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import math
import re
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference


DOJ_URL = "https://www.justice.gov/api/v1/press_releases.json"
MAX_BYTES = 2_000_000


class DojClient(Protocol):
    def page(self, name: str, *, size: int, page: int) -> tuple[list[dict], int]: ...


class DojPressReleaseClient:
    def __init__(self):
        self._next_request_at = 0.0

    def page(self, name: str, *, size: int, page: int) -> tuple[list[dict], int]:
        if (not isinstance(name, str) or not name.strip() or len(name) > 500
                or isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 50
                or isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < 10):
            raise ValueError("DOJ query must be bounded")
        delay = self._next_request_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_request_at = time.monotonic() + 0.35
        query = urlencode({"parameters[title]": name, "fields": "uuid,title,url,date,changed",
                           "pagesize": size, "page": page, "sort": "date", "direction": "DESC"})
        with urlopen(Request(f"{DOJ_URL}?{query}", headers={"Accept": "application/json",
                      "User-Agent": "JohnySkoreScout/1.0"}), timeout=20) as response:
            target = urlsplit(response.geturl())
            if target.scheme != "https" or target.hostname not in {"www.justice.gov", "justice.gov"}:
                raise ValueError("DOJ response left official host")
            body = response.read(MAX_BYTES+1)
            if len(body) > MAX_BYTES:
                raise ValueError("DOJ response exceeds byte budget")
            payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Malformed DOJ response")
        metadata, rows = payload.get("metadata"), payload.get("results")
        if not isinstance(metadata, dict) or not isinstance(rows, list) or len(rows) > size:
            raise ValueError("Malformed DOJ response")
        info, results = metadata.get("responseInfo"), metadata.get("resultset")
        if not isinstance(info, dict) or info.get("status") != 200 or not isinstance(results, dict):
            raise ValueError("DOJ response status is not usable")
        def integer(value):
            if isinstance(value, bool) or not re.fullmatch(r"\d{1,10}", str(value)):
                raise ValueError("Malformed DOJ result count/page")
            return int(value)
        total = integer(results.get("count"))
        if (integer(results.get("page")) != page or integer(results.get("pagesize")) != size
                or total < page*size + len(rows) or any(not isinstance(r, dict) for r in rows)):
            raise ValueError("Inconsistent DOJ result count/page")
        return rows, total


def _name_key(value: str) -> str:
    # Keep every legal-name word, including suffixes; remove markup/punctuation
    # only. A title containing Google is not an exact Alphabet Inc. identity.
    plain = re.sub(r"<[^>]*>", " ", html.unescape(value))
    return " ".join(re.findall(r"[a-z0-9]+", plain.casefold()))


def parse_doj_candidate(row: dict, name: str, clock: datetime) -> tuple[str, dict] | None:
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("DOJ observation needs timezone")
    if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k].strip()
                                           for k in ("uuid", "title", "url", "date")):
        raise ValueError("Malformed DOJ press release")
    try:
        uuid = str(UUID(row["uuid"]))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Malformed DOJ press-release UUID") from exc
    if uuid != row["uuid"].lower():
        raise ValueError("Noncanonical DOJ press-release UUID")
    url = public_https_reference(row["url"])
    parsed = urlsplit(url)
    if (parsed.hostname not in {"www.justice.gov", "justice.gov"}
            or parsed.port not in {None, 443} or "/pr/" not in parsed.path):
        raise ValueError("DOJ release needs its official press-release URL")
    if not re.fullmatch(r"\d{1,12}", row["date"]):
        raise ValueError("Malformed DOJ declared date")
    try:
        declared = datetime.fromtimestamp(int(row["date"]), timezone.utc)
    except (ValueError, OSError, OverflowError) as exc:
        raise ValueError("Malformed DOJ declared date") from exc
    if declared > clock:
        raise ValueError("Future DOJ declared date")
    key, title = _name_key(name), _name_key(row["title"])
    if not key or f" {key} " not in f" {title} ":
        return None  # The API's loose title filter is only a search lead.
    return uuid, {"stage": "doj_press_release_name_candidate", "title": html.unescape(row["title"])[:1000],
                  "release_url": url, "query_entity_name": name,
                  "source_declared_at": declared.isoformat(),
                  "declared_time_is_first_observation": False,
                  "query_scope": "DOJ API title filter and exact full-name words in title",
                  "complete_issuer_legal_risk_coverage": False,
                  "issuer_identity_verified": False, "liability_verified": False,
                  "scoring_applied": False}


class DojPressReleaseScoutService:
    def __init__(self, store: ScoutStore, *, client: DojClient, max_subjects: int = 25,
                 page_size: int = 25, max_pages: int = 2,
                 max_run_seconds: float = 60, max_failures: int = 3):
        for value, upper in ((max_subjects, 100), (page_size, 50), (max_pages, 10), (max_failures, 10)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError("DOJ request budget must be bounded")
        if (isinstance(max_run_seconds, bool) or not isinstance(max_run_seconds, (int, float))
                or not math.isfinite(max_run_seconds) or not 1 <= max_run_seconds <= 300):
            raise ValueError("DOJ time budget must be bounded")
        self.store, self.client = store, client
        self.max_subjects, self.page_size, self.max_pages = max_subjects, page_size, max_pages
        self.max_run_seconds, self.max_failures = max_run_seconds, max_failures

    def run(self, *, as_of: datetime | None = None, universe: set[str] | None = None):
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("DOJ observation needs timezone")
        due = self.store.specialist_due("doj", as_of=clock, limit=900)
        due = [i for i in due if universe is None or i["subject_id"] in universe][:self.max_subjects]
        if not due:
            active = self.store.specialist_coverage("doj", as_of=clock, subjects=universe)["active_identities"]
            return {"status": "OK" if active else "WAIT_IDENTITY", "checked_issuers": 0, "new_findings": 0}
        deadline = time.monotonic() + self.max_run_seconds
        checked = created = failed = truncated_count = rejected = 0
        exhausted = False
        for issuer in due:
            if time.monotonic() >= deadline or failed >= self.max_failures:
                exhausted = True
                break
            candidates, seen = [], set()
            expected_total, incomplete = None, False
            try:
                for page in range(self.max_pages):
                    if time.monotonic() >= deadline:
                        exhausted = True
                        raise TimeoutError("DOJ time budget exhausted")
                    rows, total = self.client.page(issuer["company_name"], size=self.page_size, page=page)
                    if (not isinstance(rows, list) or len(rows) > self.page_size
                            or isinstance(total, bool) or not isinstance(total, int) or total < page*self.page_size+len(rows)):
                        raise ValueError("Malformed DOJ page")
                    if expected_total is not None and total != expected_total:
                        incomplete = True
                    expected_total = total
                    for row in rows:
                        try:
                            candidate = parse_doj_candidate(row, issuer["company_name"], clock)
                        except (ValueError, TypeError, AttributeError):
                            incomplete = True
                            rejected += 1
                            continue
                        if candidate is not None:
                            object_id, details = candidate
                            if object_id in seen:
                                incomplete = True
                                rejected += 1
                                continue
                            seen.add(object_id)
                            candidates.append((object_id, details))
                    if (page+1)*self.page_size >= total:
                        break
                    if len(rows) != self.page_size:
                        incomplete = True
                        break
                else:
                    incomplete = True
            except HTTPError as exc:
                failed += 1
                if exc.code in {401, 403, 429}:
                    return {"status": "RATE_LIMITED" if exc.code == 429 else "ACCESS_BLOCKED",
                            "checked_issuers": checked, "new_findings": created, "failed_issuers": failed,
                            "truncated_issuers": truncated_count, "rejected_rows": rejected,
                            "budget_exhausted": exhausted}
                continue
            except (OSError, ValueError, TypeError, KeyError):
                failed += 1
                continue
            for object_id, detail in candidates:
                detail.update(issuer_sec_cik=issuer["cik"], query_issuer_name=issuer["company_name"])
                digest = hashlib.sha256(json.dumps(detail, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="doj", subject_id=issuer["subject_id"], source_object_id=object_id,
                    content_hash=digest, title=detail["title"], source_url=detail["release_url"],
                    locator=f"DOJ_UUID:{object_id}", published_at=clock, available_at=clock,
                    observed_at=clock, details=detail, verification_status="UNVERIFIED")
                created += int(fresh)
            self.store.record_specialist_check("doj", subject_id=issuer["subject_id"],
                                               identity_key=f"{issuer['cik']}:{issuer['company_name']}",
                                               as_of=clock, candidate_count=len(candidates), truncated=incomplete)
            checked += 1
            truncated_count += int(incomplete)
        return {"status": "PARTIAL" if failed or truncated_count or exhausted else "OK",
                "checked_issuers": checked, "new_findings": created, "failed_issuers": failed,
                "truncated_issuers": truncated_count, "rejected_rows": rejected,
                "budget_exhausted": exhausted}
