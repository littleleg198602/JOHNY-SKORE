from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentSignal,
    CommodityEnergyAgent,
    CompanyRelationship,
    DocumentRecord,
    EntityRegistryAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    RegulatoryContractAgent,
    RelationshipType,
    SupplyChainAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import utc_now
from market_checker_app.config import (
    CommodityEnergyConfig,
    CommodityEnergySourceConfig,
    RegulatoryContractConfig,
    RegulatoryContractSourceConfig,
    Stage3SourceVerificationConfig,
    SupplyChainConfig,
    SupplyChainSourceConfig,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.services.stage3_manifest_service import (
    parse_commodity_energy_sources,
    parse_regulatory_contract_sources,
    parse_supply_chain_sources,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "TEST",
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.75,
                "risk_score": 20.0,
                "action_reasons": '["Confirmed consensus"]',
            }
        ]
    )


def _configs():
    published_at = datetime(2026, 1, 15, tzinfo=timezone.utc)
    supply = SupplyChainConfig(
        enabled=True,
        sources=(
            SupplyChainSourceConfig(
                ticker="TEST",
                counterparty="Example Components",
                relationship_type="SUPPLIER",
                dependency_pct=35.0,
                publisher="Company annual report",
                published_at=published_at,
                url="https://example.com/company-report",
            ),
        ),
    )
    resource = CommodityEnergyConfig(
        enabled=True,
        sources=(
            CommodityEnergySourceConfig(
                ticker="TEST",
                resource_name="Copper",
                exposure_type="MATERIAL_INPUT",
                dependency_pct=18.0,
                publisher="Company sustainability report",
                published_at=published_at,
                url="https://example.com/sustainability-report",
            ),
        ),
    )
    regulatory = RegulatoryContractConfig(
        enabled=True,
        sources=(
            RegulatoryContractSourceConfig(
                ticker="TEST",
                event_type="CONTRACT_AWARD",
                status="ANNOUNCED",
                title="Example public contract",
                authority_or_counterparty="Example Agency",
                event_value=25_000_000.0,
                currency="USD",
                publisher="Example Agency",
                published_at=published_at,
                url="https://agency.example.org/awards/example",
            ),
        ),
    )
    return supply, resource, regulatory


def _run_stage3():
    supply, resource, regulatory = _configs()
    frame = _signals()
    original = frame.copy(deep=True)
    orchestrator = OrchestratorAgent(shadow_mode=True)
    orchestrator.register(EntityRegistryAgent())
    orchestrator.register(SupplyChainAgent(supply))
    orchestrator.register(CommodityEnergyAgent(resource))
    orchestrator.register(RegulatoryContractAgent(regulatory))
    orchestrator.register(PredictionV21AdapterAgent())
    orchestrator.register(QualityGateAgent())
    report = orchestrator.run(watchlist=["TEST"], state={"signals": frame})
    pd.testing.assert_frame_equal(original, frame)
    return report


