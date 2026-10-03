from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext, AgentEvidence, AgentResult, DocumentRecord,
)
from market_checker_app.storage.scout_store import ScoutStore


class ScoutIndexAgent(BaseAgent):
    """Expose already observed filing leads as audit-only analysis evidence."""

    name = "scout_index"
    version = "0.1"
    dependencies = ("entity_registry",)

    def __init__(self, db_path: Path) -> None:
        self.store = ScoutStore(db_path)

    def run(self, context: AgentContext) -> AgentResult:
        rows = self.store.findings_for_watchlist(
            list(context.watchlist), as_of=context.started_at,
        )
        parsed_rows = [(row, json.loads(str(row["details_json"]))) for row in rows]
        primary_documents = {
            (str(row["subject_id"]), str(row["source_object_id"]))
            for row, details in parsed_rows
            if row["source"] == "sec"
            and row["verification_status"] == "SOURCE_VERIFIED"
            and details.get("stage") == "filing_document"
        }
        visible_finding_ids = [
            str(row["finding_id"])
            for row, _details in parsed_rows
            if row["source"] == "sec"
            and row["verification_status"] == "SOURCE_VERIFIED"
        ]
        documents: list[DocumentRecord] = []
        evidence: list[AgentEvidence] = []
        superseded_indexes = 0
        for row, details in parsed_rows:
            if row["source"] != "sec" or row["verification_status"] != "SOURCE_VERIFIED":
                continue
            source_key = (str(row["subject_id"]), str(row["source_object_id"]))
            if details.get("stage") == "filing_index" and source_key in primary_documents:
                superseded_indexes += 1
                continue
            exposure = details.get("stage") == "exposure_candidate"
            content_observed = details.get("stage") == "filing_document"
            item_locators = [item["locator"] for item in details.get("item_excerpts", [])]
            document_id = f"scout-index:{row['finding_id']}"
            observed_at = datetime.fromisoformat(str(row["first_observed_at"]))
            published_at = datetime.fromisoformat(str(row["published_at"]))
            ticker = str(row["subject_id"])
            url = str(row["source_url"])
            documents.append(DocumentRecord(
                document_id=document_id, ticker=ticker,
                source="SEC EDGAR evidence" if exposure else "SEC EDGAR index",
                source_type="regulatory_filing", observed_at=observed_at,
                published_at=published_at, url=url,
                canonical_event_key=f"sec-filing:{ticker}:{row['source_object_id']}",
                metadata={"locator": row["locator"],
                          "finding_id": row["finding_id"],
                          "filing_index_only": not (content_observed or exposure),
                          "exposure_candidate": exposure,
                          "form": details.get("form"),
                          "accession_number": details.get("accession"),
                          "issuer_cik": details.get("cik"),
                          "report_date": details.get("report_date"),
                          "beneficial_ownership": details.get("beneficial_ownership"),
                          "evidence_quote": details.get("quote") if exposure else None,
                          "document_sha256": details.get("document_sha256"),
                          "item_locators": item_locators},
            ))
            evidence.append(AgentEvidence(
                evidence_id=f"scout-evidence:{row['finding_id']}", ticker=ticker,
                agent_name=self.name, event_type=("SEC_EXPOSURE_CANDIDATE" if exposure
                                                  else "SEC_FILING_INDEX"),
                observed_at=observed_at,
                summary=(
                    f"SEC podání uvádí {details.get('kind')}: "
                    f"{details.get('quote')}; totožnost a dopad se prověřují."
                    if exposure else
                    f"SEC primární dokument {row['title']} obsahuje sekce "
                    f"{', '.join(item_locators)}; dopad se prověřuje."
                    if content_observed and item_locators else
                    f"SEC eviduje podání {row['title']}; dopad se prověřuje."
                ),
                direction=0.0, risk_score=0.0, confidence=0.5,
                document_ids=[document_id], source_urls=[url],
                metadata={"scoring_applied": False,
                          "index_only": not (content_observed or exposure),
                          "exposure_candidate": exposure,
                          "finding_id": row["finding_id"]},
            ))
        self.store.record_analysis_snapshot(
            context.orchestration_id, as_of=context.started_at,
            finding_ids=visible_finding_ids,
        )
        return AgentResult(
            documents=documents, evidence=evidence,
            metadata={"visible_findings": len(documents),
                      "source_findings_in_snapshot": len(visible_finding_ids),
                      "superseded_index_documents": superseded_indexes,
                      "scoring_applied": False},
        )
