from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.services.public_source_budget import (
    PUBLIC_DAILY_LIMITS, PublicBudgetExhausted, PublicSourceBudget,
)
from market_checker_app.utils.source_validation import public_https_reference


BASE_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
DEFAULT_MODELS = Path(__file__).resolve().parents[1] / "data" / "verified_nhtsa_models.json"
MAX_RESPONSE_BYTES = 4_000_000
SUCCESS_MESSAGE = "Results returned successfully"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # One shared reservation permits one transport operation. Even an
        # official redirect cannot start another request before scope checks.
        return None


def urlopen(request: Request, *, timeout: float):
    """NHTSA-only transport seam; reject redirects before any second I/O.

    Preserve the existing mock seam without using urllib's default opener.
    Build lazily so importing the read-only reporting API initializes no client.
    """
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def recall_url(make: str, model: str, model_year: int) -> str:
    return f"{BASE_URL}?{urlencode({'make': make, 'model': model, 'modelYear': model_year})}"


def validate_nhtsa_response_scope(final_url: str, requested_url: str) -> None:
    """One exact authority/query gate shared by collection and raw case review."""
    actual, requested = urlsplit(final_url), urlsplit(requested_url)
    if (actual.scheme != "https" or actual.hostname != "api.nhtsa.gov"
            or actual.port not in {None, 443} or actual.username or actual.password
            or actual.path != requested.path or actual.fragment
            or sorted(parse_qsl(actual.query, keep_blank_values=True))
            != sorted(parse_qsl(requested.query, keep_blank_values=True))):
        raise ValueError("NHTSA redirected outside exact official query")


class NhtsaClient(Protocol):
    def recalls(self, make: str, model: str, model_year: int) -> dict: ...


class NhtsaRecallClient:
    is_live = True

    def recalls(self, make: str, model: str, model_year: int) -> dict:
        url = recall_url(make, model, model_year)
        with urlopen(Request(url, headers={"Accept": "application/json",
                                           "User-Agent": "JohnySkoreScout/1.0"}), timeout=20) as response:
            validate_nhtsa_response_scope(response.geturl(), url)
            if response.getcode() != 200:
                raise ValueError("NHTSA response was not HTTP 200")
            if response.headers.get_content_type() != "application/json":
                raise ValueError("NHTSA response was not JSON")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("NHTSA response exceeds byte bound")
            return json.loads(body, object_pairs_hook=_unique_json_object)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous duplicate key in NHTSA response")
        result[key] = value
    return result


def _validated_rows(payload: dict) -> list:
    if not isinstance(payload, dict):
        raise ValueError("NHTSA response must be an object")
    rows, count = payload["results"], payload["Count"]
    if (not isinstance(rows, list) or isinstance(count, bool) or not isinstance(count, int)
            or count != len(rows) or len(rows) > 1000):
        raise ValueError("Malformed or unbounded NHTSA response")
    # Nonempty legacy client fixtures are still validated by every exact row.
    # An empty result has no rows to establish success: require the publisher's
    # success envelope, and never convert an explicit failure into a negative.
    if (not rows or "Message" in payload) and payload.get("Message") != SUCCESS_MESSAGE:
        raise ValueError("NHTSA response does not establish publisher success")
    if any(payload.get(key) for key in ("Error", "Errors", "error", "errors")):
        raise ValueError("NHTSA response includes publisher error metadata")
    return rows


def load_verified_models(path: Path = DEFAULT_MODELS) -> list[dict]:
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("NHTSA model manifest must be a list")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("NHTSA model identity must be an object")
        ticker, year = entry.get("ticker"), entry.get("model_year")
        if (not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", ticker)
                or isinstance(year, bool) or not isinstance(year, int) or not 1990 <= year <= 2100):
            raise ValueError("NHTSA model needs ticker and year")
        for key in ("make", "model", "manufacturer"):
            if not isinstance(entry.get(key), str) or not re.fullmatch(r"[A-Za-z0-9., -]{2,80}", entry[key]):
                raise ValueError("NHTSA model needs exact names")
        identity_key = (ticker, entry["make"], entry["model"], year)
        if identity_key in seen:
            raise ValueError("Duplicate NHTSA model identity")
        seen.add(identity_key)
        evidence = urlsplit(public_https_reference(entry["product_evidence_url"]))
        if (evidence.hostname != "www.sec.gov" or evidence.port not in {None, 443}
                or not evidence.path.startswith("/Archives/edgar/data/")):
            raise ValueError("NHTSA product needs SEC filing evidence")
        known = datetime.fromisoformat(entry["known_at"])
        if known.tzinfo is None or known.utcoffset() is None:
            raise ValueError("NHTSA relationship needs observed time")
    return entries


