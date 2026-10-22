"""Actual imported records must retain their reviewed attribution boundaries."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest

from market_checker_app.services.healthcare_identity_service import HealthcareIdentityRegistry
from market_checker_app.services.nhtsa_recall_scout_service import load_verified_models
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "market_checker_app" / "data"


class Batch3RegistryAdoptionTests(unittest.TestCase):
    def test_actual_import_preserves_event_knowledge_model_and_private_entity_scope(self):
        audit = json.loads((ROOT / "evidence/batch3_identity_import_20261012/adoption.json").read_text())
        healthcare = HealthcareIdentityRegistry.load()
        by_id = {record["record_id"]: record for record in healthcare.records()}
        for decision in audit["healthcare"]["decisions"]:
            record = by_id[decision["record_id"]]
            event = date.fromisoformat(record["identifiers"]["letter_date"])
            known = datetime.fromisoformat(record["known_at"])
            args = {key: record[key] for key in ("source", "kind", "ticker", "entity_name", "identifiers")}
            args["cik"] = record["issuer_cik"]
            with self.subTest(record=record["record_id"]):
                self.assertEqual(record["effective_from"], event.isoformat())
                self.assertEqual(record["effective_to"], (event + timedelta(days=1)).isoformat())
                self.assertTrue(healthcare.resolve(**args, event_date=event, as_of=known)["issuer_attribution_allowed"])
                before = healthcare.resolve(**args, event_date=event, as_of=known - timedelta(microseconds=1))
                self.assertEqual(before["identity_resolution"], "NOT_YET_KNOWN")
                later = healthcare.resolve(**args, event_date=event + timedelta(days=1), as_of=known)
                self.assertEqual(later["identity_resolution"], "OUTSIDE_EFFECTIVE_PERIOD")
                wrong = dict(args, cik="0000000001")
                self.assertFalse(healthcare.resolve(**wrong, event_date=event, as_of=known)["issuer_attribution_allowed"])
        imported_models = {(record["ticker"], record["make"], record["model"], record["model_year"]): record
                           for record in load_verified_models()}
        for decision in audit["nhtsa"]["decisions"]:
            record = decision["service_record"]
            key = tuple(record[key] for key in ("ticker", "make", "model", "model_year"))
            self.assertEqual(imported_models[key], record)
            self.assertEqual(record["model_year"], 2026)
            self.assertFalse(decision["historical_parent_at_campaign_date_verified"])
            self.assertFalse(decision["issuer_legal_liability_verified"])
        self.assertEqual(imported_models[("GM", "CHEVROLET", "SILVERADO 1500", 2026)]["manufacturer"], "General Motors, LLC")
        counterparties = DatedCounterpartyIdentityRegistry.load()
        records = json.loads((DATA / "verified_counterparty_identities.json").read_text())["records"]
        # An independent actual public URL exercises the resolver gate only;
        # this test does not claim that URL discloses any of these counterparties.
        independent_url = "https://www.sec.gov/files/company_tickers.json"
        for record in records:
            start, end = date.fromisoformat(record["effective_from"]), date.fromisoformat(record["effective_to"])
            known = datetime.fromisoformat(record["known_at"])
            published = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
            args = {"disclosed_name": record["disclosed_name"], "published_at": published,
                    "exposure_source_url": independent_url}
            with self.subTest(counterparty=record["record_id"]):
                self.assertEqual(end, start + timedelta(days=1))
                result = counterparties.resolve(**args, as_of=known)
                self.assertEqual(result["counterparty_identity_status"], "IDENTIFIED")
                self.assertFalse(result["counterparty_financials_attribution_allowed"])
                self.assertEqual(result["counterparty_issuer_attribution_allowed"], record["relationship_role"] == "DIRECT_ISSUER")
                before = counterparties.resolve(**args, as_of=known - timedelta(microseconds=1))
                self.assertEqual(before["counterparty_identity_resolution"], "NOT_YET_KNOWN")
                after_publication = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
                after = counterparties.resolve(**dict(args, published_at=after_publication),
                                               as_of=max(known, after_publication))
                self.assertEqual(after["counterparty_identity_resolution"], "OUTSIDE_EFFECTIVE_PERIOD")
                if record["relationship_role"] == "PRIVATE_LEGAL_ENTITY":
                    self.assertIsNone(record["issuer_cik"])
                    self.assertIsNone(record["issuer_ticker"])
        self.assertEqual(hashlib.sha256((DATA / "verified_13f_securities.json").read_bytes()).hexdigest(),
                         "3b6bf0d26d6f75e91470bcc07e7a3907b2470c244a8bc55e21fb509569863de5")
