from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone
import hashlib
import re
from typing import Iterable

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    DocumentRecord,
    EntityRecord,
    GovernanceEvent,
    GovernanceEventStatus,
    GovernanceEventType,
    utc_now,
)
from market_checker_app.config import GovernanceEventConfig


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _base_form(value: object) -> str:
    return str(value or "").strip().upper().removesuffix("/A")


TEXT_PATTERNS: tuple[
    tuple[GovernanceEventType, re.Pattern[str], str, float], ...
] = (
    (
        GovernanceEventType.QUALIFIED_OPINION,
        re.compile(
            r"\b(?:qualified|adverse|disclaimer of)(?:\s+audit)?\s+opinion\b",
            re.IGNORECASE,
        ),
        "Auditní dokument obsahuje výrok vyžadující kontrolu.",
        0.85,
    ),
    (
        GovernanceEventType.MATERIAL_WEAKNESS,
        re.compile(
            r"\b(?:identified|concluded|there\s+(?:is|are)|exist(?:s|ed)?)"
            r"[^.]{0,180}\bmaterial weakness(?:es)?\b",
            re.IGNORECASE,
        ),
        "Dokument uvádí možný material weakness v interních kontrolách.",
        0.90,
    ),
    (
        GovernanceEventType.AUDITOR_CHANGE,
        re.compile(
            r"\b(?:dismissed|engaged|appointed|resigned)[^.]{0,160}"
            r"\b(?:independent accountant|independent auditor|audit firm)\b",
            re.IGNORECASE,
        ),
        "Dokument popisuje změnu externího auditora.",
        0.90,
    ),
    (
        GovernanceEventType.RESTATEMENT,
        re.compile(
            r"\b(?:financial statements should no longer be relied upon|"
            r"restatement of (?:our|the) financial statements|will restate)\b",
            re.IGNORECASE,
        ),
        "Dokument obsahuje oznámení restatementu nebo non-reliance.",
        0.95,
    ),
    (
        GovernanceEventType.EXECUTIVE_RESIGNATION,
        re.compile(
            r"\b(?:chief executive officer|chief financial officer|CEO|CFO)\b"
            r"[^.]{0,180}\b(?:resigned|resignation|departed|stepped down)\b|"
            r"\b(?:resigned|resignation|departed|stepped down)\b[^.]{0,180}"
            r"\b(?:chief executive officer|chief financial officer|CEO|CFO)\b",
            re.IGNORECASE,
        ),
        "Dokument popisuje odchod CEO nebo CFO.",
        0.90,
    ),
    (
        GovernanceEventType.DIRECTOR_RESIGNATION,
        re.compile(
            r"\b(?:director|board member)\b[^.]{0,150}"
            r"\b(?:resigned|resignation|departed|stepped down)\b|"
            r"\b(?:resigned|resignation|departed|stepped down)\b[^.]{0,150}"
            r"\b(?:director|board member)\b",
            re.IGNORECASE,
        ),
        "Dokument popisuje odchod člena boardu.",
        0.85,
    ),
    (
        GovernanceEventType.RELATED_PARTY_TRANSACTION,
        re.compile(r"\brelated[- ]party transaction(?:s)?\b", re.IGNORECASE),
        "Dokument zmiňuje transakci se spřízněnou osobou.",
        0.65,
    ),
    (
        GovernanceEventType.STOCK_PLEDGE,
        re.compile(
            r"\b(?:shares? (?:were |are )?pledged|pledged (?:his |her |their )?shares?)\b",
            re.IGNORECASE,
        ),
        "Dokument zmiňuje zastavení akcií.",
        0.70,
    ),
    (
        GovernanceEventType.STOCK_COMPENSATION,
        re.compile(r"\b(?:stock|share)-based compensation\b", re.IGNORECASE),
        "Dokument obsahuje stock/share-based compensation.",
        0.65,
    ),
)


