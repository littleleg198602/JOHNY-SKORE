from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from market_checker_app.storage.scout_store import ScoutStore


# The EIA petroleum spot-price route identifies these distinct daily series.
# USD/barrel and USD/gallon must never be combined without explicit conversion.
SERIES = {
    "RWTC": ("COMMODITY:WTI", "WTI Cushing spot", "USD/barrel"),
    "EER_EPJK_PF4_RGC_DPG": (
        "COMMODITY:JET_FUEL_GULF", "US Gulf Coast jet fuel spot", "USD/gallon"
    ),
}
CATALOG_URL = "https://www.eia.gov/opendata/browser/petroleum/pri/spt"


class EiaClient(Protocol):
    def daily_spot(self, series_id: str) -> list[dict[str, object]]: ...


class EiaApiClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("EIA API key required")
        self.api_key = api_key

    def daily_spot(self, series_id: str) -> list[dict[str, object]]:
        if series_id not in SERIES:
            raise ValueError("Unsupported EIA series")
        query = urlencode({
            "api_key": self.api_key,
            "frequency": "daily",
            "data[0]": "value",
            "facets[series][]": series_id,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": "0", "length": "2",
        })
        with urlopen(f"https://api.eia.gov/v2/petroleum/pri/spt/data/?{query}",
                     timeout=15) as response:
            payload = json.load(response)
        return payload["response"]["data"]


class EiaScoutService:
    """Observe shared price proxies; never infer an issuer's purchase price."""

    def __init__(self, store: ScoutStore, *, client: EiaClient) -> None:
        self.store = store
        self.client = client

    def run(self, *, as_of: datetime | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        created = 0
        for series, (subject, title, unit) in SERIES.items():
            for row in self.client.daily_spot(series):
                period = str(row["period"])
                try:
                    period_date = date.fromisoformat(period)
                except ValueError:
                    continue
                if str(row.get("series")) != series or period_date > clock.date():
                    continue
                try:
                    value = float(row["value"])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value):
                    continue
                digest = hashlib.sha256(f"{series}|{period}|{value}".encode()).hexdigest()
                _, new = self.store.record_finding(
                    source="eia", subject_id=subject,
                    source_object_id=f"{series}:{period}", content_hash=digest,
                    title=f"EIA {title}: {value} {unit}", source_url=CATALOG_URL,
                    locator=f"series:{series}/period:{period}",
                    # The daily period is not the API publication timestamp.
                    published_at=clock, available_at=clock, observed_at=clock,
                    details={"series_id": series, "period": period, "value": value,
                             "unit": unit, "frequency": "daily",
                             "release_timestamp_known": False,
                             "company_purchase_price": False},
                )
                created += int(new)
        return {"status": "OK", "new_findings": created}
