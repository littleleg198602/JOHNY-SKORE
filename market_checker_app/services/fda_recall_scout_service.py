from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore


API_ROOT = "https://api.fda.gov"
PRODUCT_TYPES = ("drug", "device", "food")


class FdaRecallClient(Protocol):
    def recalls(self, product_type: str, firm_name: str, *, limit: int, skip: int = 0) -> dict: ...
    def complete_response_letters(self, firm_name: str, *, limit: int, skip: int = 0) -> dict: ...


class OpenFdaRecallClient:
    """Search the public enforcement reports; no API key is required."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def _query(self, endpoint: str, search: str, limit: int, skip: int) -> dict:
        query = {"search": search, "limit": limit}
        if skip:
            query["skip"] = skip
        if self.api_key:
            query["api_key"] = self.api_key
        request = Request(f"{API_ROOT}/{endpoint}?{urlencode(query)}",
                          headers={"User-Agent": "JohnySkoreScout/1.0"})
        try:
            with urlopen(request, timeout=20) as response:
                if urlsplit(response.geturl()).hostname != "api.fda.gov":
                    raise ValueError("FDA redirected outside official host")
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                return {"results": [], "meta": {"results": {"total": 0}}}
            raise

    def recalls(self, product_type: str, firm_name: str, *, limit: int, skip: int = 0) -> dict:
        if product_type not in PRODUCT_TYPES or not 1 <= limit <= 1000 or not 0 <= skip <= 25000:
            raise ValueError("Unsupported FDA recall request")
        # The API search can match a phrase loosely; the service checks the
        # entire recalling_firm field again before attributing a candidate.
        phrase = firm_name.replace("\\", "\\\\").replace('"', '\\"')
        return self._query(f"{product_type}/enforcement.json",
                           f'recalling_firm:"{phrase}"', limit, skip)

    def complete_response_letters(self, firm_name: str, *, limit: int, skip: int = 0) -> dict:
        if not 1 <= limit <= 1000 or not 0 <= skip <= 25000:
            raise ValueError("Unsupported FDA CRL request")
        phrase = firm_name.replace("\\", "\\\\").replace('"', '\\"')
        return self._query("transparency/crl.json", f'company_name:"{phrase}"', limit, skip)


class FdaRecallScoutService:
    """Collect exact-name recall candidates, without inferring product ownership."""

    def __init__(self, store: ScoutStore, *, client: FdaRecallClient,
                 max_subjects: int = 25, max_results: int = 100,
                 max_pages: int = 2) -> None:
        if max_subjects < 1 or not 1 <= max_results <= 1000 or not 1 <= max_pages <= 10:
            raise ValueError("FDA request budget must be positive and bounded")
        self.store, self.client = store, client
        self.max_subjects, self.max_results, self.max_pages = max_subjects, max_results, max_pages

    def _pages(self, fetch: Callable[[int], dict]) -> tuple[list[dict], bool]:
        """Collect bounded offset pages; never call an incomplete result complete."""
        combined: list[dict] = []
        expected_total: int | None = None
        for page in range(self.max_pages):
            payload = fetch(page * self.max_results)
            rows = payload["results"]
            meta = payload["meta"]["results"]
            total = meta["total"]
            if (not isinstance(rows, list) or not isinstance(total, int)
                    or isinstance(total, bool) or total < len(combined) + len(rows)
                    or len(rows) > self.max_results):
                raise ValueError("Malformed FDA response")
            if "skip" in meta and meta["skip"] != page * self.max_results:
                raise ValueError("FDA page offset mismatch")
            if expected_total is not None and total != expected_total:
                return combined, True
            expected_total = total
            combined.extend(rows)
            if len(combined) >= total:
                return combined, False
            if not rows or len(rows) != self.max_results:
                return combined, True
        return combined, True

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        # Filter after fetching due names, so an outside-universe name cannot
        # exhaust the daily budget of the requested watchlist.
        due = self.store.specialist_due("fda", as_of=clock, limit=900)
        due = [row for row in due if universe is None or row["subject_id"] in universe]
        due = due[:self.max_subjects]
        created = checked = failed = truncated_count = 0
        for issuer in due:
            ticker, name, cik = (issuer[key] for key in ("subject_id", "company_name", "cik"))
            candidates: list[tuple[str, dict]] = []
            letter_candidates: list[dict] = []
            truncated = False
            try:
                for product_type in PRODUCT_TYPES:
                    rows, partial = self._pages(lambda skip: self.client.recalls(
                        product_type, name, limit=self.max_results, skip=skip))
                    truncated |= partial
                    for row in rows:
                        if not isinstance(row, dict) or str(row.get("recalling_firm", "")).strip().casefold() != name.strip().casefold():
                            continue
                        number = row.get("recall_number")
                        report_date = row.get("report_date")
                        if not isinstance(number, str) or not number or not isinstance(report_date, str):
                            continue
                        try:
                            reported = date.fromisoformat(f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:8]}")
                        except ValueError:
                            continue
                        if reported > clock.date():
                            continue
                        candidates.append((product_type, row))
                rows, partial = self._pages(lambda skip: self.client.complete_response_letters(
                    name, limit=self.max_results, skip=skip))
                truncated |= partial
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("company_name", "")).strip().casefold() != name.strip().casefold():
                        continue
                    if str(row.get("letter_type", "")).strip().upper() != "COMPLETE RESPONSE":
                        continue
                    application = row.get("application_number")
                    filename = row.get("file_name")
                    letter_date = row.get("letter_date")
                    if not all(isinstance(value, str) and value.strip()
                               for value in (application, filename, letter_date)):
                        continue
                    try:
                        signed = datetime.strptime(letter_date, "%m/%d/%Y").date()
                    except ValueError:
                        continue
                    if signed > clock.date():
                        continue
                    letter_candidates.append(row)
            except HTTPError as exc:
                if exc.code in {403, 429}:
                    return {"status": "RATE_LIMITED" if exc.code == 429 else "ACCESS_BLOCKED",
                            "checked_issuers": checked, "new_findings": created,
                            "failed_issuers": failed + 1, "truncated_issuers": truncated_count}
                failed += 1
                continue
            except (OSError, ValueError, KeyError, TypeError):
                failed += 1
                continue  # Retry the issuer next time; do not mark its check complete.
            for product_type, row in candidates:
                number = row["recall_number"]
                details = {
                    "stage": "recall_candidate", "product_type": product_type,
                    "recall_number": number, "report_date": row["report_date"],
                    "recalling_firm": row["recalling_firm"],
                    "product_description": row.get("product_description"),
                    "reason_for_recall": row.get("reason_for_recall"),
                    "classification": row.get("classification"),
                    "status": row.get("status"), "issuer_sec_cik": cik,
                    "identity_status": "NAME_ONLY",
                    "missing_evidence": "dated manufacturer, product and issuer relationship",
                    "product_attribution_allowed": False,
                    "report_date_is_publication_time": False,
                }
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="fda", subject_id=ticker,
                    source_object_id=f"{product_type}:{number}", content_hash=digest,
                    title=f"FDA recall candidate: {product_type} {number}",
                    source_url=f"{API_ROOT}/{product_type}/enforcement.json",
                    locator=f"recall_number:{number}", published_at=clock,
                    available_at=clock, observed_at=clock,
                    verification_status="UNVERIFIED", details=details,
                )
                created += int(fresh)
            for row in letter_candidates:
                application = row["application_number"]
                filename = row["file_name"]
                letter_date = row["letter_date"]
                details = {
                    "stage": "crl_candidate", "application_number": application,
                    "file_name": filename, "letter_date": letter_date,
                    "letter_type": "COMPLETE RESPONSE", "company_name": row["company_name"],
                    "issuer_sec_cik": cik, "identity_status": "NAME_ONLY",
                    "missing_evidence": "dated application, sponsor, product and issuer relationship",
                    "product_attribution_allowed": False,
                    "letter_date_is_publication_time": False,
                }
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="fda", subject_id=ticker,
                    source_object_id=f"crl:{application}:{filename}:{letter_date}",
                    content_hash=digest,
                    title=f"FDA CRL candidate: {application}",
                    source_url=f"{API_ROOT}/transparency/crl.json",
                    locator=f"application:{application}/file:{filename}",
                    # FDA can disclose historic letters much later than signing.
                    published_at=clock, available_at=clock, observed_at=clock,
                    verification_status="UNVERIFIED", details=details,
                )
                created += int(fresh)
            self.store.record_specialist_check(
                "fda", subject_id=ticker, identity_key=f"{cik}:{name}",
                as_of=clock, candidate_count=len(candidates) + len(letter_candidates),
                truncated=truncated,
            )
            checked += 1
            truncated_count += int(truncated)
        return {"status": "PARTIAL" if failed or truncated_count else "OK",
                "checked_issuers": checked, "new_findings": created,
                "failed_issuers": failed, "truncated_issuers": truncated_count}
