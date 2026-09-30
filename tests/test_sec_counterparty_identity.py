import unittest

from market_checker_app.collectors.sec_edgar_client import SecCompany
from market_checker_app.services.sec_counterparty_identity import exact_catalog_cik


class ExactCounterpartyIdentityTests(unittest.TestCase):
    def test_exact_legal_name_accepts_punctuation_and_share_classes(self):
        catalog = {
            "ACMA": SecCompany("ACMA", "0000001234", "ACME COMPONENTS, INC.", "NYSE"),
            "ACMB": SecCompany("ACMB", "0000001234", "Acme Components Inc.", "NYSE"),
        }
        self.assertEqual(("0000001234", ("ACMA", "ACMB")),
                         exact_catalog_cik("Acme Components Inc.", catalog))

    def test_near_name_and_duplicate_issuers_do_not_resolve(self):
        catalog = {
            "A": SecCompany("A", "0000001234", "Acme Components Inc.", "NYSE"),
            "B": SecCompany("B", "0000005678", "ACME COMPONENTS, INC.", "NYSE"),
        }
        self.assertIsNone(exact_catalog_cik("Acme Component Inc.", catalog))
        self.assertIsNone(exact_catalog_cik("Acme Components Inc.", catalog))


if __name__ == "__main__":
    unittest.main()
