from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from market_checker_app.storage.scout_store import ScoutStore


class FredClient(Protocol):
    def observations(self, series_id: str) -> list[dict[str, str]]: ...


class FredApiClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FRED API key required")
        self.api_key = api_key

    def observations(self, series_id: str) -> list[dict[str, str]]:
        query = urlencode({"series_id": series_id, "api_key": self.api_key,
                           "file_type": "json", "sort_order": "desc", "limit": 2})
        with urlopen(f"https://api.stlouisfed.org/fred/series/observations?{query}",
                     timeout=15) as response:
            payload = json.load(response)
        return payload["observations"]


class FredScoutService:
    """Observe macro context. Observation dates are not treated as release timestamps."""

    SERIES = ("FEDFUNDS", "CPIAUCSL", "UNRATE")

    def __init__(self, store: ScoutStore, *, client: FredClient) -> None:
        self.store = store
        self.client = client

    def run(self, *, as_of: datetime | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        created = 0
        for series in self.SERIES:
            for observation in self.client.observations(series):
                date, value = observation["date"], observation["value"]
                if value == "." or date > clock.date().isoformat():
                    continue
                # FRED values can be revised. The first time this exact value was
                # seen is its safe availability cutoff for historical analysis.
                digest = hashlib.sha256(f"{series}|{date}|{value}".encode()).hexdigest()
                _, new = self.store.record_finding(
                    source="fred", subject_id="MACRO:US", source_object_id=f"{series}:{date}",
                    content_hash=digest, title=f"FRED {series}: {value}",
                    source_url=f"https://fred.stlouisfed.org/series/{series}",
                    locator=f"observation:{date}", published_at=clock,
                    available_at=clock, observed_at=clock,
                    details={"series_id": series, "observation_date": date,
                             "value": value, "identity_status": "MACRO_CONTEXT",
                             "release_timestamp_known": False},
                )
                created += int(new)
        return {"status": "OK", "new_findings": created}
