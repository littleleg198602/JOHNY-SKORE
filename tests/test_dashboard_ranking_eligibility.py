from __future__ import annotations

import unittest

import pandas as pd

from market_checker_app.exporters.dashboard_builder import build_dashboard_tables
from market_checker_app.exporters.excel_exporter import ExcelExporter
from market_checker_app.services.visualization_service import VisualizationService


class DashboardRankingEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signals = pd.DataFrame(
            [
                {
                    "ticker": "UNRANKABLE",
                    "final_total_score": 99.0,
                    "ranking_eligible": False,
                    "ranking_status": "INELIGIBLE",
                    "rank_in_watchlist": pd.NA,
                    "last_week_change_pct": -8.0,
                    "last_14d_change_pct": -9.0,
                    "last_1m_change_pct": -10.0,
                    "last_3m_change_pct": -11.0,
                    "market_cap_usd": 1_000.0,
                },
                {
                    "ticker": "RANKABLE",
                    "final_total_score": 60.0,
                    "ranking_eligible": True,
                    "ranking_status": "USABLE",
                    "rank_in_watchlist": 1,
                    "last_week_change_pct": -2.0,
                    "last_14d_change_pct": -3.0,
                    "last_1m_change_pct": -4.0,
                    "last_3m_change_pct": -5.0,
                    "market_cap_usd": 500.0,
                },
            ]
        )

    def test_score_leaderboards_exclude_ineligible_rows(self) -> None:
        dashboard = build_dashboard_tables(self.signals)
        top, bottom = VisualizationService.prepare_top_bottom_df(self.signals)

        self.assertEqual(["RANKABLE"], dashboard["top_total"]["ticker"].tolist())
        self.assertEqual(["RANKABLE"], dashboard["bottom_total"]["ticker"].tolist())
        self.assertEqual(["RANKABLE"], top["ticker"].tolist())
        self.assertEqual(["RANKABLE"], bottom["ticker"].tolist())

    def test_excel_signal_sheet_keeps_technical_status_contract(self) -> None:
        self.assertIn("technical_status", ExcelExporter.SIGNAL_EXPORT_COLUMNS)
        self.assertIn("technical_reason", ExcelExporter.SIGNAL_EXPORT_COLUMNS)

    def test_kpi_reports_row_coverage_separately_from_usable_coverage(self) -> None:
        kpi = VisualizationService.prepare_kpi(self.signals)

        self.assertEqual(2, kpi["tickers"])
        self.assertEqual(1, kpi["ranking_eligible"])
        self.assertEqual(1, kpi["ranking_ineligible"])
        self.assertEqual(50.0, kpi["ranking_usable_coverage_pct"])


if __name__ == "__main__":
    unittest.main()
