from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from market_checker_app.agents.contracts import (
    AgentExecution,
    AgentResult,
    AgentStatus,
    FundamentalFact,
    OrchestrationReport,
)
from market_checker_app.services.sec_fundamental_feature_service import (
    build_sec_fundamental_feature_snapshots,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _fact(
    concept: str,
    value: float,
    *,
    start: str | None = None,
    end: str = "2025-01-31",
    filed: str = "2025-03-01",
    accession: str = "0001",
    unit: str = "USD",
) -> FundamentalFact:
    return FundamentalFact(
        fact_id=f"{concept}:{accession}:{start}:{end}:{value}",
        ticker="ACME",
        cik="0000000001",
        taxonomy="us-gaap",
        concept=concept,
        label=concept,
        description=concept,
        unit=unit,
        value=value,
        observed_at=_dt("2026-01-01"),
        filed_at=_dt(filed),
        form="10-Q",
        accession_number=accession,
        source_url=f"https://sec.example/{accession}",
        document_id=f"sec:{accession}",
        period_start=_dt(start) if start else None,
        period_end=_dt(end),
    )


class SecFundamentalFeatureServiceTests(unittest.TestCase):
    def _snapshot(self, facts: list[FundamentalFact], as_of: str = "2025-08-01"):
        snapshots = build_sec_fundamental_feature_snapshots(
            {"ACME": facts},
            as_of=_dt(as_of),
        )
        self.assertEqual(1, len(snapshots))
        return snapshots[0]

    def test_keeps_quarterly_and_ytd_periods_separate(self) -> None:
        facts = [
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                120.0,
                start="2025-02-01",
                end="2025-04-30",
                accession="quarter",
            ),
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                220.0,
                start="2025-02-01",
                end="2025-07-31",
                accession="ytd",
            ),
            _fact(
                "NetIncomeLoss",
                24.0,
                start="2025-02-01",
                end="2025-04-30",
                accession="quarter",
            ),
        ]

        snapshot = self._snapshot(facts)

        self.assertEqual("YTD", snapshot.period_basis)
        self.assertEqual(220.0, snapshot.values["revenue"])
        self.assertNotIn("net_margin_pct", snapshot.values)
        self.assertEqual(
            "MISSING_CONCEPT:net_margin_pct",
            snapshot.missing_reasons["net_margin_pct"],
        )

    def test_calculates_yoy_for_non_calendar_fiscal_year(self) -> None:
        facts = [
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                130.0,
                start="2024-11-01",
                end="2025-01-31",
                accession="current",
            ),
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                100.0,
                start="2023-11-01",
                end="2024-01-31",
                filed="2024-03-01",
                accession="prior",
            ),
        ]

        snapshot = self._snapshot(facts)

        self.assertEqual("QUARTER", snapshot.period_basis)
        self.assertAlmostEqual(30.0, snapshot.values["revenue_yoy_pct"])
        self.assertEqual(
            [
                "RevenueFromContractWithCustomerExcludingAssessedTax:current:2024-11-01:2025-01-31:130.0",
                "RevenueFromContractWithCustomerExcludingAssessedTax:prior:2023-11-01:2024-01-31:100.0",
            ],
            snapshot.source_fact_ids["revenue_yoy_pct"],
        )

    def test_later_restatement_cannot_change_earlier_asof_snapshot(self) -> None:
        original = _fact(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            100.0,
            start="2024-11-01",
            end="2025-01-31",
            filed="2025-03-01",
            accession="original",
        )
        restated = _fact(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            120.0,
            start="2024-11-01",
            end="2025-01-31",
            filed="2025-06-01",
            accession="restated",
        )

        before = self._snapshot([original, restated], "2025-05-01")
        after = self._snapshot([original, restated], "2025-07-01")

        self.assertEqual(100.0, before.values["revenue"])
        self.assertEqual(120.0, after.values["revenue"])
        self.assertEqual([original.fact_id], before.source_fact_ids["revenue"])
        self.assertEqual([restated.fact_id], after.source_fact_ids["revenue"])
        self.assertEqual(
            [original.fact_id, restated.fact_id],
            after.metadata["revision_lineage_fact_ids"]["revenue"],
        )

    def test_persisted_snapshot_is_not_overwritten_on_conflict(self) -> None:
        original = self._snapshot(
            [
                _fact(
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                    100.0,
                    start="2024-11-01",
                    end="2025-01-31",
                )
            ]
        )
        conflicting = replace(
            original,
            observed_at=_dt("2025-08-02"),
            values={**original.values, "revenue": 999.0},
        )

        def report(snapshot, orchestration_id: str) -> OrchestrationReport:
            execution = AgentExecution(
                agent_name="f2_sec",
                agent_version="1.2",
                required=False,
                dependencies=(),
                started_at=snapshot.observed_at,
                finished_at=snapshot.observed_at,
                elapsed_ms=0.0,
                input_count=1,
                result=AgentResult(
                    status=AgentStatus.SUCCESS,
                    fundamental_feature_snapshots=[snapshot],
                ),
            )
            return OrchestrationReport(
                orchestration_id=orchestration_id,
                started_at=snapshot.observed_at,
                finished_at=snapshot.observed_at,
                status=AgentStatus.SUCCESS,
                shadow_mode=True,
                watchlist_size=1,
                executions=[execution],
            )

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "snapshot.db")
            store.save_orchestration_report(report(original, "run-original"))
            store.save_orchestration_report(report(conflicting, "run-conflict"))
            stored = store.read_fundamental_feature_snapshots("ACME")

        self.assertEqual(1, len(stored))
        self.assertEqual(100.0, json.loads(stored.iloc[0]["values_json"])["revenue"])

    def test_missing_capex_is_explicit_and_never_zero(self) -> None:
        facts = [
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                100.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact(
                "NetCashProvidedByUsedInOperatingActivities",
                30.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
        ]

        snapshot = self._snapshot(facts)

        self.assertNotIn("capital_expenditure", snapshot.values)
        self.assertNotIn("free_cash_flow", snapshot.values)
        self.assertEqual(
            "MISSING_CONCEPT:capital_expenditure",
            snapshot.missing_reasons["capital_expenditure"],
        )
        self.assertEqual(
            "MISSING_CONCEPT:operating_cash_flow_or_capital_expenditure",
            snapshot.missing_reasons["free_cash_flow"],
        )

    def test_balance_sheet_values_align_by_period_end(self) -> None:
        facts = [
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                100.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact("CashAndCashEquivalentsAtCarryingValue", 40.0),
            _fact("LongTermDebt", 100.0),
            _fact("ShortTermBorrowings", 0.0),
            _fact("Assets", 400.0),
        ]

        snapshot = self._snapshot(facts)

        self.assertEqual(40.0, snapshot.values["cash_and_equivalents"])
        self.assertEqual(100.0, snapshot.values["total_debt"])
        self.assertAlmostEqual(2.5, snapshot.values["debt_to_cash_ratio"])
        self.assertAlmostEqual(0.25, snapshot.values["debt_to_assets_ratio"])

    def test_calculates_pdf_quality_cashflow_and_liquidity_features(self) -> None:
        facts = [
            _fact(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                200.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact("NetIncomeLoss", 20.0, start="2024-11-01", end="2025-01-31"),
            _fact(
                "OperatingIncomeLoss",
                30.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact(
                "NetCashProvidedByUsedInOperatingActivities",
                35.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact(
                "PaymentsToAcquirePropertyPlantAndEquipment",
                10.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact(
                "ResearchAndDevelopmentExpense",
                16.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact(
                "InterestExpenseNonOperating",
                5.0,
                start="2024-11-01",
                end="2025-01-31",
            ),
            _fact("Assets", 500.0),
            _fact("AssetsCurrent", 180.0),
            _fact("LiabilitiesCurrent", 90.0),
        ]

        snapshot = self._snapshot(facts)

        self.assertEqual(25.0, snapshot.values["free_cash_flow"])
        self.assertAlmostEqual(12.5, snapshot.values["free_cash_flow_margin_pct"])
        self.assertAlmostEqual(1.75, snapshot.values["cash_conversion_ratio"])
        self.assertAlmostEqual(-0.03, snapshot.values["accruals_to_assets_ratio"])
        self.assertAlmostEqual(5.0, snapshot.values["capital_expenditure_to_revenue_pct"])
        self.assertAlmostEqual(8.0, snapshot.values["research_and_development_to_revenue_pct"])
        self.assertAlmostEqual(6.0, snapshot.values["interest_coverage_ratio"])
        self.assertAlmostEqual(2.0, snapshot.values["current_ratio"])
        self.assertEqual(90.0, snapshot.values["working_capital"])


if __name__ == "__main__":
    unittest.main()
