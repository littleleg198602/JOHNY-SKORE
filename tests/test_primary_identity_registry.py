from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from market_checker_app.agents import (
    AgentStatus,
    EntityRegistryAgent,
    GateDecision,
    OrchestratorAgent,
    PredictionV21AdapterAgent,
    QualityGateAgent,
)
from market_checker_app.collectors.gleif_client import (
    GLEIF_API_ROOT,
    GleifClient,
    GleifIdentity,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.entity_identifiers import normalize_isin, normalize_lei


def _lei(body: str) -> str:
    if len(body) != 18:
        raise AssertionError(body)
    expanded = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in f"{body}00"
    )
    candidate = f"{body}{98 - (int(expanded) % 97):02d}"
    assert normalize_lei(candidate) == candidate
    return candidate


def _isin(sequence: int) -> str:
    base = f"US{sequence:09d}"
    for check_digit in range(10):
        candidate = f"{base}{check_digit}"
        try:
            if normalize_isin(candidate) == candidate:
                return candidate
        except ValueError:
            continue
    raise AssertionError(sequence)


def _identity_payload(lei: str, isin: str, ticker: str) -> dict[str, object]:
    return {
        "entity_id": f"listing:{ticker.lower()}",
        "ticker": ticker,
        "name": f"Pilot Entity {ticker}",
        "lei": lei,
        "isin": isin,
        "mic": "XNAS",
        "country_code": "US",
        "source": "trusted_exchange_manifest",
        "source_url": f"https://example.com/entities/{ticker.lower()}",
        "confidence": 0.95,
    }


class _ExactResolver:
    def __init__(self, identities: dict[str, GleifIdentity]) -> None:
        self.identities = identities
        self.calls: list[tuple[str | None, str | None]] = []

    def resolve(
        self,
        *,
        lei: str | None = None,
        isin: str | None = None,
    ) -> GleifIdentity | None:
        self.calls.append((lei, isin))
        return self.identities.get(str(lei or ""))


def _signals(ticker: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "action": "BUY",
                "forecast": "UP",
                "decision_confidence": 0.75,
                "risk_score": 20.0,
                "action_reasons": '["confirmed"]',
            }
        ]
    )


class GleifClientTests(unittest.TestCase):
    def test_exact_lei_lookup_also_normalizes_mapped_isins(self) -> None:
        lei = _lei("529900PILOT0000001")
        parent_lei = _lei("529900PILOT0000009")
        isin = _isin(1)
        calls: list[str] = []

        def transport(url: str, _headers: dict[str, str], _timeout: float):
            calls.append(url)
            if url.endswith("/isins"):
                return {
                    "data": [
                        {
                            "type": "isins",
                            "id": isin,
                            "attributes": {"isin": isin},
                        }
                    ]
                }
            return {
                "data": {
                    "type": "lei-records",
                    "id": lei,
                    "attributes": {
                        "lei": lei,
                        "entity": {
                            "legalName": {"name": "Pilot Corporation"},
                            "otherNames": [{"name": "Pilot Corp."}],
                            "legalAddress": {"country": "US"},
                            "jurisdiction": "US-DE",
                            "registeredAs": "1234567",
                            "status": "ACTIVE",
                        },
                        "registration": {"status": "ISSUED"},
                    },
                    "relationships": {
                        "direct-parent-relationship": {
                            "data": {"type": "lei-records", "id": parent_lei}
                        }
                    },
                }
            }

        identity = GleifClient(transport=transport).resolve(lei=lei)

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(lei, identity.lei)
        self.assertEqual((isin,), identity.isins)
        self.assertEqual("Pilot Corporation", identity.legal_name)
        self.assertEqual("US", identity.country_code)
        self.assertEqual(parent_lei, identity.parent_lei)
        self.assertEqual(
            [f"{GLEIF_API_ROOT}/lei-records/{lei}", f"{GLEIF_API_ROOT}/lei-records/{lei}/isins"],
            calls,
        )

    def test_client_never_queries_by_name_without_an_exact_identifier(self) -> None:
        calls: list[str] = []

        def transport(url: str, _headers: dict[str, str], _timeout: float):
            calls.append(url)
            return {}

        result = GleifClient(transport=transport).resolve()

        self.assertIsNone(result)
        self.assertEqual([], calls)


