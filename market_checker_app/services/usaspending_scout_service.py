from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference


API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DEFAULT_IDENTITIES = Path(__file__).resolve().parents[1] / "data" / "verified_usaspending_uei.json"
FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "generated_internal_id",
          "Start Date", "Award Amount", "Total Outlays", "Last Modified Date",
          "Awarding Agency", "Contract Award Type"]


class AwardsClient(Protocol):
    def awards(self, uei: str, *, start: date, end: date, page: int) -> dict: ...


class UsaSpendingApiClient:
    def awards(self, uei: str, *, start: date, end: date, page: int) -> dict:
        payload = {"filters": {"award_type_codes": ["A", "B", "C", "D"],
                               "recipient_search_text": [uei],
                               "time_period": [{"start_date": start.isoformat(),
                                                "end_date": end.isoformat()}]},
                   "fields": FIELDS, "limit": 100, "page": page,
                   "sort": "Last Modified Date", "order": "desc"}
        request = Request(API_URL, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "JohnySkoreScout/1.0"}, method="POST")
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname != "api.usaspending.gov":
                raise ValueError("USAspending redirected outside official host")
            return json.load(response)


def load_verified_identities(path: Path = DEFAULT_IDENTITIES) -> list[dict]:
    """A UEI alone cannot establish which public issuer owns its recipient."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("USAspending identity manifest must be a list")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("USAspending identity entry must be an object")
        ticker, uei = entry.get("ticker"), entry.get("uei")
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", ticker):
            raise ValueError("USAspending identity needs a canonical ticker")
        if not isinstance(uei, str) or not re.fullmatch(r"[A-Z0-9]{12}", uei):
            raise ValueError("USAspending identity needs a twelve-character UEI")
        if (ticker, uei) in seen:
            raise ValueError("Duplicate USAspending ticker/UEI")
        seen.add((ticker, uei))
        for key in ("recipient_name", "uei_evidence_url", "relationship_evidence_url"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"USAspending identity needs {key}")
        for key in ("uei_evidence_url", "relationship_evidence_url"):
            public_https_reference(entry[key])
        if entry.get("continuity_evidence_url"):
            public_https_reference(entry["continuity_evidence_url"])
        first = date.fromisoformat(entry["effective_from"])
        last = date.fromisoformat(entry["effective_to"]) if entry.get("effective_to") else None
        known = datetime.fromisoformat(entry["known_at"])
        if (last and last < first) or known.tzinfo is None or known.utcoffset() is None:
            raise ValueError("USAspending identity needs valid dated relationship and knowledge")
    return entries


class UsaSpendingScoutService:
    """Observe current prime contracts only for independently documented ticker/UEI links."""

    def __init__(self, store: ScoutStore, *, client: AwardsClient,
                 identities: list[dict], max_pages: int = 3) -> None:
        self.store, self.client, self.identities = store, client, identities
        self.max_pages = max_pages

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        if self.max_pages < 1:
            raise ValueError("USAspending page budget must be positive")
        eligible = [e for e in self.identities
                    if (universe is None or e["ticker"] in universe)
                    and datetime.fromisoformat(e["known_at"]) <= clock]
        if not eligible:
            return {"status": "WAIT_IDENTITY", "new_findings": 0, "checked_uei": 0}
        created, partial = 0, 0
        # The date filter bounds API cost. Awards outside this window have not
        # been searched, so absence cannot be interpreted as zero contracts.
        start = clock.date() - timedelta(days=730)
        for entry in eligible:
            for page in range(1, self.max_pages + 1):
                payload = self.client.awards(entry["uei"], start=start,
                                             end=clock.date(), page=page)
                rows, metadata = payload["results"], payload["page_metadata"]
                if not isinstance(rows, list) or not isinstance(metadata.get("hasNext"), bool):
                    raise ValueError("Malformed USAspending page")
                for row in rows:
                    if row.get("Recipient UEI") != entry["uei"]:
                        continue  # text search can return other recipients
                    award_id = row.get("generated_internal_id")
                    if not isinstance(award_id, str) or not award_id:
                        continue
                    try:
                        award_start = date.fromisoformat(str(row["Start Date"])[:10])
                    except (ValueError, KeyError):
                        continue
                    if not (date.fromisoformat(entry["effective_from"]) <= award_start
                            and (not entry.get("effective_to")
                                 or award_start <= date.fromisoformat(entry["effective_to"]))):
                        continue
                    if award_start > clock.date():
                        continue
                    amounts = (row.get("Award Amount"), row.get("Total Outlays"))
                    if any(isinstance(x, bool) or (x is not None and
                            (not isinstance(x, (int, float)) or not math.isfinite(x)))
                           for x in amounts):
                        continue
                    details = {"stage": "prime_contract", "uei": entry["uei"],
                               "recipient_name": row.get("Recipient Name"),
                               "award_id": row.get("Award ID"),
                               "generated_internal_id": award_id,
                               "award_start": award_start.isoformat(),
                               "last_modified_date": row.get("Last Modified Date"),
                               "reported_award_amount_usd": amounts[0],
                               "reported_total_outlays_usd": amounts[1],
                               "awarding_agency": row.get("Awarding Agency"),
                               "contract_award_type": row.get("Contract Award Type"),
                               "uei_evidence_url": entry["uei_evidence_url"],
                               "relationship_evidence_url": entry["relationship_evidence_url"],
                               "continuity_evidence_url": entry.get("continuity_evidence_url"),
                               "relationship_effective_from": entry["effective_from"],
                               "relationship_effective_to": entry.get("effective_to"),
                               "relationship_known_at": entry["known_at"],
                               "revenue_inferred": False,
                               "modification_history_complete": False}
                    digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                    _, new = self.store.record_finding(
                        source="usaspending", subject_id=entry["ticker"],
                        source_object_id=f"{entry['uei']}:{award_id}", content_hash=digest,
                        title=f"USAspending {row.get('Award ID') or award_id} – {entry['recipient_name']}",
                        source_url=API_URL, locator=f"generated_internal_id:{award_id}",
                        published_at=clock, available_at=clock, observed_at=clock,
                        details=details)
                    created += int(new)
                if not metadata["hasNext"]:
                    break
                if page == self.max_pages:
                    partial += 1
        return {"status": "PARTIAL" if partial else "OK", "new_findings": created,
                "checked_uei": len(eligible), "truncated_uei": partial}
