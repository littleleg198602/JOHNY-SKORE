from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from market_checker_app.storage.scout_store import ScoutStore


DATA_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"


class ShortInterestClient(Protocol):
    def positions(self, symbol: str, *, limit: int) -> list[dict]: ...


class FinraShortInterestClient:
    def __init__(self, client_id: str, client_secret: str) -> None:
        if not client_id or not client_secret:
            raise ValueError("FINRA public API credential required")
        self.client_id, self.client_secret = client_id, client_secret
        self._token: str | None = None

    def _access_token(self) -> str:
        if self._token is None:
            credential = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()
            request = Request(TOKEN_URL, data=b"", method="POST",
                              headers={"Authorization": f"Basic {credential}"})
            with urlopen(request, timeout=20) as response:
                if urlsplit(response.geturl()).hostname != "ews.fip.finra.org":
                    raise ValueError("FINRA authentication redirected outside official host")
                token = json.load(response).get("access_token")
            if not isinstance(token, str) or not token:
                raise ValueError("FINRA access token missing")
            self._token = token
        return self._token

    def positions(self, symbol: str, *, limit: int) -> list[dict]:
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,12}", symbol) or not 1 <= limit <= 5000:
            raise ValueError("Invalid FINRA symbol or request budget")
        body = {"limit": limit, "compareFilters": [
            {"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": symbol},
        ]}
        request = Request(DATA_URL, data=json.dumps(body).encode(), method="POST",
                          headers={"Authorization": f"Bearer {self._access_token()}",
                                   "Content-Type": "application/json", "Accept": "application/json"})
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname != "api.finra.org":
                raise ValueError("FINRA data redirected outside official host")
            rows = json.load(response)
        if not isinstance(rows, list):
            raise ValueError("Malformed FINRA response")
        return rows


class FinraShortInterestScoutService:
    """Observe settlement positions by exact symbol, pending instrument proof."""

    def __init__(self, store: ScoutStore, *, client: ShortInterestClient,
                 max_subjects: int = 25, max_results: int = 100) -> None:
        if max_subjects < 1 or not 1 <= max_results <= 5000:
            raise ValueError("FINRA request budget must be bounded")
        self.store, self.client = store, client
        self.max_subjects, self.max_results = max_subjects, max_results

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("Observation time needs timezone")
        due = self.store.specialist_due("finra", as_of=clock, limit=900, refresh_days=15)
        due = [row for row in due if universe is None or row["subject_id"] in universe]
        due = due[:self.max_subjects]
        created = checked = failed = truncated_count = 0
        for issuer in due:
            ticker, name, cik = (issuer[key] for key in ("subject_id", "company_name", "cik"))
            try:
                rows = self.client.positions(ticker, limit=self.max_results)
                if not isinstance(rows, list):
                    raise ValueError("Malformed FINRA rows")
            except HTTPError as exc:
                if exc.code in {401, 403, 429}:
                    return {"status": "WAIT_ACCESS" if exc.code in {401, 403} else "RATE_LIMITED",
                            "checked_issuers": checked, "new_findings": created,
                            "failed_issuers": failed + 1, "truncated_issuers": truncated_count}
                failed += 1
                continue
            except (OSError, ValueError, KeyError, TypeError):
                failed += 1
                continue
            truncated = len(rows) >= self.max_results
            matched = 0
            for row in rows:
                if not isinstance(row, dict) or row.get("symbolCode") != ticker:
                    continue
                try:
                    settlement = date.fromisoformat(str(row["settlementDate"]))
                    quantity = row["currentShortPositionQuantity"]
                except (ValueError, KeyError, TypeError):
                    continue
                if (settlement > clock.date() or isinstance(quantity, bool)
                        or not isinstance(quantity, int) or quantity < 0):
                    continue
                market = str(row.get("marketClassCode") or "")
                details = {
                    "stage": "short_interest_symbol_candidate", "symbol": ticker,
                    "issuer_sec_name": name, "issuer_sec_cik": cik,
                    "finra_issue_name": row.get("issueName"),
                    "market_class": market, "settlement_date": settlement.isoformat(),
                    "short_position_shares": quantity,
                    "previous_short_position_shares": row.get("previousShortPositionQuantity"),
                    "days_to_cover_reported": row.get("daysToCoverQuantity"),
                    "revision_flag": row.get("revisionFlag"),
                    "identity_status": "SYMBOL_ONLY",
                    "missing_evidence": "dated FINRA symbol to instrument identity",
                    "settlement_date_is_publication_time": False,
                    "ranking_applied": False,
                }
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="finra", subject_id=ticker,
                    source_object_id=f"{ticker}:{market}:{settlement.isoformat()}",
                    content_hash=digest,
                    title=f"FINRA short interest candidate {ticker}: {quantity} shares",
                    source_url=DATA_URL,
                    locator=f"symbolCode:{ticker}/settlementDate:{settlement.isoformat()}",
                    published_at=clock, available_at=clock, observed_at=clock,
                    verification_status="UNVERIFIED", details=details,
                )
                created += int(fresh)
                matched += 1
            self.store.record_specialist_check(
                "finra", subject_id=ticker, identity_key=f"{cik}:{name}",
                as_of=clock, candidate_count=matched, truncated=truncated,
            )
            checked += 1
            truncated_count += int(truncated)
        return {"status": "PARTIAL" if failed or truncated_count else "OK",
                "checked_issuers": checked, "new_findings": created,
                "failed_issuers": failed, "truncated_issuers": truncated_count}