class PrimaryIdentityPilotTests(unittest.TestCase):
    def test_registry_fills_missing_lei_or_single_isin_from_exact_mapping(self) -> None:
        first_lei = _lei("529900PILOT0000004")
        second_lei = _lei("529900PILOT0000005")
        parent_lei = _lei("529900PILOT0000006")
        first_isin = _isin(4)
        second_isin = _isin(5)
        first = _identity_payload(first_lei, first_isin, "ONE")
        second = _identity_payload(second_lei, second_isin, "TWO")
        first.pop("lei")
        second.pop("isin")

        class Resolver:
            def resolve(self, *, lei=None, isin=None):
                if isin == first_isin:
                    return GleifIdentity(
                        lei=first_lei,
                        legal_name="Entity One",
                        country_code="US",
                        jurisdiction="US-DE",
                        registered_as="1",
                        registration_status="ISSUED",
                        entity_status="ACTIVE",
                        parent_lei=parent_lei,
                        aliases=("Entity One Holdings",),
                        isins=(first_isin,),
                        source_url=f"{GLEIF_API_ROOT}/lei-records/{first_lei}",
                    )
                if lei == second_lei:
                    return GleifIdentity(
                        lei=second_lei,
                        legal_name="Entity Two",
                        country_code="US",
                        jurisdiction="US-DE",
                        registered_as="2",
                        registration_status="ISSUED",
                        entity_status="ACTIVE",
                        parent_lei=None,
                        isins=(second_isin,),
                        source_url=f"{GLEIF_API_ROOT}/lei-records/{second_lei}",
                    )
                return None

        orchestrator = OrchestratorAgent()
        orchestrator.register(
            EntityRegistryAgent(
                {"ONE": first, "TWO": second},
                primary_registry_client=Resolver(),
            )
        )

        report = orchestrator.run(watchlist=["ONE", "TWO"])

        by_ticker = {item.ticker: item for item in report.entities}
        self.assertEqual(first_lei, by_ticker["ONE"].lei)
        self.assertEqual(first_isin, by_ticker["ONE"].isin)
        self.assertEqual(f"lei:{parent_lei}", by_ticker["ONE"].parent_entity_id)
        self.assertIn("Entity One Holdings", by_ticker["ONE"].aliases)
        self.assertEqual(second_lei, by_ticker["TWO"].lei)
        self.assertEqual(second_isin, by_ticker["TWO"].isin)

    def test_ten_company_pilot_resolves_without_fuzzy_matching(self) -> None:
        manifests: dict[str, dict[str, object]] = {}
        resolved: dict[str, GleifIdentity] = {}
        tickers: list[str] = []
        for index in range(10):
            ticker = f"P{index:02d}"
            lei = _lei(f"529900PILOT{index:07d}")
            isin = _isin(index + 10)
            tickers.append(ticker)
            manifests[ticker] = _identity_payload(lei, isin, ticker)
            resolved[lei] = GleifIdentity(
                lei=lei,
                legal_name=f"GLEIF Pilot Entity {index}",
                country_code="US",
                jurisdiction="US-DE",
                registered_as=str(index),
                registration_status="ISSUED",
                entity_status="ACTIVE",
                parent_lei=None,
                aliases=(),
                isins=(isin,),
                source_url=f"{GLEIF_API_ROOT}/lei-records/{lei}",
            )
        resolver = _ExactResolver(resolved)
        orchestrator = OrchestratorAgent()
        orchestrator.register(
            EntityRegistryAgent(
                manifests,
                primary_registry_client=resolver,
            )
        )

        report = orchestrator.run(watchlist=tickers)

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        self.assertEqual(10, len(report.entities))
        self.assertEqual(10, len(resolver.calls))
        self.assertEqual([], report.identity_conflicts)
        execution = report.executions[0]
        self.assertEqual(10, execution.result.metadata["primary_registry_resolved"])
        self.assertTrue(
            all(item.source == "gleif_primary_registry" for item in report.entities)
        )

    def test_conflicting_registry_value_is_quarantined_and_quality_gate_rejects(self) -> None:
        ticker = "PILOT"
        existing_lei = _lei("529900PILOT0000002")
        candidate_lei = _lei("529900PILOT0000003")
        isin = _isin(100)
        resolver = _ExactResolver(
            {
                existing_lei: GleifIdentity(
                    lei=candidate_lei,
                    legal_name="Conflicting Candidate",
                    country_code="US",
                    jurisdiction="US-DE",
                    registered_as="999",
                    registration_status="ISSUED",
                    entity_status="ACTIVE",
                    parent_lei=None,
                    isins=(isin,),
                    source_url=f"{GLEIF_API_ROOT}/lei-records/{candidate_lei}",
                )
            }
        )
        orchestrator = OrchestratorAgent(shadow_mode=True)
        orchestrator.register(
            EntityRegistryAgent(
                {ticker: _identity_payload(existing_lei, isin, ticker)},
                primary_registry_client=resolver,
            )
        )
        orchestrator.register(PredictionV21AdapterAgent())
        orchestrator.register(QualityGateAgent())

        report = orchestrator.run(
            watchlist=[ticker],
            state={"signals": _signals(ticker)},
        )

        self.assertEqual(AgentStatus.FAILED, report.status)
        self.assertEqual(existing_lei, report.entities[0].lei)
        self.assertEqual(1, len(report.identity_conflicts))
        self.assertEqual("QUARANTINED", report.identity_conflicts[0].status.value)
        self.assertEqual(GateDecision.REJECT, report.quality_checks[0].decision)
        reject_codes = {
            item["code"] for item in report.quality_checks[0].metadata["rejects"]
        }
        self.assertIn("quarantined_identity_conflict", reject_codes)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "identity.db")
            store.save_orchestration_report(report)
            persisted = store.read_entity_identity_conflicts(ticker)
        self.assertEqual(1, len(persisted))
        self.assertEqual("QUARANTINED", persisted.iloc[0]["status"])


if __name__ == "__main__":
    unittest.main()
