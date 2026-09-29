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
        self.assertEqual(registry.for_ticker("OKE").code, "OIL_GAS")
        self.assertIsNone(registry.for_ticker("P"))

    def test_irrelevant_metric_is_not_missing_evidence(self):
        registry = load_research_profiles()
        self.assertEqual(registry.applicability("JPM", "BANK"), "APPLICABLE")
        self.assertEqual(registry.applicability("JPM", "INDUSTRIAL"), "NOT_APPLICABLE")
        self.assertEqual(registry.applicability("OKE", "OIL_GAS"), "APPLICABLE")
        self.assertEqual(registry.applicability("MISSING", "OIL_GAS"), "UNKNOWN")

    def test_versioned_rules_keep_conditional_sources_as_questions(self):
        registry = load_research_profiles()
        bank = registry.for_ticker("JPM")
        industrial = registry.for_ticker("CAT")
        capital = registry.for_ticker("BLK")
        self.assertIsNotNone(bank)
        self.assertIsNotNone(industrial)
        self.assertIsNotNone(capital)
        industrial_metric = next(
            rule for rule in industrial.metric_rules if "cenový mix" in rule.description
        )
        self.assertEqual("NOT_APPLICABLE", registry.rule_status("JPM", industrial_metric.rule_id))
        self.assertEqual("CANDIDATE", registry.rule_status("CAT", industrial_metric.rule_id))
        bank_subsidiary = next(
            rule for rule in capital.source_rules if "skutečné bankovní dcery" in rule.description
        )
        self.assertEqual("CANDIDATE", registry.rule_status("BLK", bank_subsidiary.rule_id))
        self.assertEqual("UNKNOWN", registry.rule_status("P", bank_subsidiary.rule_id))
        self.assertEqual("UNKNOWN", registry.rule_status("JPM", "research-v1:FAKE:metric:1"))
        self.assertTrue(bank.metric_rules[0].rule_id.startswith("research-v1:BANK:metric:1:"))

    def test_all_profiles_provide_stable_metric_and_source_rule_ids(self):
        registry = load_research_profiles()
        rules = [rule for profile in registry.profiles
                 for rule in profile.metric_rules + profile.source_rules]
        self.assertEqual(len(rules), len({rule.rule_id for rule in rules}))
        self.assertTrue(all(rule.description for rule in rules))


if __name__ == "__main__":
    unittest.main()
