"""Offline historical SEC exposure replay; no registry or live identity adoption."""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.agents.commodity_energy_agent import CommodityEnergyAgent
from market_checker_app.agents.contracts import AgentContext, AgentResult, AgentStatus, DocumentRecord
from market_checker_app.collectors.short_report_client import ShortReportClient
from market_checker_app.config import CommodityEnergyConfig, ShortReportSourceConfig, Stage3SourceVerificationConfig
from market_checker_app.services.filing_exposure_discovery_service import FilingExposureDiscoveryService

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
ACCESSION = "0000034088-26-000045"


def capture_index() -> dict[str, dict]:
    index = json.loads((FOLDER / "capture_index.json").read_text(encoding="utf-8"))
    return {row["capture_id"]: row for row in index["captures"]}


def raw(capture_id: str) -> bytes:
    metadata = capture_index()[capture_id]
    archive = (ROOT / metadata["archive_path"]).read_bytes()
    if hashlib.sha256(archive).hexdigest() != metadata["archive_sha256"]:
        raise ValueError("Archive bytes changed")
    body = gzip.decompress(archive) if metadata["archive_encoding"] == "gzip" else archive
    if hashlib.sha256(body).hexdigest() != metadata["raw_response_sha256"]:
        raise ValueError("Raw SEC response bytes changed")
    return body


def historical_input():
    index = capture_index()
    submissions = json.loads(raw("sec_xom_old_cik_submissions"))
    recent = submissions["filings"]["recent"]
    position = recent["accessionNumber"].index(ACCESSION)
    row = {key: recent[key][position] for key in
           ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument")}
    metadata = index["sec_xom_2025_10k"]
    source = ShortReportSourceConfig(ticker="XOM", publisher="SEC EDGAR",
        published_at=datetime.fromisoformat(row["filingDate"]).replace(tzinfo=timezone.utc),
        url=metadata["requested_url"], discovery_method="sec_filing")
    client = ShortReportClient(user_agent="JohnySkoreOfflineReplay", max_attempts=1,
        transport=lambda url, headers, timeout, bound: (
            raw("sec_xom_2025_10k"), "text/html", metadata["final_url"]))
    fetched = client.fetch(source)
    document = DocumentRecord(document_id="historical-sec:34088:" + ACCESSION, ticker="XOM",
        source="SEC EDGAR", source_type="regulatory_filing",
        published_at=source.published_at, observed_at=datetime.fromisoformat(metadata["observed_at"]),
        url=fetched.final_url, content_hash=fetched.content_hash, mime_type=fetched.mime_type,
        source_authority="SEC", reporting_period_end=datetime.fromisoformat(row["reportDate"]).replace(tzinfo=timezone.utc),
        metadata={"cik": "0000034088", "accession_number": ACCESSION, "form": row["form"],
                  "acceptanceDateTime": row["acceptanceDateTime"],
                  "acceptance_source_url": index["sec_xom_old_cik_submissions"]["requested_url"],
                  "acceptance_source_observed_at": index["sec_xom_old_cik_submissions"]["observed_at"],
                  "index_url": index["sec_xom_2025_10k_index"]["requested_url"]})
    return fetched, document, row


def replay() -> dict:
    clock = datetime.now(timezone.utc)
    fetched, document, row = historical_input()
    findings = FilingExposureDiscoveryService().discover(fetched, max_supply_chain=0,
        as_of=clock, filing_document=document)
    context = AgentContext(orchestration_id="batch4-energy-historical-replay", watchlist=("XOM",),
        started_at=clock, state={"sec_filing_texts_by_ticker": {"XOM": [fetched]},
            "agent_results": {"f2_sec": AgentResult(status=AgentStatus.SUCCESS, documents=[document])}})
    with patch("market_checker_app.agents.commodity_energy_agent.utc_now", return_value=clock):
        result = CommodityEnergyAgent(CommodityEnergyConfig(enabled=True,
            source_verification=Stage3SourceVerificationConfig(enabled=True))).run(context)
    return {"schema_version": 1, "kind": "ACTUAL_HISTORICAL_FILING_BOUNDED_OFFLINE_EXPOSURE_REPLAY",
        "replayed_at": clock.isoformat(), "network_requests": 0,
        "filing": row, "historical_filing_cik": "0000034088", "ticker_in_historical_filing": "XOM",
        "production_registry_modified": False, "current_q3_instrument_adopted": False,
        "issuer_ownership_interval_inferred": False,
        "discovery_findings": [{"resource_name": item.source.resource_name,
            "exposure_type": item.source.exposure_type, "reason": item.reason,
            "evidence_quote": item.evidence_quote, "provenance": item.provenance}
            for item in findings.commodity_energy],
        "agent_result": {"status": result.status.value, "metadata": result.metadata,
            "documents": [{"document_id": doc.document_id, "source_url": doc.url,
                "published_at": doc.published_at.isoformat(), "observed_at": doc.observed_at.isoformat(),
                "content_sha256": doc.content_hash, "metadata": doc.metadata} for doc in result.documents],
            "exposures": [{"resource_name": e.resource_name, "exposure_type": e.exposure_type.value,
                "published_at": e.published_at.isoformat(), "observed_at": e.observed_at.isoformat(),
                "source_url": e.source_url, "metadata": e.metadata} for e in result.resource_exposures],
            "evidence_scoring_applied": [e.metadata["scoring_applied"] for e in result.evidence]},
        "limits": ["Historical issuer 34088 only; current XOM successor/Q3 instrument identity is unresolved here.",
                   "First-person business/production statements are disclosure facts, not verified legal asset ownership.",
                   "Today's capture and replay do not reconstruct historical availability in this application's source history.",
                   "No quantity, revenue share, macro price attachment, forecast, OOS result or score/trading claim."]}


if __name__ == "__main__":
    (FOLDER / "replay.json").write_text(json.dumps(replay(), indent=2) + "\n", encoding="utf-8")
