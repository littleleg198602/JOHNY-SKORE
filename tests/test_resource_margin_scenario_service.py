from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from market_checker_app.config import (
    CommodityEnergySourceConfig,
    ResourcePricePointConfig,
)
from market_checker_app.services.resource_margin_scenario_service import (
    build_resource_margin_scenario,
)


def _at(day: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)


def _source(*, points: tuple[ResourcePricePointConfig, ...] = ()) -> CommodityEnergySourceConfig:
    return CommodityEnergySourceConfig(
        ticker="TEST",
        resource_name="Copper",
        exposure_type="MATERIAL_INPUT",
        publisher="Company annual report",
        published_at=_at(1),
        url="https://example.com/filing",
        disclosed_cost_share_of_revenue_pct=20.0,
        hedged_share_pct=30.0,
        fixed_price_share_pct=20.0,
        pass_through_pct=25.0,
        scenario_price_change_pct=15.0,
        disclosure_period="FY2025",
        evidence_quote="Copper purchases were 20% of revenue, with stated hedging.",
        price_points=points,
    )


class ResourceMarginScenarioServiceTests(unittest.TestCase):
    def test_disclosed_inputs_produce_reproducible_margin_sensitivity(self) -> None:
        point = ResourcePricePointConfig(
            observed_at=_at(2),
            available_at=_at(3),
            value=9_000.0,
            unit="USD/mt",
            currency="USD",
            source_url="https://example.com/copper-price",
        )
        report = build_resource_margin_scenario(_source(points=(point,)), as_of=_at(5))
        self.assertEqual("READY", report["status"])
        self.assertEqual(9_000.0, report["latest_price_point"]["value"])
        self.assertAlmostEqual(-1.125, report["sensitivity"]["estimated_margin_impact_pp"])
        self.assertTrue(report["scenario_assumption"]["not_a_price_forecast"])
        self.assertFalse(report["prediction_input"])

    def test_future_price_point_is_excluded_at_cutoff(self) -> None:
        future = ResourcePricePointConfig(
            observed_at=_at(2),
            available_at=_at(10),
            value=9_000.0,
            unit="USD/mt",
            currency="USD",
            source_url="https://example.com/copper-price",
        )
        report = build_resource_margin_scenario(_source(points=(future,)), as_of=_at(5))
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertIn("PRICE_SERIES_MISSING_OR_NOT_AVAILABLE_AT_CUTOFF", report["reason"])
        self.assertEqual(1, report["price_points_future_excluded"])

    def test_missing_disclosures_do_not_create_a_margin_impact(self) -> None:
        point = ResourcePricePointConfig(
            observed_at=_at(2),
            available_at=_at(3),
            value=9_000.0,
            unit="USD/mt",
            currency="USD",
            source_url="https://example.com/copper-price",
        )
        source = _source(points=(point,))
        source = CommodityEnergySourceConfig(
            ticker=source.ticker,
            resource_name=source.resource_name,
            exposure_type=source.exposure_type,
            publisher=source.publisher,
            published_at=source.published_at,
            url=source.url,
            price_points=source.price_points,
        )
        report = build_resource_margin_scenario(source, as_of=_at(5))
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertNotIn("sensitivity", report)
        self.assertIn("COST_SHARE_NOT_DISCLOSED", report["reason"])


if __name__ == "__main__":
    unittest.main()
