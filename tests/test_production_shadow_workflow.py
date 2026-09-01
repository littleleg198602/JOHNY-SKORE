from __future__ import annotations

from pathlib import Path
import unittest

from market_checker_app.services.agent_runtime_service import AgentRuntimeService
from market_checker_app.services.company_intelligence_manifest_service import (
    parse_identity_records,
)
from market_checker_app.services.watchlist_service import (
    load_watchlist,
    select_watchlist_pilot,
)
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
        self.assertFalse(hasattr(config.decision_agent, "live_application_enabled"))
        self.assertFalse(hasattr(config.evaluation_agent, "enable_after_gate"))
        self.assertTrue(config.fundamental_ingestion.enabled)
        self.assertTrue(config.financial_forensics.enabled)
        identities, identity_errors = parse_identity_records(
            settings.identity_records_text
        )
        self.assertEqual([], identity_errors)
        self.assertEqual(36, len(identities))
        self.assertEqual(36, len(config.entity_registry.identity_records))
        self.assertEqual(2, len(config.short_reports.sources))
        self.assertEqual({"MSCI", "GL"}, {source.ticker for source in config.short_reports.sources})
        self.assertEqual("MSCI", config.short_reports.sources[0].ticker)
        self.assertTrue(config.supply_chain.enabled)
        self.assertTrue(config.commodity_energy.enabled)

        universe = load_watchlist(
            ROOT / "market_checker_app" / "production_watchlist.txt"
        )
        self.assertEqual(687, len(universe))
        self.assertEqual(687, len(set(universe)))
        self.assertEqual(
            ["NVDA", "AAPL", "GOOGL", "GOOG", "MSFT"],
            universe[:5],
        )
        self.assertTrue(set(identities).issubset(universe))
        self.assertEqual(set(universe[:36]), set(identities))
        # Source-only manifests must not replace primary pilot tickers.
        pilot = select_watchlist_pilot(universe, 36)
        self.assertEqual(36, len(pilot))
        self.assertEqual(set(universe[:36]), set(pilot))
        self.assertIn("PANW", pilot)
        self.assertIn("RTX", pilot)
        self.assertNotIn("MSCI", pilot)
        self.assertNotIn("GL", pilot)
        self.assertTrue(set(pilot).issubset(universe))

    def test_windows_shadow_launcher_persists_sec_user_agent(self) -> None:
        launcher = (ROOT / "Spustit_Tydenni_Shadow.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn("call :ensure_sec_user_agent", launcher)
        self.assertIn("setx JOHNY_SKORE_SEC_USER_AGENT", launcher)
        self.assertIn('set /p "SEC_EMAIL=', launcher)
        self.assertNotIn("set /p JOHNY_SKORE_SEC_USER_AGENT", launcher)

    def test_streamlit_renders_latest_shadow_artifact_on_startup(self) -> None:
        app = (ROOT / "market_checker_app" / "app.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("def _load_latest_shadow_result(", app)
        self.assertIn('output_dir / "weekly_shadow_latest.json"', app)
        self.assertIn("_render_latest_shadow_result(output_dir)", app)
        self.assertIn("ticker_results", app)
        self.assertIn("Toto zobrazení nespouští novou analýzu.", app)

    def test_workflow_restores_history_and_runs_all_live_canaries(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "market-checker-live-smoke.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("actions/download-artifact@v4", workflow)
        self.assertIn("run-id: ${{ steps.previous-state.outputs.run_id }}", workflow)
        self.assertIn("market-checker-live-shadow-state", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("market_checker_app.live_source_smoke", workflow)
        self.assertIn(
            "--runtime-config market_checker_app/autonomous_runtime.json",
            workflow,
        )
        self.assertIn("--minimum-identity-records 36", workflow)
        self.assertEqual(
            2,
            workflow.count(
                "--ticker-file market_checker_app/production_watchlist.txt"
            ),
        )
        self.assertIn("--ticker-limit 3", workflow)
        self.assertIn("--ticker-limit 36", workflow)
        self.assertNotIn("--tickers AAPL", workflow)
        self.assertNotIn("JOHNY_SKORE_SMOKE_SHORT_REPORT_URL", workflow)
        self.assertNotIn("\n  push:\n", workflow)
        self.assertNotIn("agent/entity-101-live-pilot", workflow)
        self.assertIn("JOHNY_SKORE_SEC_USER_AGENT", workflow)
        self.assertIn("market_checker_app/autonomous_runtime.json", workflow)
        self.assertLess(
            workflow.index("Initialize or validate the persistent SQLite history"),
            workflow.index("Verify company identities and current live sources"),
        )


if __name__ == "__main__":
    unittest.main()
