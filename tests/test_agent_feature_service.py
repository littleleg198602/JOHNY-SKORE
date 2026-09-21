from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from market_checker_app.agents.contracts import (
    AgentEvidence,
    ClaimStatus,
    DocumentRecord,
    RegulatoryContractEvent,
    RegulatoryContractEventType,
    RegulatoryEventStatus,
    ResearchClaim,
)
from market_checker_app.services.agent_feature_service import (
    AGENT_FEATURE_VERSION,
    build_agent_feature_snapshots,
)


class AgentFeatureServiceTests(unittest.TestCase):
    def test_verified_agent_evidence_becomes_point_in_time_shadow_features(self) -> None:
        now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        document = DocumentRecord(
            document_id="doc-1",
            ticker="AAPL",
            source="Example IR",
            source_type="media_article",
            observed_at=now,
            published_at=now - timedelta(days=2),
            url="https://example.com/aapl",
            metadata={"source_content_support_detected": True},
        )
        event = RegulatoryContractEvent(
            event_id="event-1",
            ticker="AAPL",
            event_type=RegulatoryContractEventType.GUIDANCE_RAISE,
            status=RegulatoryEventStatus.ANNOUNCED,
            title="AAPL raises guidance",
            authority_or_counterparty="Example IR",
            observed_at=now,
            published_at=now - timedelta(days=2),
            document_id=document.document_id,
            source_url=document.url or "",
            confidence=0.8,
            source_agent_name="regulatory_contract",
        )
        evidence = AgentEvidence(
            evidence_id="evidence-1",
            ticker="AAPL",
            agent_name="financial_forensics",
            event_type="FORENSIC_SCREEN",
            observed_at=now,
            summary="risk",
            risk_score=42.0,
            confidence=0.9,
        )
        claim = ResearchClaim(
            claim_id="claim-1",
            ticker="AAPL",
            report_document_id="report-1",
            claim_type="CASH_FLOW",
            statement="test",
            status=ClaimStatus.CORROBORATED,
            observed_at=now,
            published_at=now - timedelta(days=3),
            confidence=0.8,
        )
        report = SimpleNamespace(
            orchestration_id="orch-1",
            documents=[document],
            regulatory_contract_events=[event],
            governance_events=[],
            evidence=[evidence],
            claims=[claim],
        )

        snapshots = build_agent_feature_snapshots(
            report, tickers=["AAPL", "MSFT"], as_of=now
        )
        aapl = snapshots["AAPL"]
        self.assertEqual(AGENT_FEATURE_VERSION, aapl["version"])
        self.assertEqual(1, aapl["regulatory"]["verified_event_count"])
        self.assertGreater(aapl["regulatory"]["signed_event_score"], 0.0)
        self.assertEqual(42.0, aapl["forensics"]["risk_score"])
        self.assertEqual(1, aapl["claims"]["corroborated_count"])
        self.assertTrue(snapshots["MSFT"]["missingness"]["regulatory_events"])

    def test_future_event_is_rejected_from_features(self) -> None:
        now = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        event = RegulatoryContractEvent(
            event_id="future",
            ticker="AAPL",
            event_type=RegulatoryContractEventType.SANCTION,
            status=RegulatoryEventStatus.ANNOUNCED,
            title="future",
            authority_or_counterparty="authority",
            observed_at=now,
            published_at=now + timedelta(days=1),
            document_id="doc",
            source_url="https://example.com/future",
            confidence=1.0,
            source_agent_name="regulatory_contract",
        )
        report = SimpleNamespace(
            orchestration_id="orch-1",
            documents=[],
            regulatory_contract_events=[event],
            governance_events=[],
            evidence=[],
            claims=[],
        )

        snapshot = build_agent_feature_snapshots(
            report, tickers=["AAPL"], as_of=now
        )["AAPL"]
        self.assertEqual(0, snapshot["regulatory"]["event_count"])
        self.assertEqual(1, snapshot["provenance"]["future_records_rejected"])


if __name__ == "__main__":
    unittest.main()
