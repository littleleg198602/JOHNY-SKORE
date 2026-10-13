from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import unittest

from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry, resolve_fda_crl,
)

ROOT = Path(__file__).resolve().parents[1]


class HealthcareExpansionTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(DEFAULT_HEALTHCARE_IDENTITIES.read_text())
        self.registry = HealthcareIdentityRegistry(self.payload["records"])
        self.capture = json.loads((ROOT / "evidence/healthcare_expansion_20261013.json").read_text())
        added = set(self.capture["new_record_ids"])
        self.new = [row for row in self.registry.records() if row["record_id"] in added]

    def resolve(self, row, **changes):
        arguments = dict(source=row["source"], kind=row["kind"], ticker=row["ticker"],
                         cik=row["issuer_cik"], entity_name=row["entity_name"],
                         identifiers=deepcopy(row["identifiers"]),
                         event_date=date.fromisoformat(row["effective_from"]),
                         as_of=datetime.fromisoformat(row["known_at"]))
        arguments.update(changes)
        return self.registry.resolve(**arguments)

    def test_expansion_preserves_original_observations_and_capture_bounds(self):
        seed = json.loads((ROOT / "tests/fixtures/healthcare/identity_manifest_captured_20261011.json").read_text())
        self.assertEqual(seed["records"], self.payload["records"][:5])
        self.assertEqual(len(self.payload["records"]), self.payload["record_count"])
        self.assertTrue(set(self.capture["new_record_ids"]) <=
                        {row["record_id"] for row in self.payload["records"]})
        self.assertEqual(21, len(self.new))
        self.assertEqual(18, sum(row["source"] == "cms" for row in self.new))
        self.assertFalse(self.capture["cms"]["complete"])
        self.assertEqual("20", self.capture["cms"]["next_cursor"])
        self.assertFalse(self.capture["completion_verified"])

    def test_all_live_crl_documents_resolve_with_exact_application_scope(self):
        clock = datetime.fromisoformat(self.capture["fda"]["observed_at"])
        for raw in self.capture["fda"]["rows"]:
            with self.subTest(file=raw["file_name"]):
                signed = datetime.strptime(raw["letter_date"], "%m/%d/%Y").date()
                result = resolve_fda_crl(self.registry, ticker="LLY", cik="0000059478",
                                         row=raw, signed=signed, as_of=clock)
                self.assertTrue(result["issuer_attribution_allowed"])
                if "761215" in raw["file_name"]:
                    self.assertEqual("BLA761215/ORIGINAL2", result["identity_evidence"]["identifiers"]["application_number"])
                wrong = resolve_fda_crl(self.registry, ticker="LLY", cik="0000000001",
                                        row=raw, signed=signed, as_of=clock)
                self.assertFalse(wrong["issuer_attribution_allowed"])

    def test_new_objects_deny_earlier_knowledge_wrong_issuer_and_wrong_identifier(self):
        for row in self.new:
            with self.subTest(record=row["record_id"]):
                self.assertTrue(self.resolve(row)["issuer_attribution_allowed"])
                before = datetime.fromisoformat(row["known_at"]) - timedelta(microseconds=1)
                self.assertFalse(self.resolve(row, as_of=before)["issuer_attribution_allowed"])
                self.assertFalse(self.resolve(row, cik="0000000001")["issuer_attribution_allowed"])
                identifiers = deepcopy(row["identifiers"])
                key = "enrollment_id" if row["source"] == "cms" else "letter_file_name"
                identifiers[key] = "different-object"
                self.assertFalse(self.resolve(row, identifiers=identifiers)["issuer_attribution_allowed"])

    def test_cms_capture_is_not_backdated_to_association_or_assigned_to_direct_owner(self):
        for row in self.new:
            if row["source"] != "cms":
                continue
            with self.subTest(record=row["record_id"]):
                self.assertEqual("OBSERVED_SNAPSHOT", row["time_basis"])
                self.assertEqual(datetime.fromisoformat(row["known_at"]).date().isoformat(), row["effective_from"])
                self.assertFalse(self.resolve(row, event_date=date.fromisoformat(row["effective_from"]) - timedelta(days=1))["issuer_attribution_allowed"])
                altered = deepcopy(row["identifiers"])
                altered["owner_role_code"] = "34" if altered["owner_role_code"] == "35" else "35"
                self.assertFalse(self.resolve(row, identifiers=altered)["issuer_attribution_allowed"])
