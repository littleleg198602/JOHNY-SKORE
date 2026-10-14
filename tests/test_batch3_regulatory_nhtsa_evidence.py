"""Offline checks of captured primary evidence, never a new live acceptance run."""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime, timedelta
import gzip
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit

from market_checker_app.services.nhtsa_recall_scout_service import (
    NhtsaRecallScoutService, load_verified_models,
)
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence" / "batch3_regulatory_20261012"


def proposal() -> dict:
    return json.loads((FOLDER / "proposal.json").read_text(encoding="utf-8"))


def captures() -> dict[str, dict]:
    rows = json.loads((FOLDER / "capture_index.json").read_text(encoding="utf-8"))["captures"]
    return {row["capture_id"]: row for row in rows}


def captured_body(capture_id: str) -> bytes:
    metadata = captures()[capture_id]
    body = (ROOT / metadata["archive_path"]).read_bytes()
    return gzip.decompress(body) if metadata.get("archive_encoding", "").startswith("gzip") else body


def captured_json(capture_id: str) -> dict:
    return json.loads(captured_body(capture_id))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def captured_html_text(capture_id: str) -> str:
    parser = _Text()
    parser.feed(captured_body(capture_id).decode("utf-8"))
    return " ".join(" ".join(parser.parts).split())