class StageThreeAgentTests(unittest.TestCase):
    def test_three_agents_are_audit_only_and_quality_gate_passes(self) -> None:
        report = _run_stage3()

        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertEqual(1, len(report.company_relationships))
        self.assertEqual(1, len(report.resource_exposures))
        self.assertEqual(1, len(report.regulatory_contract_events))
        self.assertEqual(1, len(report.signals))
        self.assertEqual("prediction_v21_adapter", report.signals[0].agent_name)
        self.assertEqual("BUY", report.signals[0].action)
        stage3_evidence = [
            item
            for item in report.evidence
            if item.agent_name
            in {"supply_chain", "commodity_energy", "regulatory_contract"}
        ]
        self.assertEqual(3, len(stage3_evidence))
        self.assertTrue(
            all(
                item.direction == 0.0
                and item.risk_score == 0.0
                and not item.hard_veto
                and item.metadata["scoring_applied"] is False
                for item in stage3_evidence
            )
        )
        check = report.quality_checks[0]
        self.assertEqual(1, len(check.relationship_ids))
        self.assertEqual(1, len(check.exposure_ids))
        self.assertEqual(1, len(check.regulatory_event_ids))

    def test_stage3_records_and_observations_are_persisted_without_duplicates(self) -> None:
        first = _run_stage3()
        second = _run_stage3()
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first)
            store.save_orchestration_report(second)

            self.assertEqual(1, len(store.read_company_relationships("TEST")))
            self.assertEqual(1, len(store.read_resource_exposures("TEST")))
            self.assertEqual(1, len(store.read_regulatory_contract_events("TEST")))
            checks = store.read_quality_gate_checks(first.orchestration_id)
            self.assertEqual(1, len(checks))
            self.assertIn("relationship_ids_json", checks.columns)
            self.assertIn("exposure_ids_json", checks.columns)
            self.assertIn("regulatory_event_ids_json", checks.columns)
            with store._connect() as conn:
                relationship_observations = conn.execute(
                    "SELECT COUNT(*) FROM company_relationship_observations"
                ).fetchone()[0]
                exposure_observations = conn.execute(
                    "SELECT COUNT(*) FROM resource_exposure_observations"
                ).fetchone()[0]
                event_observations = conn.execute(
                    "SELECT COUNT(*) FROM regulatory_contract_event_observations"
                ).fetchone()[0]
            self.assertEqual(2, relationship_observations)
            self.assertEqual(2, exposure_observations)
            self.assertEqual(2, event_observations)

    def test_agents_skip_future_and_unsafe_sources(self) -> None:
        future = utc_now() + timedelta(days=1)
        config = SupplyChainConfig(
            enabled=True,
            sources=(
                SupplyChainSourceConfig(
                    ticker="TEST",
                    counterparty="Future Supplier",
                    relationship_type="SUPPLIER",
                    publisher="Future source",
                    published_at=future,
                    url="https://example.com/future",
                ),
                SupplyChainSourceConfig(
                    ticker="TEST",
                    counterparty="Private Supplier",
                    relationship_type="SUPPLIER",
                    publisher="Unsafe source",
                    published_at=utc_now() - timedelta(days=1),
                    url="https://127.0.0.1/internal",
                ),
            ),
        )
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(SupplyChainAgent(config))
        report = orchestrator.run(watchlist=["TEST"])

        execution = report.executions[-1]
        self.assertEqual("UNAVAILABLE", execution.status.value)
        self.assertEqual([], execution.result.company_relationships)
        self.assertEqual(2, execution.result.metadata["rejected_sources"])

    def test_source_content_is_hashed_and_support_controls_confidence(self) -> None:
        class FixtureClient:
            def __init__(self, text: str) -> None:
                self.text = text

            def fetch(self, source):
                return FetchedShortReport(
                    source=source,
                    final_url=source.url,
                    mime_type="text/html",
                    title="Fixture filing",
                    text=self.text,
                    content_hash="c" * 64,
                    size_bytes=len(self.text.encode("utf-8")),
                    extractor="fixture",
                )

        config = SupplyChainConfig(
            enabled=True,
            sources=(
                SupplyChainSourceConfig(
                    ticker="TEST",
                    counterparty="Example Components",
                    relationship_type="SUPPLIER",
                    publisher="Company filing",
                    published_at=utc_now() - timedelta(days=1),
                    url="https://example.com/filing",
                ),
            ),
            source_verification=Stage3SourceVerificationConfig(enabled=True),
        )

        def run(text: str):
            orchestrator = OrchestratorAgent(shadow_mode=True)
            orchestrator.register(EntityRegistryAgent())
            orchestrator.register(
                SupplyChainAgent(config, client=FixtureClient(text))
            )
            orchestrator.register(PredictionV21AdapterAgent())
            orchestrator.register(QualityGateAgent())
            return orchestrator.run(
                watchlist=["TEST"],
                state={"signals": _signals()},
            )

        supported = run("Example Components supplies critical parts.")
        document = next(
            item
            for item in supported.documents
            if item.source_type == "supply_chain_reference"
        )
        self.assertTrue(document.metadata["content_fetched"])
        self.assertTrue(document.metadata["source_content_support_detected"])
        self.assertEqual("c" * 64, document.content_hash)
        self.assertIsNone(document.raw_path)
        self.assertEqual(1.0, supported.company_relationships[0].confidence)
        self.assertEqual(GateDecision.PASS, supported.quality_checks[0].decision)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(supported)
            with store._connect() as conn:
                observation = conn.execute(
                    "SELECT content_hash, mime_type, metadata_json "
                    "FROM document_observations "
                    "WHERE document_id = ?",
                    (document.document_id,),
                ).fetchone()
        self.assertEqual("c" * 64, observation[0])
        self.assertEqual("text/html", observation[1])
        self.assertIn("source_content_support_detected", observation[2])

        unsupported = run("This document discusses an unrelated company.")
        self.assertEqual(0.45, unsupported.company_relationships[0].confidence)
        self.assertFalse(
            unsupported.company_relationships[0].metadata[
                "source_content_support_detected"
            ]
        )
        self.assertEqual(GateDecision.PASS, unsupported.quality_checks[0].decision)


