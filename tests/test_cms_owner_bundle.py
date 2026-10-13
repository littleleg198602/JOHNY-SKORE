from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import unittest

from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry,
)

ROOT = Path(__file__).resolve().parents[1]


class CmsOwnerBundleTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(DEFAULT_HEALTHCARE_IDENTITIES.read_text())
        self.registry = HealthcareIdentityRegistry(self.manifest["records"])
        self.evidence = json.loads((ROOT / "evidence/cms_owner_bundle_20261013.json").read_text())
        self.clock = datetime.fromisoformat(self.evidence["observed_at"])

    def test_previous_26_records_are_byte_equivalent_to_verified_first_batch(self):
        prior = deepcopy(self.manifest)
        prior["records"] = prior["records"][:26]
        prior["record_count"] = 26
        body = (json.dumps(prior, indent=2) + "\n").encode()
        first = json.loads((ROOT / "evidence/healthcare_expansion_20261013.json").read_text())
        self.assertEqual(first["registry_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(493, self.evidence["new_records"])
        self.assertEqual(519, self.manifest["record_count"])

    def test_each_owner_object_matches_primary_capture_and_denies_wrong_issuer(self):
        for ticker, capture in self.evidence["captures"].items():
            issuer = self.evidence["sec_issuer_evidence"][ticker]
            self.assertIn(ticker, issuer["primary_trading_symbols"])
            linked = 0
            for page in capture["pages"]:
                for row in page["rows"]:
                    arguments = dict(source="cms", kind="cms_provider_owner", ticker=ticker,
                        cik=issuer["company"]["cik"], entity_name=row["ORGANIZATION NAME - OWNER"],
                        identifiers=dict(enrollment_id=row["ENROLLMENT ID"],
                            owner_associate_id=row["ASSOCIATE ID - OWNER"],
                            provider_name=row["ORGANIZATION NAME"],
                            association_date=row["ASSOCIATION DATE - OWNER"],
                            owner_role_code=row["ROLE CODE - OWNER"]),
                        event_date=self.clock.date(), as_of=self.clock)
                    allowed = self.registry.resolve(**arguments)["issuer_attribution_allowed"]
                    self.assertEqual(row["ROLE CODE - OWNER"] in {"34", "35"}, allowed)
                    linked += int(allowed)
                    arguments["cik"] = "0000000001"
                    self.assertFalse(self.registry.resolve(**arguments)["issuer_attribution_allowed"])
            self.assertEqual(self.evidence["results"][ticker]["identity_linked_candidates"], linked)
            self.assertTrue(capture["complete"])
            self.assertFalse(capture["pagination_snapshot_guaranteed"])

    def test_new_links_cannot_be_replayed_before_sec_evidence_or_backdated_ownership(self):
        ids = set(self.evidence["new_record_ids"])
        for row in self.manifest["records"]:
            if row["record_id"] not in ids:
                continue
            known = datetime.fromisoformat(row["known_at"])
            self.assertGreaterEqual(known, max(datetime.fromisoformat(x["observed_at"]) for x in row["evidence"]))
            args = dict(source="cms", kind="cms_provider_owner", ticker=row["ticker"],
                        cik=row["issuer_cik"], entity_name=row["entity_name"],
                        identifiers=row["identifiers"], event_date=date.fromisoformat(row["effective_from"]),
                        as_of=known - timedelta(microseconds=1))
            self.assertFalse(self.registry.resolve(**args)["issuer_attribution_allowed"])
            args.update(as_of=self.clock, event_date=date.fromisoformat(row["effective_from"]) - timedelta(days=1))
            self.assertFalse(self.registry.resolve(**args)["issuer_attribution_allowed"])
