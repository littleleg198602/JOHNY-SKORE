"""Replay real primary bytes; these tests do not establish exposure edges."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "evidence/batch3_sec_20261012"
spec = importlib.util.spec_from_file_location("batch3_sec_evidence_builder", ROOT / "build_proposal.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def clock(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class BatchThreeSecCounterpartyProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proposal = json.loads((ROOT / "counterparty_proposal.json").read_text())
        cls.manifest = json.loads((ROOT / "captures.json").read_text())
        cls.sources = {row["url"]: row for row in cls.manifest["captures"]}
        cls.records = cls.proposal["records"]
        cls.withheld = json.loads((ROOT / "withheld_subsidiary_candidate.json").read_text())
        cls.registry = DatedCounterpartyIdentityRegistry.load(ROOT / "counterparty_proposal.json")

    def resolve(self, row, **overrides):
        # Another issuer's captured primary index exercises independence only.
        # No supplier/customer role is inferred from this test reference.
        prefix = "lmt_2025" if row["issuer_ticker"] == "MSFT" else "msft_2026"
        url = next(source["url"] for source in self.sources.values()
                   if source["path"] == f"primary/{prefix}_index.html")
        return self.registry.resolve(**{
            "disclosed_name": row["disclosed_name"],
            "published_at": datetime.fromisoformat(row["effective_from"]).replace(tzinfo=timezone.utc),
            "as_of": clock(row["known_at"]), "exposure_source_url": url, **overrides,
        })

    def test_raw_capture_integrity_and_real_observation_clocks(self):
        self.assertEqual(15, len(self.sources))
        self.assertEqual([], self.manifest["blocked_publishers"])
        self.assertEqual(hashlib.sha256((ROOT / "captures.json").read_bytes()).hexdigest(),
                         self.proposal["source_manifest_sha256"])
        for source in self.sources.values():
            with self.subTest(url=source["url"]):
                raw = (ROOT / source["path"]).read_bytes()
                self.assertEqual(200, source["http_status"])
                self.assertEqual("RAW_HTTP_RESPONSE_BODY", source["capture_kind"])
                self.assertEqual(len(raw), source["size_bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), source["sha256"])
                self.assertLessEqual(clock(source["retrieval_started_at"]), clock(source["observed_at"]))
                self.assertLessEqual(clock(source["observed_at"]), clock(self.proposal["reviewed_at"]))

    def test_exact_sec_cik_and_ticker_are_supported_by_independent_primary_rows(self):
        expected = {"MSFT": ("Microsoft Corporation", "0000789019", "msft_2026"),
                    "LMT": ("Lockheed Martin Corporation", "0000936468", "lmt_2025"),
                    "AAPL": ("Apple Inc.", "0000320193", "aapl_2025"),
                    "NVDA": ("NVIDIA Corporation", "0001045810", "nvda_2026")}
        with (REPO / "market_checker_app/data/market_checker_687_tickers.csv").open() as handle:
            canonical = {row["ticker"] for row in csv.DictReader(handle)}
        self.assertTrue(set(expected) <= canonical)
        directs = [row for row in self.records if row["relationship_role"] == "DIRECT_ISSUER"]
        self.assertEqual(4, len(directs))
        for row in directs:
            ticker = row["issuer_ticker"]
            legal, cik, prefix = expected[ticker]
            with self.subTest(ticker=ticker):
                r1 = builder.SourceText((ROOT / f"primary/{prefix}_R1.htm").read_bytes())
                self.assertEqual(cik, r1.field("Entity Central Index Key"))
                self.assertEqual(ticker, r1.field("Trading Symbol"))
                self.assertEqual(legal, row["legal_name"])
                self.assertEqual({"scheme": "CIK", "authority": "SEC", "value": cik}, row["entity_identifier"])
                if ticker != "NVDA":
                    self.assertEqual(legal.casefold(), r1.field("Entity Registrant Name").casefold())
                result = self.resolve(row)
                self.assertEqual("IDENTIFIED", result["counterparty_identity_status"])
                self.assertTrue(result["counterparty_issuer_attribution_allowed"])
                self.assertFalse(result["counterparty_financials_attribution_allowed"])

    def test_nvidia_charter_heading_and_real_parenthetical_ticker_column_replay(self):
        row = next(row for row in self.records if row["issuer_ticker"] == "NVDA")
        r1 = builder.SourceText((ROOT / "primary/nvda_2026_R1.htm").read_bytes())
        self.assertEqual("NVIDIA CORP", r1.field("Entity Registrant Name"))
        cover = builder.SourceText((ROOT / "primary/nvda_2026_10k.htm").read_bytes())
        self.assertIn("NVIDIA CORPORATION (Exact name of registrant as specified in its charter)", cover.text)
        self.assertEqual(["0001045810"], cover.facts["dei:EntityCentralIndexKey"])
        header = next(r for r in cover.rows if "Trading Symbol(s)" in r)
        stock = next(r for r in cover.rows if "NVDA" in r and any("Common Stock" in cell for cell in r))
        self.assertEqual(header.index("Trading Symbol(s)"), stock.index("NVDA"))
        self.assertIn("Trading Symbol(s) NVDA", row["evidence"][1]["quote_fragments"])
        self.assertEqual("IDENTIFIED", self.resolve(row)["counterparty_identity_status"])
        self.assertEqual("UNMAPPED_NAME", self.resolve(row, disclosed_name="NVIDIA CORP")["counterparty_identity_resolution"])

    def test_sikorsky_independent_lei_identity_and_dated_parent_bridge_stay_separate(self):
        child = next(row for row in self.records if row["relationship_role"] == "PRIVATE_LEGAL_ENTITY")
        raw = json.loads((ROOT / "primary/sikorsky_gleif.json").read_bytes())
        attributes = raw["data"]["attributes"]
        self.assertEqual("54930054HRQM5ZB1WZ32", attributes["lei"])
        self.assertEqual(child["legal_name"], attributes["entity"]["legalName"]["name"])
        self.assertEqual("ACTIVE", child["entity_register_status"])
        self.assertEqual("LAPSED", child["lei_registration_status"])
        self.assertEqual("NON_CONFORMING", child["lei_conformity_flag"])
        self.assertEqual(attributes["registration"]["lastUpdateDate"], child["lei_last_update_at"])
        exhibit = builder.SourceText((ROOT / "primary/lmt_2025_ex21.htm").read_bytes())
        self.assertIn("Subsidiaries of Lockheed Martin Corporation", exhibit.text)
        self.assertIn(["Sikorsky Aircraft Corporation", "Delaware"], [[cell for cell in r if cell] for r in exhibit.rows])
        self.assertEqual("2025-12-31", self.withheld["relationship_source_report_date"])
        self.assertEqual("2026-10-12", child["identity_snapshot_date"])
        self.assertEqual("WITHHELD_DATES_NOT_ALIGNED", self.withheld["candidate_status"])
        self.assertFalse(self.withheld["issuer_attribution_allowed"])
        self.assertFalse(self.withheld["financial_attribution_allowed"])
        self.assertIsNone(child["issuer_ticker"])
        self.assertIsNone(child["issuer_cik"])
        self.assertIsNone(child["issuer_legal_name"])
        self.assertEqual(["ENTITY_IDENTITY"], [item["purpose"] for item in child["evidence"]])
        self.assertEqual(["ENTITY_IDENTITY", "ISSUER_IDENTITY", "ENTITY_TO_ISSUER"],
                         [item["purpose"] for item in self.withheld["evidence"]])
        result = self.resolve(child)
        self.assertEqual("IDENTIFIED", result["counterparty_identity_status"])
        self.assertFalse(result["counterparty_issuer_attribution_allowed"])
        self.assertFalse(result["counterparty_financials_attribution_allowed"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", self.resolve(child,
            published_at=datetime(2025, 12, 31, tzinfo=timezone.utc))["counterparty_identity_resolution"])
        with self.assertRaisesRegex(ValueError, "bounded"):
            DatedCounterpartyIdentityRegistry.load(ROOT / "withheld_subsidiary_candidate.json")

    def test_conflicting_real_source_clocks_never_become_verified_first_publication(self):
        self.assertEqual(["AAPL", "NVDA"], self.proposal["historical_publication_clock_conflicts"])
        for ticker in ["AAPL", "NVDA"]:
            row = next(row for row in self.records if row["issuer_ticker"] == ticker)
            for item in row["evidence"]:
                with self.subTest(ticker=ticker, purpose=item["purpose"]):
                    timing = item["filing_clock_evidence"]
                    local = datetime.fromisoformat(timing["index_accepted_text"]).replace(tzinfo=ZoneInfo("America/New_York"))
                    self.assertNotEqual(local.astimezone(timezone.utc), clock(timing["reported_acceptance_at"]))
                    self.assertTrue(timing["reported_acceptance_conflict"])
                    self.assertFalse(timing["publication_time_verified"])
                    self.assertIsNone(timing["verified_first_publication_at"])
                    self.assertFalse(item["publication_time_verified"])
                    self.assertIsNone(item["verified_first_publication_at"])
                    self.assertEqual(item["observed_at"], item["published_at"])
                    self.assertNotEqual(clock(item["published_at"]), clock(item["reported_acceptance_at"]))
                    old_cutoff = clock(item["reported_acceptance_at"]) + timedelta(minutes=1)
                    self.assertEqual("NOT_YET_KNOWN", self.resolve(row, as_of=old_cutoff)["counterparty_identity_resolution"])

    def test_source_hashes_availability_and_knowledge_are_retained_per_evidence(self):
        for row in self.records:
            self.assertEqual(self.proposal["reviewed_at"], row["known_at"])
            for item in row["evidence"]:
                source = self.sources[item["url"]]
                self.assertEqual(source["sha256"], item["archived_source_sha256"])
                self.assertEqual(source["observed_at"], item["observed_at"])
                self.assertEqual(source["last_modified"], item["http_last_modified"])
                self.assertFalse(item["publication_time_verified"])
                self.assertIsNone(item["verified_first_publication_at"])
                self.assertLessEqual(clock(item["published_at"]), clock(item["observed_at"]))
                self.assertLessEqual(clock(item["observed_at"]), clock(row["known_at"]))
                if item["publisher"] == "SEC EDGAR" and not item["reported_acceptance_conflict"]:
                    self.assertEqual(item["reported_acceptance_at"], item["published_at"])
                    self.assertFalse(item["publication_time_verified"])
                elif item["publisher"] == "GLEIF":
                    data = json.loads((ROOT / source["path"]).read_bytes())
                    self.assertEqual(data["meta"]["goldenCopy"]["publishDate"], item["published_at"])

    def test_real_citation_same_document_variants_are_insufficient(self):
        for row in self.records:
            for item in row["evidence"]:
                for url in [item["url"], item["url"] + "?view=print"]:
                    with self.subTest(record=row["record_id"], url=url):
                        result = self.resolve(row, exposure_source_url=url)
                        self.assertEqual("NAMED_ONLY", result["counterparty_identity_status"])
                        self.assertEqual("SAME_DISCLOSURE_ONLY", result["counterparty_identity_resolution"])
                        self.assertFalse(result["counterparty_issuer_attribution_allowed"])

    def test_review_gate_expiry_is_operational_and_does_not_grant_historical_knowledge(self):
        self.assertEqual(0, self.proposal["production_registry_rows_written"])
        for row in self.records:
            with self.subTest(record=row["record_id"]):
                expected_stop = (datetime.fromisoformat(row["identity_snapshot_date"]).date() + timedelta(days=1)).isoformat()
                self.assertFalse(row["legal_effective_interval_verified"])
                self.assertEqual(expected_stop, row["effective_to"])
                self.assertEqual(row["identity_snapshot_date"], row["effective_from"])
                cutoff = clock(row["known_at"]) - timedelta(microseconds=1)
                self.assertEqual("NOT_YET_KNOWN", self.resolve(row, as_of=cutoff)["counterparty_identity_resolution"])
                expired = datetime.fromisoformat(row["effective_to"]).replace(tzinfo=timezone.utc)
                self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", self.resolve(row,
                    published_at=expired, as_of=max(expired, clock(row["known_at"])))["counterparty_identity_resolution"])
        for name in ["Microsoft", "Microsoft Corp", "Apple Inc", "NVIDIA", "Sikorsky", "Lockheed Martin"]:
            self.assertEqual("UNMAPPED_NAME", self.resolve(self.records[0], disclosed_name=name)["counterparty_identity_resolution"])


if __name__ == "__main__":
    unittest.main()
