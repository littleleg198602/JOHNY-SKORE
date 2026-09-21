"""Point-in-time feature snapshots built only from already-ingested SEC facts.

This module intentionally has no network or scoring dependency.  It turns raw
facts into a traceable analytical input while preserving the distinction between
quarterly, annual, YTD and balance-sheet observations.  A filing-date-only
source is treated as available on the following UTC day: that is conservative
until an SEC accepted-at timestamp is collected explicitly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Iterable

from market_checker_app.agents.contracts import (
    FundamentalFact,
    FundamentalFeatureSnapshot,
)


SEC_FUNDAMENTAL_FEATURE_VERSION = "sec_fundamentals_pit_v3"

CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "Revenue",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_income": ("OperatingIncomeLoss",),
    "gross_profit": ("GrossProfit",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
    ),
    "research_and_development": (
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ),
    "interest_expense": (
        "InterestExpenseNonOperating",
        "InterestAndDebtExpense",
    ),
    "assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "total_debt": (
        "LongTermDebtAndShortTermBorrowings",
        "ShortTermBorrowingsAndCurrentPortionOfLongTermDebt",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ),
    "shares_outstanding": ("EntityCommonStockSharesOutstanding",),
    "weighted_average_shares_basic": (
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ),
    "weighted_average_shares_diluted": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
}

FLOW_METRICS = {
    "revenue",
    "net_income",
    "operating_income",
    "gross_profit",
    "operating_cash_flow",
    "capital_expenditure",
    "research_and_development",
    "interest_expense",
    "weighted_average_shares_basic",
    "weighted_average_shares_diluted",
}
BALANCE_METRICS = {
    "assets",
    "current_assets",
    "current_liabilities",
    "equity",
    "cash_and_equivalents",
    "total_debt",
    "shares_outstanding",
}
FEATURE_NAMES = (
    "revenue",
    "revenue_yoy_pct",
    "net_income",
    "gross_profit",
    "operating_income",
    "gross_margin_pct",
    "operating_margin_pct",
    "net_margin_pct",
    "operating_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
    "free_cash_flow_margin_pct",
    "cash_conversion_ratio",
    "accruals_to_assets_ratio",
    "capital_expenditure_to_revenue_pct",
    "research_and_development_to_revenue_pct",
    "interest_coverage_ratio",
    "current_ratio",
    "working_capital",
    "cash_and_equivalents",
    "total_debt",
    "debt_to_cash_ratio",
    "debt_to_assets_ratio",
    "shares_outstanding",
    "weighted_average_shares_basic",
    "weighted_average_shares_diluted",
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stable_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _duration_days(fact: FundamentalFact) -> int | None:
    if fact.period_start is None or fact.period_end is None:
        return None
    return (fact.period_end - fact.period_start).days


def period_basis(fact: FundamentalFact) -> str:
    """Classify a fact without converting cumulative YTD data into a quarter."""

    duration = _duration_days(fact)
    if duration is None:
        return "INSTANT"
    if 75 <= duration <= 110:
        return "QUARTER"
    if 320 <= duration <= 385:
        return "ANNUAL"
    return "YTD"


def _is_finite(fact: FundamentalFact) -> bool:
    return math.isfinite(float(fact.value))


def _available_as_of(fact: FundamentalFact, as_of: datetime) -> bool:
    # The source provides filing *date*, not accepted-at.  Same-day use would
    # therefore risk looking ahead; visibility starts at the next UTC date.
    return _as_utc(fact.filed_at).date() < _as_utc(as_of).date()


def _metric_facts(
    facts: Iterable[FundamentalFact],
    metric: str,
    as_of: datetime,
) -> list[FundamentalFact]:
    aliases = set(CONCEPTS[metric])
    return [
        fact
        for fact in facts
        if fact.concept in aliases
        and _is_finite(fact)
        and _available_as_of(fact, as_of)
    ]


def _latest_per_period(facts: Iterable[FundamentalFact]) -> list[FundamentalFact]:
    """Use the latest revision known *at the snapshot as-of*, never later."""

    selected: dict[tuple[object, ...], FundamentalFact] = {}
    for fact in facts:
        key = (
            fact.concept,
            fact.unit,
            fact.period_start,
            fact.period_end,
        )
        current = selected.get(key)
        if current is None or (fact.filed_at, fact.accession_number) > (
            current.filed_at,
            current.accession_number,
        ):
            selected[key] = fact
    return list(selected.values())


def _latest_anchor(facts: list[FundamentalFact], as_of: datetime) -> FundamentalFact | None:
    candidates: list[FundamentalFact] = []
    for metric in ("revenue", "operating_cash_flow", "assets", "cash_and_equivalents"):
        candidates.extend(_latest_per_period(_metric_facts(facts, metric, as_of)))
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda fact: (
            fact.period_end or datetime.min.replace(tzinfo=timezone.utc),
            1 if period_basis(fact) != "INSTANT" else 0,
            fact.filed_at,
            fact.accession_number,
        ),
    )


def _flow_for_anchor(
    facts: list[FundamentalFact],
    metric: str,
    anchor: FundamentalFact,
    as_of: datetime,
) -> FundamentalFact | None:
    matches = [
        fact
        for fact in _latest_per_period(_metric_facts(facts, metric, as_of))
        if fact.period_start == anchor.period_start
        and fact.period_end == anchor.period_end
        and period_basis(fact) == period_basis(anchor)
    ]
    if not matches:
        return None
    return max(matches, key=lambda fact: (fact.filed_at, fact.accession_number))


def _balance_for_anchor(
    facts: list[FundamentalFact],
    metric: str,
    anchor: FundamentalFact,
    as_of: datetime,
) -> FundamentalFact | None:
    if anchor.period_end is None:
        return None
    matches = [
        fact
        for fact in _latest_per_period(_metric_facts(facts, metric, as_of))
        if fact.period_end == anchor.period_end
    ]
    if not matches:
        return None
    return max(matches, key=lambda fact: (fact.filed_at, fact.accession_number))


def _ratio(
    numerator: FundamentalFact | None,
    denominator: FundamentalFact | None,
    label: str,
) -> tuple[float | None, str | None]:
    if numerator is None or denominator is None:
        return None, f"MISSING_CONCEPT:{label}"
    if numerator.unit != denominator.unit:
        return None, f"UNIT_MISMATCH:{label}"
    if denominator.value == 0:
        return None, f"ZERO_DENOMINATOR:{label}"
    value = numerator.value / denominator.value
    if not math.isfinite(value):
        return None, f"NONFINITE_RESULT:{label}"
    return value, None


def _debt_components(facts: list[FundamentalFact], anchor: FundamentalFact, as_of: datetime) -> list[FundamentalFact]:
    """Select one complete, non-overlapping debt disclosure from one filing.

    LongTermDebt is an aggregate of current and noncurrent long-term debt;
    ShortTermBorrowings is separate. Missing components are never assumed 0.
    Lease-inclusive current debt cannot be combined with lease-exclusive debt.
    """
    matches = [fact for fact in _latest_per_period(_metric_facts(facts, "total_debt", as_of))
               if fact.period_end == anchor.period_end and fact.period_start is None and fact.value >= 0]
    if not matches:
        return []
    newest = max(matches, key=lambda fact: (fact.filed_at, fact.accession_number, fact.fact_id))
    same_filing = {fact.concept: fact for fact in sorted(matches, key=lambda fact: fact.fact_id)
                   if fact.accession_number == newest.accession_number and fact.unit == newest.unit}
    for concepts in (
        ("LongTermDebtAndShortTermBorrowings",),
        ("LongTermDebt", "ShortTermBorrowings"),
        ("ShortTermBorrowingsAndCurrentPortionOfLongTermDebt", "LongTermDebtNoncurrent"),
        ("LongTermDebtCurrent", "LongTermDebtNoncurrent", "ShortTermBorrowings"),
    ):
        if all(concept in same_filing for concept in concepts):
            return [same_filing[concept] for concept in concepts]
    return []


def _prior_comparable_revenue(
    facts: list[FundamentalFact],
    anchor: FundamentalFact,
    as_of: datetime,
) -> FundamentalFact | None:
    if anchor.period_end is None:
        return None
    duration = _duration_days(anchor)
    if duration is None:
        return None
    candidates = []
    for fact in _latest_per_period(_metric_facts(facts, "revenue", as_of)):
        candidate_duration = _duration_days(fact)
        if (
            fact.unit == anchor.unit
            and fact.period_end is not None
            and candidate_duration is not None
            and fact.period_end < anchor.period_end
            and abs(candidate_duration - duration) <= 15
        ):
            end_delta = (anchor.period_end - fact.period_end).days
            if 330 <= end_delta <= 400:
                candidates.append(fact)
    return max(candidates, key=lambda fact: fact.period_end) if candidates else None


def build_sec_fundamental_feature_snapshots(
    facts_by_ticker: dict[str, list[FundamentalFact]],
    *,
    as_of: datetime,
    observed_at: datetime | None = None,
) -> list[FundamentalFeatureSnapshot]:
    """Create one conservative, exportable snapshot per ticker with SEC facts."""

    as_of = _as_utc(as_of)
    observed_at = _as_utc(observed_at or as_of)
    snapshots: list[FundamentalFeatureSnapshot] = []
    for ticker in sorted(facts_by_ticker):
        facts = list(facts_by_ticker[ticker])
        anchor = _latest_anchor(facts, as_of)
        if anchor is None:
            continue

        selected: dict[str, FundamentalFact] = {}
        for metric in FLOW_METRICS:
            fact = _flow_for_anchor(facts, metric, anchor, as_of)
            if fact is not None:
                selected[metric] = fact
        for metric in BALANCE_METRICS:
            if metric == "total_debt":
                continue
            fact = _balance_for_anchor(facts, metric, anchor, as_of)
            if fact is not None:
                selected[metric] = fact

        components = _debt_components(facts, anchor, as_of)
        if components:
            selected["total_debt"] = replace(
                components[0], fact_id="derived:total_debt", concept="derived_total_debt",
                value=sum(fact.value for fact in components),
            )

        values: dict[str, float] = {}
        missing: dict[str, str] = {}
        source_fact_ids: dict[str, list[str]] = defaultdict(list)

        def raw(metric: str) -> FundamentalFact | None:
            fact = selected.get(metric)
            if fact is None:
                missing[metric] = f"MISSING_CONCEPT:{metric}"
                return None
            values[metric] = fact.value
            source_fact_ids[metric].append(fact.fact_id)
            return fact

        revenue = raw("revenue")
        net_income = raw("net_income")
        gross_profit = raw("gross_profit")
        operating_income = raw("operating_income")
        operating_cash_flow = raw("operating_cash_flow")
        capex = raw("capital_expenditure")
        research_and_development = raw("research_and_development")
        interest_expense = raw("interest_expense")
        cash = raw("cash_and_equivalents")
        debt = raw("total_debt")
        assets = raw("assets")
        current_assets = raw("current_assets")
        current_liabilities = raw("current_liabilities")
        raw("equity")
        raw("shares_outstanding")
        raw("weighted_average_shares_basic")
        raw("weighted_average_shares_diluted")
        if debt is None:
            missing["total_debt"] = "INCOMPLETE_NONOVERLAPPING_DEBT_COMPONENTS"

        def calculated(
            name: str,
            numerator: FundamentalFact | None,
            denominator: FundamentalFact | None,
            *,
            scale: float = 1.0,
        ) -> None:
            value, reason = _ratio(numerator, denominator, name)
            if value is None:
                missing[name] = reason or f"MISSING_CONCEPT:{name}"
                return
            values[name] = value * scale
            for fact in (numerator, denominator):
                if fact is not None and fact.fact_id not in source_fact_ids[name]:
                    source_fact_ids[name].append(fact.fact_id)

        calculated("gross_margin_pct", gross_profit, revenue, scale=100.0)
        calculated("operating_margin_pct", operating_income, revenue, scale=100.0)
        calculated("net_margin_pct", net_income, revenue, scale=100.0)
        calculated("debt_to_cash_ratio", debt, cash)
        calculated("debt_to_assets_ratio", debt, assets)
        calculated("cash_conversion_ratio", operating_cash_flow, net_income)
        calculated("current_ratio", current_assets, current_liabilities)
        calculated("interest_coverage_ratio", operating_income, interest_expense)
        if debt is not None:
            for name, ids in source_fact_ids.items():
                if debt.fact_id in ids:
                    source_fact_ids[name] = [item for item in ids if item != debt.fact_id] + [fact.fact_id for fact in components]

        if operating_cash_flow is None or capex is None:
            missing["free_cash_flow"] = "MISSING_CONCEPT:operating_cash_flow_or_capital_expenditure"
        elif operating_cash_flow.unit != capex.unit:
            missing["free_cash_flow"] = "UNIT_MISMATCH:operating_cash_flow_or_capital_expenditure"
        else:
            values["free_cash_flow"] = operating_cash_flow.value - abs(capex.value)
            source_fact_ids["free_cash_flow"] = [
                operating_cash_flow.fact_id,
                capex.fact_id,
            ]
            if revenue is None or revenue.value == 0:
                missing["free_cash_flow_margin_pct"] = (
                    "MISSING_OR_ZERO_DENOMINATOR:revenue"
                )
            elif revenue.unit != operating_cash_flow.unit:
                missing["free_cash_flow_margin_pct"] = (
                    "UNIT_MISMATCH:free_cash_flow_margin_pct"
                )
            else:
                values["free_cash_flow_margin_pct"] = (
                    values["free_cash_flow"] / revenue.value
                ) * 100.0
                source_fact_ids["free_cash_flow_margin_pct"] = [
                    operating_cash_flow.fact_id,
                    capex.fact_id,
                    revenue.fact_id,
                ]

        if capex is None or revenue is None or revenue.value == 0:
            missing["capital_expenditure_to_revenue_pct"] = (
                "MISSING_OR_ZERO_DENOMINATOR:capital_expenditure_or_revenue"
            )
        elif capex.unit != revenue.unit:
            missing["capital_expenditure_to_revenue_pct"] = (
                "UNIT_MISMATCH:capital_expenditure_to_revenue_pct"
            )
        else:
            values["capital_expenditure_to_revenue_pct"] = (
                abs(capex.value) / revenue.value
            ) * 100.0
            source_fact_ids["capital_expenditure_to_revenue_pct"] = [
                capex.fact_id,
                revenue.fact_id,
            ]

        calculated(
            "research_and_development_to_revenue_pct",
            research_and_development,
            revenue,
            scale=100.0,
        )

        if net_income is None or operating_cash_flow is None or assets is None:
            missing["accruals_to_assets_ratio"] = (
                "MISSING_CONCEPT:net_income_or_operating_cash_flow_or_assets"
            )
        elif net_income.unit != operating_cash_flow.unit or net_income.unit != assets.unit:
            missing["accruals_to_assets_ratio"] = (
                "UNIT_MISMATCH:accruals_to_assets_ratio"
            )
        elif assets.value == 0:
            missing["accruals_to_assets_ratio"] = (
                "ZERO_DENOMINATOR:accruals_to_assets_ratio"
            )
        else:
            values["accruals_to_assets_ratio"] = (
                net_income.value - operating_cash_flow.value
            ) / assets.value
            source_fact_ids["accruals_to_assets_ratio"] = [
                net_income.fact_id,
                operating_cash_flow.fact_id,
                assets.fact_id,
            ]

        if current_assets is None or current_liabilities is None:
            missing["working_capital"] = (
                "MISSING_CONCEPT:current_assets_or_current_liabilities"
            )
        elif current_assets.unit != current_liabilities.unit:
            missing["working_capital"] = "UNIT_MISMATCH:working_capital"
        else:
            values["working_capital"] = (
                current_assets.value - current_liabilities.value
            )
            source_fact_ids["working_capital"] = [
                current_assets.fact_id,
                current_liabilities.fact_id,
            ]

        prior_revenue = _prior_comparable_revenue(facts, revenue, as_of) if revenue else None
        if revenue is None or prior_revenue is None:
            missing["revenue_yoy_pct"] = "MISSING_COMPARABLE_PRIOR_PERIOD:revenue"
        elif prior_revenue.value == 0:
            missing["revenue_yoy_pct"] = "ZERO_DENOMINATOR:revenue_yoy_pct"
        else:
            values["revenue_yoy_pct"] = ((revenue.value / prior_revenue.value) - 1.0) * 100.0
            source_fact_ids["revenue_yoy_pct"] = [revenue.fact_id, prior_revenue.fact_id]

        for name in FEATURE_NAMES:
            if name not in values and name not in missing:
                missing[name] = f"MISSING_CONCEPT:{name}"

        referenced_ids = {
            fact_id for ids in source_fact_ids.values() for fact_id in ids
        }
        referenced = [fact for fact in facts if fact.fact_id in referenced_ids]
        availability_at = max(
            (_as_utc(fact.filed_at) + timedelta(days=1) for fact in referenced),
            default=_as_utc(anchor.filed_at) + timedelta(days=1),
        )
        source_accessions = {
            name: sorted(
                {
                    fact.accession_number
                    for fact in facts
                    if fact.fact_id in ids
                }
            )
            for name, ids in source_fact_ids.items()
        }
        source_urls = {
            name: sorted(
                {
                    fact.source_url
                    for fact in facts
                    if fact.fact_id in ids
                }
            )
            for name, ids in source_fact_ids.items()
        }
        lineage: dict[str, list[str]] = {}
        for metric, fact in selected.items():
            lineage[metric] = [
                candidate.fact_id
                for candidate in sorted(
                    _metric_facts(facts, metric, as_of),
                    key=lambda candidate: (candidate.filed_at, candidate.accession_number),
                )
                if candidate.concept == fact.concept
                and candidate.unit == fact.unit
                and candidate.period_start == fact.period_start
                and candidate.period_end == fact.period_end
            ]

        snapshot_id = _stable_id(
            SEC_FUNDAMENTAL_FEATURE_VERSION,
            ticker,
            as_of.isoformat(),
            anchor.period_start.isoformat() if anchor.period_start else "",
            anchor.period_end.isoformat() if anchor.period_end else "",
            period_basis(anchor),
        )
        snapshots.append(
            FundamentalFeatureSnapshot(
                snapshot_id=snapshot_id,
                ticker=ticker,
                feature_version=SEC_FUNDAMENTAL_FEATURE_VERSION,
                as_of=as_of,
                observed_at=observed_at,
                availability_at=availability_at,
                period_basis=period_basis(anchor),
                period_start=anchor.period_start,
                period_end=anchor.period_end,
                values=dict(sorted(values.items())),
                missing_reasons=dict(sorted(missing.items())),
                source_fact_ids={key: list(value) for key, value in source_fact_ids.items()},
                source_accessions=source_accessions,
                source_urls=source_urls,
                metadata={
                    "cik": anchor.cik,
                    "source": "SEC companyfacts",
                    "scoring_applied": False,
                    "availability_precision": "FILING_DATE",
                    "availability_policy": "CONSERVATIVE_NEXT_UTC_DAY",
                    "revision_lineage_fact_ids": lineage,
                    "anchor_fact_id": anchor.fact_id,
                    "anchor_concept": anchor.concept,
                },
            )
        )
    return snapshots
