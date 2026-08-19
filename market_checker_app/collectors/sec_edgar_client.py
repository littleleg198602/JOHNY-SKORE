from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import json
import math
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zlib

from market_checker_app.utils.text import normalize_ticker


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES_ROOT = "https://www.sec.gov/Archives/edgar/data"


class SecEdgarError(RuntimeError):
    """Raised when SEC EDGAR cannot provide a valid normalized response."""


@dataclass(frozen=True, slots=True)
class SecCompany:
    ticker: str
    cik: str
    name: str
    exchange: str | None = None


@dataclass(frozen=True, slots=True)
class SecFiling:
    accession_number: str
    form: str
    filed_at: datetime
    report_date: datetime | None
    primary_document: str
    filing_url: str
    index_url: str
    primary_document_description: str | None = None


@dataclass(frozen=True, slots=True)
class SecCompanyFact:
    taxonomy: str
    concept: str
    label: str
    description: str
    unit: str
    value: float
    filed_at: datetime
    form: str
    accession_number: str
    source_url: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    frame: str | None = None


@dataclass(frozen=True, slots=True)
class SecCompanyBundle:
    company: SecCompany
    filings: tuple[SecFiling, ...]
    facts: tuple[SecCompanyFact, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


JsonTransport = Callable[[str, dict[str, str], float], dict[str, Any]]


def _utc_date(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_sec_ticker(value: str) -> str:
    return normalize_ticker(value).replace(".", "-").replace("/", "-")


def _base_form(value: object) -> str:
    return str(value or "").strip().upper().removesuffix("/A")


def _default_json_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()
        encoding = str(response.headers.get("Content-Encoding", "")).lower()
        if encoding == "gzip":
            payload = gzip.decompress(payload)
        elif encoding == "deflate":
            payload = zlib.decompress(payload)
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise SecEdgarError(f"SEC endpoint {url} nevrátil JSON objekt.")
    return decoded


class SecEdgarClient:
    """Small compliant client for the public SEC submissions and XBRL APIs."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 20.0,
        min_request_interval_seconds: float = 0.125,
        max_attempts: int = 3,
        transport: JsonTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = str(user_agent or "").strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        # SEC currently permits at most 10 requests per second.  Keep a safety
        # margin even if a caller supplies an unsafe lower value.
        self.min_request_interval_seconds = max(
            0.11,
            float(min_request_interval_seconds),
        )
        self.max_attempts = max(1, int(max_attempts))
        self._transport = transport or _default_json_transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._rate_lock = threading.Lock()
        self._last_request_at: float | None = None
        self._ticker_map: dict[str, SecCompany] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.user_agent)

    def _headers(self) -> dict[str, str]:
        if not self.user_agent:
            raise SecEdgarError(
                "SEC vyžaduje deklarovaný User-Agent ve tvaru aplikace a kontaktního e-mailu."
            )
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

    def _wait_for_rate_slot(self) -> None:
        with self._rate_lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                remaining = (
                    self.min_request_interval_seconds
                    - (now - self._last_request_at)
                )
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._monotonic()
            self._last_request_at = now

    def _request_json(self, url: str) -> dict[str, Any]:
        headers = self._headers()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_rate_slot()
            try:
                return self._transport(url, headers, self.timeout_seconds)
            except HTTPError as exc:
                last_error = exc
                retryable = exc.code in {429, 500, 502, 503, 504}
                if not retryable or attempt == self.max_attempts:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError, SecEdgarError) as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    break
            self._sleep(float(2 ** (attempt - 1)))
        raise SecEdgarError(f"SEC požadavek selhal pro {url}: {last_error}") from last_error

    @staticmethod
    def _parse_ticker_map(payload: dict[str, Any]) -> dict[str, SecCompany]:
        companies: dict[str, SecCompany] = {}
        fields = payload.get("fields")
        rows = payload.get("data")
        if isinstance(fields, list) and isinstance(rows, list):
            field_names = [str(item) for item in fields]
            for raw_row in rows:
                if not isinstance(raw_row, list):
                    continue
                row = dict(zip(field_names, raw_row))
                ticker = str(row.get("ticker") or "").strip().upper()
                cik_value = row.get("cik")
                if not ticker or cik_value in {None, ""}:
                    continue
                cik = str(cik_value).strip().zfill(10)
                companies[_normalized_sec_ticker(ticker)] = SecCompany(
                    ticker=ticker,
                    cik=cik,
                    name=str(row.get("name") or "").strip(),
                    exchange=(str(row.get("exchange") or "").strip() or None),
                )
            return companies

        # Backward-compatible parser for company_tickers.json style payloads.
        for raw_row in payload.values():
            if not isinstance(raw_row, dict):
                continue
            ticker = str(raw_row.get("ticker") or "").strip().upper()
            cik_value = raw_row.get("cik_str", raw_row.get("cik"))
            if not ticker or cik_value in {None, ""}:
                continue
            cik = str(cik_value).strip().zfill(10)
            companies[_normalized_sec_ticker(ticker)] = SecCompany(
                ticker=ticker,
                cik=cik,
                name=str(raw_row.get("title") or raw_row.get("name") or "").strip(),
            )
        return companies

    def ticker_map(self) -> dict[str, SecCompany]:
        if self._ticker_map is None:
            self._ticker_map = self._parse_ticker_map(
                self._request_json(SEC_TICKER_MAP_URL)
            )
        return dict(self._ticker_map)

    def resolve_company(self, ticker: str) -> SecCompany | None:
        return self.ticker_map().get(_normalized_sec_ticker(ticker))

    @staticmethod
    def _filing_url(cik: str, accession_number: str, filename: str) -> str:
        cik_numeric = str(int(cik))
        accession_path = accession_number.replace("-", "")
        return f"{SEC_ARCHIVES_ROOT}/{cik_numeric}/{accession_path}/{filename}"

    @classmethod
    def _parse_filings(
        cls,
        payload: dict[str, Any],
        *,
        cik: str,
        allowed_forms: tuple[str, ...],
        limit: int,
    ) -> list[SecFiling]:
        recent = payload.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return []
        allowed = {_base_form(form) for form in allowed_forms}
        forms = recent.get("form") if isinstance(recent.get("form"), list) else []
        filings: list[SecFiling] = []
        for index, raw_form in enumerate(forms):
            form = str(raw_form or "").strip().upper()
            if _base_form(form) not in allowed:
                continue

            def item(field: str) -> object:
                values = recent.get(field)
                return values[index] if isinstance(values, list) and index < len(values) else None

            accession_number = str(item("accessionNumber") or "").strip()
            primary_document = str(item("primaryDocument") or "").strip()
            filed_at = _utc_date(item("filingDate"))
            if not accession_number or not primary_document or filed_at is None:
                continue
            filings.append(
                SecFiling(
                    accession_number=accession_number,
                    form=form,
                    filed_at=filed_at,
                    report_date=_utc_date(item("reportDate")),
                    primary_document=primary_document,
                    filing_url=cls._filing_url(
                        cik,
                        accession_number,
                        primary_document,
                    ),
                    index_url=cls._filing_url(
                        cik,
                        accession_number,
                        f"{accession_number}-index.html",
                    ),
                    primary_document_description=(
                        str(item("primaryDocDescription") or "").strip() or None
                    ),
                )
            )
        filings.sort(key=lambda filing: filing.filed_at, reverse=True)
        return filings[: max(0, int(limit))]

    @classmethod
    def _parse_facts(
        cls,
        payload: dict[str, Any],
        *,
        cik: str,
        filings: list[SecFiling],
        concepts: tuple[str, ...],
        max_per_concept: int,
    ) -> list[SecCompanyFact]:
        requested = set(concepts)
        allowed_accessions = {filing.accession_number: filing for filing in filings}
        facts_root = payload.get("facts")
        if not isinstance(facts_root, dict):
            return []

        normalized: list[SecCompanyFact] = []
        seen: set[tuple[object, ...]] = set()
        for taxonomy, taxonomy_facts in facts_root.items():
            if not isinstance(taxonomy_facts, dict):
                continue
            for concept, raw_fact in taxonomy_facts.items():
                if concept not in requested or not isinstance(raw_fact, dict):
                    continue
                units = raw_fact.get("units")
                if not isinstance(units, dict):
                    continue
                candidates: list[SecCompanyFact] = []
                for unit, observations in units.items():
                    if not isinstance(observations, list):
                        continue
                    for observation in observations:
                        if not isinstance(observation, dict):
                            continue
                        accession = str(observation.get("accn") or "").strip()
                        filing = allowed_accessions.get(accession)
                        if filing is None:
                            continue
                        try:
                            value = float(observation.get("val"))
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(value):
                            continue
                        filed_at = _utc_date(observation.get("filed")) or filing.filed_at
                        key = (
                            taxonomy,
                            concept,
                            unit,
                            accession,
                            observation.get("start"),
                            observation.get("end"),
                            value,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        fiscal_year: int | None
                        try:
                            fiscal_year = int(observation.get("fy"))
                        except (TypeError, ValueError):
                            fiscal_year = None
                        candidates.append(
                            SecCompanyFact(
                                taxonomy=str(taxonomy),
                                concept=str(concept),
                                label=str(raw_fact.get("label") or concept),
                                description=str(raw_fact.get("description") or ""),
                                unit=str(unit),
                                value=value,
                                filed_at=filed_at,
                                form=str(observation.get("form") or filing.form),
                                accession_number=accession,
                                source_url=filing.filing_url,
                                period_start=_utc_date(observation.get("start")),
                                period_end=_utc_date(observation.get("end")),
                                fiscal_year=fiscal_year,
                                fiscal_period=(
                                    str(observation.get("fp") or "").strip() or None
                                ),
                                frame=(str(observation.get("frame") or "").strip() or None),
                            )
                        )
                candidates.sort(
                    key=lambda fact: (
                        fact.filed_at,
                        fact.period_end or datetime.min.replace(tzinfo=timezone.utc),
                    ),
                    reverse=True,
                )
                normalized.extend(candidates[: max(0, int(max_per_concept))])
        normalized.sort(
            key=lambda fact: (fact.concept, fact.filed_at, fact.accession_number),
            reverse=True,
        )
        return normalized

    def fetch_company_bundle(
        self,
        ticker: str,
        *,
        allowed_forms: tuple[str, ...],
        max_filings: int,
        concepts: tuple[str, ...],
        max_facts_per_concept: int,
    ) -> SecCompanyBundle | None:
        company = self.resolve_company(ticker)
        if company is None:
            return None
        submissions = self._request_json(
            SEC_SUBMISSIONS_URL.format(cik=company.cik)
        )
        filings = self._parse_filings(
            submissions,
            cik=company.cik,
            allowed_forms=allowed_forms,
            limit=max_filings,
        )
        warnings: list[str] = []
        if not filings:
            warnings.append(
                f"SEC nemá v recent submissions požadované formuláře pro {ticker}."
            )
            return SecCompanyBundle(company, tuple(), tuple(), tuple(warnings))

        companyfacts = self._request_json(
            SEC_COMPANYFACTS_URL.format(cik=company.cik)
        )
        facts = self._parse_facts(
            companyfacts,
            cik=company.cik,
            filings=filings,
            concepts=concepts,
            max_per_concept=max_facts_per_concept,
        )
        if not facts:
            warnings.append(
                f"SEC companyfacts neobsahují vybrané koncepty pro poslední formuláře {ticker}."
            )
        return SecCompanyBundle(
            company=company,
            filings=tuple(filings),
            facts=tuple(facts),
            warnings=tuple(warnings),
        )
