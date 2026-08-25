from __future__ import annotations

import json
from pathlib import Path
import unittest

from market_checker_app.collectors.european_filing_client import AUTHORITY_HOST_SUFFIXES


REGISTRY = (
    Path(__file__).resolve().parents[1]
    / "market_checker_app"
    / "data"
    / "european_authority_registry.json"
)


class EuropeanAuthorityRegistryTests(unittest.TestCase):
    def test_all_builtin_authorities_have_explicit_registry_entries(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        authorities = payload["authorities"]

        self.assertEqual(set(AUTHORITY_HOST_SUFFIXES), set(authorities))
        for authority, entry in authorities.items():
            self.assertTrue(entry["approved_hosts"])
            self.assertTrue(str(entry["official_reference"]).startswith("https://"))
            self.assertTrue(entry["adapter"])
            self.assertIn(
                entry["status"],
                {
                    "live_canary_pending",
                    "page_or_api_adapter_required",
                },
            )

    def test_rns_is_not_misrepresented_as_rss(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))

        self.assertFalse(payload["authorities"]["FCA_RNS"]["rss_available"])
        self.assertEqual(
            "page_or_api_adapter_required",
            payload["authorities"]["FCA_RNS"]["status"],
        )


if __name__ == "__main__":
    unittest.main()
