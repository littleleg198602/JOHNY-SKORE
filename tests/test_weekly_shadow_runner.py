from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
from market_checker_app.weekly_shadow_runner import (
    RuntimeConfigurationError,
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


if __name__ == "__main__":
    unittest.main()
