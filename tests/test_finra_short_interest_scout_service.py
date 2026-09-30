from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.finra_short_interest_scout_service import (
    FinraShortInterestScoutService,
)
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 9, 30, 16, tzinfo=timezone.utc)


class FakeFinra:
    def __init__(self):
        self.calls = []

    def positions(self, symbol, *, limit):
        self.calls.append(symbol)
        return [
            {"symbolCode": symbol, "settlementDate": "2026-09-15",
             "marketClassCode": "NMS", "currentShortPositionQuantity": 100,
             "issueName": "Some security"},
            {"symbolCode": "OTHER", "settlementDate": "2026-09-15",
             "currentShortPositionQuantity": 900},
            {"symbolCode": symbol, "settlementDate": "2026-10-15",
             "currentShortPositionQuantity": 200},
        ]


class FinraScoutTests(unittest.TestCase):
    def test_exact_symbol_and_settlement_are_candidates(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            store.observe_sec_identity(subject_id="AAPL", cik="0000320193",
                                       company_name="Apple Inc.", as_of=NOW)
            client = FakeFinra()
            service = FinraShortInterestScoutService(store, client=client)
            result = service.run(as_of=NOW)
            self.assertEqual((1, 1), (result["checked_issuers"], result["new_findings"]))
            self.assertEqual(0, service.run(as_of=NOW)["checked_issuers"])
            with store._connect() as conn:
                row = conn.execute("SELECT * FROM scout_findings WHERE source='finra'").fetchone()
            self.assertEqual("UNVERIFIED", row["verification_status"])
            self.assertEqual(NOW.isoformat(), row["available_at"])
            details = json.loads(row["details_json"])
            self.assertEqual("2026-09-15", details["settlement_date"])
            self.assertEqual("SYMBOL_ONLY", details["identity_status"])
            self.assertFalse(details["ranking_applied"])

    def test_finra_policy_rejects_foreign_host_and_verified_candidate(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            args = dict(source="finra", subject_id="AAPL", source_object_id="AAPL:2026-09-15",
                        content_hash="hash", title="candidate", locator="symbol:AAPL",
                        published_at=NOW, available_at=NOW, observed_at=NOW, details={})
            with self.assertRaisesRegex(ValueError, "official API"):
                store.record_finding(source_url="https://example.org/short", **args)
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                store.record_finding(source_url="https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", **args)


if __name__ == "__main__":
    unittest.main()
