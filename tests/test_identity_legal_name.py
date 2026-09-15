from __future__ import annotations

import unittest

from market_checker_app.identity_pilot_smoke import _legal_name_match


class IdentityLegalNameTests(unittest.TestCase):
    def test_amat_cosmetic_name_difference_requires_confirmed_identifiers(self) -> None:
        self.assertEqual(
            "COSMETIC_IDENTIFIER_CONFIRMED",
            _legal_name_match(
                "Applied Materials, Inc. /DE/",
                "Applied Materials, Inc.",
                identifiers_confirmed=True,
            ),
        )

    def test_cosmetic_name_difference_is_not_identity_resolution(self) -> None:
        self.assertEqual(
            "MISMATCH",
            _legal_name_match(
                "Applied Materials, Inc. /DE/",
                "Applied Materials, Inc.",
                identifiers_confirmed=False,
            ),
        )

    def test_real_name_difference_remains_a_mismatch(self) -> None:
        self.assertEqual(
            "MISMATCH",
            _legal_name_match(
                "Applied Materials, Inc.",
                "Applied Micro Circuits Corporation",
                identifiers_confirmed=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
