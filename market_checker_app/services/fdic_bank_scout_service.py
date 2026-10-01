from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference


FINANCIALS_URL = "https://api.fdic.gov/banks/financials"
DEFAULT_IDENTITIES = Path(__file__).resolve().parents[1] / "data" / "verified_fdic_banks.json"
MONETARY_FIELDS = ("ASSET", "DEP", "EQ", "NETINC")


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
            return json.load(response)


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
                 identities: list[dict]) -> None:
        self.store, self.client, self.identities = store, client, identities

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        eligible = [entry for entry in self.identities
                    if (universe is None or entry["ticker"] in universe)
                    and datetime.fromisoformat(entry["known_at"]) <= clock]
        if not eligible:
            return {"status": "WAIT_IDENTITY", "checked_banks": 0, "new_findings": 0}
        created = checked = failed = usable = rejected = 0
        for entry in eligible:
            try:
                payload = self.client.financials(entry["cert"])
                rows = payload["data"]
                if not isinstance(rows, list) or len(rows) > 2:
                    raise ValueError("Malformed or unbounded FDIC response")
            except (OSError, ValueError, KeyError, TypeError):
                failed += 1
                continue
            checked += 1
            bank_usable = False
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
                bank_usable = True
            usable += int(bank_usable)
        return {"status": "PARTIAL" if failed or rejected else "OK", "checked_banks": checked,
                "usable_banks": usable, "rejected_rows": rejected,
                "new_findings": created, "failed_banks": failed}
