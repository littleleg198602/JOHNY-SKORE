from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from market_checker_app.agents.contracts import RelationshipType
from market_checker_app.services.supply_chain_evidence_service import (
    build_supply_chain_evidence_metadata,
)


def _at(days: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)


class SupplyChainEvidenceServiceTests(unittest.TestCase):
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
