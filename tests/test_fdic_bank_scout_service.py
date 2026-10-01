from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.fdic_bank_scout_service import (
    FdicBankFindClient, FdicBankScoutService, fdic_coverage, fdic_identity_key, load_verified_banks,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import load_canonical_tickers
from market_checker_app.specialist_live_smoke import run as live_smoke


NOW = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
IDENTITY = {
    "ticker": "JPM", "cert": 628,
    "bank_name": "JPMorgan Chase Bank, National Association",
    "fdic_evidence_url": "https://banks.data.fdic.gov/bankfind-suite/bankfind/details/628",
    "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/19617/ex21.htm",
    "effective_from": "2025-12-31", "known_at": "2026-09-30T20:09:00+00:00",
}


class FakeBankFind:
    def __init__(self, row=None, error=False):
        self.row, self.error, self.calls = row, error, []

    def financials(self, cert):
        self.calls.append(cert)
        if self.error:
            raise OSError("temporary failure")
        row = self.row if self.row is not None else {
            "CERT": 628, "NAME": IDENTITY["bank_name"], "REPDTE": "2026-06-30",
            "ASSET": 1000, "DEP": 800, "EQ": 150, "NETINC": 12,
        }
        return {"data": [{"data": row}]}


class FdicBankScoutTests(unittest.TestCase):
    def test_exact_financial_name_is_dated_and_mismatch_is_visible(self):
        identity = dict(IDENTITY, financial_name="JPMORGAN CHASE BANK NA",
                        financial_name_known_at=NOW.isoformat(),
                        financial_name_evidence_url="https://api.fdic.gov/banks/financials?filters=CERT%3A628")
        row = {"CERT": 628, "NAME": "JPMORGAN CHASE BANK NA", "REPDTE": "20260630",
               "ASSET": 1000, "DEP": 800, "EQ": 150, "NETINC": 12}
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeBankFind(row=row)
            scout = FdicBankScoutService(store, client=client, identities=[identity])
            before = scout.run(as_of=NOW - timedelta(minutes=1))
            self.assertEqual(("PARTIAL", 0, 1),
                             (before["status"], before["usable_banks"], before["rejected_rows"]))
            accepted = scout.run(as_of=NOW)
            self.assertEqual(("OK", 1, 1),
                             (accepted["status"], accepted["usable_banks"], accepted["new_findings"]))
            client.row = dict(row, NAME="JPMORGAN CHASE BANK")
            self.assertEqual("PARTIAL", scout.run(as_of=NOW, recheck=True)["status"])
            client.row = dict(row, CERT=3510)
            self.assertEqual(0, scout.run(as_of=NOW, recheck=True)["usable_banks"])
            client.row = dict(row, REPDTE="2026-06-30junk")
            self.assertEqual(0, scout.run(as_of=NOW, recheck=True)["usable_banks"])
            store.record_source_run("fdic", as_of=NOW, summary=before)
            self.assertEqual(1, store.latest_source_runs()["fdic"]["rejected_rows"])

    def test_financial_name_manifest_rejects_wrong_cert_and_unobserved_alias(self):
        base = dict(IDENTITY, financial_name="JPMORGAN CHASE BANK NA",
                    financial_name_known_at=NOW.isoformat(),
                    financial_name_evidence_url="https://api.fdic.gov/banks/financials?filters=CERT%3A628")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for invalid in (
                dict(base, financial_name_evidence_url="https://api.fdic.gov/banks/financials?filters=CERT%3A3510"),
                dict(base, financial_name_known_at="2026-10-01T09:00:00"),
                {key: value for key, value in base.items() if key != "financial_name_known_at"},
            ):
                path.write_text(json.dumps([invalid]))
                with self.assertRaises(ValueError):
                    load_verified_banks(path)

    def test_production_bank_relationships_are_exact_and_dated(self):
        identities = load_verified_banks()
        self.assertEqual({("JPM", 628), ("BAC", 3510), ("WFC", 3511), ("C", 7213),
                          ("PNC", 6384), ("USB", 6548), ("TFC", 9846), ("FITB", 6672)},
                         {(row["ticker"], row["cert"]) for row in identities})
        citi = next(row for row in identities if row["ticker"] == "C")
        self.assertEqual("Citibank, National Association", citi["bank_name"])
        bac = next(row for row in identities if row["ticker"] == "BAC")
        self.assertEqual("Bank of America, National Association", bac["bank_name"])
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeBankFind(row={
                "CERT": 3510, "NAME": bac["bank_name"], "REPDTE": "2026-06-30",
                "ASSET": 1200, "DEP": 900, "EQ": 170, "NETINC": 13,
            })
            scout = FdicBankScoutService(store, client=client, identities=identities)
            self.assertEqual("WAIT_IDENTITY", scout.run(
                as_of=datetime(2026, 10, 1, 5, tzinfo=timezone.utc),
                universe={"BAC"})["status"])
            self.assertEqual(1, scout.run(
                as_of=datetime(2026, 10, 1, 7, tzinfo=timezone.utc),
                universe={"BAC"})["new_findings"])
            self.assertEqual([3510], client.calls)
            client.row["NAME"] = "Different Bank, National Association"
            self.assertEqual(0, scout.run(
                as_of=datetime(2026, 10, 2, tzinfo=timezone.utc),
                universe={"BAC"})["new_findings"])

    def test_new_bank_links_are_scoped_knowledge_dated_and_keep_issuer_provenance(self):
        identities = [row for row in load_verified_banks() if row["ticker"] in {"PNC", "USB"}]
        self.assertTrue({row["ticker"] for row in identities}.issubset(set(load_canonical_tickers())))
        self.assertEqual({"PNC": "0000713676", "USB": "0000036104"},
                         {row["ticker"]: row["issuer_cik"] for row in identities})
        for identity in identities:
            with self.subTest(ticker=identity["ticker"]), TemporaryDirectory() as directory:
                clock = datetime.fromisoformat(identity["known_at"])
                client = FakeBankFind(row={"CERT": identity["cert"], "NAME": identity["financial_name"],
                                          "REPDTE": "20260630", "ASSET": 1200})
                store = ScoutStore(Path(directory) / "test.db")
                scout = FdicBankScoutService(store, client=client, identities=[identity])
                self.assertEqual("WAIT_IDENTITY", scout.run(as_of=clock - timedelta(seconds=1))["status"])
                self.assertEqual("WAIT_IDENTITY", scout.run(as_of=clock, universe={"OTHER"})["status"])
                self.assertEqual([], client.calls)
                self.assertEqual(1, scout.run(as_of=clock, universe={identity["ticker"]})["new_findings"])
                self.assertEqual(0, scout.run(as_of=clock)["new_findings"])
                finding = store.findings_as_of(identity["ticker"], as_of=clock)[0]
                details = json.loads(finding["details_json"])
                self.assertEqual(identity["issuer_cik"], details["issuer_cik"])
                self.assertEqual(identity["issuer_identity_evidence_url"], details["issuer_identity_evidence_url"])
                self.assertFalse(details["issuer_consolidated_values"])
                self.assertFalse(details["scoring_applied"])
                self.assertEqual([], store.findings_as_of(identity["ticker"], as_of=clock - timedelta(seconds=1)))

    def test_issuer_cik_must_match_both_sec_citations(self):
        base = next(row for row in load_verified_banks() if row["ticker"] == "PNC")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            invalids = (
                dict(base, issuer_cik="0000036104"),
                dict(base, issuer_cik="713676"),
                dict(base, issuer_identity_evidence_url=base["issuer_identity_evidence_url"].replace('/713676/', '/36104/')),
                dict(base, relationship_evidence_url=base["relationship_evidence_url"].replace('/713676/', '/36104/')),
                {key: value for key, value in base.items() if key != "issuer_name"},
            )
            for invalid in invalids:
                path.write_text(json.dumps([invalid]))
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    load_verified_banks(path)

    def test_tfc_fitb_real_captures_preserve_relationship_date_and_knowledge_scope(self):
        evidence = json.loads((Path(__file__).resolve().parents[1] /
                               "evidence/fdic_tfc_fitb_identity_20261001.json").read_text())
        identities = [row for row in load_verified_banks() if row["ticker"] in {"TFC", "FITB"}]
        self.assertEqual({"TFC": (9846, "0000092230", "2025-12-31"),
                          "FITB": (6672, "0000035527", "2026-02-15")},
                         {row["ticker"]: (row["cert"], row["issuer_cik"], row["effective_from"])
                          for row in identities})
        captures = {entry["identity"]["cert"]: entry for entry in evidence["entries"]}
        for identity in identities:
            with self.subTest(ticker=identity["ticker"]), TemporaryDirectory() as directory:
                captured = captures[identity["cert"]]
                self.assertEqual(identity, captured["identity"])
                self.assertEqual(identity["bank_name"], captured["fdic_publisher_observation"]["institutions"]["data"][0]["data"]["NAME"])
                class CapturedClient:
                    calls = 0
                    payload = captured["fdic_publisher_observation"]["financials"]
                    def financials(self, cert):
                        self.calls += 1
                        self.assert_cert = cert
                        return self.payload
                client = CapturedClient()
                store = ScoutStore(Path(directory) / "test.db")
                clock = datetime.fromisoformat(identity["known_at"])
                scout = FdicBankScoutService(store, client=client, identities=[identity])
                self.assertEqual("WAIT_IDENTITY", scout.run(as_of=clock - timedelta(seconds=1))["status"])
                self.assertEqual("WAIT_IDENTITY", scout.run(as_of=clock, universe={"OTHER"})["status"])
                self.assertEqual(0, client.calls)
                result = scout.run(as_of=clock, universe=set(load_canonical_tickers()))
                self.assertEqual(("OK", 2, 1), (result["status"], result["new_findings"], result["usable_banks"]))
                replay = scout.run(as_of=clock, recheck=True)
                self.assertEqual((1, 0, 2), (replay["checked_banks"], replay["new_findings"], client.calls))
                finding = json.loads(store.findings_as_of(identity["ticker"], as_of=clock)[0]["details_json"])
                self.assertEqual(identity["issuer_cik"], finding["issuer_cik"])
                self.assertEqual(identity["effective_from"], finding["relationship_effective_from"])
                self.assertFalse(finding["scoring_applied"])
                self.assertFalse(finding["issuer_consolidated_values"])
                if identity["ticker"] == "FITB":
                    # The 2025 10-K Exhibit 21 itself is dated February 15,
                    # 2026. Do not backdate it to the 10-K reporting period.
                    row = dict(client.payload["data"][0]["data"], REPDTE="20251231")
                    client.payload = {"data": [{"data": row}]}
                    rejected = scout.run(as_of=clock, recheck=True)
                    self.assertEqual(("PARTIAL", 0, 1),
                                     (rejected["status"], rejected["usable_banks"], rejected["rejected_rows"]))

    def test_live_smoke_rejects_unknown_or_out_of_source_selection_before_io(self):
        with TemporaryDirectory() as directory:
            for sources, tickers in ((('fdic',), ('NOT_REGISTERED',)), (('ofac',), ('PNC',)), (('fdic',), ())):
                with self.assertRaises(ValueError):
                    live_smoke(output_path=Path(directory) / "never.json", sources=sources, fdic_tickers=tickers)
            self.assertFalse((Path(directory) / "never.json").exists())

    def test_dated_identity_and_bank_subsidiary_financials(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "identities.json"
            path.write_text(json.dumps([IDENTITY]), encoding="utf-8")
            identities = load_verified_banks(path)
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeBankFind()
            scout = FdicBankScoutService(store, client=client, identities=identities)
            self.assertEqual("WAIT_IDENTITY", scout.run(as_of=NOW - timedelta(hours=2))["status"])
            self.assertEqual([], client.calls)
            self.assertEqual(1, scout.run(as_of=NOW, universe={"JPM"})["new_findings"])
            self.assertEqual(0, scout.run(as_of=NOW + timedelta(days=1))["new_findings"])
            with store._connect() as conn:
                rows = conn.execute("SELECT verification_status, published_at, details_json "
                                    "FROM scout_findings WHERE source='fdic'").fetchall()
            self.assertEqual(1, len(rows))
            details = json.loads(rows[0]["details_json"])
            self.assertEqual({"ASSET": 1000, "DEP": 800, "EQ": 150, "NETINC": 12},
                             details["amounts_thousands_usd"])
            self.assertFalse(details["issuer_consolidated_values"])
            self.assertFalse(details["scoring_applied"])
            self.assertEqual("SOURCE_VERIFIED", rows[0]["verification_status"])
            self.assertEqual(NOW.isoformat(), rows[0]["published_at"])

    def test_mismatch_future_report_and_failure_do_not_produce_findings(self):
        rows = [
            {"CERT": 999, "NAME": IDENTITY["bank_name"], "REPDTE": "2026-06-30", "DEP": 1},
            {"CERT": 628, "NAME": "Different Bank", "REPDTE": "2026-06-30", "DEP": 1},
            {"CERT": 628, "NAME": IDENTITY["bank_name"], "REPDTE": "2027-06-30", "DEP": 1},
            {"CERT": 628, "NAME": IDENTITY["bank_name"], "REPDTE": "2025-09-30", "DEP": 1},
        ]
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            for row in rows:
                self.assertEqual(0, FdicBankScoutService(store, client=FakeBankFind(row),
                                    identities=[IDENTITY]).run(as_of=NOW, recheck=True)["new_findings"])
            failed = FdicBankScoutService(store, client=FakeBankFind(error=True),
                                          identities=[IDENTITY]).run(as_of=NOW, recheck=True)
            self.assertEqual(("PARTIAL", 1), (failed["status"], failed["failed_banks"]))
            compact_date = dict(rows[1], NAME=IDENTITY["bank_name"], REPDTE="20260630")
            self.assertEqual(1, FdicBankScoutService(store, client=FakeBankFind(compact_date),
                                identities=[IDENTITY]).run(as_of=NOW, recheck=True)["new_findings"])

    def test_manifest_and_source_host_policy(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "identities.json"
            invalid = dict(IDENTITY, fdic_evidence_url="https://banks.data.fdic.gov/bankfind-suite/bankfind/details/999")
            path.write_text(json.dumps([invalid]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cited CERT"):
                load_verified_banks(path)
            store = ScoutStore(Path(directory) / "test.db")
            with self.assertRaisesRegex(ValueError, "official API"):
                store.record_finding(source="fdic", subject_id="JPM", source_object_id="628:2026-06-30",
                                     content_hash="x", title="FDIC", source_url="https://fdic.example/financials",
                                     locator="CERT:628", published_at=NOW, available_at=NOW,
                                     observed_at=NOW, details={})


class BoundedFdicTests(unittest.TestCase):
    clock = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)

    def client(self, identities):
        class RegistryClient:
            calls = []
            def financials(self, cert):
                self.calls.append(cert)
                identity = next(entry for entry in identities if entry["cert"] == cert)
                return {"data": [{"data": {"CERT": cert,
                        "NAME": identity.get("financial_name", identity["bank_name"]),
                        "REPDTE": "20260630", "ASSET": 1000}}]}
        return RegistryClient()

    def test_rotation_restart_and_shared_daily_quota_then_thirty_day_refresh(self):
        # Fixed six-bank fixture; later identity onboarding has a separate test.
        identities = [entry for entry in load_verified_banks()
                      if entry["ticker"] in {"JPM", "BAC", "WFC", "C", "PNC", "USB"}]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            client = self.client(identities)
            scout = FdicBankScoutService(ScoutStore(path), client=client, identities=identities,
                                         max_banks=2, daily_request_budget=3)
            first = scout.run(as_of=self.clock)
            self.assertEqual((2, 4, True), (first["attempted_banks"], first["deferred_banks"], first["budget_exhausted"]))
            restarted = FdicBankScoutService(ScoutStore(path), client=client, identities=list(reversed(identities)),
                                             max_banks=2, daily_request_budget=3)
            second = restarted.run(as_of=self.clock + timedelta(hours=1))
            self.assertEqual((1, 3), (second["attempted_banks"], second["daily_requests"]))
            self.assertEqual(0, restarted.run(as_of=self.clock + timedelta(hours=2))["attempted_banks"])
            next_day = self.clock + timedelta(days=1)
            restarted.run(as_of=next_day)
            restarted.run(as_of=next_day + timedelta(hours=1))
            self.assertEqual(6, len(set(client.calls)))
            self.assertEqual(6, len(client.calls))
            self.assertEqual("NO_DUE_WORK", restarted.run(as_of=next_day + timedelta(hours=2))["status"])
            refreshed = restarted.run(as_of=self.clock + timedelta(days=30))
            self.assertEqual(2, refreshed["attempted_banks"])
            self.assertEqual(0, refreshed["new_findings"])

    def test_partial_empty_failed_and_new_alias_have_honest_coverage_and_retry(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeBankFind(row={"CERT": 628, "NAME": "WRONG", "REPDTE": "20260630", "ASSET": 1})
            scout = FdicBankScoutService(store, client=client, identities=[IDENTITY])
            self.assertEqual("PARTIAL", scout.run(as_of=NOW)["status"])
            self.assertEqual("NO_DUE_WORK", scout.run(as_of=NOW + timedelta(hours=23))["status"])
            self.assertEqual(1, fdic_coverage(store, [IDENTITY], as_of=NOW, subjects={"JPM"})["current_partial_banks"])
            client.row = None
            self.assertEqual(1, scout.run(as_of=NOW + timedelta(days=1))["usable_banks"])
            self.assertEqual(0, scout.run(as_of=NOW + timedelta(days=2))["attempted_banks"])
            client.error = True
            self.assertEqual(1, scout.run(as_of=NOW + timedelta(days=2), recheck=True)["failed_banks"])
            coverage = fdic_coverage(store, [IDENTITY], as_of=NOW + timedelta(days=2), subjects={"JPM"})
            self.assertEqual((0, 1), (coverage["current_usable_banks"], coverage["current_failed_banks"]))
            self.assertEqual(1, fdic_coverage(store, [IDENTITY], as_of=NOW, subjects={"JPM"})["current_partial_banks"])
            client.error = False
            alias = dict(IDENTITY, financial_name=IDENTITY["bank_name"],
                         financial_name_known_at=(NOW + timedelta(days=2)).isoformat(),
                         financial_name_evidence_url="https://api.fdic.gov/banks/financials?filters=CERT%3A628")
            self.assertEqual(fdic_identity_key(IDENTITY, NOW), fdic_identity_key(alias, NOW))
            self.assertEqual(1, FdicBankScoutService(store, client=client, identities=[alias]).run(
                as_of=NOW + timedelta(days=2))["attempted_banks"])
            class Empty:
                def financials(self, cert): return {"data": []}
            empty = FdicBankScoutService(store, client=Empty(), identities=[alias]).run(
                as_of=NOW + timedelta(days=3), recheck=True)
            self.assertEqual(("PARTIAL", 1, 0), (empty["status"], empty["empty_banks"], empty["usable_banks"]))
            self.assertEqual(1, fdic_coverage(store, [alias], as_of=NOW + timedelta(days=3),
                                            subjects={"JPM"})["current_empty_banks"])
            self.assertEqual(1, fdic_coverage(store, [alias], as_of=NOW + timedelta(days=33),
                                            subjects={"JPM"})["stale_banks"])

    def test_http_access_and_rate_limits_stop_batch_and_persist_cooldown(self):
        identities = load_verified_banks()
        for code in (401, 403, 429):
            with self.subTest(code=code), TemporaryDirectory() as directory:
                path = Path(directory) / "test.db"
                class Blocked:
                    calls = 0
                    def financials(self, cert):
                        self.calls += 1
                        raise HTTPError("https://api.fdic.gov/banks/financials", code, "blocked", {}, None)
                client = Blocked()
                first = FdicBankScoutService(ScoutStore(path), client=client, identities=identities).run(as_of=self.clock)
                expected = "RATE_LIMITED" if code == 429 else "ACCESS_BLOCKED"
                self.assertEqual((expected, 1, 1), (first["status"], first["attempted_banks"], client.calls))
                second = FdicBankScoutService(ScoutStore(path), client=client, identities=identities).run(
                    as_of=self.clock + timedelta(minutes=30))
                self.assertEqual(expected, second["status"])
                self.assertIn("retry_at", second)
                self.assertEqual(1, client.calls)
                later = self.clock + timedelta(hours=1 if code == 429 else 24)
                FdicBankScoutService(ScoutStore(path), client=client, identities=identities).run(as_of=later)
                self.assertEqual(2, client.calls)

    def test_failed_attempts_are_rotated_and_consume_quota(self):
        identities = load_verified_banks()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            client = FakeBankFind(error=True)
            service = FdicBankScoutService(ScoutStore(path), client=client, identities=identities,
                                           max_failures=2, daily_request_budget=3)
            result = service.run(as_of=self.clock)
            self.assertEqual((2, True), (result["failed_banks"], result["budget_exhausted"]))
            service.run(as_of=self.clock + timedelta(minutes=1))
            self.assertEqual(3, len(set(client.calls)))
            self.assertEqual(0, service.run(as_of=self.clock + timedelta(minutes=2), recheck=True)["attempted_banks"])

    def test_provider_lease_and_interrupted_attempt_survive_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            store = ScoutStore(path)
            token = store.claim_provider("fdic", as_of=NOW, seconds=30)
            request_id = store.reserve_fdic_request(subject_id="JPM", identity_key=fdic_identity_key(IDENTITY, NOW),
                                                    as_of=NOW, daily_limit=1, lease_token=token)
            self.assertIsNotNone(request_id)
            client = FakeBankFind()
            scout = FdicBankScoutService(ScoutStore(path), client=client, identities=[IDENTITY])
            self.assertEqual("BUSY", scout.run(as_of=NOW)["status"])
            self.assertEqual("NO_DUE_WORK", scout.run(as_of=NOW + timedelta(seconds=31))["status"])
            self.assertEqual([], client.calls)
            self.assertEqual(1, fdic_coverage(store, [IDENTITY], as_of=NOW, subjects={"JPM"})["inflight_banks"])
            self.assertEqual(1, store.fdic_daily_requests(as_of=NOW))
            self.assertEqual(1, scout.run(as_of=NOW + timedelta(days=1))["attempted_banks"])
            self.assertIsNone(store.reserve_fdic_request(subject_id="JPM", identity_key="new",
                                                         as_of=NOW, daily_limit=10, lease_token=token))

    def test_time_budget_stops_before_next_request_and_keeps_unattempted_due(self):
        identities = load_verified_banks()
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = self.client(identities)
            with patch("market_checker_app.services.fdic_bank_scout_service.time.monotonic",
                       side_effect=[0, 0, 61]):
                result = FdicBankScoutService(store, client=client, identities=identities).run(as_of=self.clock)
            self.assertEqual((1, 5, True), (result["attempted_banks"], result["deferred_banks"], result["budget_exhausted"]))
            self.assertEqual(5, fdic_coverage(store, identities, as_of=self.clock,
                                            subjects=set(load_canonical_tickers()))["never_attempted_banks"])

    def test_duplicate_and_unbounded_responses_do_not_claim_usable_coverage(self):
        for payload in ({"data": [{"data": {"CERT": 628, "NAME": IDENTITY["bank_name"],
                                                 "REPDTE": "20260630", "ASSET": 1}}] * 2},
                        {"data": [1, 2, 3]}, {"data": None}, {}):
            with self.subTest(payload=payload), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "test.db")
                class Payload:
                    def financials(self, cert): return payload
                result = FdicBankScoutService(store, client=Payload(), identities=[IDENTITY]).run(as_of=NOW)
                self.assertEqual("PARTIAL", result["status"])
                self.assertEqual(0, fdic_coverage(store, [IDENTITY], as_of=NOW,
                                                subjects={"JPM"})["current_usable_banks"])

    def test_canonical_scope_and_invalid_budgets_are_rejected_before_io(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeBankFind()
            scout = FdicBankScoutService(store, client=client, identities=[dict(IDENTITY, ticker="OUTSIDE")])
            self.assertEqual("WAIT_IDENTITY", scout.run(as_of=NOW, universe={"OUTSIDE"})["status"])
            for config in ({"max_banks": 0}, {"daily_request_budget": 51}, {"max_failures": True},
                           {"max_run_seconds": float("nan")}, {"max_run_seconds": 301}):
                with self.subTest(config=config), self.assertRaises(ValueError):
                    FdicBankScoutService(store, client=client, identities=[IDENTITY], **config)
            with self.assertRaises(ValueError):
                store.fdic_daily_requests(as_of=NOW.replace(tzinfo=None))
            self.assertEqual([], client.calls)

    def test_http_body_is_bounded_and_redirect_cannot_escape_official_host(self):
        class Response:
            host = "https://api.fdic.gov/banks/financials"
            body = b'{"data": []}'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.host
            def read(self, size):
                self.read_limit = size
                return self.body[:size]
        response = Response()
        with patch("market_checker_app.services.fdic_bank_scout_service.urlopen", return_value=response):
            self.assertEqual({"data": []}, FdicBankFindClient().financials(628))
            self.assertEqual(1_000_001, response.read_limit)
            response.body = b" " * 1_000_001
            with self.assertRaisesRegex(ValueError, "byte budget"):
                FdicBankFindClient().financials(628)
            response.host = "https://example.com/financials"
            with self.assertRaisesRegex(ValueError, "official host"):
                FdicBankFindClient().financials(628)


if __name__ == "__main__":
    unittest.main()
