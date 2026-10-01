from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_checker_app.services.fdic_bank_scout_service import (
    FdicBankScoutService, load_verified_banks,
)
from market_checker_app.storage.scout_store import ScoutStore


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
            self.assertEqual("PARTIAL", scout.run(as_of=NOW)["status"])
            client.row = dict(row, CERT=3510)
            self.assertEqual(0, scout.run(as_of=NOW)["usable_banks"])
            client.row = dict(row, REPDTE="2026-06-30junk")
            self.assertEqual(0, scout.run(as_of=NOW)["usable_banks"])
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
        self.assertEqual({("JPM", 628), ("BAC", 3510), ("WFC", 3511), ("C", 7213)},
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
                                    identities=[IDENTITY]).run(as_of=NOW)["new_findings"])
            failed = FdicBankScoutService(store, client=FakeBankFind(error=True),
                                          identities=[IDENTITY]).run(as_of=NOW)
            self.assertEqual(("PARTIAL", 1), (failed["status"], failed["failed_banks"]))
            compact_date = dict(rows[1], NAME=IDENTITY["bank_name"], REPDTE="20260630")
            self.assertEqual(1, FdicBankScoutService(store, client=FakeBankFind(compact_date),
                                identities=[IDENTITY]).run(as_of=NOW)["new_findings"])

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


if __name__ == "__main__":
    unittest.main()
