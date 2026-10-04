from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from market_checker_app.services.sec13f_scout_service import (
    Sec13fScoutService, load_verified_securities,
)
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
URL = "https://www.sec.gov/files/datastandardsinnovation/data/form-13f-data-sets/01jun2026-31aug2026_form13f.zip"
SECURITY = {
    "ticker": "AAPL", "issuer_cik": "0000320193", "cusip": "037833100", "issuer_name": "APPLE INC",
    "class_description": "COM",
    "cusip_evidence_url": "https://www.sec.gov/files/investment/13flist2026q2-txt.txt",
    "instrument_evidence_url": "https://www.sec.gov/Archives/edgar/data/102909/000010290926000630/xslSCHEDULE_13G_X02/primary_doc.xml",
    "ticker_evidence_url": "https://www.sec.gov/files/company_tickers.json",
    "effective_from": "2026-04-01", "effective_to": "2026-06-30",
    "known_at": "2026-09-30T20:15:51+00:00",
}


def sample_zip():
    accession = "0001108893-26-000003"
    submission = ("ACCESSION_NUMBER\tFILING_DATE\tSUBMISSIONTYPE\tCIK\tPERIODOFREPORT\n"
                  f"{accession}\t15-MAY-2026\t13F-HR\t1108893\t31-MAR-2026\n"
                  "0001108893-26-000004\t15-AUG-2026\t13F-HR\t1108893\t30-JUN-2026\n"
                  "0001108893-26-000005\t16-AUG-2026\t13F-HR/A\t1108893\t30-JUN-2026\n")
    coverpage = ("ACCESSION_NUMBER\tISAMENDMENT\tAMENDMENTNO\tAMENDMENTTYPE\n"
                 "0001108893-26-000003\t\t\t\n"
                 "0001108893-26-000004\t\t\t\n"
                 "0001108893-26-000005\tY\t1\tRESTATEMENT\n")
    header = "ACCESSION_NUMBER\tINFOTABLE_SK\tCUSIP\tNAMEOFISSUER\tTITLEOFCLASS\tVALUE\tSSHPRNAMT\tSSHPRNAMTTYPE\tPUTCALL\n"
    rows = [
        "0001108893-26-000004\t1\t037833100\tAPPLE, INC.\tCOM\t45539635\t179438\tSH\t",
        "0001108893-26-000004\t2\t037833900\tAPPLE INC\tCALL\t100\t10\tSH\tCALL",
        "0001108893-26-000005\t3\t037833100\tAPPLE INC\tCOM\t100\t10\tSH\t",
        "0001108893-26-000004\t4\t037833100\tOTHER INC\tCOM\t100\t10\tSH\t",
    ]
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("SUBMISSION.tsv", submission)
        archive.writestr("COVERPAGE.tsv", coverpage)
        archive.writestr("INFOTABLE.tsv", header + "\n".join(rows) + "\n")
    return stream.getvalue()


def amendment_zip(*, amendment_type="NEW HOLDINGS", amendment_number=1, include_initial=True):
    initial = "0001108893-26-000004"
    amendment = "0001108893-26-000005"
    submission = "ACCESSION_NUMBER\tFILING_DATE\tSUBMISSIONTYPE\tCIK\tPERIODOFREPORT\n"
    if include_initial:
        submission += f"{initial}\t15-AUG-2026\t13F-HR\t1108893\t30-JUN-2026\n"
    submission += f"{amendment}\t16-AUG-2026\t13F-HR/A\t1108893\t30-JUN-2026\n"
    coverpage = "ACCESSION_NUMBER\tISAMENDMENT\tAMENDMENTNO\tAMENDMENTTYPE\n"
    if include_initial:
        coverpage += f"{initial}\t\t\t\n"
    coverpage += f"{amendment}\tY\t{amendment_number}\t{amendment_type}\n"
    info = ("ACCESSION_NUMBER\tINFOTABLE_SK\tCUSIP\tNAMEOFISSUER\tTITLEOFCLASS\t"
            "VALUE\tSSHPRNAMT\tSSHPRNAMTTYPE\tPUTCALL\n")
    if include_initial:
        info += f"{initial}\t1\t037833100\tAPPLE INC\tCOM\t200\t20\tSH\t\n"
    info += f"{amendment}\t2\t037833100\tAPPLE INC\tCOM\t100\t10\tSH\t\n"
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("SUBMISSION.tsv", submission)
        archive.writestr("COVERPAGE.tsv", coverpage)
        archive.writestr("INFOTABLE.tsv", info)
    return stream.getvalue()