class _FutureSupplyChainAgent(BaseAgent):
    name = "supply_chain"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        now = utc_now()
        future = context.started_at + timedelta(days=1)
        document = DocumentRecord(
            document_id="stage3:supply:future",
            ticker="TEST",
            source="Fixture",
            source_type="supply_chain_reference",
            observed_at=now,
            published_at=future,
            url="https://example.com/future-source",
        )
        relationship = CompanyRelationship(
            relationship_id="relationship:future",
            ticker="TEST",
            counterparty="Future Supplier",
            relationship_type=RelationshipType.SUPPLIER,
            observed_at=now,
            published_at=future,
            document_id=document.document_id,
            source_url=document.url or "",
            metadata={"scoring_applied": False},
        )
        evidence = AgentEvidence(
            evidence_id="evidence:future",
            ticker="TEST",
            agent_name=self.name,
            event_type="SUPPLY_CHAIN_RELATIONSHIP_RECORDED",
            observed_at=now,
            summary="Future fixture.",
            confidence=1.0,
            document_ids=[document.document_id],
            source_urls=[document.url or ""],
            metadata={
                "scoring_applied": False,
                "relationship_id": relationship.relationship_id,
            },
        )
        return AgentResult(
            documents=[document],
            company_relationships=[relationship],
            evidence=[evidence],
        )


class _ViolatingSupplyChainAgent(BaseAgent):
    name = "supply_chain"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        now = utc_now()
        document = DocumentRecord(
            document_id="stage3:supply:violation",
            ticker="TEST",
            source="Fixture",
            source_type="supply_chain_reference",
            observed_at=now,
            published_at=now - timedelta(days=1),
            url="https://example.com/fixture",
        )
        evidence = AgentEvidence(
            evidence_id="evidence:stage3-violation",
            ticker="TEST",
            agent_name=self.name,
            event_type="SUPPLY_CHAIN_RELATIONSHIP_RECORDED",
            observed_at=now,
            summary="Deliberate stage-3 scoring violation.",
            risk_score=25.0,
            confidence=1.0,
            document_ids=[document.document_id],
            source_urls=[document.url or ""],
            metadata={"scoring_applied": False},
        )
        signal = AgentSignal(
            signal_id="signal:stage3-violation",
            ticker="TEST",
            agent_name=self.name,
            agent_version=self.version,
            event_type="SUPPLY_CHAIN_SIGNAL",
            observed_at=now,
            action="NO_TRADE",
            forecast="FLAT",
            direction=0.0,
            risk_score=0.0,
            confidence=1.0,
            reasons=["deliberate_fixture"],
            evidence_ids=[evidence.evidence_id],
        )
        return AgentResult(documents=[document], evidence=[evidence], signals=[signal])


class StageThreeQualityGateTests(unittest.TestCase):
    def _run_with(self, agent: BaseAgent):
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(agent)
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())
        return orchestrator.run(
            watchlist=["TEST"],
            state={"signals": _signals()},
        )

    def test_quality_gate_rejects_future_point_in_time_record(self) -> None:
        report = self._run_with(_FutureSupplyChainAgent())
        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        reject_codes = {
            item["code"] for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("future_stage3_publication", reject_codes)

    def test_quality_gate_blocks_stage3_signal_and_risk_score(self) -> None:
        report = self._run_with(_ViolatingSupplyChainAgent())
        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        reject_codes = {
            item["code"] for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("stage3_agent_emitted_signal", reject_codes)
        self.assertIn("stage3_shadow_value_violation", reject_codes)


class StageThreeManifestTests(unittest.TestCase):
    def test_manifests_parse_valid_rows_and_reject_private_or_ambiguous_sources(self) -> None:
        supply, supply_errors = parse_supply_chain_sources(
            "TEST | Supplier | SUPPLIER | 25 | Filing | 2026-01-01 | https://example.com/filing\n"
            "TEST | Unsafe | SUPPLIER | - | Filing | 2026-01-01 | https://127.0.0.1/private"
        )
        resources, resource_errors = parse_commodity_energy_sources(
            "TEST | Natural gas | FUEL | 12,5 | Report | 2026-01-01 | https://example.com/report"
        )
        events, event_errors = parse_regulatory_contract_sources(
            "TEST | CONTRACT_AWARD | ANNOUNCED | Award | Agency | 1000 | - | Agency | 2026-01-01 | https://example.org/award"
        )

        self.assertEqual(1, len(supply))
        self.assertEqual(1, len(supply_errors))
        self.assertEqual(1, len(resources))
        self.assertEqual([], resource_errors)
        self.assertEqual(12.5, resources[0].dependency_pct)
        self.assertEqual(0, len(events))
        self.assertEqual(1, len(event_errors))


if __name__ == "__main__":
    unittest.main()
