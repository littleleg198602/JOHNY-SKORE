from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from market_checker_app.collectors.sec_edgar_client import SecCompany
from market_checker_app.services.sec_counterparty_identity import (
    DatedCounterpartyIdentityRegistry,
    exact_catalog_cik,
)


CLOCK = datetime(2025, 11, 1, tzinfo=timezone.utc)
DISCLOSED_AT = datetime(2025, 10, 31, tzinfo=timezone.utc)
EXPOSURE_URL = "https://www.sec.gov/Archives/edgar/data/1/test-10k.htm"


def identity_record() -> dict:
    """Synthetic reviewed-evidence fixture, never a live legal-person proof."""
    evidence = {
        "url": "https://www.sec.gov/Archives/edgar/data/1234/000000123425000001/identity.htm",
        "locator": "cover", "publisher": "SEC EDGAR",
        "quote": "Acme Components Inc. CIK 0000001234 common stock ticker ACME.",
        "published_at": "2025-02-01T00:00:00Z", "observed_at": "2025-02-02T00:00:00Z",
    }
    return {
        "record_id": "synthetic-acme-2025", "legal_name": "Acme Components Inc.",
        "disclosed_name": "Acme Components Inc.", "relationship_role": "DIRECT_ISSUER",
        "entity_identifier": {"scheme": "CIK", "authority": "SEC", "value": "0000001234"},
        "issuer_ticker": "ACME", "issuer_cik": "0000001234",
        "issuer_legal_name": "Acme Components Inc.",
        "effective_from": "2025-01-01", "effective_to": "2026-01-01",
        "known_at": "2025-03-01T00:00:00Z",
        "evidence": [dict(evidence, purpose="ENTITY_IDENTITY"),
                     dict(evidence, purpose="ISSUER_IDENTITY")],
    }


def subsidiary_record() -> dict:
    row = identity_record()
    row.update(record_id="synthetic-acme-subsidiary", relationship_role="SUBSIDIARY_OF_ISSUER",
               issuer_ticker="PARN", issuer_cik="0000005678", issuer_legal_name="Parent Holdings Inc.")
    row["evidence"] = [row["evidence"][0], {
        "purpose": "ISSUER_IDENTITY", "locator": "cover", "publisher": "SEC EDGAR",
        "url": "https://www.sec.gov/Archives/edgar/data/5678/000000567825000001/cover.htm",
        "quote": "Parent Holdings Inc. CIK 0000005678 common stock ticker PARN.",
        "published_at": "2025-02-01T00:00:00Z", "observed_at": "2025-02-02T00:00:00Z",
    }, {
        "purpose": "ENTITY_TO_ISSUER", "locator": "exhibit 21", "publisher": "SEC EDGAR",
        "url": "https://www.sec.gov/Archives/edgar/data/5678/000000567825000001/ex21.htm",
        "quote": "Acme Components Inc. is a subsidiary of Parent Holdings Inc.",
        "published_at": "2025-02-01T00:00:00Z", "observed_at": "2025-02-02T00:00:00Z",
    }]
    return row


def resolve(registry: DatedCounterpartyIdentityRegistry, **overrides: object) -> dict:
    return registry.resolve(**{
        "disclosed_name": "Acme Components Inc.", "published_at": DISCLOSED_AT,
        "as_of": CLOCK, "exposure_source_url": EXPOSURE_URL, **overrides,
    })


class ExactCounterpartyIdentityTests(unittest.TestCase):
    def test_exact_legal_name_accepts_punctuation_and_share_classes(self):
        catalog = {
            "ACMA": SecCompany("ACMA", "0000001234", "ACME COMPONENTS, INC.", "NYSE"),
            "ACMB": SecCompany("ACMB", "0000001234", "Acme Components Inc.", "NYSE"),
        }
        self.assertEqual(("0000001234", ("ACMA", "ACMB")),
                         exact_catalog_cik("Acme Components Inc.", catalog))

    def test_near_name_and_duplicate_issuers_do_not_resolve(self):
        catalog = {
            "A": SecCompany("A", "0000001234", "Acme Components Inc.", "NYSE"),
            "B": SecCompany("B", "0000005678", "ACME COMPONENTS, INC.", "NYSE"),
        }
        self.assertIsNone(exact_catalog_cik("Acme Component Inc.", catalog))
        self.assertIsNone(exact_catalog_cik("Acme Components Inc.", catalog))


