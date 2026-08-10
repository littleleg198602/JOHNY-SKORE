from __future__ import annotations

import unittest

from market_checker_app.analysis.scoring import _build_decision_modules


class MissingYahooScoringTests(unittest.TestCase):
    def test_zero_confidence_yahoo_has_no_directional_contribution(self) -> None:
        modules = _build_decision_modules(
            news_score=60,
            tech_score=60,
            analyst_score=50,
            panic_score=40,
            news_confidence=70,
            tech_confidence=80,
            analyst_confidence=0,
            panic_confidence=60,
            context="test",
        )

        analyst = next(module for module in modules if module.module == "analysts")
        self.assertEqual(50.0, analyst.bull_contribution)
        self.assertEqual(50.0, analyst.bear_contribution)
        self.assertEqual("neutral", analyst.direction)
        self.assertEqual(0.0, analyst.confidence)


if __name__ == "__main__":
    unittest.main()
