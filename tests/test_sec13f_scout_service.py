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
    "ticker": "AAPL", "cusip": "037833100", "issuer_name": "APPLE INC",
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
        archive.writestr("INFOTABLE.tsv", header + "\n".join(rows) + "\n")
    return stream.getvalue()


class FakeClient:
    def __init__(self):
        self.downloads = 0

    def latest_url(self):
        return URL

    def dataset(self, url):
        self.downloads += 1
        return sample_zip()


class Sec13fTests(unittest.TestCase):
    def test_bounded_dated_ingest_and_no_repeat_download(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "securities.json"
            path.write_text(json.dumps([SECURITY]), encoding="utf-8")
            store = ScoutStore(Path(directory) / "test.db")
            client = FakeClient()
            scout = Sec13fScoutService(store, client=client,
                                       securities=load_verified_securities(path))
            self.assertEqual("WAIT_IDENTITY", scout.run(as_of=NOW - timedelta(hours=2))["status"])
            self.assertEqual(1, scout.run(as_of=NOW, universe={"AAPL"})["new_findings"])
            self.assertEqual("CURRENT", scout.run(as_of=NOW + timedelta(days=1))["status"])
            self.assertEqual(1, client.downloads)
            with store._connect() as conn:
                rows = conn.execute("SELECT source_url, published_at, details_json FROM scout_findings "
                                    "WHERE source='sec13f'").fetchall()
            self.assertEqual(1, len(rows))
            self.assertIn("/Archives/edgar/data/1108893/", rows[0]["source_url"])
            details = json.loads(rows[0]["details_json"])
            self.assertEqual(45539635, details["as_filed_value_usd"])
            self.assertEqual("2026-06-30", details["period_of_report"])
            self.assertFalse(details["amendments_included"])
            self.assertEqual(NOW.isoformat(), rows[0]["published_at"])

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


if __name__ == "__main__":
    unittest.main()
