from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
import json
from pathlib import Path

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext, AgentEvidence, AgentResult, DocumentRecord,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.services.sec_form4_service import (
    PARSER_VERSION as FORM4_PARSER_VERSION, form4_transactions_as_of,
)
from market_checker_app.services.specialist_identity_config import configured_counterparty_identities


class ScoutIndexAgent(BaseAgent):
    """Expose already observed filing leads as audit-only analysis evidence."""

    name = "scout_index"
    version = "0.1"
    dependencies = ("entity_registry",)

    def __init__(self, db_path: Path) -> None:
        self.store = ScoutStore(db_path)
        self.counterparty_identities, self.counterparty_identity_manifest_status = configured_counterparty_identities()

    def run(self, context: AgentContext) -> AgentResult:
        rows = self.store.findings_for_watchlist(
            list(context.watchlist), as_of=context.started_at,
            form4_parser_version=FORM4_PARSER_VERSION,
        )
        parsed_rows = [(row, json.loads(str(row["details_json"]))) for row in rows]
        primary_documents = {
            (str(row["subject_id"]), str(row["source_object_id"]))
            for row, details in parsed_rows
            if row["source"] == "sec"
            and row["verification_status"] == "SOURCE_VERIFIED"
            and details.get("stage") == "filing_document"
        }
        upgraded_form4_documents: dict[tuple[str, str], dict] = {}
        for row, details in parsed_rows:
            if (row["source"] == "sec" and row["verification_status"] == "SOURCE_VERIFIED"
                    and details.get("stage") == "filing_document"
                    and isinstance(details.get("form4_observation"), dict)
                    and details["form4_observation"].get("parser_version") == FORM4_PARSER_VERSION):
                key = (str(row["subject_id"]), str(row["source_object_id"]))
                # Latest available representation drives the report; older
                # document versions still belong to its immutable audit snapshot.
                prior = upgraded_form4_documents.get(key)
                if prior is None or row["first_observed_at"] > prior["first_observed_at"]:
                    upgraded_form4_documents[key] = row
                elif (row["first_observed_at"] == prior["first_observed_at"]
                      and row["content_hash"] != prior["content_hash"]):
                    raise ValueError("Ambiguous simultaneous Form 4 primary versions")
        visible_finding_ids = [
            str(row["finding_id"])
            for row, _details in parsed_rows
            if row["source"] == "sec"
            and row["verification_status"] == "SOURCE_VERIFIED"
        ]
        documents: list[DocumentRecord] = []
        evidence: list[AgentEvidence] = []
        superseded_indexes = 0
        superseded_form4_versions = 0
        insider_by_ticker: dict[str, list] = {}
        durable_form4_accessions: dict[str, set[str]] = {}
        for row, details in parsed_rows:
            if row["source"] != "sec" or row["verification_status"] != "SOURCE_VERIFIED":
                continue
            source_key = (str(row["subject_id"]), str(row["source_object_id"]))
            if details.get("stage") == "filing_index" and source_key in primary_documents:
                superseded_indexes += 1
                continue
            form4 = details.get("form4_observation")
            if (details.get("stage") == "filing_document" and source_key in upgraded_form4_documents
                    and row["finding_id"] != upgraded_form4_documents[source_key]["finding_id"]):
                superseded_form4_versions += 1
                continue
            exposure = details.get("stage") == "exposure_candidate"
            content_observed = details.get("stage") == "filing_document"
            item_locators = [item["locator"] for item in details.get("item_excerpts", [])]
            document_id = f"scout-index:{row['finding_id']}"
            observed_at = datetime.fromisoformat(str(row["first_observed_at"]))
            published_at = datetime.fromisoformat(str(row["published_at"]))
            ticker = str(row["subject_id"])
            url = str(row["source_url"])
            if content_observed and isinstance(form4, Mapping):
                durable_form4_accessions.setdefault(ticker, set()).add(str(details["accession"]))
                insider_by_ticker.setdefault(ticker, []).extend(form4_transactions_as_of(
                    form4, issuer_cik=str(details["cik"]), accession_number=str(details["accession"]),
                    source_url=url, knowledge_at=context.started_at,
                ))
            supplier_identity = None
            if exposure and details.get("kind") == "supply_chain":
                try:
                    supplier_identity = self.counterparty_identities.resolve(
                        disclosed_name=str(details.get("resource_or_counterparty", "")),
                        published_at=published_at, as_of=context.started_at,
                        exposure_source_url=url)
                except (TypeError, ValueError):
                    supplier_identity = {"counterparty_identity_status": "NAMED_ONLY",
                                         "counterparty_identity_resolution": "INVALID_CANDIDATE",
                                         "counterparty_issuer_attribution_allowed": False,
                                         "counterparty_financials_attribution_allowed": False}
            documents.append(DocumentRecord(
                document_id=document_id, ticker=ticker,
                source="SEC EDGAR evidence" if exposure else "SEC EDGAR index",
                source_type="regulatory_filing", observed_at=observed_at,
                published_at=published_at, url=url,
                canonical_event_key=f"sec-filing:{ticker}:{row['source_object_id']}",
                metadata={"locator": row["locator"],
                          "finding_id": row["finding_id"],
                          "filing_index_only": not (content_observed or exposure),
                          "source_verified_primary": content_observed,
                          "exposure_candidate": exposure,
                          "counterparty_identity": supplier_identity,
                          "form": details.get("form"),
                          "accession_number": details.get("accession"),
                          "issuer_cik": details.get("cik"),
                          "report_date": details.get("report_date"),
                          "beneficial_ownership": details.get("beneficial_ownership"),
                          "form4_observation": form4,
                          "form4_identity_review": details.get("form4_identity_review") if form4 else None,
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
                          "counterparty_identity": supplier_identity,
                          "finding_id": row["finding_id"]},
            ))
        self.store.record_analysis_snapshot(
            context.orchestration_id, as_of=context.started_at,
            finding_ids=visible_finding_ids,
        )
        updates: dict[str, object] = {}
        if durable_form4_accessions:
            existing = context.state.get("sec_insider_transactions_by_ticker")
            merged = {ticker: list(values) for ticker, values in existing.items()
                      if isinstance(values, list)} if isinstance(existing, Mapping) else {}
            for ticker, accessions in durable_form4_accessions.items():
                # Complete durable observations replace legacy first-owner-only
                # rows, including a joint report that intentionally emits none.
                merged[ticker] = [item for item in merged.get(ticker, [])
                                  if getattr(item, "accession_number", None) not in accessions]
                merged[ticker].extend(insider_by_ticker.get(ticker, []))
            updates["sec_insider_transactions_by_ticker"] = merged
        return AgentResult(
            documents=documents, evidence=evidence,
            metadata={"visible_findings": len(documents),
                      "source_findings_in_snapshot": len(visible_finding_ids),
                      "superseded_index_documents": superseded_indexes,
                      "superseded_form4_versions": superseded_form4_versions,
                      "observed_form4_transactions": sum(len(values) for values in insider_by_ticker.values()),
                      "counterparty_identity_manifest_status": self.counterparty_identity_manifest_status,
                      "counterparty_identity_scope": self.counterparty_identities.fingerprint(as_of=context.started_at),
                      "scoring_applied": False},
            state_updates=updates,
        )
