import unittest

from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.utils.ticker_universe import load_canonical_tickers


class ResearchProfilesTest(unittest.TestCase):
    def test_all_39_profiles_and_input_discrepancy_are_explicit(self):
        registry = load_research_profiles()
        self.assertEqual(len(registry.profiles), 39)
        self.assertEqual(len(registry.by_ticker), 687)
        self.assertEqual(len(load_canonical_tickers()), 687)
        self.assertEqual(registry.source_only, ("P",))
        self.assertEqual(registry.unmapped_input, ("OKE",))

    def test_irrelevant_metric_is_not_missing_evidence(self):
        registry = load_research_profiles()
        self.assertEqual(registry.applicability("JPM", "BANK"), "APPLICABLE")
        self.assertEqual(registry.applicability("JPM", "INDUSTRIAL"), "NOT_APPLICABLE")
        self.assertEqual(registry.applicability("OKE", "OIL_GAS"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
