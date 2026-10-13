"""Validate exact recall responses; captured HTTP400 empties are never negatives."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from market_checker_app.services.nhtsa_recall_scout_service import (
    MAX_RESPONSE_BYTES, NhtsaRecallClient, NhtsaRecallScoutService, SUCCESS_MESSAGE,
    recall_url,
)
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence" / "batch4_regulatory_20261013"
NOW = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
MODEL = {"ticker": "F", "manufacturer": "Ford Motor Company", "make": "FORD",
         "model": "F-150", "model_year": 2026,
         "product_evidence_url": "https://www.sec.gov/Archives/edgar/data/37996/000003799626000015/f-20251231.htm",
         "known_at": "2026-10-12T15:08:33.934642+00:00"}
ROW = {"Make": "FORD", "Model": "F-150", "ModelYear": "2026",
       "Manufacturer": "Ford Motor Company", "NHTSACampaignNumber": "26V104000",
       "ReportReceivedDate": "20/02/2026"}
EMPTY = {"Count": 0, "Message": SUCCESS_MESSAGE, "results": []}


def captures() -> dict[str, dict]:
    return {row["capture_id"]: row for row in
            json.loads((FOLDER / "capture_index.json").read_text(encoding="utf-8"))["captures"]}


def raw(capture_id: str) -> bytes:
    metadata = captures()[capture_id]
    if metadata.get("public_raw_available") is False or metadata["archive_path"] is None:
        raise ValueError("Capture body is withheld from the public tree")
    return (ROOT / metadata["archive_path"]).read_bytes()


class PayloadClient:
    def __init__(self, payload: object):
        self.payload, self.calls = payload, []

    def recalls(self, make: str, model: str, model_year: int) -> dict:
        self.calls.append((make, model, model_year))
        return deepcopy(self.payload)


class ArchivedResponse(BytesIO):
    """Only reads the exact stored response; no HTTP request is made by tests."""
    def __init__(self, body: bytes, url: str, *, status: int = 200,
                 content_type: str = "application/json;charset=UTF-8"):
        super().__init__(body)
        self.url, self.status = url, status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status


def counts(store: ScoutStore) -> tuple[int, list[tuple]]:
    with store._connect() as connection:
        findings = connection.execute("SELECT COUNT(*) FROM scout_findings WHERE source='nhtsa'").fetchone()[0]
        checks = connection.execute("SELECT candidate_count, truncated, checked_at FROM scout_specialist_checks "
                                    "WHERE source='nhtsa' ORDER BY identity_key").fetchall()
    return findings, [tuple(row) for row in checks]


class Batch4RegulatoryValidationTests(unittest.TestCase):
    def test_unsuccessful_empty_metadata_is_not_cached_and_can_retry(self) -> None:
        invalid = [
            {"Count": 0, "results": []},
            dict(EMPTY, Message="No results or server unavailable"),
            dict(EMPTY, Message=None),
            dict(EMPTY, Error="Database unavailable"),
            dict(EMPTY, errors=["timeout"]),
            dict(EMPTY, Count=False),
            dict(EMPTY, Count=1),
            dict(EMPTY, results={}),
            [],
        ]
        for payload in invalid:
            with self.subTest(payload=payload), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "check.db")
                client = PayloadClient(payload)
                service = NhtsaRecallScoutService(store, client=client, models=[MODEL])
                result = service.run(as_of=NOW)
                self.assertEqual(("PARTIAL", 0, 1),
                                 (result["status"], result["checked_models"], result["failed_models"]))
                self.assertEqual((0, []), counts(store))
                client.payload = deepcopy(EMPTY)
                self.assertEqual("OK", service.run(as_of=NOW)["status"])
                self.assertEqual(2, len(client.calls))
                self.assertEqual((0, [(0, 0, NOW.isoformat())]), counts(store))

    def test_successful_empty_fixture_checks_only_exact_model_and_refreshes(self) -> None:
        # Synthetic success contract, explicitly not a claimed live HTTP200 negative.
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "check.db")
            client = PayloadClient(EMPTY)
            service = NhtsaRecallScoutService(store, client=client, models=[MODEL])
            self.assertEqual("OK", service.run(as_of=NOW)["status"])
            self.assertEqual((0, [(0, 0, NOW.isoformat())]), counts(store))
            self.assertEqual(0, service.run(as_of=NOW + timedelta(days=1))["checked_models"])
            self.assertEqual(1, len(client.calls))
            self.assertEqual(1, service.run(as_of=NOW + timedelta(days=31))["checked_models"])
            self.assertEqual(2, len(client.calls))
            with store._connect() as connection:
                self.assertEqual({"F"}, {row[0] for row in connection.execute(
                    "SELECT subject_id FROM scout_specialist_checks")})

    def test_explicit_error_on_nonempty_results_keeps_previous_findings_and_check(self) -> None:
        for metadata in ({"Message": "Request failed"}, {"Error": "Publisher error"}):
            with self.subTest(metadata=metadata), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "check.db")
                client = PayloadClient({"Count": 1, "Message": SUCCESS_MESSAGE, "results": [ROW]})
                service = NhtsaRecallScoutService(store, client=client, models=[MODEL])
                self.assertEqual(1, service.run(as_of=NOW)["new_findings"])
                previous = counts(store)
                client.payload.update(metadata)
                failed = service.run(as_of=NOW + timedelta(days=31))
                self.assertEqual(("PARTIAL", 0, 1),
                                 (failed["status"], failed["checked_models"], failed["failed_models"]))
                self.assertEqual(previous, counts(store))

    def test_mismatched_row_keeps_matching_row_and_marks_partial_check(self) -> None:
        for wrong in (dict(ROW, ModelYear="2025"), dict(ROW, Manufacturer="Another Ford entity")):
            with self.subTest(wrong=wrong), TemporaryDirectory() as directory:
                store = ScoutStore(Path(directory) / "check.db")
                client = PayloadClient({"Count": 2, "Message": SUCCESS_MESSAGE, "results": [ROW, wrong]})
                service = NhtsaRecallScoutService(store, client=client, models=[MODEL])
                result = service.run(as_of=NOW)
                self.assertEqual(("PARTIAL", 1, 1),
                                 (result["status"], result["new_findings"], result["rejected_rows"]))
                self.assertEqual((1, [(1, 1, NOW.isoformat())]), counts(store))
                self.assertEqual(1, service.run(as_of=NOW + timedelta(days=1))["checked_models"])

    def test_corrected_manufacturer_evidence_and_legacy_checks_become_due(self) -> None:
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "check.db")
            old_key = ":".join((MODEL["make"], MODEL["model"], str(MODEL["model_year"]), MODEL["known_at"]))
            store.record_specialist_check("nhtsa", subject_id="F", identity_key=old_key,
                                          as_of=NOW, candidate_count=0, truncated=False)
            client = PayloadClient(EMPTY)
            for entry in (MODEL, dict(MODEL, manufacturer="Documented corrected manufacturer"),
                          dict(MODEL, product_evidence_url="https://www.sec.gov/Archives/edgar/data/37996/reviewed.htm")):
                service = NhtsaRecallScoutService(store, client=client, models=[entry])
                self.assertEqual(1, service.run(as_of=NOW)["checked_models"])
                self.assertEqual(0, service.run(as_of=NOW)["checked_models"])
            self.assertEqual(3, len(client.calls))
            self.assertEqual(4, len(counts(store)[1]))
            self.assertEqual(0, counts(store)[0])

    def test_exact_transport_rejects_other_official_route_scope_and_host(self) -> None:
        url = recall_url("FORD", "F-150", 2026)
        wrong_urls = [url.replace("F-150", "MUSTANG"), url.replace("modelYear=2026", "modelYear=2025"),
                      url.replace("/recalls/recallsByVehicle", "/complaints/complaintsByVehicle"),
                      url + "&modelYear=2025", url.replace("api.nhtsa.gov", "other.nhtsa.gov"),
                      url.replace("https:", "http:"), url + "#another-scope"]
        for wrong in wrong_urls:
            with self.subTest(url=wrong), patch(
                "market_checker_app.services.nhtsa_recall_scout_service.urlopen",
                return_value=ArchivedResponse(json.dumps(EMPTY).encode(), wrong)
            ):
                with self.assertRaisesRegex(ValueError, "exact official query"):
                    NhtsaRecallClient().recalls("FORD", "F-150", 2026)

    def test_equivalent_official_query_order_is_allowed_without_changing_scope(self) -> None:
        url = "https://api.nhtsa.gov/recalls/recallsByVehicle?modelYear=2026&model=F-150&make=FORD"
        with patch("market_checker_app.services.nhtsa_recall_scout_service.urlopen",
                   return_value=ArchivedResponse(json.dumps(EMPTY).encode(), url)):
            self.assertEqual(EMPTY, NhtsaRecallClient().recalls("FORD", "F-150", 2026))

    def test_transport_requires_http200_json_bounded_unique_envelope(self) -> None:
        url = recall_url("FORD", "F-150", 2026)
        cases = [
            (json.dumps(EMPTY).encode(), 206, "application/json", "HTTP 200"),
            (json.dumps(EMPTY).encode(), 200, "text/html", "not JSON"),
            (b" " * (MAX_RESPONSE_BYTES + 1), 200, "application/json", "byte bound"),
            (b'{"Count":1,"Count":0,"Message":"Results returned successfully","results":[]}',
             200, "application/json", "duplicate key"),
        ]
        for body, status, content_type, error in cases:
            with self.subTest(error=error), patch(
                "market_checker_app.services.nhtsa_recall_scout_service.urlopen",
                return_value=ArchivedResponse(body, url, status=status, content_type=content_type)
            ):
                with self.assertRaisesRegex(ValueError, error):
                    NhtsaRecallClient().recalls("FORD", "F-150", 2026)

    def test_actual_captured_http400_empties_have_no_complete_check(self) -> None:
        for capture_id, make, model in (
            ("nhtsa_recalls_cadillac_ct4_2026", "CADILLAC", "CT4"),
            ("nhtsa_recalls_ford_f650sd_2026", "FORD", "F-650 SD"),
        ):
            # Request-envelope tests only. Neither model is added to issuer registry.
            with self.subTest(capture_id=capture_id), TemporaryDirectory() as directory:
                metadata = captures()[capture_id]
                self.assertEqual((400, EMPTY), (metadata["http_status"], json.loads(raw(capture_id))))
                entry = dict(MODEL, make=make, model=model)
                store = ScoutStore(Path(directory) / "check.db")
                error = HTTPError(metadata["requested_url"], metadata["http_status"],
                                  "Captured HTTP error", None, BytesIO(raw(capture_id)))
                with patch("market_checker_app.services.nhtsa_recall_scout_service.urlopen", side_effect=error) as http:
                    service = NhtsaRecallScoutService(store, client=NhtsaRecallClient(), models=[entry])
                    for _ in range(2):
                        result = service.run(as_of=NOW)
                        self.assertEqual(("PARTIAL", 0, 1),
                                         (result["status"], result["checked_models"], result["failed_models"]))
                    self.assertEqual(2, http.call_count)
                self.assertEqual((0, []), counts(store))

    def test_actual_positive_transport_and_production_collector_replay(self) -> None:
        for capture_id, ticker, make, model, manufacturer, expected in (
            ("nhtsa_recalls_ford_mustang_2026", "F", "FORD", "MUSTANG", "Ford Motor Company", 5),
            ("nhtsa_recalls_chevrolet_trailblazer_2026", "GM", "CHEVROLET", "TRAILBLAZER", "General Motors, LLC", 1),
        ):
            # Test-local request identity; no new issuer/product adoption is asserted.
            with self.subTest(capture_id=capture_id), TemporaryDirectory() as directory:
                metadata = captures()[capture_id]
                entry = dict(MODEL, ticker=ticker, make=make, model=model, manufacturer=manufacturer,
                             known_at=NOW.isoformat())
                if ticker == "GM":
                    entry["product_evidence_url"] = (
                        "https://www.sec.gov/Archives/edgar/data/1467858/000119312526344698/d145571d8k.htm")
                store = ScoutStore(Path(directory) / "check.db")
                with patch("market_checker_app.services.nhtsa_recall_scout_service.urlopen", return_value=
                           ArchivedResponse(raw(capture_id), metadata["final_url"],
                                            status=metadata["http_status"], content_type=metadata["content_type"])):
                    result = NhtsaRecallScoutService(store, client=NhtsaRecallClient(), models=[entry]).run(as_of=NOW)
                self.assertEqual(("OK", expected, 0),
                                 (result["status"], result["new_findings"], result["rejected_rows"]))
                self.assertEqual((expected, [(expected, 0, NOW.isoformat())]), counts(store))
                with store._connect() as connection:
                    for row in connection.execute("SELECT available_at, details_json FROM scout_findings"):
                        self.assertEqual(NOW.isoformat(), row["available_at"])
                        self.assertFalse(json.loads(row["details_json"])["scoring_applied"])

    def test_capture_bytes_metadata_exact_models_and_honest_negative_limits(self) -> None:
        index = captures()
        self.assertEqual(8, len(index))
        self.assertEqual((6, 2), (sum(r["http_status"] == 200 for r in index.values()),
                                  sum(r["http_status"] == 400 for r in index.values())))
        for capture_id, metadata in index.items():
            with self.subTest(capture_id=capture_id):
                self.assertEqual(metadata["archive_sha256"], metadata["raw_response_sha256"])
                self.assertLessEqual(metadata["raw_response_bytes"], metadata["max_response_bytes"])
                self.assertEqual(metadata["requested_url"], metadata["final_url"])
                self.assertIn(urlsplit(metadata["final_url"]).hostname,
                              {"api.nhtsa.gov", "vpic.nhtsa.dot.gov", "www.nhtsa.gov"})
                self.assertLessEqual(datetime.fromisoformat(metadata["started_at"]),
                                     datetime.fromisoformat(metadata["observed_at"]))
                if capture_id == "nhtsa_api_docs":
                    self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", metadata["outcome"])
                    self.assertIsNone(metadata["archive_path"])
                    self.assertFalse(metadata["public_raw_available"])
                    self.assertFalse(metadata["acceptance_claim_allowed"])
                    continue
                # All seven public model/recall bodies retain their exact
                # original bytes, size and hashes; none use the withheld branch.
                self.assertIn(metadata["outcome"], {"HTTP_SUCCESS", "HTTP_ERROR"})
                body = raw(capture_id)
                self.assertEqual(len(body), metadata["raw_response_bytes"])
                self.assertEqual(hashlib.sha256(body).hexdigest(), metadata["archive_sha256"])
                if metadata.get("results_field"):
                    payload = json.loads(body)
                    self.assertEqual(metadata["result_rows"], len(payload[metadata["results_field"]]))
        for capture_id, make, model, model_id in (
            ("nhtsa_models_cadillac_2026", "CADILLAC", "CT4", 26602),
            ("nhtsa_models_ford_2026", "FORD", "Mustang", 1781),
            ("nhtsa_models_chevrolet_2026", "CHEVROLET", "Trailblazer", 4548),
        ):
            payload = json.loads(raw(capture_id))
            self.assertEqual(f"Make:{make} | ModelYear:2026", payload["SearchCriteria"])
            self.assertTrue(any(r["Make_Name"] == make and r["Model_Name"] == model and
                                r["Model_ID"] == model_id for r in payload["Results"]))
        self.assertFalse(any(r["http_status"] == 200 and r.get("payload_Count") == 0
                             for r in index.values() if r["capture_id"].startswith("nhtsa_recalls_")))

    def test_documentation_withholding_is_explicit_and_cannot_supply_raw_acceptance(self) -> None:
        index = captures()
        withheld = {capture_id for capture_id, metadata in index.items()
                    if metadata.get("publication_status") == "WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE"}
        self.assertEqual({"nhtsa_api_docs"}, withheld)
        self.assertEqual(7, sum(metadata["archive_path"] is not None for metadata in index.values()))
        metadata = index["nhtsa_api_docs"]
        self.assertEqual(metadata, json.loads(
            (FOLDER / "raw" / "nhtsa_api_docs.metadata.json").read_text(encoding="utf-8")))
        self.assertEqual(("HTTP_SUCCESS", 200), (metadata["capture_outcome"], metadata["http_status"]))
        self.assertEqual((147164, 147164), (metadata["archive_bytes"], metadata["raw_response_bytes"]))
        self.assertEqual("84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
                         metadata["raw_response_sha256"])
        self.assertEqual("https://www.nhtsa.gov/nhtsa-datasets-and-apis", metadata["requested_url"])
        self.assertFalse(metadata["publicly_available"])
        self.assertFalse(metadata["acceptance_claim_allowed"])
        self.assertIsNone(metadata["archive_path"])
        self.assertFalse((FOLDER / "raw" / "nhtsa_api_docs.html").exists())
        with self.assertRaisesRegex(ValueError, "withheld"):
            raw("nhtsa_api_docs")


if __name__ == "__main__":
    unittest.main()
