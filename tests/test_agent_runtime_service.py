from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from market_checker_app.services.agent_runtime_service import (
    AgentRuntimeService,
    AgentRuntimeSettings,
)


class AgentRuntimeServiceTests(unittest.TestCase):
    def test_missing_file_uses_safe_stage4_shadow_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings, warning = AgentRuntimeService(
                Path(tmp) / "agent_runtime.json"
            ).load()

        self.assertIsNone(warning)
        self.assertTrue(settings.stage4_shadow_enabled)
        self.assertTrue(settings.auto_discover_short_reports)
        self.assertTrue(settings.auto_discover_regulatory_events)

    def test_settings_round_trip_is_atomic_and_contains_no_sec_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "agent_runtime.json"
            service = AgentRuntimeService(path)
            expected = AgentRuntimeSettings(
                stage4_shadow_enabled=True,
                short_reports_enabled=True,
                short_report_sources_text=(
                    "AAPL | Example | 2026-01-01 | https://example.com/report"
                ),
                supply_chain_enabled=True,
                supply_chain_sources_text=(
                    "AAPL | Supplier | SUPPLIER | 10 | Filing | 2026-01-01 | "
                    "https://example.com/filing"
                ),
            )

            service.save(expected)
            loaded, warning = service.load()
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertIsNone(warning)
        self.assertEqual(expected, loaded)
        self.assertEqual(1, payload["schema_version"])
        self.assertNotIn("user_agent", json.dumps(payload).lower())

    def test_corrupt_file_fails_closed_to_defaults_with_visible_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_runtime.json"
            path.write_text('{"schema_version": 999}', encoding="utf-8")

            settings, warning = AgentRuntimeService(path).load()

        self.assertTrue(settings.stage4_shadow_enabled)
        self.assertIsNotNone(warning)
        self.assertIn("nepodporovaná verze", warning or "")

    def test_unknown_setting_is_rejected_instead_of_silently_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_runtime.json"
            path.write_text(
                '{"schema_version": 1, "settings": {"stage4_shdow_enabled": true}}',
                encoding="utf-8",
            )

            settings, warning = AgentRuntimeService(path).load()

        self.assertTrue(settings.stage4_shadow_enabled)
        self.assertIn("neznámé položky", warning or "")


if __name__ == "__main__":
    unittest.main()
