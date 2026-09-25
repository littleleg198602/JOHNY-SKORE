from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Protocol

from market_checker_app.collectors.sec_edgar_client import SecCompany, SecEdgarClient, SecFiling
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.text import normalize_ticker


class FilingIndexClient(Protocol):
    def fetch_filing_index(
        self, ticker: str, *, allowed_forms: tuple[str, ...],
        max_filings: int, max_historical_submission_files: int,
    ) -> tuple[SecCompany, tuple[SecFiling, ...]] | None: ...


class SecScoutService:
    """Find SEC filing leads in a durable queue; no predictions or orders."""

    FORMS = ("10-K", "10-Q", "8-K", "20-F", "6-K", "40-F")

    def __init__(
        self, store: ScoutStore, *, user_agent: str = "",
        client: FilingIndexClient | None = None,
    ) -> None:
        self.store = store
        self.client = client or (
            SecEdgarClient(user_agent=user_agent) if user_agent and "@" in user_agent else None
        )

    def schedule(self, tickers: list[str], *, as_of: datetime) -> int:
        unique = {normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)}
        for ticker in sorted(unique):
            self.store.enqueue(
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
            return {"processed": 0, "new_findings": 0, "status": "BUSY"}
        try:
            return self._run_claimed_batch(as_of=as_of, limit=limit)
        finally:
            self.store.release_provider("sec", token)

    def _run_claimed_batch(
        self, *, as_of: datetime | None, limit: int,
    ) -> dict[str, int | str]:
        processed = new_findings = failed = 0
        for _ in range(limit):
            clock = as_of or datetime.now(timezone.utc)
            job = self.store.lease(as_of=clock)
            if job is None:
                break
            processed += 1
            try:
                if job.source != "sec":
                    raise ValueError(f"Unsupported source: {job.source}")
                index = self.client.fetch_filing_index(
                    job.subject_id, allowed_forms=self.FORMS, max_filings=30,
                    max_historical_submission_files=1,
                )
                if index is None:
                    raise ValueError(f"IDENTITY_UNRESOLVED: {job.subject_id}")
                company, filings = index
                if normalize_ticker(company.ticker) != job.subject_id:
                    raise ValueError("IDENTITY_CONFLICT: SEC ticker differs from job")
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
                self.store.finish(job, as_of=clock)
            except Exception as exc:
                failed += 1
                self.store.finish(
                    job, as_of=as_of or datetime.now(timezone.utc),
                    error=f"{type(exc).__name__}: {exc}",
                    retry_after=timedelta(minutes=30),
                )
        return {"processed": processed, "new_findings": new_findings,
                "failed": failed, "status": "PARTIAL" if failed else "OK"}
