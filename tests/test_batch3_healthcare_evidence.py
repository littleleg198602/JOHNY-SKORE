"""Replay real archived FDA/SEC bytes; no live requests or runtime writes."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit

from evidence.batch3_healthcare_20261012.replay import (
    EVIDENCE_DIR, build_replay_report, capture, normalized_html,
    read_json, replay_fda_capture, resolve_record,
)
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, resolve_fda_crl,
)
from market_checker_app.utils.ticker_universe import load_canonical_tickers


class Batch3HealthcareEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = read_json(EVIDENCE_DIR / "identity_proposal.json")
        cls.review = read_json(EVIDENCE_DIR / "source_review.json")
        cls.registry = HealthcareIdentityRegistry.load(EVIDENCE_DIR / "identity_proposal.json")
        cls.report = build_replay_report()
        cls.clock = datetime.fromisoformat(cls.report["replay_as_of"])

    def test_all_original_response_bodies_have_bounded_integrity_and_real_clocks(self):
        index = read_json(EVIDENCE_DIR / "capture_index.json")
        self.assertEqual(18, len(index["captures"]))
        self.assertEqual(18, self.report["verified_capture_count"])
        for entry in index["captures"]:
            with self.subTest(capture=entry["capture_key"]):
                meta, raw = capture(entry["capture_key"])
                self.assertEqual(entry["body_bytes"], len(raw))
                requested = datetime.fromisoformat(meta["requested_at"])
                observed = datetime.fromisoformat(meta["observed_at"])
                self.assertIsNotNone(requested.utcoffset())
                self.assertLessEqual(requested, observed)
                self.assertEqual(date(2026, 10, 12), observed.date())
                self.assertEqual(urlsplit(meta["request_url"]).hostname,
                                 urlsplit(meta["response_url"]).hostname)
                self.assertNotIn("set-cookie", {k.casefold() for k in meta["response_headers"]})
        self.assertEqual(0, self.report["network_requests"])

    def test_capture_tampering_fails_before_any_object_attribution(self):
        key = "fda_crl_pfizer_exact_phrase"
        meta, raw = capture(key)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "captures").mkdir()
            (root / "captures" / f"{key}.metadata.json").write_text(json.dumps(meta))
            (root / "captures" / meta["body_path"]).write_bytes(raw + b" ")
            with self.assertRaisesRegex(ValueError, "integrity"):
                replay_fda_capture(key, root)

    def test_independent_sec_fields_and_locators_are_in_the_received_bodies(self):
        self.assertEqual({"PFE", "MRK", "ABBV"}, {r["ticker"] for r in self.registry.records()})
        self.assertTrue({"PFE", "MRK", "ABBV"} <= set(load_canonical_tickers()))
        for source in self.review["source_reviews"]:
            with self.subTest(source=source["capture_key"]):
                meta, raw = capture(source["capture_key"])
                self.assertEqual(200, meta["http_status"])
                self.assertEqual(int(source["issuer_cik"]), int(urlsplit(source["source_url"]).path.split("/")[4]))
                text = normalized_html(raw)
                self.assertIn(source["issuer_name"].casefold(), text.casefold())
                for excerpt in source["excerpts"]:
                    self.assertEqual(excerpt["text"],
                                     text[excerpt["normalized_text_start"]:excerpt["normalized_text_end"]])
                if source["ticker"] == "ABBV":
                    self.assertIn(source["issuer_cik"], text)
                    self.assertIn("Common Stock, par value $0.01 per share ABBV", text)
                elif source["capture_key"].endswith("10k"):
                    self.assertIn("under the symbol", text)
                    self.assertIn(source["ticker"], text)

    def test_real_letters_resolve_only_after_the_actual_review_knowledge_time(self):
        self.assertEqual(5, self.manifest["record_count"])
        self.assertEqual(4, self.manifest["distinct_application_count"])
        for event in self.report["event_replays"]:
            with self.subTest(record=event["record_id"]):
                self.assertEqual("EXACT_DATED_OBJECT", event["object_resolution"])
                self.assertTrue(event["product_attribution_allowed_at_event"])
                self.assertEqual("NOT_YET_KNOWN", event["before_known_resolution"])
                self.assertEqual("WRONG_ISSUER", event["wrong_issuer_resolution"])
                self.assertEqual("UNMAPPED_OBJECT", event["wrong_letter_resolution"])
        for record in self.registry.records():
            known = datetime.fromisoformat(record["known_at"])
            self.assertEqual(self.manifest["reviewed_at"], record["known_at"])
            for evidence in record["evidence"]:
                self.assertLessEqual(datetime.fromisoformat(evidence["observed_at"]), known)
                self.assertGreater(known.date(), date.fromisoformat(record["identifiers"]["letter_date"]))

    def test_event_only_intervals_never_establish_later_application_ownership(self):
        for event in self.report["event_replays"]:
            with self.subTest(record=event["record_id"]):
                self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", event["exclusive_end_resolution"])
                self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", event["current_event_resolution"])
                self.assertFalse(event["ownership_history_established"])
                self.assertEqual(timedelta(days=1),
                                 date.fromisoformat(event["effective_to"]) - date.fromisoformat(event["effective_from"]))
        for record in self.registry.records():
            prior = resolve_record(self.registry, record,
                                   event_date=date.fromisoformat(record["effective_from"]) - timedelta(days=1),
                                   as_of=self.clock)
            self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", prior["identity_resolution"])
            self.assertIn(record["entity_name"], self.registry.query_names(
                "fda", record["ticker"], record["issuer_cik"], as_of=self.clock))

    def test_shared_abbvie_pdf_keeps_both_letter_dates_distinct_and_no_care_of_alias(self):
        events = [e for e in self.report["event_replays"] if e["application_number"] == "NDA216962"]
        self.assertEqual({"2023-03-17", "2024-06-18"}, {e["letter_date"] for e in events})
        records = [r for r in self.registry.records() if r["identifiers"]["application_number"] == "NDA216962"]
        self.assertEqual(1, len({r["identifiers"]["letter_file_name"] for r in records}))
        for record in records:
            changed = deepcopy(record["identifiers"])
            changed["letter_date"] = "2025-01-01"
            result = resolve_record(self.registry, record, identifiers=changed,
                                    event_date=date(2025, 1, 1), as_of=self.clock)
            self.assertEqual("UNMAPPED_OBJECT", result["identity_resolution"])
        _, raw = capture("fda_crl_pfizer")
        ireland = json.loads(raw)["results"][0]
        self.assertEqual("Pfizer Ireland Pharmaceuticals", ireland["company_name"])
        self.assertIn("C/O Pfizer Inc.", ireland["text"])
        result = resolve_fda_crl(self.registry, ticker="PFE", cik="0000078003", row=ireland,
                                 signed=date(2022, 1, 21), as_of=self.clock)
        self.assertEqual("UNMAPPED_OBJECT", result["identity_resolution"])
        self.assertFalse(result["product_attribution_allowed"])

    def test_real_complete_partial_and_not_found_responses_replay_existing_fda_handlers(self):
        queries = {r["capture_key"]: r for r in self.report["queries"]}
        for key, count in (("fda_crl_pfizer_exact_phrase", 1),
                           ("fda_crl_merck_exact_phrase", 1),
                           ("fda_crl_abbvie_exact_phrase", 3),
                           ("fda_crl_abbvie_2023_positive_control", 1)):
            self.assertEqual(("COMPLETE_QUERY_POSITIVE", count, count, False),
                             tuple(queries[key][k] for k in ("status", "returned_count", "publisher_query_total", "partial")))
        partial = queries["fda_crl_abbvie_partial"]
        self.assertEqual(("PARTIAL_QUERY_POSITIVE", 1, 3, True),
                         tuple(partial[k] for k in ("status", "returned_count", "publisher_query_total", "partial")))
        negative = queries["fda_crl_pfizer_2026_negative"]
        self.assertEqual((404, "COMPLETE_QUERY_NO_MATCH", 0, 0, False),
                         tuple(negative[k] for k in ("http_status", "status", "returned_count", "publisher_query_total", "partial")))
        for query in queries.values():
            self.assertFalse(query["issuer_wide_scope"])
            self.assertIn("published CRL dataset snapshot", query["completeness_scope"])

    def test_sec_access_block_is_preserved_and_excluded_from_issuer_proof(self):
        meta, raw = capture("sec_pfe_2017_index")
        self.assertEqual(403, meta["http_status"])
        self.assertNotIn(b"No matches found!", raw)
        self.assertNotIn("sec_pfe_2017_index", {s["capture_key"] for s in self.review["source_reviews"]})
        for record in self.registry.records():
            self.assertNotIn(meta["request_url"], {e["url"] for e in record["evidence"]})
        with self.assertRaisesRegex(ValueError, "archived FDA CRL endpoint"):
            replay_fda_capture("sec_pfe_2017_index")

    def test_proposed_object_links_do_not_create_runtime_or_issuer_wide_coverage(self):
        diagnostic = self.report["proposal_coverage_diagnostic"]
        self.assertEqual("VALID", diagnostic["identity_manifest"]["status"])
        self.assertEqual("NOT_PROVIDED", diagnostic["runtime"]["status"])
        fda = diagnostic["fda"]
        self.assertEqual(3, fda["documented_issuer_object_subjects"])
        self.assertEqual({"EXACT_DATED_OBJECT": 5}, fda["object_status_counts"])
        for key in ("mapped_subjects", "mapped_objects", "queried_issuers",
                    "current_complete_source_checks", "current_partial_source_checks",
                    "issuer_wide_complete_checks"):
            self.assertEqual(0, fda[key])
        self.assertFalse(fda["negative_issuer_completeness_verified"])
        self.assertEqual((5, 5), (fda["expired_interval_records"], fda["historical_event_only_records"]))
        self.assertFalse(any(diagnostic["acceptance"].values()))
        self.assertFalse(self.report["production_registry_mutated"])


if __name__ == "__main__":
    unittest.main()