class FakeClient:
    def __init__(self, payload=None):
        self.downloads = 0
        self.payload = payload or sample_zip()

    def latest_url(self):
        return URL

    def dataset(self, url):
        self.downloads += 1
        return self.payload


class Sec13fTests(unittest.TestCase):
    def test_production_manifest_has_twenty_five_cited_canonical_instruments(self):
        securities = load_verified_securities()
        self.assertEqual(
            {
                ("AAPL", "0000320193", "037833100", "COM"),
                ("MSFT", "0000789019", "594918104", "COM"),
                ("NVDA", "0001045810", "67066G104", "COM"),
                ("AMZN", "0001018724", "023135106", "COM"),
                ("META", "0001326801", "30303M102", "CL A"),
                ("GOOGL", "0001652044", "02079K305", "CAP STK CL A"),
                ("TSLA", "0001318605", "88160R101", "COM"),
                ("AVGO", "0001730168", "11135F101", "COM"),
                ("AMD", "0000002488", "007903107", "COM"),
                ("JPM", "0000019617", "46625H100", "COM"),
                ("V", "0001403161", "92826C839", "COM CL A"),
                ("MA", "0001141391", "57636Q104", "CL A"),
                ("JNJ", "0000200406", "478160104", "COM"),
                ("XOM", "0000034088", "30231G102", "COM"),
                ("WMT", "0000104169", "931142103", "COM"),
                ("LLY", "0000059478", "532457108", "COM"),
                ("INTC", "0000050863", "458140100", "COM"),
                ("CSCO", "0000858877", "17275R102", "COM"),
                ("ABBV", "0001551152", "00287Y109", "COM"),
                ("DIS", "0001744489", "254687106", "COM"),
                ("BAC", "0000070858", "060505104", "COM"),
                ("PFE", "0000078003", "717081103", "COM"),
                ("KO", "0000021344", "191216100", "COM"),
                ("ORCL", "0001341439", "68389X105", "COM"),
                ("NFLX", "0001065280", "64110L106", "COM"),
            },
            {(entry["ticker"], entry["issuer_cik"], entry["cusip"],
              entry["class_description"]) for entry in securities},
        )

    def test_bounded_dated_ingest_and_no_repeat_download(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "securities.json"
            path.write_text(json.dumps([SECURITY]), encoding="utf-8")
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeClient()
            scout = Sec13fScoutService(store, client=client,
                                       securities=load_verified_securities(path))
            waiting = scout.run(as_of=NOW - timedelta(hours=2))
            self.assertEqual("WAIT_IDENTITY", waiting["status"])
            self.assertEqual(0.0, waiting["identity_coverage_ratio"])
            ingested = scout.run(as_of=NOW, universe={"AAPL", "MSFT", "ZZZ"})
            self.assertEqual(1, ingested["new_findings"])
            self.assertEqual("requested_universe", ingested["identity_coverage_basis"])
            self.assertEqual(3, ingested["identity_requested_tickers"])
            self.assertEqual(1, ingested["identity_mapped_tickers"])
            self.assertEqual(2, ingested["identity_unmapped_tickers"])
            self.assertEqual(0.333333, ingested["identity_coverage_ratio"])
            current = scout.run(as_of=NOW + timedelta(days=1), universe={"AAPL", "MSFT"})
            self.assertEqual("CURRENT", current["status"])
            self.assertEqual(0.5, current["identity_coverage_ratio"])
            self.assertEqual(1, client.downloads)
            with store._connect() as conn:
                rows = conn.execute("SELECT source_url, published_at, details_json FROM scout_findings "
                                    "WHERE source='sec13f'").fetchall()
            self.assertEqual(1, len(rows))
            self.assertIn("/Archives/edgar/data/1108893/", rows[0]["source_url"])
            details = json.loads(rows[0]["details_json"])
            self.assertEqual(100, details["as_filed_value_usd"])
            self.assertEqual("2026-06-30", details["period_of_report"])
            self.assertTrue(details["amendments_included"])
            self.assertEqual("13F-HR/A", details["submission_type"])
            self.assertEqual(["0001108893-26-000005"], details["effective_filing_accessions"])
            self.assertEqual(
                ["0001108893-26-000004", "0001108893-26-000005"],
                details["filing_chain_accessions"],
            )
            self.assertEqual(NOW.isoformat(), rows[0]["published_at"])

    def test_new_holdings_amendment_adds_to_initial_without_superseding_it(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "new-holdings.db")
            result = Sec13fScoutService(
                store, client=FakeClient(amendment_zip()), securities=[SECURITY]
            ).run(as_of=NOW, universe={"AAPL"})
            self.assertEqual("OK", result["status"])
            self.assertEqual(1, result["reconstructed_amendment_groups"])
            self.assertEqual(2, result["new_findings"])
            with store._connect() as conn:
                details = [json.loads(row[0]) for row in conn.execute(
                    "SELECT details_json FROM scout_findings WHERE source='sec13f' "
                    "ORDER BY source_object_id"
                ).fetchall()]
            self.assertEqual([200, 100], [row["as_filed_value_usd"] for row in details])
            self.assertTrue(all(row["amendments_included"] for row in details))
            self.assertTrue(all(len(row["effective_filing_accessions"]) == 2 for row in details))

    def test_orphan_and_unknown_amendments_fail_closed(self):
        for name, payload in (
            ("orphan", amendment_zip(include_initial=False)),
            ("unknown", amendment_zip(amendment_type="OTHER")),
            ("missing-prior-amendment", amendment_zip(amendment_number=2)),
        ):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                result = Sec13fScoutService(
                    ScoutStore(Path(directory) / f"{name}.db"),
                    client=FakeClient(payload), securities=[SECURITY],
                ).run(as_of=NOW, universe={"AAPL"})
                self.assertEqual("PARTIAL", result["status"])
                self.assertEqual(1, result["unresolved_amendment_groups"])
                self.assertEqual(0, result["new_findings"])

    def test_manifest_and_untrusted_url_fail_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "securities.json"
            path.write_text(json.dumps([dict(SECURITY, cusip_evidence_url="https://example.org/list")]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "official SEC"):
                load_verified_securities(path)
            store = ScoutStore(Path(directory) / "test.db")
            with self.assertRaisesRegex(ValueError, "official EDGAR"):
                store.record_finding(source="sec13f", subject_id="AAPL", source_object_id="x",
                                     content_hash="x", title="13F", source_url="https://example.org/filing",
                                     locator="x", published_at=NOW, available_at=NOW,
                                     observed_at=NOW, details={})

    def test_bad_cusip_check_digit_and_wrong_dataset_class_fail_closed(self):
        with TemporaryDirectory() as directory:
            bad_path = Path(directory) / "bad.json"
            bad_path.write_text(json.dumps([dict(SECURITY, cusip="037833101")]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "canonical ticker and CUSIP"):
                load_verified_securities(bad_path)

            class_path = Path(directory) / "class.json"
            class_path.write_text(
                json.dumps([dict(SECURITY, class_description="CL A")]), encoding="utf-8"
            )
            store = ScoutStore(Path(directory) / "class.db")
            result = Sec13fScoutService(
                store, client=FakeClient(), securities=load_verified_securities(class_path)
            ).run(as_of=NOW, universe={"AAPL"})
            self.assertEqual(0, result["new_findings"])
            self.assertEqual(0, result["matched_rows"])


if __name__ == "__main__":
    unittest.main()
