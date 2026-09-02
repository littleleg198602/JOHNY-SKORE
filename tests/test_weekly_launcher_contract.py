from __future__ import annotations

from pathlib import Path
import unittest


class WeeklyLauncherContractTests(unittest.TestCase):
    def test_windows_weekly_launcher_uses_full_production_watchlist(self) -> None:
        launcher = (
            Path(__file__).resolve().parents[1]
            / "Spustit_Tydenni_Shadow.bat"
        )
        text = launcher.read_text(encoding="utf-8")

        self.assertIn("weekly_shadow_runner", text)
        self.assertIn("production_watchlist.txt", text)
        self.assertNotIn("--ticker-limit", text)
        self.assertNotIn("pro 36 tickeru", text)
