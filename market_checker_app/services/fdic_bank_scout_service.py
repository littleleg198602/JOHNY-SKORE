from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.ticker_universe import load_canonical_tickers


FINANCIALS_URL = "https://api.fdic.gov/banks/financials"
DEFAULT_IDENTITIES = Path(__file__).resolve().parents[1] / "data" / "verified_fdic_banks.json"
MONETARY_FIELDS = ("ASSET", "DEP", "EQ", "NETINC")
FDIC_REFRESH_DAYS = 30
FDIC_DAILY_REQUEST_BUDGET = 10


class FdicFinancialClient(Protocol):
    def financials(self, cert: int) -> dict: ...


class FdicBankFindClient:
    def financials(self, cert: int) -> dict:
        if isinstance(cert, bool) or not isinstance(cert, int) or cert <= 0:
            raise ValueError("Invalid FDIC certificate")
        query = urlencode({"filters": f"CERT:{cert}",
                           "fields": "CERT,NAME,REPDTE,ASSET,DEP,EQ,NETINC",
                           "sort_by": "REPDTE", "sort_order": "DESC", "limit": 2,
                           "format": "json"})
        request = Request(f"{FINANCIALS_URL}?{query}",
                          headers={"Accept": "application/json",
                                   "User-Agent": "JohnySkoreScout/1.0"})
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname != "api.fdic.gov":
                raise ValueError("FDIC redirected outside official host")
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ValueError("FDIC response exceeds byte budget")
            return json.loads(body)


