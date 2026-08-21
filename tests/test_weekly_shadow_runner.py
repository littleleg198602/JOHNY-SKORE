from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
from market_checker_app.weekly_shadow_runner import (
    RuntimeConfigurationError,
    _readiness_summary,
    build_runtime_config,
)


class WeeklyShadowRunnerTests(unittest.TestCase):
    def _config(self, settings: AgentRuntimeSettings):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        return build_runtime_config(
            settings,
            output_dir=root,
            sqlite_path=root / "history.db",
            sec_user_agent="",
        )

    def test_default_runner_is_persistent_shadow_and_cannot_enable_live(self) -> None:
        config = self._config(AgentRuntimeSettings())

        self.assertTrue(config.save_history)
        self.assertTrue(config.agent_shadow_mode)
        self.assertTrue(config.decision_agent.enabled)
        self.assertTrue(config.evaluation_agent.enabled)
        self.assertFalse(config.decision_agent.live_application_enabled)
        self.assertFalse(config.evaluation_agent.enable_after_gate)
        self.assertEqual((), config.decision_agent.live_policy_allowlist)

    def test_enabled_manual_agent_without_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeConfigurationError, "SupplyChainAgent"):
            self._config(AgentRuntimeSettings(supply_chain_enabled=True))

    def test_invalid_manifest_is_rejected_before_network_run(self) -> None:
        with self.assertRaisesRegex(RuntimeConfigurationError, "Short report řádek"):
            self._config(
                AgentRuntimeSettings(
                    short_reports_enabled=True,
                    short_report_sources_text="AAPL | broken",
                )
            )

    def test_sec_ingest_requires_runtime_contact_identity(self) -> None:
        with self.assertRaisesRegex(
            RuntimeConfigurationError,
            "JOHNY_SKORE_SEC_USER_AGENT",
        ):
            self._config(AgentRuntimeSettings(sec_fundamentals_enabled=True))

    def test_sec_enables_automatic_network_agents_without_manual_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_runtime_config(
                AgentRuntimeSettings(sec_fundamentals_enabled=True),
                output_dir=root,
                sqlite_path=root / "history.db",
                sec_user_agent="JohnySkore test@example.com",
            )

        self.assertTrue(config.supply_chain.enabled)
        self.assertTrue(config.supply_chain.auto_discover_from_sec_filings)
        self.assertEqual((), config.supply_chain.sources)
        self.assertTrue(config.commodity_energy.enabled)
        self.assertTrue(config.commodity_energy.auto_discover_from_sec_filings)
        self.assertEqual((), config.commodity_energy.sources)

    def test_readiness_fails_closed_until_all_real_evidence_exists(self) -> None:
        config = self._config(AgentRuntimeSettings())
        result = {
            "activation_state": "INSUFFICIENT_DATA",
            "evaluation_sample_count": 31,
            "evaluation_distinct_weeks": 2,
            "evaluation_gate_passed": False,
            "evaluation_consecutive_passes": 0,
            "quality_gate_decision": "PASS",
            "decision_applied_count": 0,
            "live_application_authorized": False,
        }

        readiness = _readiness_summary(result, config)

        self.assertFalse(readiness["accuracy_improvement_proven"])
        self.assertFalse(readiness["live_buy_sell_ready"])
        self.assertFalse(readiness["live_buy_sell_enabled"])
        self.assertIn("minimum_oos_samples:31/200", readiness["blockers"])
        self.assertIn("minimum_distinct_weeks:2/12", readiness["blockers"])

    def test_eligible_policy_is_ready_but_still_not_enabled_in_shadow(self) -> None:
        config = self._config(AgentRuntimeSettings())
        result = {
            "activation_state": "ELIGIBLE",
            "evaluation_sample_count": 250,
            "evaluation_distinct_weeks": 14,
            "evaluation_gate_passed": True,
            "evaluation_consecutive_passes": 3,
            "evaluation_required_consecutive_passes": 3,
            "quality_gate_decision": "PASS",
            "decision_applied_count": 0,
            "live_application_authorized": False,
        }

        readiness = _readiness_summary(result, config)

        self.assertTrue(readiness["accuracy_improvement_proven"])
        self.assertTrue(readiness["live_buy_sell_ready"])
        self.assertFalse(readiness["live_buy_sell_enabled"])
        self.assertIn("shadow_mode_active", readiness["blockers"])


if __name__ == "__main__":
    unittest.main()
