import json
import unittest

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover - test environment dependency guard
    pd = None

if pd is not None:
    from market_checker_app.services.visualization_service import VisualizationService
else:  # pragma: no cover
    VisualizationService = None


@unittest.skipIf(pd is None or VisualizationService is None, "pandas není v testovacím prostředí dostupný")
class VisualizationHoldCalibrationTests(unittest.TestCase):
    def test_prepare_hold_calibration_outputs_expected_sections(self):
        df = pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "signal": "HOLD",
                    "bull_score": 52,
                    "bear_score": 48,
                    "bull_bear_spread": 4,
                    "final_confidence": 74,
                    "blocked_reasons": json.dumps(["bull_bear_balance_hold_band"]),
                    "module_breakdown": json.dumps(
                        [
                            {"module": "news", "direction": "bullish", "bull_contribution": 70, "bear_contribution": 30},
                            {"module": "technical", "direction": "bearish", "bull_contribution": 25, "bear_contribution": 72},
                            {"module": "panic", "direction": "bearish", "bull_contribution": 20, "bear_contribution": 80},
                            {"module": "analysts", "direction": "neutral", "bull_contribution": 51, "bear_contribution": 49},
                        ]
                    ),
                    "news_score": 65,
                    "tech_score": 35,
                    "risk_score": 78,
                    "yahoo_score": 52,
                    "news_confidence": 72,
                    "tech_confidence": 79,
                    "yahoo_confidence": 61,
                    "behavioral_confidence": 76,
                },
                {
                    "ticker": "BBB",
                    "signal": "BUY",
                    "bull_score": 70,
                    "bear_score": 30,
                    "bull_bear_spread": 40,
                    "final_confidence": 80,
                    "blocked_reasons": json.dumps([]),
                    "module_breakdown": json.dumps(
                        [
                            {"module": "news", "direction": "bullish", "bull_contribution": 78, "bear_contribution": 20},
                            {"module": "technical", "direction": "bullish", "bull_contribution": 80, "bear_contribution": 15},
                        ]
                    ),
                    "news_score": 72,
                    "tech_score": 77,
                    "risk_score": 45,
                    "yahoo_score": 60,
                    "news_confidence": 76,
                    "tech_confidence": 82,
                    "yahoo_confidence": 67,
                    "behavioral_confidence": 64,
                },
            ]
        )

        result = VisualizationService.prepare_hold_calibration(df)
        self.assertIn("hold_diagnostics", result)
        self.assertIn("hold_concentration", result)
        self.assertIn("sensitivity_distribution", result)
        self.assertIn("confidence_sanity", result)
        self.assertIn("technical_driver_effectiveness", result)
        self.assertFalse(result["hold_diagnostics"].empty)
        self.assertFalse(result["hold_concentration"].empty)
        self.assertFalse(result["sensitivity_distribution"].empty)
        self.assertGreaterEqual(result["confidence_sanity"]["hold_count"], 1)


if __name__ == "__main__":
    unittest.main()
