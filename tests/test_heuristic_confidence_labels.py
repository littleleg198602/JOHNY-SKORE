from __future__ import annotations

from pathlib import Path
import unittest


class HeuristicConfidenceLabelTests(unittest.TestCase):
    def test_dashboard_does_not_present_confidence_as_calibrated_probability(self) -> None:
        app = (Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py").read_text(encoding="utf-8")

        self.assertIn("interní heuristická míra kvality", app)
        self.assertIn("heuristická confidence %", app)
        self.assertNotIn("Rozhodnutí posledního 36tickerového pilotu", app)


if __name__ == "__main__":
    unittest.main()
