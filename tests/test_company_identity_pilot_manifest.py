from __future__ import annotations

from pathlib import Path
import unittest

from market_checker_app.services.company_intelligence_manifest_service import (
    parse_identity_records,
)


MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "market_checker_app"
    / "data"
    / "company_identity_pilot.txt"
)

EXPECTED = {
    "AAPL": ("US0378331005", "0000320193"),
    "MSFT": ("US5949181045", "0000789019"),
    "NVDA": ("US67066G1040", "0001045810"),
    "JPM": ("US46647P1021", "0000019617"),
    "GD": ("US3695501086", "0000040533"),
    "CSG": ("NL0015073TS8", None),
    "ASML": ("NL0010273215", None),
    "AIR": ("NL0000235190", None),
    "ADYEN": ("NL0012969182", None),
    "MC": ("FR0000121014", None),
}


class CompanyIdentityPilotManifestTests(unittest.TestCase):
    def test_manifest_contains_ten_real_pilot_companies(self) -> None:
        records, errors = parse_identity_records(
            MANIFEST.read_text(encoding="utf-8")
        )

        self.assertEqual([], errors)
        self.assertEqual(set(EXPECTED), set(records))
        self.assertEqual(10, len(records))

        for ticker, (isin, cik) in EXPECTED.items():
            record = records[ticker]
            self.assertEqual(isin, record["isin"])
            self.assertEqual(cik, record.get("cik"))
            self.assertFalse(record["metadata"]["name_matching_used"])
            self.assertTrue(str(record["source_url"]).startswith("https://"))
            self.assertTrue(
                record.get("lei") or record.get("isin") or record.get("cik")
            )

    def test_duplicate_ticker_with_different_identity_is_rejected(self) -> None:
        records, errors = parse_identity_records(
            MANIFEST.read_text(encoding="utf-8")
            + "\nAAPL | Conflicting identity | - | US5949181045 | - | XNAS | US | Nasdaq | test | https://example.com/conflict\n"
        )

        self.assertEqual(10, len(records))
        self.assertTrue(any("konfliktní záznam" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
