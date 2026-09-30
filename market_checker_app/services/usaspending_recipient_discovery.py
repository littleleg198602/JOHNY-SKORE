from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore


RECIPIENT_URL = "https://api.usaspending.gov/api/v2/recipient/"


class RecipientClient(Protocol):
    def recipients(self, name: str, *, page: int) -> dict: ...


class UsaSpendingRecipientClient:
    def recipients(self, name: str, *, page: int) -> dict:
        request = Request(
            RECIPIENT_URL,
            data=json.dumps({"keyword": name, "award_type": "contracts",
                             "page": page, "limit": 50}).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "JohnySkoreScout/1.0"}, method="POST",
        )
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname != "api.usaspending.gov":
                raise ValueError("USAspending recipient lookup redirected outside official host")
            return json.load(response)


class UsaSpendingRecipientDiscovery:
    """Find public recipient candidates; never imply corporate ownership from a name."""

    def __init__(self, store: ScoutStore, *, client: RecipientClient,
                 max_subjects: int = 100, max_pages: int = 2) -> None:
        self.store, self.client = store, client
        self.max_subjects, self.max_pages = max_subjects, max_pages

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        if self.max_subjects < 1 or self.max_pages < 1:
            raise ValueError("Recipient discovery budgets must be positive")
        # Fetch extra due names if a subset is outside the canonical universe.
        due = self.store.recipient_discovery_due(as_of=clock, limit=900)
        due = [row for row in due if universe is None or row["subject_id"] in universe]
        due = due[:self.max_subjects]
        created, partial, failed, checked = 0, 0, 0, 0
        for issuer in due:
            name, ticker = issuer["company_name"], issuer["subject_id"]
            found: set[str] = set()
            truncated = False
            name_failed = False
            for page in range(1, self.max_pages + 1):
                try:
                    payload = self.client.recipients(name, page=page)
                    rows, meta = payload["results"], payload["page_metadata"]
                    if not isinstance(rows, list) or not isinstance(meta.get("hasNext"), bool):
                        raise ValueError("Malformed USAspending recipient listing")
                except (OSError, ValueError, KeyError, TypeError):
                    failed += 1
                    name_failed = True
                    break  # keep the name due for the next run
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    uei, recipient_id = row.get("uei"), row.get("id")
                    recipient_name = row.get("name")
                    if (not isinstance(uei, str) or not re.fullmatch(r"[A-Z0-9]{12}", uei)
                            or not isinstance(recipient_id, str) or not recipient_id
                            or not isinstance(recipient_name, str)
                            or recipient_name.casefold().strip() != name.casefold().strip()):
                        continue
                    if recipient_id in found:
                        continue
                    found.add(recipient_id)
                    details = {"stage": "recipient_candidate", "uei": uei,
                               "issuer_sec_name": name, "recipient_name": recipient_name,
                               "recipient_id": recipient_id,
                               "recipient_level": row.get("recipient_level"),
                               "identity_status": "NAME_ONLY",
                               "missing_evidence": "dated legal entity/issuer ownership relationship",
                               "contract_attribution_allowed": False}
                    digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                    _, new = self.store.record_finding(
                        source="usaspending", subject_id=ticker,
                        source_object_id=f"recipient:{recipient_id}", content_hash=digest,
                        title=f"Kandidát příjemce: {recipient_name} (UEI {uei})",
                        source_url=RECIPIENT_URL, locator=f"recipient_id:{recipient_id}",
                        published_at=clock, available_at=clock, observed_at=clock,
                        verification_status="UNVERIFIED", details=details,
                    )
                    created += int(new)
                if not meta["hasNext"]:
                    break
                if page == self.max_pages:
                    truncated = True
            if name_failed:
                continue
            self.store.record_recipient_discovery_check(
                subject_id=ticker, company_name=name, as_of=clock,
                candidate_count=len(found), truncated=truncated,
            )
            partial += int(truncated)
            checked += 1
        return {"status": "PARTIAL" if partial or failed else "OK",
                "checked_names": checked, "new_candidates": created,
                "truncated_names": partial, "failed_names": failed}
