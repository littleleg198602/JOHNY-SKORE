from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import json
import math
import re
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zlib
from xml.etree import ElementTree

from market_checker_app.utils.text import normalize_ticker


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_SUBMISSIONS_FILE_URL = "https://data.sec.gov/submissions/{name}"
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
    items: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SecInsiderTransaction:
    accession_number: str
    owner_cik: str | None
    owner_name: str
    transaction_date: datetime
    transaction_code: str
    acquired_disposed: str | None
    shares: float | None
    price_per_share: float | None
    shares_owned_after: float | None
    ownership_nature: str | None
    derivative: bool
    source_url: str


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
    insider_transactions: tuple[SecInsiderTransaction, ...] = field(
        default_factory=tuple
    )
    historical_submission_files_loaded: int = 0


JsonTransport = Callable[[str, dict[str, str], float], dict[str, Any]]
TextTransport = Callable[[str, dict[str, str], float], bytes]


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


def _default_text_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> bytes:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()
        encoding = str(response.headers.get("Content-Encoding", "")).lower()
        if encoding == "gzip":
            payload = gzip.decompress(payload)
        elif encoding == "deflate":
            payload = zlib.decompress(payload)
    return payload


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
        text_transport: TextTransport | None = None,
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
        self._text_transport = text_transport or _default_text_transport
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

    def _request_bytes(self, url: str) -> bytes:
        headers = self._headers()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_rate_slot()
            try:
                payload = self._text_transport(url, headers, self.timeout_seconds)
                if not payload:
                    raise SecEdgarError(f"SEC endpoint {url} returned an empty body")
                return payload
            except (HTTPError, URLError, TimeoutError, SecEdgarError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError) and exc.code not in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }:
                    break
                if attempt == self.max_attempts:
                    break
                self._sleep(float(2 ** (attempt - 1)))
        raise SecEdgarError(f"SEC document request failed for {url}: {last_error}")

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

    @staticmethod
    def _raw_primary_document(form: str, primary_document: str) -> str:
        """Return the raw ownership XML path instead of SEC's XSL view."""

        if _base_form(form) in {"3", "4", "5"}:
            return re.sub(
                r"^xslF\d+X\d+/",
                "",
                primary_document,
                count=1,
                flags=re.IGNORECASE,
            )
        return primary_document

    @staticmethod
    def _recent_table(payload: dict[str, Any]) -> dict[str, Any]:
        filings = payload.get("filings")
        if isinstance(filings, dict):
            recent = filings.get("recent")
            if isinstance(recent, dict):
                return recent
        recent = payload.get("recent")
        if isinstance(recent, dict):
            return recent
        if isinstance(payload.get("form"), list):
            return payload
        return {}

    @classmethod
    def _merged_submissions(
        cls,
        payloads: list[dict[str, Any]],
    ) -> dict[str, Any]:
        merged: dict[str, list[object]] = {}
        prior_rows = 0
        for payload in payloads:
            table = cls._recent_table(payload)
            row_count = max(
                (
                    len(value)
                    for value in table.values()
                    if isinstance(value, list)
                ),
                default=0,
            )
            for field_name in set(merged).difference(table):
                merged[field_name].extend([None] * row_count)
            for field_name in table:
                target = merged.setdefault(field_name, [None] * prior_rows)
                values = table.get(field_name)
                if isinstance(values, list):
                    target.extend(values[:row_count])
                    if len(values) < row_count:
                        target.extend([None] * (row_count - len(values)))
                else:
                    target.extend([None] * row_count)
            prior_rows += row_count
        return {"filings": {"recent": merged}}

    @staticmethod
    def _historical_submission_names(payload: dict[str, Any]) -> list[str]:
        filings = payload.get("filings")
        raw_files = filings.get("files") if isinstance(filings, dict) else None
        names: list[str] = []
        if not isinstance(raw_files, list):
            return names
        for raw_file in raw_files:
            if not isinstance(raw_file, dict):
                continue
            name = str(raw_file.get("name") or "").strip()
            if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
                continue
            if name not in names:
                names.append(name)
        return names

    @staticmethod
    def _needs_historical_filings(
        filings: list[SecFiling],
        *,
        allowed_forms: tuple[str, ...],
        limit: int,
    ) -> bool:
        safe_limit = max(0, int(limit))
        if len(filings) < safe_limit:
            return True
        allowed = {_base_form(form) for form in allowed_forms}
        counts: dict[str, int] = {}
        for filing in filings:
            base = _base_form(filing.form)
            counts[base] = counts.get(base, 0) + 1
        annual_family = allowed & {"10-K", "20-F", "40-F"}
        quarterly_family = allowed & {"10-Q", "6-K"}
        minimum_delta_depth = min(2, safe_limit)
        for family in (annual_family, quarterly_family):
            if family and sum(counts.get(form, 0) for form in family) < minimum_delta_depth:
                return True
        return False

    @classmethod
    def _parse_filings(
        cls,
        payload: dict[str, Any],
        *,
        cik: str,
        allowed_forms: tuple[str, ...],
        limit: int,
    ) -> list[SecFiling]:
        recent = cls._recent_table(payload)
        if not recent:
            return []
        allowed_order = list(
            dict.fromkeys(
                _base_form(form)
                for form in allowed_forms
                if _base_form(form)
            )
        )
        allowed = set(allowed_order)
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
            candidate = SecFiling(
                accession_number=accession_number,
                form=form,
                filed_at=filed_at,
                report_date=_utc_date(item("reportDate")),
                primary_document=primary_document,
                filing_url=cls._filing_url(
                    cik,
                    accession_number,
                    cls._raw_primary_document(form, primary_document),
                ),
                index_url=cls._filing_url(
                    cik,
                    accession_number,
                    f"{accession_number}-index.html",
                ),
                primary_document_description=(
                    str(item("primaryDocDescription") or "").strip() or None
                ),
                items=tuple(
                    part.strip()
                    for part in str(item("items") or "").split(",")
                    if part.strip()
                ),
            )
            existing = next(
                (
                    filing
                    for filing in filings
                    if filing.accession_number == accession_number
                ),
                None,
            )
            if existing is None:
                filings.append(candidate)
            elif existing != candidate:
                raise SecEdgarError(
                    "SEC submissions obsahují konfliktní duplicitní accession "
                    f"{accession_number}."
                )
        filings.sort(key=lambda filing: filing.filed_at, reverse=True)
        safe_limit = max(0, int(limit))
        if not safe_limit:
            return []

        # A burst of 8-K filings must not crowd all comparable 10-Q/10-K
        # statements out of the bounded bundle. Select filings round-robin by
        # requested base form, then restore chronological output order.
        filings_by_form: dict[str, list[SecFiling]] = {
            form: [] for form in allowed_order
        }
        for filing in filings:
            filings_by_form.setdefault(_base_form(filing.form), []).append(filing)
        selected: list[SecFiling] = []
        round_index = 0
        while len(selected) < safe_limit:
            added = False
            for form in allowed_order:
                group = filings_by_form.get(form, [])
                if round_index >= len(group):
                    continue
                selected.append(group[round_index])
                added = True
                if len(selected) >= safe_limit:
                    break
            if not added:
                break
            round_index += 1
        selected.sort(key=lambda filing: filing.filed_at, reverse=True)
        return selected

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

    @staticmethod
    def _parse_form4_transactions(
        payload: bytes,
        *,
        filing: SecFiling,
    ) -> list[SecInsiderTransaction]:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise SecEdgarError(
                f"Form 4 XML {filing.accession_number} cannot be parsed: {exc}"
            ) from exc
        for node in root.iter():
            node.tag = str(node.tag).rsplit("}", 1)[-1]

        def value(node: ElementTree.Element, path: str) -> str | None:
            found = node.find(path)
            text = str(found.text or "").strip() if found is not None else ""
            return text or None

        def number(raw: str | None) -> float | None:
            if raw is None:
                return None
            try:
                parsed = float(raw.replace(",", ""))
            except ValueError:
                return None
            return parsed if math.isfinite(parsed) and parsed >= 0.0 else None

        owner_cik = value(root, ".//reportingOwnerId/rptOwnerCik")
        if owner_cik:
            owner_cik = owner_cik.zfill(10)
        owner_name = value(root, ".//reportingOwnerId/rptOwnerName") or "Unknown insider"
        normalized: list[SecInsiderTransaction] = []
        for table_name, derivative in (
            ("nonDerivativeTable", False),
            ("derivativeTable", True),
        ):
            table = root.find(f".//{table_name}")
            if table is None:
                continue
            for transaction in list(table):
                if not str(transaction.tag).endswith("Transaction"):
                    continue
                transaction_date = _utc_date(
                    value(transaction, ".//transactionDate/value")
                )
                transaction_code = (
                    value(transaction, ".//transactionCoding/transactionCode")
                    or "UNKNOWN"
                ).upper()
                if transaction_date is None:
                    continue
                normalized.append(
                    SecInsiderTransaction(
                        accession_number=filing.accession_number,
                        owner_cik=owner_cik,
                        owner_name=owner_name,
                        transaction_date=transaction_date,
                        transaction_code=transaction_code,
                        acquired_disposed=(
                            value(
                                transaction,
                                ".//transactionAmounts/transactionAcquiredDisposedCode/value",
                            )
                            or None
                        ),
                        shares=number(
                            value(
                                transaction,
                                ".//transactionAmounts/transactionShares/value",
                            )
                        ),
                        price_per_share=number(
                            value(
                                transaction,
                                ".//transactionAmounts/transactionPricePerShare/value",
                            )
                        ),
                        shares_owned_after=number(
                            value(
                                transaction,
                                ".//postTransactionAmounts/sharesOwnedFollowingTransaction/value",
                            )
                        ),
                        ownership_nature=value(
                            transaction,
                            ".//ownershipNature/directOrIndirectOwnership/value",
                        ),
                        derivative=derivative,
                        source_url=filing.filing_url,
                    )
                )
        return normalized

    def fetch_company_bundle(
        self,
        ticker: str,
        *,
        allowed_forms: tuple[str, ...],
        max_filings: int,
        max_historical_submission_files: int = 2,
        concepts: tuple[str, ...],
        max_facts_per_concept: int,
    ) -> SecCompanyBundle | None:
        company = self.resolve_company(ticker)
        if company is None:
            return None
        submissions = self._request_json(
            SEC_SUBMISSIONS_URL.format(cik=company.cik)
        )
        submission_payloads = [submissions]
        merged_submissions = self._merged_submissions(submission_payloads)
        filings = self._parse_filings(
            merged_submissions,
            cik=company.cik,
            allowed_forms=allowed_forms,
            limit=max_filings,
        )
        historical_files_loaded = 0
        for name in self._historical_submission_names(submissions)[
            : max(0, int(max_historical_submission_files))
        ]:
            if not self._needs_historical_filings(
                filings,
                allowed_forms=allowed_forms,
                limit=max_filings,
            ):
                break
            historical = self._request_json(
                SEC_SUBMISSIONS_FILE_URL.format(name=quote(name))
            )
            submission_payloads.append(historical)
            historical_files_loaded += 1
            merged_submissions = self._merged_submissions(submission_payloads)
            filings = self._parse_filings(
                merged_submissions,
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
        insider_transactions: list[SecInsiderTransaction] = []
        for filing in filings:
            if _base_form(filing.form) != "4":
                continue
            try:
                insider_transactions.extend(
                    self._parse_form4_transactions(
                        self._request_bytes(filing.filing_url),
                        filing=filing,
                    )
                )
            except Exception as exc:
                warnings.append(
                    f"SEC Form 4 {filing.accession_number} could not be normalized: {exc}"
                )
        return SecCompanyBundle(
            company=company,
            filings=tuple(filings),
            facts=tuple(facts),
            warnings=tuple(warnings),
            insider_transactions=tuple(insider_transactions),
            historical_submission_files_loaded=historical_files_loaded,
        )