def fdic_identity_key(entry: dict, clock: datetime) -> str:
    """A changed legal relationship or newly known financial alias is due again."""
    keys = ("ticker", "cert", "bank_name", "fdic_evidence_url", "relationship_evidence_url",
            "effective_from", "effective_from_basis", "effective_from_evidence_url",
            "known_at", "issuer_cik", "issuer_name", "issuer_identity_evidence_url")
    identity = {key: entry[key] for key in keys if key in entry}
    if ("financial_name" in entry
            and datetime.fromisoformat(entry["financial_name_known_at"]) <= clock):
        identity.update({key: entry[key] for key in
                         ("financial_name", "financial_name_known_at", "financial_name_evidence_url")})
    return "latest-two-v1:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def fdic_coverage(store: ScoutStore, identities: list[dict], *, as_of: datetime,
                  subjects: set[str]) -> dict[str, int]:
    """Count recent requests for mapped subsidiaries, never complete issuer groups."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("FDIC coverage time needs timezone")
    eligible = [entry for entry in identities if entry["ticker"] in subjects
                and datetime.fromisoformat(entry["known_at"]) <= as_of]
    states = store.fdic_check_states(as_of=as_of)
    result = {"mapped_subjects": len({entry["ticker"] for entry in eligible}),
              "mapped_banks": len(eligible), "never_attempted_banks": 0, "stale_banks": 0,
              "current_usable_banks": 0, "current_partial_banks": 0,
              "current_empty_banks": 0, "current_failed_banks": 0, "inflight_banks": 0}
    for entry in eligible:
        state = states.get((entry["ticker"], fdic_identity_key(entry, as_of)))
        if state is None:
            result["never_attempted_banks"] += 1
        elif state["attempted_at"] <= (as_of - timedelta(days=FDIC_REFRESH_DAYS)).astimezone(timezone.utc).isoformat():
            result["stale_banks"] += 1
        else:
            key = {"USABLE": "current_usable_banks", "PARTIAL": "current_partial_banks",
                   "EMPTY": "current_empty_banks", "FAILED": "current_failed_banks",
                   "RUNNING": "inflight_banks"}[state["outcome"]]
            result[key] += 1
    return result


def load_verified_banks(path: Path = DEFAULT_IDENTITIES) -> list[dict]:
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("FDIC identity manifest must be a list")
    seen: set[tuple[str, int]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("FDIC identity must be an object")
        ticker, cert = entry.get("ticker"), entry.get("cert")
        if (not isinstance(ticker, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", ticker)
                or isinstance(cert, bool) or not isinstance(cert, int) or cert <= 0):
            raise ValueError("FDIC identity needs canonical ticker and CERT")
        if (ticker, cert) in seen:
            raise ValueError("Duplicate ticker/CERT relationship")
        seen.add((ticker, cert))
        if not isinstance(entry.get("bank_name"), str) or not entry["bank_name"].strip():
            raise ValueError("FDIC identity needs exact bank name")
        for key in ("fdic_evidence_url", "relationship_evidence_url"):
            url = public_https_reference(entry.get(key, ""))
            host = urlsplit(url).hostname
            if key == "fdic_evidence_url" and host not in {"www.fdic.gov", "banks.data.fdic.gov"}:
                raise ValueError("FDIC identity needs official bank citation")
            if key == "relationship_evidence_url" and host != "www.sec.gov":
                raise ValueError("FDIC relationship needs official SEC citation")
            if key == "fdic_evidence_url" and not urlsplit(url).path.rstrip("/").endswith(f"/{cert}"):
                raise ValueError("FDIC evidence must point to the cited CERT")
        issuer_keys = ("issuer_cik", "issuer_name", "issuer_identity_evidence_url")
        if any(key in entry for key in issuer_keys):
            if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in issuer_keys):
                raise ValueError("FDIC issuer identity needs CIK, exact name and citation")
            cik = entry["issuer_cik"]
            if not re.fullmatch(r"\d{10}", cik) or int(cik) == 0:
                raise ValueError("Invalid FDIC issuer CIK")
            for key in ("issuer_identity_evidence_url", "relationship_evidence_url"):
                citation = urlsplit(public_https_reference(entry[key]))
                prefix = f"/Archives/edgar/data/{int(cik)}/"
                if citation.hostname != "www.sec.gov" or not citation.path.startswith(prefix):
                    raise ValueError("FDIC issuer citations must match its exact SEC CIK")
        floor_keys = ("effective_from_basis", "effective_from_evidence_url")
        if any(key in entry for key in floor_keys):
            if (entry.get("effective_from_basis") != "filing_publication_floor"
                    or "issuer_cik" not in entry):
                raise ValueError("FDIC publication floor needs exact issuer identity and basis")
            citation = urlsplit(public_https_reference(entry.get("effective_from_evidence_url", "")))
            prefix = f"/Archives/edgar/data/{int(entry['issuer_cik'])}/"
            if (citation.hostname != "www.sec.gov" or citation.port not in {None, 443}
                    or not citation.path.startswith(prefix)):
                raise ValueError("FDIC publication floor citation must match its exact SEC CIK")
        start = date.fromisoformat(entry["effective_from"])
        known = datetime.fromisoformat(entry["known_at"])
        if known.tzinfo is None or known.utcoffset() is None or start > known.date():
            raise ValueError("FDIC relationship needs dated evidence")
        alias_keys = ("financial_name", "financial_name_known_at", "financial_name_evidence_url")
        if any(key in entry for key in alias_keys):
            if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in alias_keys):
                raise ValueError("FDIC financial name needs exact name, observation and citation")
            alias_known = datetime.fromisoformat(entry["financial_name_known_at"])
            if alias_known.tzinfo is None or alias_known.utcoffset() is None:
                raise ValueError("FDIC financial name needs observed time")
            alias_url = urlsplit(public_https_reference(entry["financial_name_evidence_url"]))
            filters = parse_qs(alias_url.query).get("filters", [])
            if (alias_url.hostname != "api.fdic.gov" or alias_url.port not in {None, 443}
                    or alias_url.path != "/banks/financials" or filters != [f"CERT:{cert}"]):
                raise ValueError("FDIC financial name citation must match the exact CERT")
    return entries


class FdicBankScoutService:
    """Observe bank subsidiary balance sheets for dated, cited CERT links."""

    def __init__(self, store: ScoutStore, *, client: FdicFinancialClient,
                 identities: list[dict], max_banks: int = 5,
                 daily_request_budget: int = FDIC_DAILY_REQUEST_BUDGET,
                 max_run_seconds: float = 60, max_failures: int = 3) -> None:
        for value, upper in ((max_banks, 20), (daily_request_budget, 50), (max_failures, 10)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError("FDIC query budget must be bounded")
        if isinstance(max_run_seconds, bool) or not 1 <= max_run_seconds <= 300:
            raise ValueError("FDIC time budget must be bounded")
        self.store, self.client, self.identities = store, client, identities
        self.max_banks, self.daily_request_budget = max_banks, daily_request_budget
        self.max_run_seconds, self.max_failures = max_run_seconds, max_failures

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None, recheck: bool = False) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        scope = set(load_canonical_tickers())
        if universe is not None:
            scope &= universe
        eligible = [entry for entry in self.identities
                    if entry["ticker"] in scope
                    and datetime.fromisoformat(entry["known_at"]) <= clock]
        if not eligible:
            return {"status": "WAIT_IDENTITY", "checked_banks": 0, "new_findings": 0}
        token = self.store.claim_provider("fdic", as_of=clock, seconds=math.ceil(self.max_run_seconds) + 25)
        if token is None:
            cooldown = self.store.provider_cooldown("fdic", as_of=clock)
            return {"status": ("RATE_LIMITED" if cooldown["reason"] == "FDIC HTTP 429"
                               else "ACCESS_BLOCKED") if cooldown else "BUSY",
                    "checked_banks": 0, "new_findings": 0,
                    **({"retry_at": cooldown["retry_at"]} if cooldown else {})}
        try:
            return self._run_claimed(eligible, clock, token, recheck=recheck)
        finally:
            self.store.release_provider("fdic", token)

    def _run_claimed(self, eligible: list[dict], clock: datetime, token: str, *, recheck: bool):
        states = self.store.fdic_check_states(as_of=clock)
        due = []
        for entry in eligible:
            key = fdic_identity_key(entry, clock)
            state = states.get((entry["ticker"], key))
            interval = FDIC_REFRESH_DAYS if state and state["outcome"] == "USABLE" else 1
            if recheck or state is None or datetime.fromisoformat(state["attempted_at"]) <= clock - timedelta(days=interval):
                due.append((state["attempted_at"] if state else "", entry["ticker"], entry["cert"], entry, key))
        due.sort(key=lambda item: item[:3])
        created = checked = failed = usable = rejected = attempted = empty = 0
        status = "OK" if due else "NO_DUE_WORK"
        exhausted = False
        deadline = time.monotonic() + self.max_run_seconds
        for _, _, _, entry, identity_key in due:
            if attempted >= self.max_banks or failed >= self.max_failures or time.monotonic() >= deadline:
                exhausted = True
                break
            request_id = self.store.reserve_fdic_request(
                subject_id=entry["ticker"], identity_key=identity_key, as_of=clock,
                daily_limit=self.daily_request_budget, lease_token=token)
            if request_id is None:
                exhausted = True
                break
            attempted += 1
            try:
                payload = self.client.financials(entry["cert"])
                rows = payload["data"]
                if not isinstance(rows, list) or len(rows) > 2:
                    raise ValueError("Malformed or unbounded FDIC response")
            except HTTPError as exc:
                failed += 1
                self.store.finish_fdic_request(request_id, as_of=clock, outcome="FAILED")
                if exc.code in {401, 403, 429}:
                    status = "RATE_LIMITED" if exc.code == 429 else "ACCESS_BLOCKED"
                    self.store.defer_provider("fdic", token, as_of=clock,
                                              retry_after=timedelta(hours=1 if exc.code == 429 else 24),
                                              reason=f"FDIC HTTP {exc.code}")
                    break
                continue
            except (OSError, ValueError, KeyError, TypeError):
                failed += 1
                self.store.finish_fdic_request(request_id, as_of=clock, outcome="FAILED")
                continue
            checked += 1
            bank_usable = 0
            bank_rejected_before = rejected
            seen_dates = set()
            expected_name = entry["bank_name"]
            alias_available = ("financial_name" in entry and
                               datetime.fromisoformat(entry["financial_name_known_at"]) <= clock)
            if alias_available:
                expected_name = entry["financial_name"]
            for wrapper in rows:
                row = wrapper.get("data") if isinstance(wrapper, dict) else None
                if (not isinstance(row, dict) or isinstance(row.get("CERT"), bool)
                        or not isinstance(row.get("CERT"), int) or row["CERT"] != entry["cert"]):
                    rejected += 1
                    continue
                if str(row.get("NAME", "")).strip().casefold() != expected_name.strip().casefold():
                    rejected += 1
                    continue
                try:
                    raw_date = str(row["REPDTE"])
                    report_date = (datetime.strptime(raw_date, "%Y%m%d").date()
                                   if re.fullmatch(r"\d{8}", raw_date)
                                   else date.fromisoformat(raw_date))
                except (ValueError, KeyError):
                    rejected += 1
                    continue
                if not date.fromisoformat(entry["effective_from"]) <= report_date <= clock.date():
                    rejected += 1
                    continue
                if report_date in seen_dates:
                    rejected += 1
                    continue
                seen_dates.add(report_date)
                values: dict[str, int | float | None] = {}
                for field in MONETARY_FIELDS:
                    value = row.get(field)
                    if (value is not None and not isinstance(value, bool)
                            and isinstance(value, (int, float)) and math.isfinite(value)):
                        values[field] = value
                    else:
                        values[field] = None
                if not any(value is not None for value in values.values()):
                    rejected += 1
                    continue
                details = {"stage": "bank_subsidiary_financials", "cert": entry["cert"],
                           "bank_name": entry["bank_name"], "report_date": report_date.isoformat(),
                           "source_financial_name": row["NAME"],
                           "amounts_thousands_usd": values,
                           "fdic_evidence_url": entry["fdic_evidence_url"],
                           "relationship_evidence_url": entry["relationship_evidence_url"],
                           "relationship_effective_from": entry["effective_from"],
                           "relationship_known_at": entry["known_at"],
                           "issuer_consolidated_values": False,
                           "report_date_is_publication_time": False,
                           "scoring_applied": False}
                if entry.get("effective_from_basis") == "filing_publication_floor":
                    # An undated exhibit plus its publication date does not
                    # establish an ownership effective date or historical as-of.
                    details.update(relationship_effective_from=None,
                                   relationship_as_of_verified=False,
                                   report_date_eligibility_from=entry["effective_from"],
                                   report_date_eligibility_basis=entry["effective_from_basis"],
                                   report_date_floor_evidence_url=entry["effective_from_evidence_url"])
                if alias_available:
                    details.update(financial_name_known_at=entry["financial_name_known_at"],
                                   financial_name_evidence_url=entry["financial_name_evidence_url"])
                if "issuer_cik" in entry:
                    details.update(issuer_cik=entry["issuer_cik"], issuer_name=entry["issuer_name"],
                                   issuer_identity_evidence_url=entry["issuer_identity_evidence_url"])
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="fdic", subject_id=entry["ticker"],
                    source_object_id=f"{entry['cert']}:{report_date.isoformat()}",
                    content_hash=digest,
                    title=f"FDIC {entry['bank_name']}: {report_date.isoformat()}",
                    source_url=FINANCIALS_URL,
                    locator=f"CERT:{entry['cert']}/REPDTE:{report_date.isoformat()}",
                    published_at=clock, available_at=clock, observed_at=clock,
                    details=details,
                )
                created += int(fresh)
                bank_usable += 1
            usable += int(bank_usable > 0)
            bank_rejected = rejected - bank_rejected_before
            empty += int(not rows)
            self.store.finish_fdic_request(request_id, as_of=clock,
                                           outcome="PARTIAL" if bank_rejected else "USABLE" if bank_usable else "EMPTY",
                                           usable_rows=bank_usable, rejected_rows=bank_rejected)
        if status == "OK" and (failed or rejected or empty or exhausted):
            status = "PARTIAL"
        return {"status": status, "checked_banks": checked,
                "usable_banks": usable, "rejected_rows": rejected,
                "new_findings": created, "failed_banks": failed, "empty_banks": empty,
                "attempted_banks": attempted, "due_banks": len(due),
                "deferred_banks": len(due) - attempted, "budget_exhausted": exhausted,
                "daily_requests": self.store.fdic_daily_requests(as_of=clock)}
