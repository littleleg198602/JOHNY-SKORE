from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentContext,
    AgentResult,
    DecisionAgent,
    DocumentRecord,
    DocumentSourceResolution,
    EntityRegistryAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
    RegulatoryContractAgent,
    SourceResolutionAgent,
)
from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.source_policy import canonical_event_key_for
from market_checker_app.collectors.european_filing_feed_client import (
    EuropeanFilingFeedClient,
)
from market_checker_app.config import (
    DecisionAgentConfig,
    EuropeanFilingConfig,
    EuropeanFilingFeedConfig,
    RegulatoryContractConfig,
    RegulatoryContractSourceConfig,
)
from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
from market_checker_app.services.stage3_manifest_service import (
    parse_regulatory_contract_sources,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.entity_identifiers import normalize_isin, normalize_lei
from market_checker_app.weekly_shadow_runner import (
    RuntimeConfigurationError,
    build_runtime_config,
)


def _lei(body: str) -> str:
    expanded = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in f"{body}00"
    )
    result = f"{body}{98 - int(expanded) % 97:02d}"
    assert normalize_lei(result) == result
    return result


def _isin(sequence: int) -> str:
    base = f"NL{sequence:09d}"
    for digit in range(10):
        candidate = f"{base}{digit}"
        try:
            if normalize_isin(candidate) == candidate:
                return candidate
        except ValueError:
            continue
    raise AssertionError(sequence)


TEST_LEI = _lei("529900RUNTIME00001")
TEST_ISIN = _isin(901)
REPORTING_PERIOD_END = datetime(2025, 12, 31, tzinfo=timezone.utc)


def _identity() -> dict[str, object]:
    return {
        "entity_id": "listing:test",
        "ticker": "TEST",
        "name": "Runtime Test N.V.",
        "lei": TEST_LEI,
        "isin": TEST_ISIN,
        "mic": "XAMS",
        "country_code": "NL",
        "source": "trusted_runtime_manifest",
        "source_url": "https://example.com/identity/test",
        "confidence": 1.0,
    }


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "TEST",
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.80,
                "risk_score": 10.0,
                "action_reasons": '["confirmed"]',
            }
        ]
    )


