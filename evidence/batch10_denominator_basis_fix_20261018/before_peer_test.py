"""Independent basis-scope challenge; all changed source bodies are synthetic."""
from unittest import TestCase
from unittest.mock import patch

from market_checker_app.collectors import sec_supplier_revenue_denominator_review as service
from market_checker_app.services import sec_supplier_concentration_review as concentration
from tests.test_sec_supplier_revenue_denominator_review import NOW, POLICY, changed, fixture


EXPLICIT_SCOPE_EXCLUSION = (
    " The 2024 and 2023 net revenue totals in these statements exclude subsidiary sales"
    " that remain included in the customer concentration percentages."
)
REVISED_DEFINITION = " The definition of revenue was revised for fiscal 2024 and fiscal 2023."
ADJUSTED_PRESENTATION = " Prior-year net revenue amounts were adjusted to conform to the current-year presentation."
ROUNDING_ONLY = " Customer concentration percentages are rounded to the nearest whole percentage point."


class DenominatorBasisPeerTests(TestCase):
    def review_policy(self, qualification):
        request, captures = changed(fixture(), POLICY.encode(), (POLICY + qualification).encode())
        with patch.object(service, "_physical_clock", return_value=NOW), patch.object(concentration, "_physical_clock", return_value=NOW), \
                patch("socket.socket.connect", side_effect=AssertionError("No source HTTP")), \
                patch("sqlite3.connect", side_effect=AssertionError("No DB")):
            return service.review_sec_supplier_revenue_denominator(request, captures)

    def assert_no_attribution_upgrade(self, report):
        for field in ("customer_amount_calculated", "counterparty_purchases_verified", "direct_purchase_share_verified",
                      "physical_quantity_denominator_verified", "counterparty_identity_continuity_verified",
                      "historical_as_filed_comparison_verified", "current_instrument_or_q3_verified", "out_of_sample_verified"):
            self.assertFalse(report[field], field)
        self.assertEqual(0, report["registry_adoptions"])
        self.assertEqual(0, report["source_http_requests"])
        self.assertEqual(0, report["db_writes"])

    def assert_basis_not_proven(self, report):
        self.assert_no_attribution_upgrade(report)
        if report["claim"] is not None:
            self.assertNotEqual("SAME_ORIGINAL_SCOPE_PERIOD_AND_BASIS_SUPPORTED", report["claim"]["concentration_join_status"],
                                "A source qualification contradicts or changes the denominator basis.")
            for row in report["claim"]["quantities"]:
                self.assertFalse(row.get("same_original_period_and_denominator_basis_supported", False),
                                 "Matching date/CIK/context/value facts cannot override an unproved basis join.")

    def test_explicit_difference_in_subsidiary_scope_cannot_prove_denominator_basis(self):
        report = self.review_policy(EXPLICIT_SCOPE_EXCLUSION)
        self.assert_basis_not_proven(report)

    def test_postposed_definition_revision_and_adjusted_comparatives_do_not_prove_basis(self):
        for qualification in (REVISED_DEFINITION, ADJUSTED_PRESENTATION):
            with self.subTest(qualification=qualification):
                self.assert_basis_not_proven(self.review_policy(qualification))

    def test_rounding_qualification_never_produces_customer_amount_or_historical_identity(self):
        report = self.review_policy(ROUNDING_ONLY)
        self.assert_no_attribution_upgrade(report)
        if report["claim"] is not None:
            claim = report["claim"]
            self.assertIn(ROUNDING_ONLY.strip(), claim["definition_policy_literal"])
            self.assertEqual("COMPARATIVES_AS_REPORTED_IN_THIS_CURRENT_FILING", claim["comparative_version_scope"])
            self.assertEqual("UNKNOWN_NOT_RECONSTRUCTED", claim["definition_comparability_across_prior_filings"])
            self.assertEqual("0000004127", claim["denominator_owner"]["owner_cik"])
            self.assertFalse(claim["customer_percentage_times_amount_performed"])
            for quantity in claim["quantities"]:
                self.assertEqual(-5, quantity["decimals"])
                self.assertEqual(6, quantity["scale"])
                self.assertIsNone(quantity["customer_amount"])
                self.assertFalse(quantity["customer_amount_calculated"])
