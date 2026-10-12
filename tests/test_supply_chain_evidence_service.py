from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from market_checker_app.agents.contracts import RelationshipType
from market_checker_app.services.supply_chain_evidence_service import (
    build_supply_chain_evidence_metadata,
)
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry
from tests.test_sec_counterparty_identity import (
    CLOCK, DISCLOSED_AT, EXPOSURE_URL, identity_record, subsidiary_record,
)


def _at(days: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)


class SupplyChainEvidenceServiceTests(unittest.TestCase):
    def test_manual_identified_claim_and_stale_existing_metadata_do_not_bypass_evidence(self) -> None:
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST", counterparty="Acme Components Inc.",
            relationship_type=RelationshipType.SUPPLIER,
            published_at=DISCLOSED_AT, observed_at=CLOCK,
            source_url=EXPOSURE_URL, counterparty_identity_status="IDENTIFIED",
            existing={"counterparty_identity_status": "IDENTIFIED",
                      "counterparty_identity_evidence": {"guessed": True},
                      "counterparty_issuer_attribution_allowed": True},
        )
        self.assertEqual("NAMED_ONLY", metadata["counterparty_identity_status"])
        self.assertEqual("IDENTIFIED", metadata["configured_counterparty_identity_status"])
        self.assertEqual("NAME_ONLY", metadata["evidence_missingness"]["counterparty_identity"])
        self.assertIsNone(metadata["counterparty_identity_evidence"])
        self.assertFalse(metadata["counterparty_issuer_attribution_allowed"])

    def test_subsidiary_metadata_uses_independent_identity_without_inheriting_parent_metrics(self) -> None:
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST", counterparty="Acme Components Inc.",
            relationship_type=RelationshipType.SUPPLIER,
            published_at=DISCLOSED_AT, observed_at=CLOCK,
            source_url=EXPOSURE_URL, counterparty_identity_status="NAMED_ONLY",
            counterparty_identities=DatedCounterpartyIdentityRegistry([subsidiary_record()]),
            product_or_input="semiconductor components",
        )
        self.assertEqual("IDENTIFIED", metadata["counterparty_identity_status"])
        self.assertEqual("KNOWN", metadata["evidence_missingness"]["counterparty_identity"])
        self.assertEqual("Acme Components Inc. -> TEST", metadata["edge_path"])
        self.assertEqual("PARN", metadata["counterparty_identity_evidence"]["issuer_ticker"])
        self.assertFalse(metadata["counterparty_financials_attribution_allowed"])
        self.assertEqual("UNKNOWN", metadata["evidence_missingness"]["counterparty_country"])
        self.assertFalse(metadata["scoring_applied"])

    def test_historical_cutoff_controls_identity_even_when_observed_later(self) -> None:
        cutoff = datetime(2025, 2, 20, tzinfo=timezone.utc)
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST", counterparty="Acme Components Inc.",
            relationship_type=RelationshipType.CUSTOMER,
            published_at=cutoff, observed_at=CLOCK, identity_as_of=cutoff,
            source_url=EXPOSURE_URL,
            counterparty_identities=DatedCounterpartyIdentityRegistry([identity_record()]),
        )
        self.assertEqual("NOT_YET_KNOWN", metadata["counterparty_identity_resolution"])
        self.assertEqual("NAMED_ONLY", metadata["counterparty_identity_status"])
        self.assertEqual("TEST -> Acme Components Inc.", metadata["edge_path"])

    def test_anonymous_name_cannot_be_promoted_by_configured_identified_claim(self) -> None:
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST", counterparty="Unnamed major customer",
            relationship_type=RelationshipType.CUSTOMER,
            published_at=DISCLOSED_AT, observed_at=CLOCK, source_url=EXPOSURE_URL,
            counterparty_identity_status="IDENTIFIED",
            counterparty_identities=DatedCounterpartyIdentityRegistry([identity_record()]),
        )
        self.assertEqual("ANONYMOUS", metadata["counterparty_identity_status"])
        self.assertEqual("NOT_DISCLOSED", metadata["evidence_missingness"]["counterparty_identity"])

    def test_named_supplier_without_registry_identity_is_not_identified(self) -> None:
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST", counterparty="Acme Components Inc.",
            relationship_type=RelationshipType.SUPPLIER,
            published_at=_at(0), observed_at=_at(1),
            source_url="https://www.sec.gov/example",
            counterparty_identity_status="NAMED_ONLY",
            product_or_input="semiconductor components",
        )
        self.assertEqual("NAMED_ONLY", metadata["counterparty_identity_status"])
        self.assertEqual("NAME_ONLY", metadata["evidence_missingness"]["counterparty_identity"])
        self.assertFalse(metadata["prediction_input"])

    def test_anonymous_customer_stays_anonymous_with_explicit_missingness(self) -> None:
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST",
            counterparty="Unnamed major customer",
            relationship_type=RelationshipType.CUSTOMER,
            published_at=_at(0),
            observed_at=_at(20),
            source_url="https://www.sec.gov/example",
            disclosure_period="FY2025",
            relationship_context="CONCENTRATION",
            evidence_quote="Our largest customer accounted for 24% of revenue.",
            discovery_method="sec_filing",
        )
        self.assertEqual("TEST -> Unnamed major customer", metadata["edge_path"])
        self.assertEqual("ANONYMOUS", metadata["counterparty_identity_status"])
        self.assertEqual("NOT_DISCLOSED", metadata["evidence_missingness"]["counterparty_identity"])
        self.assertEqual("EXPLICIT_FILING", metadata["evidence_level"])
        self.assertEqual("FRESH", metadata["evidence_freshness"])
        self.assertFalse(metadata["prediction_input"])

    def test_identified_supplier_can_carry_disclosed_context_and_staleness(self) -> None:
        metadata = build_supply_chain_evidence_metadata(
            ticker="TEST",
            counterparty="Example Components Ltd.",
            relationship_type=RelationshipType.SUPPLIER,
            published_at=_at(0),
            observed_at=_at(401),
            source_url="https://example.com/annual-report",
            counterparty_identity_status="IDENTIFIED",
            product_or_input="semiconductor components",
            counterparty_country="JP",
            disclosure_period="FY2025",
            relationship_context="SINGLE_SOURCE",
            evidence_quote="Example Components is our sole supplier.",
            evidence_level="EXPLICIT_FILING",
        )
        self.assertEqual("Example Components Ltd. -> TEST", metadata["edge_path"])
        self.assertEqual("STALE", metadata["evidence_freshness"])
        self.assertEqual("KNOWN", metadata["evidence_missingness"]["counterparty_country"])
        self.assertEqual("SINGLE_SOURCE", metadata["relationship_context"])

    def test_invalid_identity_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "counterparty_identity_status"):
            build_supply_chain_evidence_metadata(
                ticker="TEST",
                counterparty="Example",
                relationship_type=RelationshipType.SUPPLIER,
                published_at=_at(0),
                observed_at=_at(1),
                source_url="https://example.com/source",
                counterparty_identity_status="GUESSED",
            )


if __name__ == "__main__":
    unittest.main()
