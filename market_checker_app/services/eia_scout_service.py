from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import math
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.services.public_source_budget import (
    PublicBudgetExhausted, PublicSourceBudget,
)


# The EIA petroleum spot-price route identifies these distinct daily series.
# USD/barrel and USD/gallon must never be combined without explicit conversion.
SERIES = {
    "RWTC": ("COMMODITY:WTI", "WTI Cushing spot", "USD/barrel"),
    "EER_EPJK_PF4_RGC_DPG": (
        "COMMODITY:JET_FUEL_GULF", "US Gulf Coast jet fuel spot", "USD/gallon"
    ),
}
CATALOG_URL = "https://www.eia.gov/opendata/browser/petroleum/pri/spt"
MAX_BYTES = 250_000
MAX_ROWS = 2
DATA_PATH = "/v2/petroleum/pri/spt/data/"


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
            target = urlsplit(response.geturl())
            if (target.scheme != "https" or target.hostname != "api.eia.gov"
                    or target.port not in {None, 443} or target.path != DATA_PATH):
                raise ValueError("EIA response left official endpoint")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("EIA response exceeds byte budget")
            payload = json.loads(body)
        result = payload.get("response") if isinstance(payload, dict) else None
        rows = result.get("data") if isinstance(result, dict) else None
        if (not isinstance(rows, list) or len(rows) > MAX_ROWS
                or any(not isinstance(row, dict) for row in rows)):
            raise ValueError("Malformed or unbounded EIA response")
        return rows


class EiaScoutService:
    """Observe shared price proxies; never infer an issuer's purchase price."""

    def __init__(self, store: ScoutStore, *, client: EiaClient,
                 max_run_seconds: float = 40) -> None:
        if (isinstance(max_run_seconds, bool) or not isinstance(max_run_seconds, (int, float))
                or not math.isfinite(max_run_seconds) or not 1 <= max_run_seconds <= 120):
            raise ValueError("EIA time budget must be bounded")
        self.store = store
        self.client = client
        self.max_run_seconds = max_run_seconds

    def run(self, *, as_of: datetime | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        clock = clock.astimezone(timezone.utc)
        budget = PublicSourceBudget(self.store, "eia", as_of=clock,
                                    max_run_seconds=self.max_run_seconds)
        if not budget.acquire():
            return self._series_summary(budget.blocked_summary())
        try:
            return self._series_summary(budget.annotate(self._collect(clock, budget)))
        finally:
            budget.release()

    @staticmethod
    def _series_summary(summary: dict) -> dict:
        # Shared series consume ledger subjects; they are never issuer checks.
        for key, alias in (("daily_issuers", "daily_series"),
                           ("daily_issuer_limit", "daily_series_limit"),
                           ("attempted_issuers", "attempted_series"),
                           ("quota_deferred_issuers", "quota_deferred_series")):
            if key in summary:
                summary[alias] = summary.pop(key)
        summary.pop("checked_issuers", None)
        summary.update(query_scope="latest_two_observations_per_shared_series",
                       quota_unit="commodity_series", company_exposure_verified=False)
        return summary

    def _collect(self, clock: datetime, budget: PublicSourceBudget) -> dict:
        due = budget.select_due([{"subject_id": subject, "series_id": series}
                                 for series, (subject, _, _) in SERIES.items()])
        created = checked = failed = rejected = usable = incomplete = 0
        summary = {"status": "NO_DUE_WORK" if not due else "OK", "new_findings": 0}
        deadline = time.monotonic() + self.max_run_seconds
        for item in due:
            if time.monotonic() >= deadline:
                summary.update(status="PARTIAL", budget_exhausted=True)
                break
            series, subject = item["series_id"], item["subject_id"]
            _, title, unit = SERIES[series]
            try:
                rows = budget.call(subject, f"{series}:{unit}:daily:latest-two-v1",
                                   lambda: self.client.daily_spot(series))
            except PublicBudgetExhausted:
                break
            except HTTPError as error:
                failed += 1
                if error.code in {401, 403, 429}:
                    summary["status"] = "RATE_LIMITED" if error.code == 429 else "ACCESS_BLOCKED"
                    break
                summary["status"] = "PARTIAL"
                continue
            except (OSError, ValueError, TypeError, KeyError):
                failed += 1
                summary["status"] = "PARTIAL"
                continue
            if (not isinstance(rows, list) or len(rows) > MAX_ROWS
                    or any(not isinstance(row, dict) for row in rows)):
                failed += 1
                summary["status"] = "PARTIAL"
                continue
            checked += 1
            valid = []
            periods = Counter(str(row.get("period", "")) for row in rows)
            for row in rows:
                period = str(row.get("period", ""))
                try:
                    period_date = date.fromisoformat(period)
                    value = float(row["value"])
                    if (row.get("series") != series or period_date.isoformat() != period
                            or period_date > clock.date()
                            or isinstance(row["value"], bool) or not math.isfinite(value)
                            or periods[period] > 1):
                        raise ValueError("Invalid EIA observation")
                except (ValueError, TypeError, KeyError):
                    rejected += 1
                    continue
                valid.append((period, value))
            if not valid or len(valid) != len(rows):
                incomplete += 1
                summary["status"] = "PARTIAL"
            for period, value in valid:
                digest = hashlib.sha256(f"{series}|{period}|{value}".encode()).hexdigest()
                _, new = self.store.record_finding(
                    source="eia", subject_id=subject,
                    source_object_id=f"{series}:{period}", content_hash=digest,
                    title=f"EIA {title}: {value} {unit}", source_url=CATALOG_URL,
                    locator=f"series:{series}/period:{period}",
                    # Period identifies the economic observation, not release time.
                    published_at=clock, available_at=clock, observed_at=clock,
                    details={"series_id": series, "period": period, "value": value,
                             "unit": unit, "frequency": "daily",
                             "release_timestamp_known": False,
                             "company_purchase_price": False},
                )
                created += int(new)
                usable += 1
        summary.update(new_findings=created, checked_series=checked,
                       usable_rows=usable, rejected_rows=rejected,
                       incomplete_series=incomplete, failed_series=failed)
        return summary