class NhtsaRecallScoutService:
    """Observe model-year campaigns for explicitly documented issuer products."""

    def __init__(self, store: ScoutStore, *, client: NhtsaClient,
                 models: list[dict], max_models: int = 10,
                 max_run_seconds: float = 180) -> None:
        if not 1 <= max_models <= 25:
            raise ValueError("NHTSA query budget must be bounded")
        self.store, self.client, self.models, self.max_models = store, client, models, max_models
        if (isinstance(max_run_seconds, bool) or not math.isfinite(max_run_seconds)
                or not 1 <= max_run_seconds <= 180):
            raise ValueError("NHTSA run time must be bounded")
        self.max_run_seconds = max_run_seconds

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("NHTSA observation time needs timezone")
        if getattr(self.client, "is_live", False):
            actual = datetime.now(timezone.utc)
            if clock.astimezone(timezone.utc).date() != actual.date():
                raise ValueError("LIVE NHTSA requests require their actual UTC day")
            clock = actual
        eligible = [entry for entry in self.models if (universe is None or entry["ticker"] in universe)
                    and datetime.fromisoformat(entry["known_at"]) <= clock]
        if not eligible:
            return {"status": "WAIT_IDENTITY", "checked_models": 0, "new_findings": 0}
        if "nhtsa" not in PUBLIC_DAILY_LIMITS:
            return {"status": "WAIT_SHARED_BUDGET", "checked_models": 0, "new_findings": 0}
        budget = PublicSourceBudget(self.store, "nhtsa", as_of=clock,
                                    max_run_seconds=self.max_run_seconds)
        if not budget.acquire():
            return {**budget.blocked_summary(), "checked_models": 0,
                    "new_findings": 0, "budget_subject_type": "VEHICLE_MAKE_ONLY"}
        try:
            result = budget.annotate(self._run_due(eligible, clock=clock, budget=budget))
            result["budget_subject_type"] = "VEHICLE_MAKE_ONLY"
            if budget.stop_status == "WAIT_QUOTA":
                result["status"] = "WAIT_QUOTA"
            return result
        finally:
            budget.release()

    def _run_due(self, eligible: list[dict], *, clock: datetime,
                 budget: PublicSourceBudget) -> dict[str, int | str]:
        checked = created = failed = rejected = 0
        due = []
        for entry in eligible:
            # Scope corrections must invalidate old monthly negative checks.
            # The version also prevents reuse of empties accepted without a
            # successful publisher envelope by older collector versions.
            identity_key = "publisher-success-v2:" + hashlib.sha256(json.dumps(
                {key: entry[key] for key in ("make", "model", "model_year", "manufacturer",
                                             "product_evidence_url", "known_at")},
                sort_keys=True).encode()).hexdigest()
            if not self.store.specialist_check_due("nhtsa", subject_id=entry["ticker"],
                                                   identity_key=identity_key, as_of=clock):
                continue
            due.append({"subject_id": "MAKE:" + entry["make"].upper(),
                        "entry": entry, "identity_key": identity_key})
        for candidate in budget.select_due(due):
            if checked + failed >= self.max_models:
                break
            entry, identity_key = candidate["entry"], candidate["identity_key"]
            try:
                payload = budget.call(candidate["subject_id"], identity_key,
                    lambda: self.client.recalls(entry["make"], entry["model"], entry["model_year"]))
                rows = _validated_rows(payload)
            except PublicBudgetExhausted:
                break
            except HTTPError as exc:
                if exc.code in {401, 403, 429}:
                    return {"status": "RATE_LIMITED" if exc.code == 429 else "ACCESS_BLOCKED",
                            "checked_models": checked, "new_findings": created,
                            "failed_models": failed + 1}
                failed += 1
                continue
            except (OSError, ValueError, KeyError, TypeError):
                failed += 1
                continue
            checked += 1
            matched = 0
            rejected_before = rejected
            for row in rows:
                if not isinstance(row, dict):
                    rejected += 1
                    continue
                if (str(row.get("Make", "")).casefold() != entry["make"].casefold()
                        or str(row.get("Model", "")).casefold() != entry["model"].casefold()
                        or str(row.get("ModelYear", "")) != str(entry["model_year"])
                        or str(row.get("Manufacturer", "")).casefold() != entry["manufacturer"].casefold()):
                    rejected += 1
                    continue
                campaign = row.get("NHTSACampaignNumber")
                if not isinstance(campaign, str) or not re.fullmatch(r"\d{2}[A-Z]\d{6}", campaign):
                    rejected += 1
                    continue
                try:
                    # Live recall API evidence uses day/month/year (18/06/2025).
                    # Use that contract consistently; never guess per row.
                    reported = datetime.strptime(row["ReportReceivedDate"], "%d/%m/%Y").date()
                except (KeyError, TypeError, ValueError):
                    rejected += 1
                    continue
                if reported > clock.date():
                    rejected += 1
                    continue
                details = {"stage": "nhtsa_model_year_recall", "make": entry["make"],
                           "model": entry["model"], "model_year": entry["model_year"],
                           "manufacturer": entry["manufacturer"], "campaign": campaign,
                           "report_received_date": reported.isoformat(),
                           "component": str(row.get("Component") or "")[:300],
                           "summary": str(row.get("Summary") or "")[:1000],
                           "park_it": row.get("parkIt"), "park_outside": row.get("parkOutSide"),
                           "product_evidence_url": entry["product_evidence_url"],
                           "product_relationship_known_at": entry["known_at"],
                           "report_date_is_publication_time": False,
                           "issuer_financial_impact_verified": False, "scoring_applied": False}
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="nhtsa", subject_id=entry["ticker"],
                    source_object_id=f"{entry['make']}:{entry['model']}:{entry['model_year']}:{campaign}",
                    content_hash=digest, title=f"NHTSA {campaign} – {entry['make']} {entry['model']}",
                    source_url=recall_url(entry["make"], entry["model"], entry["model_year"]),
                    locator=f"NHTSACampaignNumber:{campaign}", published_at=clock,
                    available_at=clock, observed_at=clock, details=details)
                created += int(fresh)
                matched += 1
            self.store.record_specialist_check("nhtsa", subject_id=entry["ticker"],
                                               identity_key=identity_key, as_of=clock,
                                               candidate_count=matched,
                                               truncated=rejected > rejected_before)
        return {"status": "PARTIAL" if failed or rejected or len(eligible) > self.max_models else "OK",
                "checked_models": checked, "new_findings": created, "failed_models": failed,
                "rejected_rows": rejected}
