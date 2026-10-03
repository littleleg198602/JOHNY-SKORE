from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from market_checker_app.services.usaspending_scout_service import (
    UsaSpendingScoutService, load_verified_identities, DEFAULT_IDENTITIES,
)
from market_checker_app.storage.scout_store import ScoutStore


CLOCK = datetime(2026, 9, 30, tzinfo=timezone.utc)
IDENTITY = {"ticker": "LMT", "uei": "ABCDEFGHIJKL",
            "recipient_name": "TEST RECIPIENT INC",
            "uei_evidence_url": "https://www.usaspending.gov/recipient/example",
            "relationship_evidence_url": "https://www.sec.gov/Archives/edgar/data/example",
            "effective_from": "2020-01-01", "effective_to": None,
            "known_at": "2026-09-01T00:00:00+00:00"}


class StubAwards:
    def __init__(self):
        self.calls = []
        self.amount = 100

    def awards(self, uei, *, start, end, page):
        self.calls.append((uei, start, end, page))
        row = {"generated_internal_id": "CONT_A_123", "Award ID": "123",
               "Recipient UEI": uei, "Recipient Name": "TEST RECIPIENT INC",
               "Start Date": "2026-08-01", "Award Amount": self.amount,
               "Total Outlays": 10, "Last Modified Date": "2026-09-29"}
        return {"results": [row, {**row, "Recipient UEI": "ZZZZZZZZZZZZ",
                                  "generated_internal_id": "CONT_A_BAD"},
                            {**row, "Start Date": "2019-01-01",
                             "generated_internal_id": "CONT_A_OLD"}],
                "page_metadata": {"hasNext": False}}


