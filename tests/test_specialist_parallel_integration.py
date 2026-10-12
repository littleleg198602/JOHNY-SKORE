"""Offline checks of configured identity scopes through actual runtime hooks."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.agents.contracts import AgentContext
from market_checker_app.agents.scout_index_agent import ScoutIndexAgent
from market_checker_app.agents.supply_chain_agent import SupplyChainAgent
from market_checker_app.config import SupplyChainConfig, SupplyChainSourceConfig
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry
from market_checker_app.services.specialist_acceptance_service import build_specialist_acceptance_report
from market_checker_app.services.specialist_identity_config import (
    configured_counterparty_identities, configured_regulatory_identities,
)
from market_checker_app.storage.scout_store import ScoutStore
from tests.test_sec_counterparty_identity import identity_record, CLOCK, DISCLOSED_AT, EXPOSURE_URL


class SpecialistParallelIntegrationTests(unittest.TestCase):
    def test_contract_display_preserves_signed_deltas_and_hides_uncomparable_values(self):
        with TemporaryDirectory() as folder:
            store = ScoutStore(Path(folder) / "scout.db")
            for index, audit in enumerate((None, {
                    "status": "OBSERVED_REVISION", "comparable": True,
                    "obligation_delta_usd": -250.0, "outlays_delta_usd": 50.0,
                    "source_revision_order_confirmed": True}, {
                    "status": "IDENTITY_CHANGED", "comparable": False,
                    "obligation_delta_usd": 999.0, "outlays_delta_usd": 999.0,
                    "source_revision_order_confirmed": True})):
                store.record_finding(source="usaspending", subject_id="LMT",
                    source_object_id=f"synthetic-award:{index}", content_hash=str(index) * 64,
                    title=f"Synthetic award {index}", source_url="https://api.usaspending.gov/api/v2/search/spending_by_award/",
                    locator="synthetic cumulative snapshot", published_at=CLOCK, available_at=CLOCK, observed_at=CLOCK,
                    details={"stage": "prime_contract", "reported_award_amount_usd": 1000,
                             "reported_total_outlays_usd": 100, "modification_audit": audit})
            before = store.db_path.read_bytes()
            rows = {row["title"]: row for row in store.latest_findings(["LMT"], as_of=CLOCK, source="usaspending")}
            self.assertEqual(before, store.db_path.read_bytes())
            self.assertEqual(-250.0, rows["Synthetic award 1"]["observed_obligation_delta_usd"])
            self.assertEqual(50.0, rows["Synthetic award 1"]["observed_outlays_delta_usd"])
            self.assertTrue(rows["Synthetic award 1"]["source_revision_order_confirmed"])
            self.assertEqual("NO_ARCHIVED_COMPARISON", rows["Synthetic award 0"]["revision_audit_status"])
            for index in (0, 2):
                self.assertIsNone(rows[f"Synthetic award {index}"]["observed_obligation_delta_usd"])
                self.assertIsNone(rows[f"Synthetic award {index}"]["observed_outlays_delta_usd"])
                self.assertFalse(rows[f"Synthetic award {index}"]["source_revision_order_confirmed"])

    def test_invalid_optional_inputs_fail_closed_without_exporting_path(self):
        with TemporaryDirectory() as folder:
            hidden = str(Path(folder) / "sensitive-config-input.json")
            identities, status = configured_counterparty_identities(
                {"JOHNY_SKORE_COUNTERPARTY_IDENTITIES_FILE": hidden})
            self.assertEqual("INVALID_IDENTITY_MANIFEST", status)
            resolution = identities.resolve(disclosed_name="Acme Components Inc.",
                published_at=DISCLOSED_AT, as_of=CLOCK, exposure_source_url=EXPOSURE_URL)
            self.assertEqual("NAMED_ONLY", resolution["counterparty_identity_status"])
            _, regulatory_status = configured_regulatory_identities(
                {"JOHNY_SKORE_REGULATORY_IDENTITIES_FILE": hidden})
            self.assertEqual("INVALID_IDENTITY_MANIFEST", regulatory_status)
            self.assertNotIn(hidden, json.dumps((status, resolution, regulatory_status)))
            self.assertEqual("UNCONFIGURED", configured_regulatory_identities({})[1])

    def test_epa_acceptance_uses_the_new_registry_scope_even_when_unconfigured(self):
        now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as folder:
            store = ScoutStore(Path(folder) / "scout.db")
            store.observe_sec_identity(subject_id="DOW", cik="0000000001",
                                       company_name="SYNTHETIC CHEMICAL COMPANY", as_of=now)
            store.record_specialist_check("epa", subject_id="DOW",
                identity_key="0000000001:SYNTHETIC CHEMICAL COMPANY", as_of=now,
                candidate_count=0, truncated=False)
            report = build_specialist_acceptance_report(store, as_of=now, environment={})
            coverage = report["rotating_source_coverage"]["epa"]
            self.assertEqual(1, coverage["active_identities"])
            self.assertEqual(0, coverage["current_complete"])
            self.assertEqual(1, coverage["not_current"])
            self.assertFalse(report["completion_verified"])
            self.assertEqual(687, report["healthcare_identity_coverage"]["universe"]["subjects"])
            invalid = build_specialist_acceptance_report(store, as_of=now, environment={
                "JOHNY_SKORE_REGULATORY_IDENTITIES_FILE": str(Path(folder) / "absent.json")})
            self.assertEqual("INVALID_IDENTITY_MANIFEST", invalid["rotating_source_coverage"]["epa"]["status"])

    def test_supply_chain_runtime_resolves_independent_identity_without_scoring(self):
        source = SupplyChainSourceConfig(ticker="AAPL", counterparty="Acme Components Inc.",
            relationship_type="SUPPLIER", publisher="SEC EDGAR", published_at=DISCLOSED_AT,
            url=EXPOSURE_URL, counterparty_identity_status="IDENTIFIED")
        config = SupplyChainConfig(enabled=True, sources=(source,))
        context = AgentContext("synthetic-supplier-hook", ("AAPL",), CLOCK)
        unresolved = SupplyChainAgent(config, counterparty_identities=DatedCounterpartyIdentityRegistry([])).run(context)
        self.assertEqual("NAMED_ONLY", unresolved.company_relationships[0].metadata["counterparty_identity_status"])
        verified = SupplyChainAgent(config, counterparty_identities=DatedCounterpartyIdentityRegistry([identity_record()])).run(context)
        detail = verified.company_relationships[0].metadata
        self.assertEqual("IDENTIFIED", detail["counterparty_identity_status"])
        self.assertFalse(detail["counterparty_financials_attribution_allowed"])
        self.assertFalse(verified.metadata["scoring_applied"])
        self.assertEqual(0, verified.evidence[0].direction)

    def test_durable_scout_resolves_previously_saved_candidate_at_analysis_time(self):
        with TemporaryDirectory() as folder:
            db = Path(folder) / "scout.db"
            manifest = Path(folder) / "counterparties.json"
            manifest.write_text(json.dumps({"schema_version": 1, "records": [identity_record()]}))
            store = ScoutStore(db)
            store.record_finding(source="sec", subject_id="AAPL", source_object_id="synthetic:supplier:1",
                content_hash="a" * 64, title="Synthetic cited supplier", source_url=EXPOSURE_URL,
                locator="synthetic paragraph", published_at=DISCLOSED_AT, available_at=CLOCK, observed_at=CLOCK,
                details={"stage": "exposure_candidate", "kind": "supply_chain",
                         "resource_or_counterparty": "Acme Components Inc.", "quote": "Synthetic supplier disclosure"})
            with patch.dict("os.environ", {"JOHNY_SKORE_COUNTERPARTY_IDENTITIES_FILE": str(manifest)}):
                result = ScoutIndexAgent(db).run(AgentContext("synthetic-durable-hook", ("AAPL",), CLOCK))
            resolution = result.documents[0].metadata["counterparty_identity"]
            self.assertEqual("IDENTIFIED", resolution["counterparty_identity_status"])
            self.assertFalse(resolution["counterparty_financials_attribution_allowed"])
            self.assertFalse(result.metadata["scoring_applied"])
            self.assertEqual("LOADED", result.metadata["counterparty_identity_manifest_status"])


if __name__ == "__main__":
    unittest.main()
