from datetime import datetime, timezone

import unittest

from market_checker_app.services.fred_scout_service import FredScoutService
from market_checker_app.storage.scout_store import ScoutStore


class StubFred:
    def observations(self, series_id):
        return [{"date": "2026-09-01", "value": "4.2"},
                {"date": "2026-10-01", "value": "5.1"},
                {"date": "2026-08-01", "value": "."}]


class FredScoutTests(unittest.TestCase):
  def test_fred_records_dated_macro_observations_without_future_or_missing_values(self):
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as directory:
      store = ScoutStore(Path(directory) / "scout.sqlite")
      self.check_observations(store)

  def check_observations(self, store):
    service = FredScoutService(store, client=StubFred())
    clock = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert service.run(as_of=clock)["new_findings"] == 3
    assert service.run(as_of=clock)["new_findings"] == 0
    with store._connect() as conn:
        rows = conn.execute("SELECT source, subject_id, available_at, details_json "
                            "FROM scout_findings ORDER BY source_object_id").fetchall()
    assert len(rows) == 3
    assert all(row["source"] == "fred" and row["subject_id"] == "MACRO:US" for row in rows)
    assert all(row["available_at"] == clock.isoformat() for row in rows)
    assert all('"release_timestamp_known": false' in row["details_json"] for row in rows)


  def test_fred_rejects_unofficial_url(self):
    from tempfile import TemporaryDirectory
    from pathlib import Path
    with TemporaryDirectory() as directory:
      store = ScoutStore(Path(directory) / "scout.sqlite")
      self.check_url(store)

  def check_url(self, store):
    clock = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with self.assertRaisesRegex(ValueError, "official FRED"):
        store.record_finding(source="fred", subject_id="MACRO:US", source_object_id="X",
                             content_hash="abc", title="X", source_url="https://example.org/x",
                             locator="x", published_at=clock, available_at=clock,
                             observed_at=clock, details={})