class DatedCounterpartyIdentityTests(unittest.TestCase):
    def test_exact_independent_direct_issuer_identity_retains_legal_id_and_role(self):
        result = resolve(DatedCounterpartyIdentityRegistry([identity_record()]),
                         disclosed_name="  ACME   Components Inc. ")
        self.assertEqual("IDENTIFIED", result["counterparty_identity_status"])
        self.assertEqual("EXACT_DATED_LEGAL_ENTITY", result["counterparty_identity_resolution"])
        self.assertEqual("0000001234", result["counterparty_identity_evidence"]["entity_identifier"]["value"])
        self.assertEqual("DIRECT_ISSUER", result["counterparty_identity_evidence"]["relationship_role"])
        self.assertEqual("2025-10-31", result["counterparty_identity_evidence"]["matched_disclosure_date"])
        self.assertTrue(result["counterparty_issuer_attribution_allowed"])
        self.assertFalse(result["counterparty_financials_attribution_allowed"])

    def test_suffix_punctuation_brand_and_near_name_are_not_inferred(self):
        registry = DatedCounterpartyIdentityRegistry([identity_record()])
        for name in ("Acme", "Acme Components", "Acme Components LLC", "Acme Components Inc",
                     "Acme Component Inc.", "Acme Components Europe Inc."):
            with self.subTest(name=name):
                self.assertEqual("UNMAPPED_NAME", resolve(registry, disclosed_name=name)["counterparty_identity_resolution"])

    def test_alias_requires_independent_exact_name_relationship(self):
        row = identity_record()
        row["disclosed_name"] = "Acme Parts"
        with self.assertRaisesRegex(ValueError, "alias"):
            DatedCounterpartyIdentityRegistry([row])
        row["evidence"].append(dict(row["evidence"][0], purpose="DISCLOSED_NAME",
                                    quote="Acme Parts is a trade name of Acme Components Inc."))
        result = resolve(DatedCounterpartyIdentityRegistry([row]), disclosed_name="Acme Parts")
        self.assertEqual("IDENTIFIED", result["counterparty_identity_status"])
        self.assertEqual("Acme Components Inc.", result["counterparty_identity_evidence"]["legal_name"])
        row["evidence"][-1]["quote"] = "Acme Parts is not a trade name of Acme Components Inc."
        with self.assertRaisesRegex(ValueError, "alias"):
            DatedCounterpartyIdentityRegistry([row])

    def test_subsidiary_parent_and_counterparty_stay_separate(self):
        result = resolve(DatedCounterpartyIdentityRegistry([subsidiary_record()]))
        evidence = result["counterparty_identity_evidence"]
        self.assertEqual("SUBSIDIARY_OF_ISSUER", evidence["relationship_role"])
        self.assertEqual("0000001234", evidence["entity_identifier"]["value"])
        self.assertEqual("0000005678", evidence["issuer_cik"])
        self.assertTrue(result["counterparty_issuer_attribution_allowed"])
        self.assertFalse(result["counterparty_financials_attribution_allowed"])

    def test_subsidiary_requires_explicit_link_and_both_exact_legal_names(self):
        for bad_quote in ("Acme Components Inc. collaborates with Parent Holdings Inc.",
                          "Acme Components Inc. is a subsidiary of Another Parent Inc.",
                          "Acme Components Inc. is not a subsidiary of Parent Holdings Inc."):
            with self.subTest(quote=bad_quote):
                row = subsidiary_record()
                row["evidence"][2]["quote"] = bad_quote
                with self.assertRaises(ValueError):
                    DatedCounterpartyIdentityRegistry([row])
        row = subsidiary_record()
        row["evidence"].pop()
        with self.assertRaisesRegex(ValueError, "evidence"):
            DatedCounterpartyIdentityRegistry([row])

    def test_private_legal_entity_resolves_without_guessing_a_public_parent(self):
        row = identity_record()
        row.update(relationship_role="PRIVATE_LEGAL_ENTITY", issuer_cik=None,
                   issuer_ticker=None, issuer_legal_name=None,
                   entity_identifier={"scheme": "LEGAL_REGISTER", "authority": "Companies House", "value": "01234567"})
        row["evidence"] = [dict(row["evidence"][0],
            url="https://find-and-update.company-information.service.gov.uk/company/01234567",
            quote="Acme Components Inc. company number 01234567.")]
        result = resolve(DatedCounterpartyIdentityRegistry([row]))
        self.assertEqual("IDENTIFIED", result["counterparty_identity_status"])
        self.assertFalse(result["counterparty_issuer_attribution_allowed"])
        row["issuer_ticker"] = "ACME"
        with self.assertRaisesRegex(ValueError, "Private legal"):
            DatedCounterpartyIdentityRegistry([row])

    def test_future_knowledge_and_effective_periods_remain_unresolved(self):
        registry = DatedCounterpartyIdentityRegistry([identity_record()])
        before_known = datetime(2025, 2, 20, tzinfo=timezone.utc)
        self.assertEqual("NOT_YET_KNOWN", resolve(registry, published_at=before_known, as_of=before_known)["counterparty_identity_resolution"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", resolve(registry,
            published_at=datetime(2024, 12, 31, tzinfo=timezone.utc))["counterparty_identity_resolution"])
        self.assertEqual("OUTSIDE_EFFECTIVE_PERIOD", resolve(registry,
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            as_of=datetime(2026, 1, 2, tzinfo=timezone.utc))["counterparty_identity_resolution"])
        self.assertEqual("FUTURE_DISCLOSURE", resolve(registry,
            published_at=datetime(2025, 11, 2, tzinfo=timezone.utc))["counterparty_identity_resolution"])

    def test_active_competing_parent_links_are_ambiguous_globally(self):
        registry = DatedCounterpartyIdentityRegistry([identity_record(), subsidiary_record()])
        result = resolve(registry)
        self.assertEqual("NAMED_ONLY", result["counterparty_identity_status"])
        self.assertEqual("AMBIGUOUS_DATED_IDENTITY", result["counterparty_identity_resolution"])
        self.assertIsNone(result["counterparty_identity_evidence"])

    def test_future_competing_evidence_does_not_leak_back_to_an_earlier_cutoff(self):
        current = identity_record()
        future = subsidiary_record()
        future["known_at"] = "2025-12-01T00:00:00Z"
        registry = DatedCounterpartyIdentityRegistry([current, future])
        self.assertEqual("IDENTIFIED", resolve(registry)["counterparty_identity_status"])
        self.assertEqual("AMBIGUOUS_DATED_IDENTITY", resolve(registry,
            as_of=datetime(2025, 12, 1, tzinfo=timezone.utc))["counterparty_identity_resolution"])
        self.assertEqual(DatedCounterpartyIdentityRegistry([current]).fingerprint(as_of=CLOCK),
                         registry.fingerprint(as_of=CLOCK))

    def test_exposure_document_and_query_variants_cannot_independently_prove_identity(self):
        row = identity_record()
        registry = DatedCounterpartyIdentityRegistry([row])
        same = row["evidence"][0]["url"]
        for url in (same, same + "?view=print", same.replace("www.sec.gov", "sec.gov"),
                    same.replace("/identity.htm", "/./identity.htm")):
            with self.subTest(url=url):
                result = resolve(registry, exposure_source_url=url)
                self.assertEqual("SAME_DISCLOSURE_ONLY", result["counterparty_identity_resolution"])
                self.assertEqual("NAMED_ONLY", result["counterparty_identity_status"])

    def test_invalid_identity_fields_citations_dates_and_roles_are_rejected(self):
        cases = []
        for field, value in (("relationship_role", "NAME_MATCH"), ("known_at", "2025-03-01"),
                             ("effective_to", "2025-01-01"), ("issuer_cik", "1234")):
            row = identity_record()
            row[field] = value
            cases.append(row)
        for field, value in (("url", "https://www.sec.gov/Archives/edgar/data/9999/identity.htm"),
                             ("url", "https://example.com/acme-identity.htm"),
                             ("url", "https://localhost/identity"),
                             ("quote", "Acme Components Inc. without a legal identifier."),
                             ("observed_at", "2025-04-01T00:00:00Z"),
                             ("published_at", "2025-03-02T00:00:00Z")):
            row = identity_record()
            row["evidence"][0][field] = value
            cases.append(row)
        row = identity_record()
        row["evidence"][1]["quote"] = "Acme Components Inc. CIK 0000001234 stock ticker WRONG."
        cases.append(row)
        for index, row in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                DatedCounterpartyIdentityRegistry([row])
        with self.assertRaisesRegex(ValueError, "timezone"):
            resolve(DatedCounterpartyIdentityRegistry([identity_record()]), as_of=datetime(2025, 11, 1))

    def test_load_is_bounded_and_duplicate_record_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            DatedCounterpartyIdentityRegistry([identity_record(), identity_record()])
        with self.assertRaisesRegex(ValueError, "bounded"):
            DatedCounterpartyIdentityRegistry([{}] * 10001)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "counterparties.json"
            path.write_text(json.dumps({"schema_version": 1, "records": [identity_record()]}))
            self.assertEqual("IDENTIFIED", resolve(DatedCounterpartyIdentityRegistry.load(path))["counterparty_identity_status"])
            path.write_text(json.dumps({"schema_version": 2, "records": []}))
            with self.assertRaisesRegex(ValueError, "schema|manifest"):
                DatedCounterpartyIdentityRegistry.load(path)
            with path.open("wb") as handle:
                handle.truncate(10_000_001)
            with self.assertRaisesRegex(ValueError, "byte limit"):
                DatedCounterpartyIdentityRegistry.load(path)

    def test_evidence_is_immutable_across_input_and_output_mutation(self):
        row = identity_record()
        registry = DatedCounterpartyIdentityRegistry([row])
        row["legal_name"] = "Changed Name"
        result = resolve(registry)
        result["counterparty_identity_evidence"]["legal_name"] = "Changed Output"
        self.assertEqual("Acme Components Inc.", resolve(registry)["counterparty_identity_evidence"]["legal_name"])


if __name__ == "__main__":
    unittest.main()
