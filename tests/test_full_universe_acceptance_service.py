from __future__ import annotations

import unittest

from market_checker_app.services.full_universe_acceptance_service import (
    FullUniverseAcceptanceError,
    validate_full_universe_report,
)


class FullUniverseAcceptanceServiceTests(unittest.TestCase):
    def _summary(self) -> dict[str, object]:
        return {
            "requested_tickers": ["AAPL", "MSFT", "NVDA"],
            "ticker_results": [
                {"ticker": "AAPL", "decision_signal": "HOLD"},
                {"ticker": "MSFT", "decision_signal": "INSUFFICIENT_DATA"},
            ],
            "universe_coverage": {
                "requested": 3,
                "reported": 2,
                "missing": 1,
                "missing_tickers": ["NVDA"],
            },
            "pipeline_status": "PARTIAL",
            "quality_gate_decision": "REJECT",
        }

    def test_accepts_explicit_missing_ticker_without_claiming_pipeline_success(self) -> None:
        report = validate_full_universe_report(
            self._summary(),
            expected_tickers=["AAPL", "MSFT", "NVDA"],
        )

        self.assertEqual("ACCEPTED", report["status"])
        self.assertTrue(report["analysis_only"])
        self.assertEqual(1, report["missing_tickers"])
        self.assertEqual("PARTIAL", report["pipeline_status"])
        self.assertEqual("REJECT", report["quality_gate_decision"])

    def test_rejects_report_with_different_requested_universe(self) -> None:
        with self.assertRaisesRegex(FullUniverseAcceptanceError, "requested_tickers"):
            validate_full_universe_report(
                self._summary(),
                expected_tickers=["AAPL", "MSFT", "GOOG"],
            )

    def test_rejects_when_coverage_hides_a_missing_ticker(self) -> None:
        broken = self._summary()
        broken["universe_coverage"] = {
            "requested": 3,
            "reported": 3,
            "missing": 0,
            "missing_tickers": [],
        }
        with self.assertRaisesRegex(FullUniverseAcceptanceError, "reported"):
            validate_full_universe_report(
                broken,
                expected_tickers=["AAPL", "MSFT", "NVDA"],
            )


if __name__ == "__main__":
    unittest.main()
