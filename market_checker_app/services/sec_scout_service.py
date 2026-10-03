from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Protocol

from market_checker_app.collectors.sec_edgar_client import (
    SecAccessBlockedError, SecCompany, SecEdgarClient, SecFiling,
    SecRateLimitedError,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.config import ShortReportSourceConfig
from market_checker_app.services.filing_exposure_discovery_service import FilingExposureDiscoveryService
from market_checker_app.services.sec_counterparty_identity import exact_catalog_cik
from market_checker_app.services.sec_document_extraction import (
    extract_schedule_13_ownership,
    extract_sec_item_excerpts,
    readable_sec_text,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.text import normalize_ticker


class FilingIndexClient(Protocol):
    def fetch_filing_index(
        self, ticker: str, *, allowed_forms: tuple[str, ...],
        max_filings: int, max_historical_submission_files: int,
    ) -> tuple[SecCompany, tuple[SecFiling, ...]] | None: ...

    def fetch_filing_document(self, filing: SecFiling, *, cik: str) -> bytes: ...


class SecScoutService:
    """Find SEC filing leads in a durable queue; no predictions or orders."""

    FORMS = (
        "10-K", "10-Q", "8-K", "20-F", "6-K", "40-F",
        "4", "SC 13D", "SC 13G",
    )

    def __init__(
        self, store: ScoutStore, *, user_agent: str = "",
        client: FilingIndexClient | None = None,
    ) -> None:
        self.store = store
        self.client = client or (
            SecEdgarClient(user_agent=user_agent) if user_agent and "@" in user_agent else None
        )

    def schedule(
        self, tickers: list[str], *, as_of: datetime, new_only: bool = False,
    ) -> int:
        unique = {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
        for ticker in sorted(unique):
            enqueue = self.store.enqueue_if_absent if new_only else self.store.enqueue
            enqueue(
                source="sec", subject_id=ticker, reason="daily_filings",
                due_at=as_of,
            )
        return len(unique)

    def run_batch(self, *, as_of: datetime | None = None, limit: int = 25) -> dict[str, int | str]:
        if self.client is None:
            return {"processed": 0, "new_findings": 0, "status": "WAIT_ACCESS"}
        if limit < 1:
            raise ValueError("Batch limit must be positive")
        token = self.store.claim_provider(
            "sec", as_of=as_of or datetime.now(timezone.utc),
        )
        if token is None:
            cooldown = self.store.provider_cooldown(
                "sec", as_of=as_of or datetime.now(timezone.utc),
            )
            if cooldown:
                return {"processed": 0, "new_findings": 0,
                        "status": ("ACCESS_BLOCKED" if cooldown["reason"] == "SEC HTTP 403"
                                   else "RATE_LIMITED"),
                        "retry_at": cooldown["retry_at"]}
            return {"processed": 0, "new_findings": 0, "status": "BUSY"}
        try:
            return self._run_claimed_batch(as_of=as_of, limit=limit, token=token)
        finally:
            self.store.release_provider("sec", token)

    def _run_claimed_batch(
        self, *, as_of: datetime | None, limit: int, token: str,
    ) -> dict[str, int | str]:
        processed = new_findings = failed = 0
        for _ in range(limit):
            clock = as_of or datetime.now(timezone.utc)
            if not self.store.renew_provider("sec", token, as_of=clock):
                return {"processed": processed, "new_findings": new_findings,
                        "failed": failed, "status": "LEASE_LOST"}
            job = self.store.lease(as_of=clock, source="sec")
            if job is None:
                break
            processed += 1
            try:
                if job.source != "sec":
                    raise ValueError(f"Unsupported source: {job.source}")
                if job.reason.startswith("filing_document:"):
                    new_findings += self._process_document(job.subject_id, job.cursor, clock)
                    self.store.finish(job, as_of=clock)
                    continue
                index = self.client.fetch_filing_index(
                    job.subject_id, allowed_forms=self.FORMS, max_filings=30,
                    max_historical_submission_files=1,
                )
                if index is None:
                    raise ValueError(f"IDENTITY_UNRESOLVED: {job.subject_id}")
                company, filings = index
                if normalize_ticker(company.ticker) != job.subject_id:
                    raise ValueError("IDENTITY_CONFLICT: SEC ticker differs from job")
                if not self.store.observe_sec_identity(
                    subject_id=job.subject_id, cik=company.cik,
                    company_name=company.name, as_of=clock,
                ):
                    raise ValueError("IDENTITY_CONFLICT: SEC CIK changed; candidate quarantined")
                document_targets: set[str] = set()
                for form_family in (
                    "8-K", "10-Q", "10-K", "6-K", "20-F", "40-F",
                    "4", "SC 13D", "SC 13G",
                ):
                    selected = next((f for f in filings if f.form.removesuffix("/A") == form_family), None)
                    if selected is not None:
                        document_targets.add(selected.accession_number)
                for filing in filings:
                    if filing.filed_at > clock:
                        continue
                    digest = hashlib.sha256(
                        f"{company.cik}|{filing.accession_number}|{filing.filing_url}".encode()
                    ).hexdigest()
                    finding_id, created = self.store.record_finding(
                        source="sec", subject_id=job.subject_id,
                        source_object_id=f"{company.cik}:{filing.accession_number}",
                        content_hash=digest,
                        title=f"{company.name}: {filing.form}",
                        source_url=filing.filing_url,
                        locator=f"accession:{filing.accession_number}",
                        published_at=filing.filed_at,
                        # EDGAR index gives a filing date, not guaranteed publication
                        # time. First actual observation is the safe availability cutoff.
                        available_at=clock, observed_at=clock,
                        details={"form": filing.form, "cik": company.cik,
                                 "accession": filing.accession_number,
                                 "primary_document": filing.primary_document,
                                 "index_url": filing.index_url,
                                 "items": list(filing.items),
                                 "stage": "filing_index",
                                 "report_date": filing.report_date.isoformat()
                                 if filing.report_date else None},
                    )
                    if created:
                        new_findings += 1
                        self.store.add_lead(
                            subject_id=job.subject_id, finding_id=finding_id,
                            question=(f"Co se v podání {filing.form} změnilo "
                                      "a co to znamená pro firmu?"),
                            as_of=clock,
                        )
                    if (created and filing.accession_number in document_targets
                            and callable(getattr(self.client, "fetch_filing_document", None))):
                        self.store.enqueue(
                            source="sec", subject_id=job.subject_id,
                            reason=f"filing_document:{filing.accession_number}",
                            due_at=clock, priority=-1, cursor=finding_id,
                        )
                self.store.finish(job, as_of=clock)
            except (SecRateLimitedError, SecAccessBlockedError) as exc:
                failed += 1
                blocked = isinstance(exc, SecAccessBlockedError)
                retry_after = (timedelta(minutes=30) if blocked else
                               timedelta(seconds=exc.retry_after_seconds))
                self.store.finish(
                    job, as_of=clock, error=str(exc), retry_after=retry_after,
                )
                self.store.defer_provider(
                    "sec", token, as_of=clock, retry_after=retry_after,
                    reason="SEC HTTP 403" if blocked else "SEC Retry-After",
                )
                return {"processed": processed, "new_findings": new_findings,
                        "failed": failed,
                        "status": "ACCESS_BLOCKED" if blocked else "RATE_LIMITED",
                        "retry_at": (clock + retry_after).isoformat()}
            except Exception as exc:
                failed += 1
                self.store.finish(
                    job, as_of=as_of or datetime.now(timezone.utc),
                    error=f"{type(exc).__name__}: {exc}",
                    retry_after=timedelta(minutes=30),
                )
        return {"processed": processed, "new_findings": new_findings,
                "failed": failed, "status": "PARTIAL" if failed else "OK"}

    def _process_document(
        self, subject_id: str, index_finding_id: str | None, clock: datetime,
    ) -> int:
        if not index_finding_id:
            raise ValueError("Missing SEC index finding for document job")
        index = self.store.finding_by_id(index_finding_id, subject_id=subject_id)
        if index is None or index["source"] != "sec":
            raise ValueError("SEC index finding is missing or belongs to another issuer")
        details = json.loads(str(index["details_json"]))
        if details.get("stage") != "filing_index":
            raise ValueError("Document job does not reference a filing index")
        accession = str(details["accession"])
        if index["source_object_id"] != f"{details['cik']}:{accession}":
            raise ValueError("SEC index identity conflict")
        filing = SecFiling(
            accession_number=accession, form=str(details["form"]),
            filed_at=datetime.fromisoformat(str(index["published_at"])),
            report_date=(datetime.fromisoformat(details["report_date"])
                         if details.get("report_date") else None),
            primary_document=str(details["primary_document"]),
            filing_url=str(index["source_url"]), index_url=str(details["index_url"]),
            items=tuple(details.get("items") or ()),
        )
        document = self.client.fetch_filing_document(filing, cik=str(details["cik"]))
        digest = hashlib.sha256(document).hexdigest()
        sections = extract_sec_item_excerpts(document, form=filing.form)
        ownership = extract_schedule_13_ownership(document, form=filing.form)
        catalog = None
        if filing.form.removesuffix("/A") in {"10-K", "10-Q", "20-F", "40-F"}:
            catalog_reader = getattr(self.client, "ticker_map", None)
            if callable(catalog_reader):
                catalog = catalog_reader()
        finding_id, created = self.store.record_finding(
            source="sec", subject_id=subject_id,
            source_object_id=str(index["source_object_id"]), content_hash=digest,
            title=f"{index['title']}: primární dokument",
            source_url=filing.filing_url,
            locator=f"accession:{accession}/primary:{filing.primary_document}",
            published_at=filing.filed_at, available_at=clock, observed_at=clock,
            verification_status="SOURCE_VERIFIED",
            details={"stage": "filing_document", "parser_version": "sec-html-v1",
                     "index_finding_id": index_finding_id, "form": filing.form,
                     "cik": details["cik"], "accession": accession,
                     "report_date": details.get("report_date"),
                     "document_sha256": digest, "bytes": len(document),
                     "beneficial_ownership": ownership,
                     "item_excerpts": [{"locator": item, "excerpt": excerpt}
                                       for item, excerpt in sections]},
        )
        if created:
            parent = self.store.add_lead(
                subject_id=subject_id, finding_id=index_finding_id,
                question=f"Co se v podání {filing.form} změnilo a co to znamená pro firmu?",
                as_of=clock,
            )
            self.store.advance_lead(
                parent, status="INVESTIGATING", as_of=clock,
                reason="Primární dokument SEC byl stažen a přiřazen k podání.",
            )
            for locator, _excerpt in sections:
                self.store.add_lead(
                    subject_id=subject_id, finding_id=finding_id,
                    parent_lead_id=parent,
                    question=f"Prověřit {locator} z podání {accession} a dopad na firmu.",
                    as_of=clock,
                )
            extra_findings = 0
            if filing.form.removesuffix("/A") in {"10-K", "10-Q", "20-F", "40-F"}:
                extra_findings = self._record_exposure_candidates(
                    subject_id=subject_id, filing=filing, document=document,
                    document_hash=digest, document_finding_id=finding_id, clock=clock,
                    catalog=catalog,
                )
            return int(created) + extra_findings
        return 0

    def _record_exposure_candidates(
        self, *, subject_id: str, filing: SecFiling, document: bytes,
        document_hash: str, document_finding_id: str, clock: datetime,
        catalog: dict[str, SecCompany] | None,
    ) -> int:
        fetched = FetchedShortReport(
            source=ShortReportSourceConfig(
                ticker=subject_id, publisher="SEC EDGAR",
                published_at=filing.filed_at, url=filing.filing_url,
            ),
            final_url=filing.filing_url, mime_type="text/html",
            title=filing.form, text=readable_sec_text(document),
            content_hash=document_hash, size_bytes=len(document), extractor="sec-html-v1",
        )
        candidates = FilingExposureDiscoveryService().discover(fetched)
        count = 0
        for kind, findings in (("supply_chain", candidates.supply_chain),
                               ("commodity_energy", candidates.commodity_energy)):
            for position, candidate in enumerate(findings):
                quote = candidate.evidence_quote[:1000]
                # Each excerpt is a cited question, not a verified counterparty,
                # hedge ratio, price, or forecast.
                evidence_hash = hashlib.sha256(
                    f"{document_hash}|{kind}|{candidate.reason}|{quote}".encode()
                ).hexdigest()
                resource = (candidate.source.counterparty if kind == "supply_chain"
                            else candidate.source.resource_name)
                catalog_match = (
                    exact_catalog_cik(resource, catalog)
                    if kind == "supply_chain" and catalog is not None
                    and candidate.source.counterparty_identity_status == "NAMED_ONLY"
                    else None
                )
                candidate_id, new = self.store.record_finding(
                    source="sec", subject_id=subject_id,
                    source_object_id=f"{filing.accession_number}:{kind}:{position}",
                    content_hash=evidence_hash,
                    title=f"{subject_id}: {kind} – {resource}",
                    source_url=filing.filing_url,
                    locator=f"accession:{filing.accession_number}/exposure:{kind}:{position}",
                    published_at=filing.filed_at, available_at=clock, observed_at=clock,
                    details={"stage": "exposure_candidate", "kind": kind,
                             "document_finding_id": document_finding_id,
                             "document_sha256": document_hash,
                             "reason": candidate.reason, "quote": quote,
                             "resource_or_counterparty": resource,
                             "identity_status": getattr(candidate.source, "counterparty_identity_status", None),
                             "sec_catalog_match": (
                                 {"cik": catalog_match[0], "tickers": list(catalog_match[1]),
                                  "observed_at": clock.isoformat(),
                                  "source_url": "https://www.sec.gov/files/company_tickers_exchange.json"}
                                 if catalog_match else None
                             ),
                             "confidence": candidate.source.confidence,
                             "dependency_pct": getattr(candidate.source, "dependency_pct", None),
                             "product_or_input": getattr(candidate.source, "product_or_input", None),
                             "hedged_share_pct": getattr(candidate.source, "hedged_share_pct", None),
                             "disclosure_period": candidate.source.disclosure_period},
                )
                if new:
                    count += 1
                    self.store.add_lead(
                        subject_id=subject_id, finding_id=candidate_id, as_of=clock,
                        question=(f"Ověřit {kind} z podání {filing.accession_number}: "
                                  f"{resource}; doložit identitu, období a finanční dopad."),
                    )
        return count
