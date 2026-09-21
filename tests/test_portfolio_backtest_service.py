from __future__ import annotations

import unittest

from market_checker_app.services.portfolio_backtest_service import (
    build_cost_aware_portfolio_backtest,
)


class PortfolioBacktestServiceTests(unittest.TestCase):
    def test_cost_stress_reduces_net_performance_and_reports_risk(self) -> None:
        samples = []
        for week in range(1, 9):
            for index in range(10):
                samples.append(
                    {
                        "week": f"2026-W{week:02d}",
                        "ticker": f"T{index}",
                        "target_value": 0.02 if index < 2 else -0.005,
                        "momentum_probability_up": 0.55 - index * 0.01,
                        "candidate_probability_up": 0.90 - index * 0.05,
                    }
                )

        report = build_cost_aware_portfolio_backtest(
            samples,
            top_fraction=0.2,
            max_positions=5,
            round_trip_cost_bps=20,
        )

        low = report["scenarios"]["0.5x"]["candidate"]
        high = report["scenarios"]["2.0x"]["candidate"]
        self.assertEqual("EVALUATED", report["status"])
        self.assertGreater(
            low["cumulative_net_excess_return"],
            high["cumulative_net_excess_return"],
        )
        self.assertEqual(8, low["active_week_count"])
        self.assertIn("maximum_drawdown", low)
        self.assertIn("sharpe", low)
        self.assertIn("turnover", low["weekly"][0])

    def test_probability_gate_can_leave_week_uninvested(self) -> None:
        report = build_cost_aware_portfolio_backtest(
            [
                {
                    "week": "2026-W01",
                    "ticker": "AAPL",
                    "target_value": 0.03,
                    "momentum_probability_up": 0.4,
                    "candidate_probability_up": 0.45,
                }
            ]
        )
        candidate = report["scenarios"]["1.0x"]["candidate"]
        self.assertEqual(0, candidate["active_week_count"])
        self.assertEqual(0.0, candidate["cumulative_net_excess_return"])


if __name__ == "__main__":
    unittest.main()