class GovernanceEventAgent(BaseAgent):
    """Extract auditable governance events without emitting a trading signal."""

    name = "governance_event"
    version = "1.0"
    required = False
    dependencies = ("entity_registry",)

    def __init__(
        self,
        config: GovernanceEventConfig | None = None,
        *,
        dependencies: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config or GovernanceEventConfig()
        if dependencies is not None:
            self.dependencies = dependencies

    @staticmethod
    def _documents(context: AgentContext) -> list[DocumentRecord]:
        results = context.state.get("agent_results")
        if not isinstance(results, Mapping):
            return []
        return [
            document
            for result in results.values()
            if isinstance(result, AgentResult)
            for document in result.documents
        ]

    @staticmethod
    def _text_index(context: AgentContext) -> dict[str, str]:
        indexed: dict[str, str] = {}
        for state_key in (
            "sec_filing_texts_by_ticker",
            "european_filing_texts_by_ticker",
        ):
            by_ticker = context.state.get(state_key)
            if not isinstance(by_ticker, Mapping):
                continue
            for values in by_ticker.values():
                if not isinstance(values, list):
                    continue
                for fetched in values:
                    text = str(getattr(fetched, "text", "") or "").strip()
                    if not text:
                        continue
                    for url in (
                        getattr(fetched, "final_url", None),
                        getattr(getattr(fetched, "source", None), "url", None),
                    ):
                        if url:
                            indexed[str(url)] = text
        return indexed

    @staticmethod
    def _items(document: DocumentRecord) -> set[str]:
        raw_items = document.metadata.get("items", [])
        if isinstance(raw_items, str):
            values = raw_items.split(",")
        elif isinstance(raw_items, list):
            values = raw_items
        else:
            values = []
        return {str(item).strip() for item in values if str(item).strip()}

    @staticmethod
    def _entity_for(
        registry: Mapping[str, object],
        document: DocumentRecord,
    ) -> EntityRecord | None:
        entity = registry.get(document.ticker)
        return entity if isinstance(entity, EntityRecord) else None

    @staticmethod
    def _event(
        *,
        document: DocumentRecord,
        legal_entity_id: str,
        event_type: GovernanceEventType,
        status: GovernanceEventStatus,
        title: str,
        confidence: float,
        discriminator: object,
        actor: str | None = None,
        transaction_type: str | None = None,
        shares: float | None = None,
        price_per_share: float | None = None,
        event_value: float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> GovernanceEvent:
        published_at = document.published_at or document.observed_at
        return GovernanceEvent(
            event_id="governance:" + _stable_id(
                document.document_id,
                event_type.value,
                discriminator,
            ),
            ticker=document.ticker,
            event_type=event_type,
            status=status,
            title=title,
            observed_at=utc_now(),
            published_at=published_at,
            document_id=document.document_id,
            source_url=document.url or "",
            legal_entity_id=legal_entity_id,
            confidence=confidence,
            actor=actor,
            transaction_type=transaction_type,
            shares=shares,
            price_per_share=price_per_share,
            event_value=event_value,
            metadata=dict(metadata or {}),
        )

    def _document_events(
        self,
        document: DocumentRecord,
        *,
        legal_entity_id: str,
        text: str,
    ) -> Iterable[GovernanceEvent]:
        form = _base_form(document.metadata.get("form"))
        items = self._items(document)
        if "4.01" in items:
            yield self._event(
                document=document,
                legal_entity_id=legal_entity_id,
                event_type=GovernanceEventType.AUDITOR_CHANGE,
                status=GovernanceEventStatus.VERIFIED,
                title="SEC Item 4.01 – změna certifikujícího auditora",
                confidence=1.0,
                discriminator="item-4.01",
                metadata={"sec_item": "4.01", "scoring_applied": False},
            )
        if "4.02" in items:
            yield self._event(
                document=document,
                legal_entity_id=legal_entity_id,
                event_type=GovernanceEventType.RESTATEMENT,
                status=GovernanceEventStatus.VERIFIED,
                title="SEC Item 4.02 – non-reliance / restatement",
                confidence=1.0,
                discriminator="item-4.02",
                metadata={"sec_item": "4.02", "scoring_applied": False},
            )
        if "3.02" in items:
            yield self._event(
                document=document,
                legal_entity_id=legal_entity_id,
                event_type=GovernanceEventType.DILUTION,
                status=GovernanceEventStatus.VERIFIED,
                title="SEC Item 3.02 – neregistrovaný prodej cenných papírů",
                confidence=0.95,
                discriminator="item-3.02",
                metadata={"sec_item": "3.02", "scoring_applied": False},
            )
        if form in {"SC 13D", "SC 13G"}:
            yield self._event(
                document=document,
                legal_entity_id=legal_entity_id,
                event_type=GovernanceEventType.BENEFICIAL_OWNERSHIP_CHANGE,
                status=GovernanceEventStatus.VERIFIED,
                title=f"SEC {form} – významný vlastnický podíl",
                confidence=1.0,
                discriminator=form,
                metadata={"form": form, "scoring_applied": False},
            )
        if form == "S-1" or form.startswith("424B"):
            yield self._event(
                document=document,
                legal_entity_id=legal_entity_id,
                event_type=GovernanceEventType.DILUTION,
                status=GovernanceEventStatus.UNVERIFIED,
                title=f"SEC {form} – nabídka cenných papírů vyžaduje diluční analýzu",
                confidence=0.60,
                discriminator=form,
                metadata={
                    "form": form,
                    "offering_not_assumed_completed": True,
                    "scoring_applied": False,
                },
            )
        if not self.config.scan_filing_text or len(text) < self.config.minimum_pattern_characters:
            return
        for event_type, pattern, title, confidence in TEXT_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            snippet = text[max(0, match.start() - 90) : match.end() + 180].strip()
            yield self._event(
                document=document,
                legal_entity_id=legal_entity_id,
                event_type=event_type,
                status=GovernanceEventStatus.UNVERIFIED,
                title=title,
                confidence=confidence,
                discriminator=match.group(0).casefold(),
                metadata={
                    "pattern_match": match.group(0),
                    "context_snippet": snippet,
                    "human_review_required": True,
                    "scoring_applied": False,
                },
            )

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["GovernanceEventAgent je vypnutý v konfiguraci."],
            )
        documents = self._documents(context)
        registry = context.state.get("entities_by_ticker")
        registry = registry if isinstance(registry, Mapping) else {}
        text_index = self._text_index(context)
        events: dict[str, GovernanceEvent] = {}
        warnings: list[str] = []
        future_documents = 0
        future_transactions = 0

        document_by_accession = {
            (
                str(document.ticker).strip().upper(),
                str(document.metadata.get("accession_number")),
            ): document
            for document in documents
            if document.metadata.get("accession_number")
        }
        for document in documents:
            published_at = document.published_at or document.observed_at
            if published_at > context.started_at:
                future_documents += 1
                continue
            entity = self._entity_for(registry, document)
            legal_entity_id = document.legal_entity_id or (
                entity.legal_entity_id if entity else None
            )
            if not legal_entity_id:
                warnings.append(
                    f"Governance {document.ticker}: dokument {document.document_id} nemá právní entitu."
                )
                continue
            text = text_index.get(document.url or "", "")
            for event in self._document_events(
                document,
                legal_entity_id=legal_entity_id,
                text=text,
            ):
                events[event.event_id] = event

        raw_transactions = context.state.get("sec_insider_transactions_by_ticker")
        if isinstance(raw_transactions, Mapping):
            for ticker, transactions in raw_transactions.items():
                if not isinstance(transactions, list):
                    continue
                entity = registry.get(ticker)
                if not isinstance(entity, EntityRecord) or not entity.legal_entity_id:
                    warnings.append(
                        f"Governance {ticker}: Form 4 transakce nemá právní entitu."
                    )
                    continue
                for transaction in transactions:
                    accession = str(
                        getattr(transaction, "accession_number", "") or ""
                    )
                    document = document_by_accession.get(
                        (str(ticker).strip().upper(), accession)
                    )
                    if document is None:
                        warnings.append(
                            f"Governance {ticker}: Form 4 {accession} nemá DocumentRecord."
                        )
                        continue
                    document_published_at = (
                        document.published_at or document.observed_at
                    )
                    if document_published_at > context.started_at:
                        continue
                    code = str(getattr(transaction, "transaction_code", "UNKNOWN"))
                    acquired_disposed = str(
                        getattr(transaction, "acquired_disposed", "") or ""
                    ).upper()
                    transaction_type = {
                        "P": "PURCHASE",
                        "S": "SALE",
                        "A": "GRANT",
                        "M": "OPTION_EXERCISE",
                        "F": "TAX_WITHHOLDING",
                    }.get(code.upper(), code.upper())
                    shares = getattr(transaction, "shares", None)
                    price = getattr(transaction, "price_per_share", None)
                    event_value = (
                        float(shares) * float(price)
                        if shares is not None and price is not None
                        else None
                    )
                    actor = str(getattr(transaction, "owner_name", "") or "") or None
                    transaction_date = getattr(
                        transaction,
                        "transaction_date",
                        None,
                    )
                    if transaction_date is not None:
                        if (
                            transaction_date.tzinfo is None
                            or transaction_date.utcoffset() is None
                        ):
                            transaction_date = transaction_date.replace(
                                tzinfo=timezone.utc
                            )
                        else:
                            transaction_date = transaction_date.astimezone(
                                timezone.utc
                            )
                        if transaction_date > context.started_at:
                            future_transactions += 1
                            continue
                    transaction_date_key = (
                        transaction_date.date().isoformat()
                        if hasattr(transaction_date, "date")
                        else str(transaction_date or "")
                    )
                    event = self._event(
                        document=document,
                        legal_entity_id=entity.legal_entity_id,
                        event_type=GovernanceEventType.INSIDER_TRADE,
                        status=GovernanceEventStatus.VERIFIED,
                        title=(
                            f"Form 4 insider {transaction_type.lower()} – "
                            f"{actor or 'unknown owner'}"
                        ),
                        confidence=1.0,
                        discriminator=(
                            actor,
                            transaction_date_key,
                            code,
                            shares,
                            price,
                        ),
                        actor=actor,
                        transaction_type=transaction_type,
                        shares=shares,
                        price_per_share=price,
                        event_value=event_value,
                        metadata={
                            "accession_number": accession,
                            "owner_cik": getattr(transaction, "owner_cik", None),
                            "acquired_disposed": acquired_disposed,
                            "derivative": bool(getattr(transaction, "derivative", False)),
                            "transaction_date": str(
                                transaction_date or ""
                            ),
                            "scoring_applied": False,
                        },
                    )
                    events[event.event_id] = event

        ordered_events = sorted(
            events.values(),
            key=lambda item: (item.published_at, item.event_id),
        )
        evidence = [
            AgentEvidence(
                evidence_id=_stable_id(
                    context.orchestration_id,
                    self.name,
                    event.event_id,
                ),
                ticker=event.ticker,
                agent_name=self.name,
                event_type="GOVERNANCE_EVENT_RECORDED",
                observed_at=event.observed_at,
                summary=event.title,
                direction=0.0,
                risk_score=0.0,
                confidence=event.confidence,
                hard_veto=False,
                reasons=[
                    "governance_event_is_not_a_trading_signal",
                    f"status:{event.status.value}",
                ],
                document_ids=[event.document_id],
                source_urls=[event.source_url],
                metadata={
                    "governance_event_id": event.event_id,
                    "governance_event_type": event.event_type.value,
                    "scoring_applied": False,
                },
            )
            for event in ordered_events
        ]
        by_ticker: dict[str, list[GovernanceEvent]] = {}
        for event in ordered_events:
            by_ticker.setdefault(event.ticker, []).append(event)
        return AgentResult(
            status=(
                AgentStatus.PARTIAL
                if warnings or future_documents or future_transactions
                else AgentStatus.SUCCESS
            ),
            governance_events=ordered_events,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "documents_examined": len(documents),
                "events": len(ordered_events),
                "future_documents_ignored": future_documents,
                "future_transactions_ignored": future_transactions,
                "signals_emitted": 0,
                "scoring_applied": False,
            },
            state_updates={"governance_events_by_ticker": by_ticker},
        )
