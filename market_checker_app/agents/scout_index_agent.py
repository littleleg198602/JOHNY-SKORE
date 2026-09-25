from __future__ import annotations

from datetime import datetime
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
        documents: list[DocumentRecord] = []
        evidence: list[AgentEvidence] = []
        for row in rows:
            if row["source"] != "sec" or row["verification_status"] != "SOURCE_VERIFIED":
                continue
            document_id = f"scout-index:{row['finding_id']}"
            observed_at = datetime.fromisoformat(str(row["first_observed_at"]))
            published_at = datetime.fromisoformat(str(row["published_at"]))
            ticker = str(row["subject_id"])
            url = str(row["source_url"])
            documents.append(DocumentRecord(
                document_id=document_id, ticker=ticker, source="SEC EDGAR index",
                source_type="regulatory_filing", observed_at=observed_at,
                published_at=published_at, url=url,
                canonical_event_key=f"sec-filing:{ticker}:{row['source_object_id']}",
                metadata={"locator": row["locator"],
                          "finding_id": row["finding_id"],
                          "filing_index_only": True},
            ))
            evidence.append(AgentEvidence(
                evidence_id=f"scout-evidence:{row['finding_id']}", ticker=ticker,
                agent_name=self.name, event_type="SEC_FILING_INDEX",
                observed_at=observed_at,
                summary=f"SEC eviduje podání {row['title']}.",
                direction=0.0, risk_score=0.0, confidence=0.5,
                document_ids=[document_id], source_urls=[url],
                metadata={"scoring_applied": False, "index_only": True,
                          "finding_id": row["finding_id"]},
            ))
        return AgentResult(
            documents=documents, evidence=evidence,
            metadata={"visible_findings": len(documents), "scoring_applied": False},
        )
