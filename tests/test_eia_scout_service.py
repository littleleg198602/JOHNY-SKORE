from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.eia_scout_service import EiaApiClient, EiaScoutService, MAX_BYTES
from market_checker_app.storage.scout_store import ScoutStore


class StubEia:
    def daily_spot(self, series_id):
        return [
            {"series": series_id, "period": "2026-09-29", "value": "2.5"},
            {"series": series_id, "period": "2026-10-01", "value": "3.0"},
        ]


class EiaScoutTests(unittest.TestCase):
    def test_series_summary_survives_restart_without_issuer_coverage_or_credentials(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "scout.db"
            clock = datetime(2026, 9, 30, tzinfo=timezone.utc)
            store = ScoutStore(db)
            first = EiaScoutService(store, client=StubEia()).run(as_of=clock)
            store.record_source_run("eia", as_of=clock,
                                    summary={**first, "api_key": "synthetic-test-key"})
            saved = ScoutStore(db).latest_source_runs()["eia"]
            for key in ("daily_series", "daily_series_limit", "attempted_series",
                        "quota_deferred_series", "checked_series", "incomplete_series",
                        "failed_series", "usable_rows", "query_scope", "quota_unit",
                        "company_exposure_verified"):
                self.assertIn(key, first)
                self.assertEqual(first[key], saved[key], key)
            self.assertFalse(saved["company_exposure_verified"])
            self.assertNotIn("checked_issuers", saved)
            self.assertNotIn("api_key", saved)
            self.assertNotIn("synthetic-test-key", json.dumps(saved))
            second = EiaScoutService(ScoutStore(db), client=StubEia()).run(as_of=clock)
            store.record_source_run("eia", as_of=clock + timedelta(minutes=1), summary=second)
            saved = ScoutStore(db).latest_source_runs()["eia"]
            self.assertEqual(("WAIT_QUOTA", 0, 0, 2),
                             (saved["status"], saved["attempted_requests"],
                              saved["attempted_series"], saved["daily_series"]))
            self.assertFalse(saved["company_exposure_verified"])

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

    def test_restart_does_not_repeat_series_until_next_utc_day(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "scout.db"
            now = datetime(2026, 10, 2, tzinfo=timezone.utc)
            calls = []
            class Client:
                def daily_spot(self, series):
                    calls.append(series)
                    return [{"series": series, "period": "2026-10-01", "value": "2.5"}]
            first = EiaScoutService(ScoutStore(db), client=Client()).run(as_of=now)
            second = EiaScoutService(ScoutStore(db), client=Client()).run(as_of=now)
            self.assertEqual(("OK", 2, 2), (first["status"], first["daily_requests"], first["daily_series"]))
            self.assertEqual(("WAIT_QUOTA", 0), (second["status"], second["attempted_requests"]))
            self.assertEqual(2, len(calls))
            self.assertNotIn("checked_issuers", first)
            next_day = EiaScoutService(ScoutStore(db), client=Client()).run(as_of=now + timedelta(days=1))
            self.assertEqual(0, next_day["new_findings"])
            self.assertEqual(4, len(calls))

    def test_second_series_failure_preserves_first_and_consumes_failed_quota(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "scout.db"
            now = datetime(2026, 10, 2, tzinfo=timezone.utc)
            class Client:
                def daily_spot(self, series):
                    if series != "RWTC":
                        raise OSError("synthetic-test-key must never enter diagnostics")
                    return [{"series": series, "period": "2026-10-01", "value": "-5"}]
            store = ScoutStore(db)
            result = EiaScoutService(store, client=Client()).run(as_of=now)
            self.assertEqual(("PARTIAL", 1, 1, 2), (result["status"], result["new_findings"], result["failed_series"], result["daily_requests"]))
            self.assertNotIn("synthetic-test-key", json.dumps(result))
            self.assertEqual(1, len(store.latest_findings(["COMMODITY:WTI"], source="eia", as_of=now)))
            self.assertEqual("WAIT_QUOTA", EiaScoutService(ScoutStore(db), client=Client()).run(as_of=now)["status"])

    def test_access_rejection_stops_and_persists_cooldown_without_key(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "scout.db"
            now = datetime(2026, 10, 2, tzinfo=timezone.utc)
            calls = []
            class Client:
                def daily_spot(self, series):
                    calls.append(series)
                    raise HTTPError("https://api.eia.gov/?api_key=synthetic-test-key", 429,
                                    "rate limit", {"Retry-After": "7200"}, None)
            result = EiaScoutService(ScoutStore(db), client=Client()).run(as_of=now)
            self.assertEqual(("RATE_LIMITED", 1), (result["status"], result["attempted_requests"]))
            again = EiaScoutService(ScoutStore(db), client=Client()).run(as_of=now + timedelta(minutes=5))
            self.assertEqual(("RATE_LIMITED", 0), (again["status"], again["attempted_requests"]))
            self.assertEqual(1, len(calls))
            self.assertNotIn("synthetic-test-key", json.dumps((result, again)))

    def test_busy_provider_does_not_make_network_calls(self):
        with TemporaryDirectory() as directory:
            now = datetime(2026, 10, 2, tzinfo=timezone.utc)
            store = ScoutStore(Path(directory) / "scout.db")
            store.claim_provider("eia", as_of=now)
            class Client:
                def daily_spot(self, series):
                    raise AssertionError("busy lease must stop before I/O")
            result = EiaScoutService(store, client=Client()).run(as_of=now)
            self.assertEqual(("BUSY", 0), (result["status"], result["attempted_requests"]))

    def test_empty_malformed_and_ambiguous_series_never_become_complete(self):
        cases = [[], "invalid", [{"series": "RWTC", "period": "2026-10-01", "value": True}],
                 [{"series": "RWTC", "period": "2026-10-01", "value": "2"},
                  {"series": "RWTC", "period": "2026-10-01", "value": "3"}],
                 [{"series": "RWTC", "period": "20261001", "value": "2"}]]
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        for rows in cases:
            with self.subTest(rows=rows), TemporaryDirectory() as directory:
                class Client:
                    def daily_spot(self, series):
                        return rows
                result = EiaScoutService(ScoutStore(Path(directory) / "scout.db"), client=Client()).run(as_of=now)
                self.assertEqual(("PARTIAL", 0, 2), (result["status"], result["new_findings"], result["attempted_requests"]))

    def test_http_client_bounds_body_schema_and_official_redirect(self):
        class Response:
            def __init__(self, body, url):
                self.body, self.url = body, url
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return self.url
            def read(self, size): return self.body[:size]
        official = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
        client = EiaApiClient("synthetic-test-key")
        for body, url in ((b"x" * (MAX_BYTES + 1), official),
                          (b'{"response":{"data":{}}}', official),
                          (b'{"response":{"data":[]}}', "https://example.org/"),
                          (b'{"response":{"data":[{},{},{}]}}', official)):
            with self.subTest(url=url, bytes=len(body)), patch(
                    "market_checker_app.services.eia_scout_service.urlopen", return_value=Response(body, url)):
                with self.assertRaises(ValueError):
                    client.daily_spot("RWTC")


if __name__ == "__main__":
    unittest.main()