class UsaSpendingScoutTests(unittest.TestCase):
    def test_documented_sikorsky_subsidiary_uei_is_scoped_to_lmt(self):
        entries = load_verified_identities(DEFAULT_IDENTITIES)
        sikorsky = next(entry for entry in entries if entry["uei"] == "UTJWTSLMFNG4")
        self.assertEqual("LMT", sikorsky["ticker"])
        self.assertEqual("2015-11-06", sikorsky["effective_from"])
        self.assertIn("sec.gov/Archives/", sikorsky["continuity_evidence_url"])

        class SikorskyAward:
            def awards(self, uei, *, start, end, page):
                return {"results": [{"generated_internal_id":
                         "CONT_AWD_SPE4A125F1406_9700_SPE4A122G0005_9700",
                         "Award ID": "SPE4A125F1406", "Recipient UEI": uei,
                         "Recipient Name": "SIKORSKY AIRCRAFT CORPORATION",
                         "Start Date": "2025-08-28", "Award Amount": 4162.13,
                         "Total Outlays": 0.0}],
                        "page_metadata": {"hasNext": False}}
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            service = UsaSpendingScoutService(store, client=SikorskyAward(),
                                               identities=[sikorsky])
            before = datetime(2026, 9, 30, 12, 15, tzinfo=timezone.utc)
            after = datetime(2026, 9, 30, 12, 16, tzinfo=timezone.utc)
            self.assertEqual("WAIT_IDENTITY", service.run(as_of=before)["status"])
            self.assertEqual(1, service.run(as_of=after, universe={"LMT"})["new_findings"])
            self.assertEqual(0, len(store.findings_as_of("LMT", as_of=before)))
            finding = store.findings_as_of("LMT", as_of=after)[0]
            detail = json.loads(finding["details_json"])
            self.assertEqual(4162.13, detail["reported_award_amount_usd"])
            self.assertEqual(0.0, detail["reported_total_outlays_usd"])
            self.assertEqual(sikorsky["continuity_evidence_url"],
                             detail["continuity_evidence_url"])
            self.assertEqual("2015-11-06", detail["relationship_effective_from"])
            self.assertIsNone(detail["relationship_effective_to"])

    def test_documented_northrop_subsidiary_uei_is_scoped_to_noc(self):
        entries = load_verified_identities(DEFAULT_IDENTITIES)
        northrop = next(entry for entry in entries if entry["uei"] == "LCV2N9FVV739")
        self.assertEqual("NOC", northrop["ticker"])
        self.assertEqual("NORTHROP GRUMMAN SYSTEMS CORPORATION",
                         northrop["recipient_name"])
        self.assertEqual("2025-12-31", northrop["effective_from"])
        self.assertIn("000113342126000003", northrop["relationship_evidence_url"])

        class NorthropAward:
            def awards(self, uei, *, start, end, page):
                return {"results": [{"generated_internal_id":
                         "CONT_AWD_SPE4A525F6417_9700_SPE4A122G0004_9700",
                         "Award ID": "SPE4A525F6417", "Recipient UEI": uei,
                         "Recipient Name": "NORTHROP GRUMMAN SYSTEMS CORPORATION",
                         "Start Date": "2026-06-23", "Award Amount": 9984595.0,
                         "Total Outlays": 0.0}],
                        "page_metadata": {"hasNext": False}}

        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            service = UsaSpendingScoutService(store, client=NorthropAward(),
                                               identities=[northrop])
            before = datetime(2026, 10, 2, 19, 4, tzinfo=timezone.utc)
            after = datetime(2026, 10, 2, 19, 5, tzinfo=timezone.utc)
            self.assertEqual("WAIT_IDENTITY", service.run(as_of=before)["status"])
            self.assertEqual(1, service.run(as_of=after, universe={"NOC"})["new_findings"])
            detail = json.loads(store.findings_as_of("NOC", as_of=after)[0]["details_json"])
            self.assertEqual("2025-12-31", detail["relationship_effective_from"])
            self.assertIsNone(detail["relationship_effective_to"])
            self.assertEqual(northrop["known_at"], detail["relationship_known_at"])

    def test_exact_uei_relationship_dates_revisions_and_point_in_time(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            client = StubAwards()
            service = UsaSpendingScoutService(store, client=client, identities=[IDENTITY])
            self.assertEqual("WAIT_IDENTITY", service.run(as_of=CLOCK, universe={"OTHER"})["status"])
            self.assertEqual(1, service.run(as_of=CLOCK, universe={"LMT"})["new_findings"])
            self.assertEqual(0, service.run(as_of=CLOCK, universe={"LMT"})["new_findings"])
            client.amount = 120
            later = datetime(2026, 10, 1, tzinfo=timezone.utc)
            self.assertEqual(1, service.run(as_of=later, universe={"LMT"})["new_findings"])
            self.assertEqual(1, len(store.findings_as_of("LMT", as_of=CLOCK)))
            findings = store.findings_as_of("LMT", as_of=later)
            self.assertEqual(2, len(findings))
            amounts = {json.loads(row["details_json"])["reported_award_amount_usd"]
                       for row in findings}
            self.assertEqual({100, 120}, amounts)
            self.assertTrue(all(not json.loads(row["details_json"])["revenue_inferred"]
                                for row in findings))
            self.assertEqual("ABCDEFGHIJKL", client.calls[0][0])

    def test_manifest_requires_independent_cited_dated_link(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "uei.json"
            path.write_text(json.dumps([IDENTITY]), encoding="utf-8")
            self.assertEqual([IDENTITY], load_verified_identities(path))
            path.write_text(json.dumps([{**IDENTITY, "relationship_evidence_url": "http://bad"}]))
            with self.assertRaises(ValueError):
                load_verified_identities(path)

    def test_truncation_is_explicit_and_foreign_host_is_rejected(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "test.db")
            class Pages(StubAwards):
                def awards(self, uei, *, start, end, page):
                    return {"results": [], "page_metadata": {"hasNext": True}}
            result = UsaSpendingScoutService(store, client=Pages(),
                                              identities=[IDENTITY], max_pages=2).run(as_of=CLOCK)
            self.assertEqual("PARTIAL", result["status"])
            with self.assertRaisesRegex(ValueError, "official API"):
                store.record_finding(source="usaspending", subject_id="LMT",
                    source_object_id="x", content_hash="a", title="x",
                    source_url="https://api.usaspending.gov.example.com/award/x",
                    locator="x", published_at=CLOCK, available_at=CLOCK,
                    observed_at=CLOCK, details={})


if __name__ == "__main__":
    unittest.main()