class ArchivedRecalls:
    """Replay exact bytes/status from the live captures; no network method exists."""

    SCOPES = {
        ("FORD", "F-150", 2026): "nhtsa_recalls_ford_f150_2026",
        ("CHEVROLET", "SILVERADO 1500", 2026): "nhtsa_recalls_chevrolet_silverado1500_2026",
        ("FORD", "F-650 SD", 2026): "nhtsa_recalls_ford_f650sd_2026",
        ("CADILLAC", "CT4", 2026): "nhtsa_recalls_cadillac_ct4_2026",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def recalls(self, make: str, model: str, model_year: int) -> dict:
        key = (make, model, model_year)
        self.calls.append(key)
        capture_id = self.SCOPES[key]
        metadata = captures()[capture_id]
        if metadata["http_status"] != 200:
            raise HTTPError(metadata["requested_url"], metadata["http_status"],
                            "Captured HTTP error", None, BytesIO(captured_body(capture_id)))
        return deepcopy(captured_json(capture_id))


def finding_details(store: ScoutStore) -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute("SELECT details_json FROM scout_findings WHERE source='nhtsa'").fetchall()
    return [json.loads(row["details_json"]) for row in rows]


class Batch3RegulatoryEvidenceTests(unittest.TestCase):
    def test_archive_bytes_official_hosts_counts_and_observation_clocks(self) -> None:
        manifest = proposal()
        index = captures()
        self.assertEqual(24, len(index))
        self.assertEqual((22, 2), (sum(m["http_status"] == 200 for m in index.values()),
                                   sum(m["http_status"] == 400 for m in index.values())))
        review_clock = datetime.fromisoformat(manifest["reviewed_at"])
        official_hosts = {"api.nhtsa.gov", "vpic.nhtsa.dot.gov", "static.nhtsa.gov",
                          "www.sec.gov", "data.sec.gov"}
        for capture_id, metadata in index.items():
            with self.subTest(capture_id=capture_id):
                self.assertEqual(metadata, json.loads(
                    (FOLDER / "raw" / f"{capture_id}.metadata.json").read_text()))
                parsed = urlsplit(metadata["requested_url"])
                self.assertEqual("https", parsed.scheme)
                self.assertIn(parsed.hostname, official_hosts)
                self.assertEqual(metadata["requested_url"], metadata.get("final_url", metadata["requested_url"]))
                archive = (ROOT / metadata["archive_path"]).read_bytes()
                self.assertEqual(len(archive), metadata["archive_bytes"])
                self.assertEqual(hashlib.sha256(archive).hexdigest(), metadata["archive_sha256"])
                body = captured_body(capture_id)
                self.assertEqual(hashlib.sha256(body).hexdigest(),
                                 metadata.get("raw_response_sha256", metadata["archive_sha256"]))
                if "raw_response_bytes" in metadata:
                    self.assertEqual(len(body), metadata["raw_response_bytes"])
                self.assertLessEqual(datetime.fromisoformat(metadata["started_at"]),
                                     datetime.fromisoformat(metadata["observed_at"]))
                self.assertLessEqual(datetime.fromisoformat(metadata["observed_at"]), review_clock)
                if metadata["archive_path"].endswith(".json"):
                    payload = json.loads(body)
                    if metadata.get("results_field"):
                        self.assertEqual(metadata["count"], len(payload[metadata["results_field"]]))

    def test_proposed_manifest_is_exact_canonical_and_dated_without_invented_intervals(self) -> None:
        manifest = proposal()
        models = load_verified_models(FOLDER / "models.proposed.json")
        self.assertFalse(manifest["production_registry_modified"])
        self.assertEqual(2, manifest["proposed_records_count"])
        self.assertEqual({("F", "Ford Motor Company", "FORD", "F-150", 2026),
                          ("GM", "General Motors, LLC", "CHEVROLET", "SILVERADO 1500", 2026)},
                         {(r["ticker"], r["manufacturer"], r["make"], r["model"], r["model_year"])
                          for r in models})
        canonical_path = ROOT / manifest["canonical_universe"]["path"]
        with canonical_path.open(encoding="utf-8", newline="") as stream:
            universe = {row["ticker"] for row in csv.DictReader(stream)}
        self.assertEqual(687, len(universe))
        self.assertTrue({"F", "GM"} <= universe)
        self.assertEqual(manifest["canonical_universe"]["sha256"],
                         hashlib.sha256(canonical_path.read_bytes()).hexdigest())
        self.assertEqual(models, [r["service_record"] for r in manifest["records"]])
        for record in manifest["records"]:
            self.assertEqual(record["known_at"], record["reviewed_at"])
            self.assertEqual([2026], record["effective_knowledge"]["model_years"])
            self.assertIsNone(record["effective_knowledge"]["effective_from"])
            self.assertIsNone(record["effective_knowledge"]["effective_to"])
            self.assertFalse(record["effective_knowledge"]["historical_relationship_at_campaign_date_verified"])
            self.assertFalse(record["effective_knowledge"]["publication_time_from_report_date"])
            for item in record["evidence"]:
                metadata = captures()[item["capture_id"]]
                self.assertEqual(item["archive_sha256"], metadata["archive_sha256"])
                self.assertEqual(item["url"], metadata["requested_url"])
                self.assertEqual(item["observed_at"], metadata["observed_at"])
                self.assertLessEqual(datetime.fromisoformat(item["observed_at"]),
                                     datetime.fromisoformat(record["known_at"]))
                if item["purpose"].startswith("ISSUER") or item["purpose"] == "EXACT_MANUFACTURER_ISSUER_RELATIONSHIP":
                    self.assertTrue(urlsplit(item["url"]).path.startswith(
                        f"/Archives/edgar/data/{int(record['issuer_cik'])}/"))

    def test_primary_legal_manufacturer_to_issuer_chain_and_literal_recall_names(self) -> None:
        for capture_id, manufacturer_id, manufacturer_name, make_id, make_name in (
            ("nhtsa_manufacturer_ford_976", 976, "FORD MOTOR COMPANY", 460, "FORD"),
            ("nhtsa_manufacturer_gm_984", 984, "GENERAL MOTORS LLC", 467, "CHEVROLET"),
        ):
            payload = captured_json(capture_id)
            self.assertEqual(1, payload["Count"])
            row = payload["Results"][0]
            self.assertEqual((manufacturer_id, manufacturer_name, "UNITED STATES (USA)"),
                             (row["Mfr_ID"], row["Mfr_Name"], row["Country"]))
            makes = captured_json(f"nhtsa_makes_{'ford_976' if manufacturer_id == 976 else 'gm_984'}")
            self.assertIn({"Make_ID": make_id, "Make_Name": make_name, "Mfr_Name": manufacturer_name},
                          makes["Results"])
        ford = captured_html_text("sec_ford_2025_10k")
        self.assertIn("Ford Motor Company was incorporated in Delaware in 1919", ford)
        self.assertIn("lower F-150 wholesales", ford)
        self.assertIn("F New York Stock Exchange", ford)
        gm = captured_html_text("sec_gm_20260807_8k")
        self.assertIn("General Motors LLC (the “Coordinator”), a wholly owned subsidiary of the Company", gm)
        self.assertIn("GENERAL MOTORS COMPANY", gm)
        self.assertIn("GM New York Stock Exchange", gm)
        self.assertIn("General Motors LLC Delaware", captured_html_text("sec_gm_2025_ex21"))
        records = {row["service_record"]["ticker"]: row for row in proposal()["records"]}
        alias_review = records["GM"]["manufacturer_name_review"]
        self.assertTrue(alias_review["inference_explicit"])
        self.assertFalse(alias_review["automatic_punctuation_normalization_allowed"])
        self.assertEqual("General Motors, LLC", alias_review["recall_api_exact_name"])
        for ticker, capture_id, expected_count in (
            ("F", "nhtsa_recalls_ford_f150_2026", 5),
            ("GM", "nhtsa_recalls_chevrolet_silverado1500_2026", 3),
        ):
            model = records[ticker]["service_record"]
            payload = captured_json(capture_id)
            self.assertEqual(expected_count, payload["Count"])
            for row in payload["results"]:
                self.assertEqual((model["manufacturer"], model["make"], model["model"], "2026"),
                                 (row["Manufacturer"], row["Make"], row["Model"], row["ModelYear"]))
            self.assertEqual({r["NHTSACampaignNumber"] for r in payload["results"]},
                             {r["campaign"] for r in records[ticker]["live_scope_summary"]["campaigns"]})

    def test_replay_real_five_plus_three_uses_first_knowledge_and_persists_without_duplicate(self) -> None:
        models = load_verified_models(FOLDER / "models.proposed.json")
        clock = datetime.fromisoformat(models[0]["known_at"])
        with TemporaryDirectory() as directory:
            database = Path(directory) / "replay.db"
            store = ScoutStore(database)
            client = ArchivedRecalls()
            service = NhtsaRecallScoutService(store, client=client, models=models, max_models=2)
            before = service.run(as_of=clock - timedelta(microseconds=1), universe={"F", "GM"})
            self.assertEqual({"status": "WAIT_IDENTITY", "checked_models": 0, "new_findings": 0}, before)
            self.assertEqual([], client.calls)
            summary = service.run(as_of=clock, universe={"F", "GM"})
            self.assertEqual({"status": "OK", "checked_models": 2, "new_findings": 8,
                              "failed_models": 0, "rejected_rows": 0},
                             {key: summary[key] for key in
                              ("status", "checked_models", "new_findings", "failed_models", "rejected_rows")})
            self.assertEqual((2, 8), (summary["daily_requests"], summary["daily_request_limit"]))
            with store._connect() as conn:
                rows = conn.execute("SELECT subject_id, COUNT(*) AS n FROM scout_findings "
                                    "WHERE source='nhtsa' GROUP BY subject_id").fetchall()
                checks = conn.execute("SELECT subject_id, candidate_count, truncated "
                                      "FROM scout_specialist_checks WHERE source='nhtsa'").fetchall()
                timestamps = conn.execute("SELECT published_at, available_at, first_observed_at "
                                          "FROM scout_findings WHERE source='nhtsa'").fetchall()
            self.assertEqual({"F": 5, "GM": 3}, {row["subject_id"]: row["n"] for row in rows})
            self.assertEqual({"F": (5, 0), "GM": (3, 0)},
                             {row["subject_id"]: (row["candidate_count"], row["truncated"]) for row in checks})
            for row in timestamps:
                self.assertEqual((clock.isoformat(),) * 3, tuple(row))
            for details in finding_details(store):
                self.assertEqual(clock.isoformat(), details["product_relationship_known_at"])
                self.assertFalse(details["issuer_financial_impact_verified"])
                self.assertFalse(details["scoring_applied"])
                self.assertFalse(details["report_date_is_publication_time"])
            restarted = NhtsaRecallScoutService(ScoutStore(database), client=client, models=models)
            self.assertEqual(0, restarted.run(as_of=clock + timedelta(days=1))["checked_models"])
            later = restarted.run(as_of=clock + timedelta(days=31))
            self.assertEqual(("OK", 2, 0), (later["status"], later["checked_models"], later["new_findings"]))
            self.assertEqual(8, len(finding_details(ScoutStore(database))))

    def test_exact_gm_comma_variant_is_required_by_existing_matcher(self) -> None:
        model = load_verified_models(FOLDER / "models.proposed.json")[1]
        clock = datetime.fromisoformat(model["known_at"])
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "replay.db")
            wrong_spelling = dict(model, manufacturer="General Motors LLC")
            summary = NhtsaRecallScoutService(store, client=ArchivedRecalls(),
                models=[wrong_spelling]).run(as_of=clock)
            self.assertEqual(("PARTIAL", 0, 3),
                             (summary["status"], summary["new_findings"], summary["rejected_rows"]))
            self.assertEqual([], finding_details(store))
            with store._connect() as conn:
                check = conn.execute("SELECT candidate_count, truncated FROM scout_specialist_checks").fetchone()
            self.assertEqual((0, 1), tuple(check))

    def test_actual_400_empty_probes_remain_partial_and_create_no_complete_checks(self) -> None:
        manifest = proposal()
        clock = datetime.fromisoformat(manifest["reviewed_at"])
        self.assertEqual("NOT_ESTABLISHED", manifest["complete_negative_live"]["status"])
        cadillac_models = captured_json("nhtsa_model_cadillac_ct4_2026")
        self.assertTrue(any(r["Make_Name"] == "CADILLAC" and r["Model_Name"] == "CT4"
                            for r in cadillac_models["Results"]))
        for probe in manifest["partial_live_probes"]:
            with self.subTest(capture_id=probe["capture_id"]), TemporaryDirectory() as directory:
                self.assertEqual({"Count": 0, "Message": "Results returned successfully", "results": []},
                                 captured_json(probe["capture_id"]))
                self.assertEqual(400, captures()[probe["capture_id"]]["http_status"])
                self.assertFalse(probe["complete_negative"])
                self.assertFalse(probe["proposal_inclusion"])
                # Test request envelope only. This failed probe is not an approved issuer product.
                model = dict(manifest["records"][0 if probe["query"]["ticker_context_only"] == "F" else 1]["service_record"],
                             make=probe["query"]["make"], model=probe["query"]["model"])
                store = ScoutStore(Path(directory) / "replay.db")
                summary = NhtsaRecallScoutService(store, client=ArchivedRecalls(), models=[model]).run(as_of=clock)
                self.assertEqual({"status": "PARTIAL", "checked_models": 0, "new_findings": 0,
                                  "failed_models": 1, "rejected_rows": 0},
                                 {key: summary[key] for key in
                                  ("status", "checked_models", "new_findings", "failed_models", "rejected_rows")})
                self.assertEqual(1, summary["daily_requests"])
                self.assertEqual([], finding_details(store))
                with store._connect() as conn:
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM scout_specialist_checks").fetchone()[0])

    def test_later_captured_http400_keeps_earlier_positive_and_budget_is_partial(self) -> None:
        models = load_verified_models(FOLDER / "models.proposed.json")
        clock = datetime.fromisoformat(models[0]["known_at"])
        # Error request envelope only; the CADILLAC/CT4 product is not proposed for adoption.
        failed_probe = dict(models[1], make="CADILLAC", model="CT4")
        with TemporaryDirectory() as directory:
            database = Path(directory) / "partial.db"
            client = ArchivedRecalls()
            summary = NhtsaRecallScoutService(ScoutStore(database), client=client,
                models=[models[0], failed_probe], max_models=2).run(as_of=clock)
            self.assertEqual(("PARTIAL", 1, 5, 1), (summary["status"], summary["checked_models"],
                                                   summary["new_findings"], summary["failed_models"]))
            self.assertEqual(5, len(finding_details(ScoutStore(database))))
            with ScoutStore(database)._connect() as conn:
                subjects = [r[0] for r in conn.execute("SELECT subject_id FROM scout_specialist_checks")]
            self.assertEqual(["F"], subjects)
        with TemporaryDirectory() as directory:
            client = ArchivedRecalls()
            store = ScoutStore(Path(directory) / "bounded.db")
            bounded = NhtsaRecallScoutService(store, client=client, models=models,
                                             max_models=1).run(as_of=clock)
            self.assertEqual(("PARTIAL", 1, 5), (bounded["status"], bounded["checked_models"], bounded["new_findings"]))
            self.assertEqual([("FORD", "F-150", 2026)], client.calls)


if __name__ == "__main__":
    unittest.main()
