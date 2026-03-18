import unittest

from market_checker_app.analysis.scoring import validate_decision_scenarios
from market_checker_app.config import DecisionModuleWeights, DecisionThresholds


class DecisionEngineScenarioTests(unittest.TestCase):
    def test_scenarios_expected_bands(self):
        rows = validate_decision_scenarios(DecisionModuleWeights(), DecisionThresholds())
        self.assertEqual(len(rows), 6)
        failed = [row for row in rows if not row["pass"]]
        self.assertEqual([], failed, f"Scenario mismatches: {failed}")

    def test_strong_buy_not_allowed_in_extreme_panic_scenario(self):
        rows = validate_decision_scenarios(DecisionModuleWeights(), DecisionThresholds())
        scenario_f = [row for row in rows if row["scenario_name"] == "F bullish but extreme panic"][0]
        self.assertNotEqual("STRONG BUY", scenario_f["final_signal"])


if __name__ == "__main__":
    unittest.main()
