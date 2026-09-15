from __future__ import annotations

from pathlib import Path
import unittest


class WeeklyLauncherContractTests(unittest.TestCase):
    def test_windows_weekly_launcher_uses_full_production_watchlist_and_label_queue(self) -> None:
        launcher = (
            Path(__file__).resolve().parents[1]
            / "Spustit_Tydenni_Shadow.bat"
        )
        text = launcher.read_text(encoding="utf-8")

        self.assertIn("prediction_label_runner", text)
        self.assertIn("--limit 1000", text)
        self.assertIn("--page-size 120", text)
        self.assertIn("weekly_shadow_runner", text)
        self.assertIn("production_watchlist.txt", text)
        self.assertNotIn("--ticker-limit", text)
        self.assertNotIn("pro 36 tickeru", text)

    def test_github_weekly_analysis_uses_full_production_watchlist_and_label_queue(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "market-checker-live-smoke.yml"
        )
        text = workflow.read_text(encoding="utf-8")
        analysis_marker = "Run the persistent weekly Stage 4 shadow"
        analysis = text[text.index(analysis_marker):]
        label_marker = "Resolve mature historical prediction labels"
        label_block = text[text.index(label_marker):text.index("Verify company identities")]

        self.assertIn("prediction_label_runner", label_block)
        self.assertIn("--limit 1000", label_block)
        self.assertIn("--page-size 120", label_block)
        self.assertIn("--time-budget-seconds 240", label_block)
        self.assertIn("production_watchlist.txt", analysis)
        self.assertNotIn("--ticker-limit", analysis)
        self.assertIn("--ticker-limit 3", text[:text.index(analysis_marker)])


if __name__ == "__main__":
    unittest.main()