class RuntimeManifestTests(unittest.TestCase):
    def _settings(self, *, include_identity: bool = True) -> AgentRuntimeSettings:
        identity = (
            "TEST | Runtime Test N.V. | - | "
            f"{TEST_ISIN} | {TEST_LEI} | XAMS | NL | Euronext | "
            "Trusted registry | https://example.com/identity/test"
            if include_identity
            else ""
        )
        filing = (
            "TEST | EURONEXT | annual_report | FY 2025 report | "
            "2026-08-20T10:00:00+00:00 | 2025-12-31 | "
            f"{TEST_LEI} | {TEST_ISIN} | true | true | en | "
            "TEST:FY2025 | https://live.euronext.com/test-fy2025.xhtml"
        )
        return AgentRuntimeSettings(
            identity_records_text=identity,
            european_filings_enabled=True,
            european_filing_sources_text=filing,
        )

    def test_weekly_runtime_wires_identity_and_european_filings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_runtime_config(
                self._settings(),
                output_dir=root,
                sqlite_path=root / "history.db",
                sec_user_agent="",
            )

        self.assertIn("TEST", config.entity_registry.identity_records)
        self.assertTrue(config.european_filings.enabled)
        self.assertEqual(1, len(config.european_filings.sources))
        self.assertEqual(TEST_LEI, config.european_filings.sources[0].lei)

    def test_identity_dependent_runtime_fails_before_network_without_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(
                RuntimeConfigurationError,
                "Identity manifest.*TEST",
            ):
                build_runtime_config(
                    self._settings(include_identity=False),
                    output_dir=root,
                    sqlite_path=root / "history.db",
                    sec_user_agent="",
                )

    def test_quality_gate_rejects_unresolved_identity_when_component_requires_it(self) -> None:
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(
            watchlist=["TEST"],
            state={
                "signals": _signals(),
                "identity_required_tickers": ["TEST"],
            },
        )

        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        codes = {
            issue["code"]
            for issue in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("unresolved_identity", codes)


class EuropeanFeedDiscoveryTests(unittest.TestCase):
    def test_feed_accepts_only_entry_with_exact_lei_or_isin(self) -> None:
        payload = f"""<?xml version='1.0' encoding='utf-8'?>
        <rss version='2.0'><channel>
          <item><title>Runtime Test {TEST_ISIN} annual report</title>
            <description>Issuer LEI {TEST_LEI}</description>
            <pubDate>Thu, 20 Aug 2026 10:00:00 GMT</pubDate>
            <link>https://live.euronext.com/test-fy2025.xhtml</link></item>
          <item><title>Unrelated issuer report</title>
            <pubDate>Thu, 20 Aug 2026 09:00:00 GMT</pubDate>
            <link>https://live.euronext.com/unrelated.xhtml</link></item>
        </channel></rss>""".encode("utf-8")

        def transport(url, _headers, _timeout, _limit):
            return payload, "application/rss+xml", url

        config = EuropeanFilingConfig(enabled=True, fetch_content=False)
        client = EuropeanFilingFeedClient(config, transport=transport)
        feed = EuropeanFilingFeedConfig(
            ticker="TEST",
            authority="EURONEXT",
            document_type="annual_report",
            feed_url="https://live.euronext.com/test-feed.xml",
            lei=TEST_LEI,
            isin=TEST_ISIN,
            audited=True,
            esef=True,
        )

        discovered = client.discover(
            feed,
            as_of=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )

        self.assertEqual(1, len(discovered))
        self.assertEqual("official_feed", discovered[0].discovery_method)
        self.assertEqual(TEST_ISIN, discovered[0].isin)


class _MediaDocumentAgent(BaseAgent):
    name = "media_document"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        entity = context.state["entities_by_ticker"]["TEST"]
        return AgentResult(
            documents=[
                DocumentRecord(
                    document_id="media-document",
                    ticker="TEST",
                    source="Example News",
                    source_type="media_article",
                    observed_at=context.started_at,
                    published_at=context.started_at - timedelta(hours=1),
                    url="https://news.example.com/test-fy2025",
                    legal_entity_id=entity.legal_entity_id,
                    issuer_id=entity.issuer_id,
                    instrument_id=entity.instrument_id,
                    reporting_period_end=REPORTING_PERIOD_END,
                    metadata={"document_type": "annual_report"},
                )
            ]
        )


class _RegulatoryDocumentAgent(BaseAgent):
    name = "regulatory_document"
    dependencies = ("entity_registry",)

    def run(self, context: AgentContext) -> AgentResult:
        entity = context.state["entities_by_ticker"]["TEST"]
        return AgentResult(
            documents=[
                DocumentRecord(
                    document_id="regulatory-document",
                    ticker="TEST",
                    source="AFM",
                    source_type="regulatory_filing",
                    observed_at=context.started_at,
                    published_at=context.started_at - timedelta(hours=2),
                    url="https://www.afm.nl/test-fy2025",
                    legal_entity_id=entity.legal_entity_id,
                    issuer_id=entity.issuer_id,
                    instrument_id=entity.instrument_id,
                    reporting_period_end=REPORTING_PERIOD_END,
                    metadata={"document_type": "annual_report"},
                )
            ]
        )


class _ForgedSourceResolutionAgent(BaseAgent):
    name = "source_resolution"
    dependencies = (
        "entity_registry",
        "media_document",
        "regulatory_document",
    )

    def run(self, context: AgentContext) -> AgentResult:
        results = context.state["agent_results"]
        media = results["media_document"].documents[0]
        regulator = results["regulatory_document"].documents[0]
        canonical_key = canonical_event_key_for(media)
        assert canonical_key == canonical_event_key_for(regulator)
        return AgentResult(
            document_source_resolutions=[
                DocumentSourceResolution(
                    resolution_id="forged-resolution",
                    canonical_event_key=canonical_key or "",
                    ticker="TEST",
                    legal_entity_id=media.legal_entity_id,
                    preferred_document_id=media.document_id,
                    retained_document_ids=(media.document_id, regulator.document_id),
                    observed_at=context.started_at,
                )
            ]
        )


class GlobalSourceResolutionTests(unittest.TestCase):
    def _run(self, *, with_regulator: bool):
        orchestrator = OrchestratorAgent()
        orchestrator.register(EntityRegistryAgent({"TEST": _identity()}))
        orchestrator.register(_MediaDocumentAgent())
        dependencies = ["entity_registry", "media_document"]
        if with_regulator:
            orchestrator.register(_RegulatoryDocumentAgent())
            dependencies.append("regulatory_document")
        orchestrator.register(
            SourceResolutionAgent(dependencies=tuple(dependencies))
        )
        return orchestrator.run(watchlist=["TEST"])

    def test_global_preference_and_preference_change_are_persisted(self) -> None:
        first = self._run(with_regulator=False)
        second = self._run(with_regulator=True)

        self.assertEqual(
            "media-document",
            first.document_source_resolutions[0].preferred_document_id,
        )
        self.assertEqual(
            "regulatory-document",
            second.document_source_resolutions[0].preferred_document_id,
        )
        self.assertIn(
            "|ANNUAL_REPORT|2025-12-31",
            second.document_source_resolutions[0].canonical_event_key,
        )
        self.assertEqual(
            {"media-document", "regulatory-document"},
            set(second.document_source_resolutions[0].retained_document_ids),
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "source-resolution.db")
            store.save_orchestration_report(first)
            store.save_orchestration_report(second)
            current = store.read_document_source_resolutions("TEST")
            history = store.read_document_source_resolution_observations(
                first.document_source_resolutions[0].resolution_id
            )

        self.assertEqual("regulatory-document", current.iloc[0]["preferred_document_id"])
        self.assertEqual(2, len(history))
        self.assertEqual("media-document", history.iloc[-1]["previous_preferred_document_id"])

    def test_quality_gate_rejects_forged_global_source_preference(self) -> None:
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent({"TEST": _identity()}))
        orchestrator.register(_MediaDocumentAgent())
        orchestrator.register(_RegulatoryDocumentAgent())
        orchestrator.register(_ForgedSourceResolutionAgent())
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(
            watchlist=["TEST"],
            state={"signals": _signals()},
        )

        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        codes = {
            issue["code"]
            for issue in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("source_resolution_wrong_preference", codes)


class RegulatoryDecisionIntegrationTests(unittest.TestCase):
    def test_legacy_regulatory_manifest_stays_media_only(self) -> None:
        sources, errors = parse_regulatory_contract_sources(
            "TEST | INVESTIGATION | ACTIVE | Investigation | AFM | - | - | "
            "News | 2026-08-20T10:00:00+00:00 | "
            "https://news.example.com/investigation"
        )

        self.assertEqual([], errors)
        self.assertEqual("media_article", sources[0].source_type)

    def test_explicit_primary_regulatory_manifest_keeps_authority_and_key(self) -> None:
        sources, errors = parse_regulatory_contract_sources(
            "TEST | INVESTIGATION | ACTIVE | Investigation | AFM | - | - | "
            "AFM | 2026-08-20T10:00:00+00:00 | "
            "https://www.afm.nl/investigation | regulatory_filing | AFM | "
            "TEST:AFM:INVESTIGATION:2026"
        )

        self.assertEqual([], errors)
        self.assertEqual("regulatory_filing", sources[0].source_type)
        self.assertEqual("AFM", sources[0].source_authority)
        self.assertEqual(
            "TEST:AFM:INVESTIGATION:2026",
            sources[0].canonical_event_key,
        )

    def test_official_regulatory_event_reaches_conservative_decision_overlay(self) -> None:
        now = datetime.now(timezone.utc)
        source = RegulatoryContractSourceConfig(
            ticker="TEST",
            event_type="INVESTIGATION",
            status="ACTIVE",
            title="Regulator opens formal investigation",
            authority_or_counterparty="AFM",
            publisher="AFM",
            published_at=now - timedelta(days=1),
            url="https://www.afm.nl/formal-investigation-test",
            confidence=1.0,
            source_type="regulatory_filing",
            source_authority="AFM",
            canonical_event_key="TEST:AFM:INVESTIGATION:2026",
        )
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(EntityRegistryAgent({"TEST": _identity()}))
        orchestrator.register(
            RegulatoryContractAgent(
                RegulatoryContractConfig(enabled=True, sources=(source,))
            )
        )
        orchestrator.register(
            SourceResolutionAgent(
                dependencies=("entity_registry", "regulatory_contract")
            )
        )
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(
            DecisionAgent(
                DecisionAgentConfig(suppression_score_threshold=1.0),
                dependencies=("prediction_v21_adapter", "source_resolution"),
            )
        )
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(
            watchlist=["TEST"],
            state={
                "signals": _signals(),
                "identity_required_tickers": ["TEST"],
            },
        )

        regulatory_document = next(
            item
            for item in report.documents
            if item.metadata.get("stage_record_type")
            == "regulatory_contract_event"
        )
        decision = report.decisions[0]
        self.assertEqual("regulatory_filing", regulatory_document.source_type)
        self.assertEqual(600, regulatory_document.source_priority)
        self.assertEqual(f"lei:{TEST_LEI}", regulatory_document.legal_entity_id)
        self.assertEqual("NO_TRADE", decision.proposed_action)
        self.assertIn("serious_regulatory_events", decision.metadata["risk_components"])
        self.assertEqual(GateDecision.PASS, report.quality_checks[0].decision)
        self.assertFalse(decision.applied_to_prediction)


if __name__ == "__main__":
    unittest.main()
