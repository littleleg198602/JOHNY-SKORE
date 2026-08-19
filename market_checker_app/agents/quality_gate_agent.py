from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
from typing import Any
from urllib.parse import urlparse

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentSignal,
    AgentStatus,
    ClaimStatus,
    DocumentRecord,
    GateDecision,
    QualityGateCheck,
    ResearchClaim,
    utc_now,
)
from market_checker_app.config import QualityGateConfig
from market_checker_app.utils.text import normalize_ticker


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


class QualityGateAgent(BaseAgent):
    """Validate analytical outputs without changing the trading prediction."""

    name = "quality_gate"
    version = "1.0"
    required = True
    dependencies = ("entity_registry", "prediction_v21_adapter")

    ALLOWED_ACTIONS = {"BUY", "SELL", "NO_TRADE"}
    ALLOWED_FORECASTS = {"UP", "DOWN", "FLAT"}
    FORECAST_DIRECTIONS = {"UP": 1.0, "DOWN": -1.0, "FLAT": 0.0}

    def __init__(
        self,
        config: QualityGateConfig | None = None,
        *,
        minimum_action_confidence: float = 0.30,
        dependencies: tuple[str, ...] | None = None,
    ) -> None:
        self.config = config or QualityGateConfig()
        self.minimum_action_confidence = max(
            0.0,
            min(1.0, float(minimum_action_confidence)),
        )
        if dependencies is not None:
            self.dependencies = dependencies

    @staticmethod
    def _agent_outputs(context_state: dict[str, Any]) -> tuple[
        list[AgentSignal],
        list[AgentEvidence],
        list[DocumentRecord],
        list[ResearchClaim],
        dict[str, list[ResearchClaim]],
    ]:
        results = context_state.get("agent_results")
        if not isinstance(results, dict):
            return [], [], [], [], {}

        signals: list[AgentSignal] = []
        evidence: list[AgentEvidence] = []
        documents: list[DocumentRecord] = []
        claims_by_id: dict[str, ResearchClaim] = {}
        claim_versions_by_id: dict[str, list[ResearchClaim]] = defaultdict(list)
        for result in results.values():
            if not isinstance(result, AgentResult):
                continue
            signals.extend(result.signals)
            evidence.extend(result.evidence)
            documents.extend(result.documents)
            for claim in result.claims:
                claim_versions_by_id[claim.claim_id].append(claim)
                claims_by_id[claim.claim_id] = claim
        return (
            signals,
            evidence,
            documents,
            list(claims_by_id.values()),
            dict(claim_versions_by_id),
        )

    def _check_timestamp(
        self,
        value: datetime,
        now: datetime,
        label: str,
        rejects: list[dict[str, str]],
    ) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            rejects.append(
                _issue("naive_timestamp", f"{label} nemá časové pásmo.")
            )
            return

        age_minutes = (now - value).total_seconds() / 60.0
        if age_minutes > self.config.max_signal_age_minutes:
            rejects.append(
                _issue(
                    "stale_observation",
                    f"{label} je starý {age_minutes:.1f} minuty.",
                )
            )
        elif age_minutes < -self.config.max_future_clock_skew_minutes:
            rejects.append(
                _issue(
                    "future_observation",
                    f"{label} leží {abs(age_minutes):.1f} minuty v budoucnosti.",
                )
            )

    @staticmethod
    def _check_expiry(
        value: datetime | None,
        now: datetime,
        label: str,
        rejects: list[dict[str, str]],
    ) -> None:
        if value is None:
            return
        if value.tzinfo is None or value.utcoffset() is None:
            rejects.append(_issue("naive_expiry", f"{label} nemá časové pásmo."))
        elif value <= now:
            rejects.append(_issue("expired_output", f"{label} již expiroval."))

    def _check_signal(
        self,
        signal: AgentSignal,
        *,
        now: datetime,
        evidence_by_id: dict[str, AgentEvidence],
        evidence_id_counts: Counter[str],
        rejects: list[dict[str, str]],
        warnings: list[dict[str, str]],
    ) -> None:
        if signal.action not in self.ALLOWED_ACTIONS:
            rejects.append(
                _issue("invalid_action", f"Nepovolená action {signal.action!r}.")
            )
        if signal.forecast not in self.ALLOWED_FORECASTS:
            rejects.append(
                _issue("invalid_forecast", f"Nepovolený forecast {signal.forecast!r}.")
            )
        else:
            expected_direction = self.FORECAST_DIRECTIONS[signal.forecast]
            if abs(signal.direction - expected_direction) > 1e-9:
                rejects.append(
                    _issue(
                        "direction_mismatch",
                        "Číselný směr neodpovídá hodnotě forecast.",
                    )
                )

        if signal.action == "BUY" and signal.forecast != "UP":
            rejects.append(
                _issue("buy_forecast_conflict", "BUY musí mít forecast UP.")
            )
        if signal.action == "SELL" and signal.forecast != "DOWN":
            rejects.append(
                _issue("sell_forecast_conflict", "SELL musí mít forecast DOWN.")
            )
        if signal.hard_veto and signal.action != "NO_TRADE":
            rejects.append(
                _issue("veto_action_conflict", "Hard veto dovoluje pouze NO_TRADE.")
            )
        if (
            signal.action in {"BUY", "SELL"}
            and signal.confidence < self.minimum_action_confidence
        ):
            rejects.append(
                _issue(
                    "low_action_confidence",
                    "Obchodní akce nedosahuje minimální povolené důvěry.",
                )
            )
        if signal.action in {"BUY", "SELL"} and not signal.reasons:
            warnings.append(
                _issue("missing_action_reason", "Obchodní akce nemá vysvětlení.")
            )
        if not signal.agent_name.strip() or not signal.agent_version.strip():
            rejects.append(
                _issue("missing_agent_identity", "Signálu chybí identita nebo verze agenta.")
            )

        self._check_timestamp(signal.observed_at, now, "Signál", rejects)
        self._check_expiry(signal.expires_at, now, "Signál", rejects)

        if not signal.evidence_ids:
            rejects.append(
                _issue("missing_evidence_link", "Signál nemá vazbu na evidence.")
            )
            return

        linked_evidence: list[AgentEvidence] = []
        for evidence_id in signal.evidence_ids:
            if evidence_id_counts[evidence_id] != 1:
                rejects.append(
                    _issue(
                        "invalid_evidence_cardinality",
                        f"Evidence {evidence_id} musí existovat právě jednou.",
                    )
                )
                continue
            evidence = evidence_by_id[evidence_id]
            linked_evidence.append(evidence)
            if evidence.ticker != signal.ticker:
                rejects.append(
                    _issue(
                        "evidence_ticker_mismatch",
                        f"Evidence {evidence_id} patří jinému tickeru.",
                    )
                )

        if signal.hard_veto and linked_evidence and not any(
            evidence.hard_veto for evidence in linked_evidence
        ):
            rejects.append(
                _issue(
                    "unsubstantiated_veto",
                    "Hard veto signálu není potvrzeno navázanou evidencí.",
                )
            )
        if any(evidence.hard_veto for evidence in linked_evidence) and not signal.hard_veto:
            rejects.append(
                _issue(
                    "ignored_evidence_veto",
                    "Signál ignoruje hard veto navázané evidence.",
                )
            )

    def _check_evidence(
        self,
        evidence: AgentEvidence,
        *,
        now: datetime,
        document_ids: set[str],
        rejects: list[dict[str, str]],
        warnings: list[dict[str, str]],
    ) -> None:
        self._check_timestamp(evidence.observed_at, now, "Evidence", rejects)
        self._check_expiry(evidence.valid_until, now, "Evidence", rejects)

        missing_documents = sorted(set(evidence.document_ids).difference(document_ids))
        if missing_documents:
            rejects.append(
                _issue(
                    "unknown_document_link",
                    f"Evidence odkazuje na neznámé dokumenty: {missing_documents}.",
                )
            )

        if (
            self.config.require_external_provenance
            and evidence.event_type not in self.config.provenance_exempt_event_types
        ):
            if not evidence.document_ids and not evidence.source_urls:
                rejects.append(
                    _issue(
                        "missing_external_provenance",
                        "Externí evidence nemá dokument ani zdrojovou URL.",
                    )
                )
            elif not evidence.document_ids:
                warnings.append(
                    _issue(
                        "unarchived_external_source",
                        "Externí evidence má URL, ale nemá archivovaný dokument.",
                    )
                )

    @staticmethod
    def _is_primary_sec_document(document: DocumentRecord) -> bool:
        hostname = str(urlparse(document.url or "").hostname or "").lower()
        return document.source_type == "regulatory_filing" and (
            hostname == "sec.gov" or hostname.endswith(".sec.gov")
        )

    def _check_claim(
        self,
        claim: ResearchClaim,
        *,
        now: datetime,
        documents_by_id: dict[str, DocumentRecord],
        claim_versions: list[ResearchClaim],
        rejects: list[dict[str, str]],
    ) -> None:
        document_ids = set(documents_by_id)
        self._check_timestamp(claim.observed_at, now, "Tvrzení", rejects)
        if claim.published_at.tzinfo is None or claim.published_at.utcoffset() is None:
            rejects.append(
                _issue("naive_claim_publication", "Datum publikace tvrzení nemá časové pásmo.")
            )
        elif (
            claim.published_at - now
        ).total_seconds() > self.config.max_future_clock_skew_minutes * 60.0:
            rejects.append(
                _issue("future_claim_publication", "Tvrzení má budoucí datum publikace.")
            )
        if not claim.statement.strip():
            rejects.append(_issue("empty_claim", "Tvrzení nemá text."))
        if not isinstance(claim.status, ClaimStatus):
            rejects.append(_issue("invalid_claim_status", "Tvrzení má neplatný stav."))
        origin_versions = [
            version
            for version in claim_versions
            if version.status == ClaimStatus.UNVERIFIED
            and version.verification_agent_name is None
        ]
        if len(claim_versions) > 2:
            rejects.append(
                _issue(
                    "too_many_claim_versions",
                    "Tvrzení má v jednom běhu více než dvě verze.",
                )
            )
        if claim.status == ClaimStatus.UNVERIFIED:
            if len(claim_versions) != 1 or len(origin_versions) != 1:
                rejects.append(
                    _issue(
                        "duplicate_unverified_claim",
                        "Neověřené tvrzení musí mít v jednom běhu právě jednu zdrojovou verzi.",
                    )
                )
        elif len(claim_versions) != 2 or len(origin_versions) != 1:
            rejects.append(
                _issue(
                    "claim_missing_unverified_origin",
                    "Ověřenému tvrzení chybí právě jedna původní neověřená verze.",
                )
            )
        if origin_versions:
            origin = origin_versions[0]
            if (
                origin.ticker != claim.ticker
                or origin.report_document_id != claim.report_document_id
                or origin.claim_type != claim.claim_type
                or origin.statement != claim.statement
                or origin.published_at != claim.published_at
                or origin.source_agent_name != claim.source_agent_name
            ):
                rejects.append(
                    _issue(
                        "claim_identity_mutated",
                        "Ověřovací agent změnil identitu nebo text původního tvrzení.",
                    )
                )
        if claim.status != ClaimStatus.UNVERIFIED and (
            not claim.verification_agent_name
            or not claim.verification_summary.strip()
        ):
            rejects.append(
                _issue(
                    "claim_missing_verification_identity",
                    "Ověřenému tvrzení chybí identita nebo shrnutí ověření.",
                )
            )
        if claim.report_document_id not in document_ids:
            rejects.append(
                _issue(
                    "unknown_claim_report",
                    f"Tvrzení odkazuje na neznámý report {claim.report_document_id}.",
                )
            )
        else:
            report_document = documents_by_id[claim.report_document_id]
            if report_document.source_type != "short_report":
                rejects.append(
                    _issue(
                        "claim_report_type_mismatch",
                        "Zdrojový dokument tvrzení není označen jako short report.",
                    )
                )
            if (
                not report_document.url
                or report_document.url not in claim.source_urls
            ):
                rejects.append(
                    _issue(
                        "claim_report_url_mismatch",
                        "URL zdrojového reportu chybí mezi zdroji tvrzení.",
                    )
                )
            if report_document.published_at != claim.published_at:
                rejects.append(
                    _issue(
                        "claim_report_date_mismatch",
                        "Datum tvrzení neodpovídá datu zdrojového reportu.",
                    )
                )
        missing_documents = sorted(
            set(claim.evidence_document_ids).difference(document_ids)
        )
        if missing_documents:
            rejects.append(
                _issue(
                    "unknown_claim_evidence",
                    f"Tvrzení odkazuje na neznámé dokumenty: {missing_documents}.",
                )
            )
        if claim.report_document_id not in claim.evidence_document_ids:
            rejects.append(
                _issue(
                    "claim_missing_report_provenance",
                    "Tvrzení nemá report mezi svými důkazními dokumenty.",
                )
            )
        if not claim.source_urls:
            rejects.append(
                _issue("claim_missing_source_url", "Tvrzení nemá zdrojovou URL.")
            )
        if claim.status in {ClaimStatus.CORROBORATED, ClaimStatus.CONTRADICTED}:
            if claim.confidence <= 0.0:
                rejects.append(
                    _issue(
                        "verified_claim_without_confidence",
                        "Potvrzené nebo vyvrácené tvrzení nemá kladnou důvěru.",
                    )
                )
            primary_documents = set(claim.evidence_document_ids).difference(
                {claim.report_document_id}
            )
            if not primary_documents:
                rejects.append(
                    _issue(
                        "claim_missing_primary_evidence",
                        "Potvrzené nebo vyvrácené tvrzení nemá nezávislý primární dokument.",
                    )
                )
            else:
                primary_sec_documents = [
                    documents_by_id[document_id]
                    for document_id in primary_documents
                    if document_id in documents_by_id
                    and self._is_primary_sec_document(
                        documents_by_id[document_id]
                    )
                ]
                if not primary_sec_documents:
                    rejects.append(
                        _issue(
                            "claim_missing_primary_sec_evidence",
                            "Ověřené tvrzení nemá nezávislý regulatorní dokument SEC.",
                        )
                    )

    def run(self, context: AgentContext) -> AgentResult:
        (
            signals,
            evidence,
            documents,
            claims,
            claim_versions_by_id,
        ) = self._agent_outputs(context.state)
        now = utc_now()
        expected_tickers = {
            normalize_ticker(ticker) for ticker in context.watchlist if normalize_ticker(ticker)
        }
        registered_entities = context.state.get("entities_by_ticker")
        registered_tickers = (
            set(registered_entities)
            if isinstance(registered_entities, dict)
            else set()
        )

        evidence_id_counts = Counter(item.evidence_id for item in evidence)
        evidence_by_id = {item.evidence_id: item for item in evidence}
        signal_id_counts = Counter(item.signal_id for item in signals)
        documents_by_id = {
            document.document_id: document
            for document in documents
            if document.document_id
        }
        document_ids = set(documents_by_id)
        v21_by_ticker: dict[str, list[AgentSignal]] = defaultdict(list)
        all_signals_by_ticker: dict[str, list[AgentSignal]] = defaultdict(list)
        evidence_by_ticker: dict[str, list[AgentEvidence]] = defaultdict(list)
        claims_by_ticker: dict[str, list[ResearchClaim]] = defaultdict(list)
        for signal in signals:
            all_signals_by_ticker[signal.ticker].append(signal)
            if signal.agent_name == "prediction_v21_adapter":
                v21_by_ticker[signal.ticker].append(signal)
        for item in evidence:
            evidence_by_ticker[item.ticker].append(item)
        for claim in claims:
            claims_by_ticker[claim.ticker].append(claim)

        checks: list[QualityGateCheck] = []
        all_tickers = sorted(
            expected_tickers
            | set(v21_by_ticker)
            | set(all_signals_by_ticker)
            | set(evidence_by_ticker)
            | set(claims_by_ticker)
        )
        for ticker in all_tickers:
            rejects: list[dict[str, str]] = []
            gate_warnings: list[dict[str, str]] = []
            ticker_signals = all_signals_by_ticker.get(ticker, [])
            ticker_evidence = evidence_by_ticker.get(ticker, [])
            ticker_claims = claims_by_ticker.get(ticker, [])
            v21_signals = v21_by_ticker.get(ticker, [])

            if ticker not in expected_tickers:
                rejects.append(
                    _issue("unexpected_ticker", "Výstup obsahuje ticker mimo watchlist.")
                )
            if ticker not in registered_tickers:
                rejects.append(
                    _issue("unregistered_entity", "Ticker chybí v registru entit.")
                )
            if self.config.require_full_v21_coverage:
                if not v21_signals:
                    rejects.append(
                        _issue("missing_v21_signal", "Ticker nemá predikci v2.1.")
                    )
                elif len(v21_signals) > 1:
                    rejects.append(
                        _issue("duplicate_v21_signal", "Ticker má více predikcí v2.1.")
                    )

            for signal in ticker_signals:
                if signal_id_counts[signal.signal_id] != 1:
                    rejects.append(
                        _issue(
                            "duplicate_signal_id",
                            f"Signal ID {signal.signal_id} není unikátní.",
                        )
                    )
                self._check_signal(
                    signal,
                    now=now,
                    evidence_by_id=evidence_by_id,
                    evidence_id_counts=evidence_id_counts,
                    rejects=rejects,
                    warnings=gate_warnings,
                )

            for item in ticker_evidence:
                if evidence_id_counts[item.evidence_id] != 1:
                    rejects.append(
                        _issue(
                            "duplicate_evidence_id",
                            f"Evidence ID {item.evidence_id} není unikátní.",
                        )
                    )
                self._check_evidence(
                    item,
                    now=now,
                    document_ids=document_ids,
                    rejects=rejects,
                    warnings=gate_warnings,
                )

            for claim in ticker_claims:
                self._check_claim(
                    claim,
                    now=now,
                    documents_by_id=documents_by_id,
                    claim_versions=claim_versions_by_id.get(claim.claim_id, []),
                    rejects=rejects,
                )

            if rejects:
                decision = GateDecision.REJECT
                message = f"Kontrola zamítla ticker: {len(rejects)} kritických problémů."
            elif gate_warnings:
                decision = GateDecision.WARN
                message = f"Kontrola našla {len(gate_warnings)} nekritických problémů."
            else:
                decision = GateDecision.PASS
                message = "Všechny kontrolní podmínky prošly."

            checks.append(
                QualityGateCheck(
                    check_id=_stable_id(
                        context.orchestration_id,
                        self.name,
                        ticker,
                        "prediction_integrity",
                    ),
                    ticker=ticker,
                    gate_name="prediction_integrity",
                    decision=decision,
                    observed_at=now,
                    message=message,
                    related_agent_names=sorted(
                        {signal.agent_name for signal in ticker_signals}
                        | {item.agent_name for item in ticker_evidence}
                        | {claim.source_agent_name for claim in ticker_claims}
                        | {
                            claim.verification_agent_name
                            for claim in ticker_claims
                            if claim.verification_agent_name
                        }
                    ),
                    signal_ids=[signal.signal_id for signal in ticker_signals],
                    evidence_ids=[item.evidence_id for item in ticker_evidence],
                    claim_ids=[claim.claim_id for claim in ticker_claims],
                    metadata={
                        "rejects": rejects,
                        "warnings": gate_warnings,
                        "shadow_mode": context.shadow_mode,
                    },
                )
            )

        reject_count = sum(check.decision == GateDecision.REJECT for check in checks)
        warning_count = sum(check.decision == GateDecision.WARN for check in checks)
        pass_count = sum(check.decision == GateDecision.PASS for check in checks)
        if reject_count:
            status = AgentStatus.FAILED
            decision = GateDecision.REJECT
            agent_warnings = [
                f"Quality gate zamítl {reject_count} tickerů; shadow režim zachoval původní predikci."
            ]
        elif warning_count:
            status = AgentStatus.PARTIAL
            decision = GateDecision.WARN
            agent_warnings = [
                f"Quality gate označil {warning_count} tickerů varováním."
            ]
        else:
            status = AgentStatus.SUCCESS
            decision = GateDecision.PASS
            agent_warnings = []

        summary = {
            "decision": decision.value,
            "pass_count": pass_count,
            "warning_count": warning_count,
            "reject_count": reject_count,
            "checked_tickers": len(checks),
            "shadow_mode": context.shadow_mode,
        }
        return AgentResult(
            status=status,
            quality_checks=checks,
            warnings=agent_warnings,
            metadata=summary,
            state_updates={
                "quality_gate_summary": summary,
                "quality_gate_checks": checks,
            },
        )
