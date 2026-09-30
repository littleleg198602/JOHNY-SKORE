from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from market_checker_app.services.eia_scout_service import EiaScoutService
from market_checker_app.storage.scout_store import ScoutStore


class StubEia:
    def daily_spot(self, series_id):
        return [
            {"series": series_id, "period": "2026-09-29", "value": "2.5"},
            {"series": series_id, "period": "2026-10-01", "value": "3.0"},
            {"series": "OTHER", "period": "2026-09-29", "value": "99"},
            {"series": series_id, "period": "2026-09-28", "value": None},
        ]


class EiaScoutTests(unittest.TestCase):
    def test_energy_series_have_separate_units_and_safe_availability(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            clock = datetime(2026, 9, 30, tzinfo=timezone.utc)
            service = EiaScoutService(store, client=StubEia())
            self.assertEqual(2, service.run(as_of=clock)["new_findings"])
            self.assertEqual(0, service.run(as_of=clock)["new_findings"])
            with store._connect() as conn:
                rows = conn.execute("SELECT subject_id, available_at, details_json "
                                    "FROM scout_findings ORDER BY subject_id").fetchall()
            self.assertEqual({"COMMODITY:WTI", "COMMODITY:JET_FUEL_GULF"},
                             {row["subject_id"] for row in rows})
            self.assertEqual({"USD/barrel", "USD/gallon"},
                             {json.loads(row["details_json"])["unit"] for row in rows})
            self.assertTrue(all(row["available_at"] == clock.isoformat() for row in rows))
            self.assertTrue(all(not json.loads(row["details_json"])["company_purchase_price"]
                                for row in rows))

    def test_eia_rejects_non_eia_host(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            clock = datetime(2026, 9, 30, tzinfo=timezone.utc)
            with self.assertRaisesRegex(ValueError, "official EIA"):
                store.record_finding(source="eia", subject_id="COMMODITY:WTI",
                                     source_object_id="RWTC:2026-09-29", content_hash="abc",
                                     title="WTI", source_url="https://example.org/price",
                                     locator="test", published_at=clock,
                                     available_at=clock, observed_at=clock, details={})


if __name__ == "__main__":
    unittest.main()
