from __future__ import annotations

from pathlib import Path
import unittest

from market_checker_app.services.agent_runtime_service import AgentRuntimeService
from market_checker_app.weekly_shadow_runner import build_runtime_config


ROOT = Path(__file__).resolve().parents[1]


class ProductionShadowWorkflowTests(unittest.TestCase):
    def test_committed_runtime_is_shadow_only_and_enables_autonomous_sources(self) -> None:
        settings, warning = AgentRuntimeService(
            ROOT / "market_checker_app" / "autonomous_runtime.json"
        ).load()
        config = build_runtime_config(
            settings,
            output_dir=ROOT / "outputs",
            sqlite_path=ROOT / "outputs" / "workflow-contract-test.db",
            sec_user_agent="JohnySkoreTests tests@example.com",
        )

        self.assertIsNone(warning)
        self.assertTrue(config.agent_shadow_mode)
        self.assertFalse(config.decision_agent.live_application_enabled)
        self.assertFalse(config.evaluation_agent.enable_after_gate)
        self.assertTrue(config.fundamental_ingestion.enabled)
        self.assertTrue(config.financial_forensics.enabled)
        self.assertEqual(1, len(config.short_reports.sources))
        self.assertTrue(config.supply_chain.enabled)
        self.assertTrue(config.commodity_energy.enabled)

    def test_workflow_restores_history_and_runs_all_live_canaries(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "market-checker-live-smoke.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("actions/download-artifact@v4", workflow)
        self.assertIn("run-id: ${{ steps.previous-state.outputs.run_id }}", workflow)
        self.assertIn("market-checker-live-shadow-state", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("market_checker_app.live_source_smoke", workflow)
        self.assertIn("JOHNY_SKORE_SEC_USER_AGENT", workflow)
        self.assertIn("market_checker_app/autonomous_runtime.json", workflow)
        self.assertLess(
            workflow.index("Initialize or validate the persistent SQLite history"),
            workflow.index("Verify current Yahoo, RSS, SEC and short-report sources"),
        )
        self.assertIn("Verify canonical 687-ticker universe", workflow)
        preflight_start = workflow.index("Verify canonical 687-ticker universe")
        runner_start = workflow.index("Run the persistent weekly Stage 4 shadow")
        self.assertLess(preflight_start, runner_start)
        runner_block = workflow[runner_start:]
        self.assertNotIn("--tickers", runner_block.split(
            "Preserve rolling state and source/readiness audits",
            1,
        )[0])
        self.assertIn("--no-mt5", runner_block)
        self.assertIn("len(tickers)==687", workflow)


if __name__ == "__main__":
    unittest.main()
