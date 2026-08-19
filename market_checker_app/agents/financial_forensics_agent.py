from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import math
from typing import Any, Iterable

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentEvidence,
    AgentResult,
    AgentStatus,
    FundamentalFact,
    utc_now,
)
from market_checker_app.config import FinancialForensicsConfig
from market_checker_app.utils.text import normalize_ticker


CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_income": ("OperatingIncomeLoss",),
    "gross_profit": ("GrossProfit",),
    "assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "liabilities": ("Liabilities",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
    ),
    "long_term_debt_current": ("LongTermDebtCurrent",),
    "long_term_debt_noncurrent": ("LongTermDebtNoncurrent",),
    "long_term_debt": ("LongTermDebt",),
    "short_term_debt": ("ShortTermBorrowings",),
    "receivables": ("AccountsReceivableNetCurrent",),
    "inventory": ("InventoryNet",),
}

EXPECTED_METRICS = 10


def _stable_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _form_base(value: str) -> str:
    return str(value or "").strip().upper().removesuffix("/A")


def _duration_days(fact: FundamentalFact) -> int | None:
    if fact.period_start is None or fact.period_end is None:
        return None
    return max(0, (fact.period_end - fact.period_start).days)


def _fact_sort_key(fact: FundamentalFact) -> tuple[datetime, datetime, str]:
    return (
        fact.period_end or datetime.min.replace(tzinfo=timezone.utc),
        fact.filed_at,
        fact.accession_number,
    )


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _concept_facts(
    facts: Iterable[FundamentalFact],
    metric: str,
) -> list[FundamentalFact]:
    aliases = set(CONCEPTS[metric])
    return [fact for fact in facts if fact.concept in aliases and _finite(fact.value)]


def _canonical_series(
    facts: Iterable[FundamentalFact],
    metric: str,
) -> list[FundamentalFact]:
    by_period: dict[tuple[object, ...], FundamentalFact] = {}
    for fact in _concept_facts(facts, metric):
        key = (
            fact.unit,
            fact.period_start.isoformat() if fact.period_start else None,
            fact.period_end.isoformat() if fact.period_end else None,
            _form_base(fact.form),
        )
        existing = by_period.get(key)
        if existing is None or fact.filed_at > existing.filed_at:
            by_period[key] = fact
    return sorted(by_period.values(), key=_fact_sort_key, reverse=True)


def _latest_pair(
    facts: Iterable[FundamentalFact],
    metric: str,
) -> tuple[FundamentalFact | None, FundamentalFact | None]:
    series = _canonical_series(facts, metric)
    if not series:
        return None, None
    latest = series[0]
    latest_duration = _duration_days(latest)
    for candidate in series[1:]:
        if candidate.unit != latest.unit:
            continue
        if (
            latest.period_end is not None
            and candidate.period_end is not None
            and candidate.period_end >= latest.period_end
        ):
            # The same comparative period can be repeated in several filings.
            # It is not a prior observation suitable for a growth calculation.
            continue
        candidate_duration = _duration_days(candidate)
        if latest_duration is None:
            if candidate_duration is None:
                return latest, candidate
            continue
        if candidate_duration is None:
            continue
        if abs(candidate_duration - latest_duration) <= 15:
            return latest, candidate
    return latest, None


def _match_period(
    anchor: FundamentalFact | None,
    candidates: Iterable[FundamentalFact],
    metric: str,
) -> FundamentalFact | None:
    if anchor is None:
        return None
    matches = _concept_facts(candidates, metric)
    exact = [
        fact
        for fact in matches
        if fact.unit == anchor.unit
        and fact.period_start == anchor.period_start
        and fact.period_end == anchor.period_end
    ]
    if exact:
        return max(exact, key=lambda fact: (fact.filed_at, fact.accession_number))
    same_end = [
        fact
        for fact in matches
        if fact.unit == anchor.unit and fact.period_end == anchor.period_end
    ]
    if same_end:
        return max(same_end, key=lambda fact: (fact.filed_at, fact.accession_number))
    return None


def _growth_pct(
    latest: FundamentalFact | None,
    previous: FundamentalFact | None,
) -> float | None:
    if latest is None or previous is None or previous.value == 0:
        return None
    return ((latest.value / previous.value) - 1.0) * 100.0


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _round_metric(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


class FinancialForensicsAgent(BaseAgent):
    """Create conservative anomaly diagnostics from normalized SEC facts.

    Findings are screening indicators, not fraud conclusions, trading signals,
    or automatic vetoes.  Decision integration remains a later stage.
    """

    name = "financial_forensics"
    version = "1.0"
    required = False
    dependencies = ("f2_sec",)

    def __init__(self, config: FinancialForensicsConfig | None = None) -> None:
        self.config = config or FinancialForensicsConfig()

    def _analyse_ticker(
        self,
        ticker: str,
        facts: list[FundamentalFact],
    ) -> tuple[dict[str, float], list[dict[str, Any]]]:
        metrics: dict[str, float] = {}
        findings: list[dict[str, Any]] = []

        def finding(
            code: str,
            severity: str,
            message: str,
            *,
            value: float | None = None,
            threshold: float | None = None,
        ) -> None:
            findings.append(
                {
                    "code": code,
                    "severity": severity,
                    "message": message,
                    "value": _round_metric(value),
                    "threshold": _round_metric(threshold),
                }
            )

        revenue, previous_revenue = _latest_pair(facts, "revenue")
        revenue_growth = _growth_pct(revenue, previous_revenue)
        if revenue_growth is not None:
            metrics["revenue_growth_pct"] = revenue_growth

        net_income = _match_period(revenue, facts, "net_income")
        if revenue is not None and net_income is not None:
            net_margin = _ratio(net_income.value, revenue.value)
            if net_margin is not None:
                metrics["net_margin_pct"] = net_margin * 100.0

        operating_income = _match_period(revenue, facts, "operating_income")
        if revenue is not None and operating_income is not None:
            operating_margin = _ratio(operating_income.value, revenue.value)
            if operating_margin is not None:
                metrics["operating_margin_pct"] = operating_margin * 100.0

        gross_profit = _match_period(revenue, facts, "gross_profit")
        if revenue is not None and gross_profit is not None:
            gross_margin = _ratio(gross_profit.value, revenue.value)
            if gross_margin is not None:
                metrics["gross_margin_pct"] = gross_margin * 100.0

        cash_anchor = net_income or revenue
        operating_cash_flow = _match_period(
            cash_anchor,
            facts,
            "operating_cash_flow",
        )
        if net_income is not None and operating_cash_flow is not None:
            cash_conversion = _ratio(operating_cash_flow.value, net_income.value)
            if cash_conversion is not None:
                metrics["cash_conversion_ratio"] = cash_conversion
                if net_income.value > 0 and operating_cash_flow.value < 0:
                    finding(
                        "positive_income_negative_operating_cash_flow",
                        "HIGH",
                        "Kladný čistý zisk není podpořen provozním cash flow.",
                        value=cash_conversion,
                        threshold=0.0,
                    )
                elif net_income.value > 0 and cash_conversion < self.config.low_cash_conversion_ratio:
                    finding(
                        "low_cash_conversion",
                        "WARN",
                        "Provozní cash flow je nízké vůči vykázanému zisku.",
                        value=cash_conversion,
                        threshold=self.config.low_cash_conversion_ratio,
                    )

        capital_expenditure = _match_period(
            operating_cash_flow,
            facts,
            "capital_expenditure",
        )
        if operating_cash_flow is not None and capital_expenditure is not None:
            free_cash_flow = operating_cash_flow.value - abs(capital_expenditure.value)
            metrics["free_cash_flow_proxy"] = free_cash_flow
            if net_income is not None and net_income.value > 0 and free_cash_flow < 0:
                finding(
                    "positive_income_negative_free_cash_flow",
                    "WARN",
                    "Kladný zisk doprovází záporný proxy free cash flow.",
                    value=free_cash_flow,
                    threshold=0.0,
                )

        assets, _ = _latest_pair(facts, "assets")
        liabilities = _match_period(assets, facts, "liabilities")
        if assets is not None and liabilities is not None:
            liabilities_to_assets = _ratio(liabilities.value, assets.value)
            if liabilities_to_assets is not None:
                metrics["liabilities_to_assets_ratio"] = liabilities_to_assets
                if liabilities_to_assets >= self.config.critical_liabilities_to_assets_ratio:
                    finding(
                        "liabilities_exceed_assets",
                        "HIGH",
                        "Závazky dosahují nebo překračují celková aktiva.",
                        value=liabilities_to_assets,
                        threshold=self.config.critical_liabilities_to_assets_ratio,
                    )
                elif liabilities_to_assets >= self.config.high_liabilities_to_assets_ratio:
                    finding(
                        "high_liabilities_to_assets",
                        "WARN",
                        "Podíl závazků na aktivech je zvýšený; interpretace závisí na sektoru.",
                        value=liabilities_to_assets,
                        threshold=self.config.high_liabilities_to_assets_ratio,
                    )

        if assets is not None:
            direct_debt = _match_period(assets, facts, "long_term_debt")
            debt_components = [
                _match_period(assets, facts, "long_term_debt_current"),
                _match_period(assets, facts, "long_term_debt_noncurrent"),
                _match_period(assets, facts, "short_term_debt"),
            ]
            if direct_debt is not None:
                debt_value = direct_debt.value + sum(
                    item.value for item in debt_components[2:] if item is not None
                )
            else:
                available_components = [item for item in debt_components if item is not None]
                debt_value = (
                    sum(item.value for item in available_components)
                    if available_components
                    else None
                )
            if debt_value is not None:
                debt_to_assets = _ratio(debt_value, assets.value)
                if debt_to_assets is not None:
                    metrics["debt_to_assets_ratio"] = debt_to_assets
                    if debt_to_assets >= self.config.critical_debt_to_assets_ratio:
                        finding(
                            "critical_debt_to_assets",
                            "HIGH",
                            "Úročený dluh je velmi vysoký vůči aktivům.",
                            value=debt_to_assets,
                            threshold=self.config.critical_debt_to_assets_ratio,
                        )
                    elif debt_to_assets >= self.config.high_debt_to_assets_ratio:
                        finding(
                            "high_debt_to_assets",
                            "WARN",
                            "Úročený dluh je zvýšený vůči aktivům.",
                            value=debt_to_assets,
                            threshold=self.config.high_debt_to_assets_ratio,
                        )

        current_assets, _ = _latest_pair(facts, "current_assets")
        current_liabilities = _match_period(
            current_assets,
            facts,
            "current_liabilities",
        )
        if current_assets is not None and current_liabilities is not None:
            current_ratio = _ratio(current_assets.value, current_liabilities.value)
            if current_ratio is not None:
                metrics["current_ratio"] = current_ratio
                if current_ratio < self.config.critical_current_ratio:
                    finding(
                        "critical_current_ratio",
                        "HIGH",
                        "Krátkodobá likvidita je výrazně pod konzervativní hranicí.",
                        value=current_ratio,
                        threshold=self.config.critical_current_ratio,
                    )
                elif current_ratio < self.config.low_current_ratio:
                    finding(
                        "low_current_ratio",
                        "WARN",
                        "Krátkodobá likvidita je pod 1,0; interpretace závisí na oboru.",
                        value=current_ratio,
                        threshold=self.config.low_current_ratio,
                    )

        if net_income is not None and operating_cash_flow is not None and assets is not None:
            accruals_to_assets = _ratio(
                net_income.value - operating_cash_flow.value,
                assets.value,
            )
            if accruals_to_assets is not None:
                metrics["accruals_to_assets_ratio"] = accruals_to_assets
                if accruals_to_assets > 0.10:
                    finding(
                        "high_accruals_to_assets",
                        "WARN",
                        "Rozdíl mezi ziskem a provozním cash flow je vysoký vůči aktivům.",
                        value=accruals_to_assets,
                        threshold=0.10,
                    )

        for metric_name, code_prefix in (
            ("receivables", "receivables"),
            ("inventory", "inventory"),
        ):
            latest, previous = _latest_pair(facts, metric_name)
            item_growth = _growth_pct(latest, previous)
            if item_growth is None:
                continue
            metrics[f"{code_prefix}_growth_pct"] = item_growth
            if (
                revenue_growth is not None
                and item_growth - revenue_growth
                > self.config.working_capital_growth_divergence_pct
            ):
                finding(
                    f"{code_prefix}_growth_outpaces_revenue",
                    "WARN",
                    (
                        "Růst pohledávek výrazně převyšuje růst tržeb."
                        if code_prefix == "receivables"
                        else "Růst zásob výrazně převyšuje růst tržeb."
                    ),
                    value=item_growth - revenue_growth,
                    threshold=self.config.working_capital_growth_divergence_pct,
                )

        restatements: list[dict[str, Any]] = []
        grouped: dict[tuple[object, ...], list[FundamentalFact]] = defaultdict(list)
        for fact in facts:
            grouped[
                (
                    fact.taxonomy,
                    fact.concept,
                    fact.unit,
                    fact.period_start,
                    fact.period_end,
                )
            ].append(fact)
        for group in grouped.values():
            accessions = {fact.accession_number for fact in group}
            if len(accessions) < 2:
                continue
            values = [float(fact.value) for fact in group if _finite(fact.value)]
            if len(values) < 2:
                continue
            denominator = max(max(abs(value) for value in values), 1.0)
            change_pct = (max(values) - min(values)) / denominator * 100.0
            if change_pct >= self.config.material_restatement_pct:
                restatements.append(
                    {
                        "concept": group[0].concept,
                        "period_end": (
                            group[0].period_end.isoformat()
                            if group[0].period_end
                            else None
                        ),
                        "change_pct": round(change_pct, 4),
                        "accessions": sorted(accessions),
                    }
                )
        if restatements:
            metrics["potential_restatement_count"] = float(len(restatements))
            finding(
                "potential_restatement_or_recast",
                "WARN",
                "Stejné účetní období má mezi podáními materiálně odlišné hodnoty; může jít o restatement nebo recast.",
                value=float(len(restatements)),
                threshold=1.0,
            )

        dated_facts = [fact for fact in facts if fact.period_end is not None]
        if dated_facts:
            latest_filing_fact = max(
                dated_facts,
                key=lambda fact: (
                    fact.period_end
                    or datetime.min.replace(tzinfo=timezone.utc),
                    fact.filed_at,
                ),
            )
            lag_days = (latest_filing_fact.filed_at - latest_filing_fact.period_end).days
            metrics["filing_lag_days"] = float(lag_days)
            form = _form_base(latest_filing_fact.form)
            threshold = (
                self.config.annual_filing_lag_days
                if form in {"10-K", "20-F", "40-F"}
                else self.config.quarterly_filing_lag_days
            )
            if lag_days > threshold:
                finding(
                    "long_filing_lag",
                    "WARN",
                    "Odstup mezi koncem období a filingem je neobvykle dlouhý.",
                    value=float(lag_days),
                    threshold=float(threshold),
                )

        if restatements:
            metrics["potential_restatement_max_change_pct"] = max(
                item["change_pct"] for item in restatements
            )
        return metrics, findings

    def run(self, context: AgentContext) -> AgentResult:
        if not self.config.enabled:
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["FinancialForensicsAgent je vypnutý v konfiguraci."],
            )
        raw_by_ticker = context.state.get("fundamental_facts_by_ticker")
        if not isinstance(raw_by_ticker, dict):
            return AgentResult(
                status=AgentStatus.UNAVAILABLE,
                warnings=["FinancialForensicsAgent nedostal fundamental_facts_by_ticker."],
            )

        observed_at = utc_now()
        evidence: list[AgentEvidence] = []
        summaries: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        low_coverage = 0
        missing = 0

        for raw_ticker in context.watchlist:
            ticker = normalize_ticker(raw_ticker)
            raw_facts = raw_by_ticker.get(ticker)
            facts = (
                [fact for fact in raw_facts if isinstance(fact, FundamentalFact)]
                if isinstance(raw_facts, list)
                else []
            )
            if not facts:
                missing += 1
                continue
            metrics, findings = self._analyse_ticker(ticker, facts)
            if not metrics:
                missing += 1
                continue

            coverage = min(1.0, len(metrics) / EXPECTED_METRICS)
            if coverage < self.config.minimum_metric_coverage:
                low_coverage += 1
            high_count = sum(item["severity"] == "HIGH" for item in findings)
            warn_count = sum(item["severity"] == "WARN" for item in findings)
            risk_score = min(100.0, high_count * 25.0 + warn_count * 10.0)
            document_ids = sorted({fact.document_id for fact in facts if fact.document_id})
            source_urls = sorted({fact.source_url for fact in facts if fact.source_url})
            if not document_ids or not source_urls:
                missing += 1
                warnings.append(
                    f"FinancialForensicsAgent {ticker}: fakta nemají úplnou provenance."
                )
                continue

            finding_codes = [str(item["code"]) for item in findings]
            summary = (
                f"Finanční forenzní screening: HIGH={high_count}, WARN={warn_count}, "
                f"pokrytí metrik={coverage:.0%}. Nejde o závěr o podvodu ani obchodní signál."
            )
            metadata = {
                "metrics": {key: _round_metric(value) for key, value in metrics.items()},
                "findings": findings,
                "fact_count": len(facts),
                "metric_coverage": round(coverage, 4),
                "methodology_version": self.version,
                "fraud_conclusion": False,
                "scoring_applied": False,
                "sector_adjustment_applied": False,
                "shadow_mode": context.shadow_mode,
            }
            evidence.append(
                AgentEvidence(
                    evidence_id=_stable_id(
                        context.orchestration_id,
                        self.name,
                        ticker,
                        "screening",
                    ),
                    ticker=ticker,
                    agent_name=self.name,
                    event_type="FINANCIAL_FORENSICS",
                    observed_at=observed_at,
                    summary=summary,
                    direction=0.0,
                    risk_score=risk_score,
                    confidence=coverage,
                    hard_veto=False,
                    reasons=finding_codes,
                    document_ids=document_ids,
                    source_urls=source_urls,
                    metadata=metadata,
                )
            )
            summaries[ticker] = {
                "risk_score": risk_score,
                "confidence": coverage,
                "metrics": metadata["metrics"],
                "findings": findings,
            }

        if not evidence:
            status = AgentStatus.UNAVAILABLE
        elif low_coverage or missing:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        if low_coverage:
            warnings.append(
                f"FinancialForensicsAgent: {low_coverage} tickerů má nízké pokrytí metrik."
            )
        if missing:
            warnings.append(
                f"FinancialForensicsAgent: {missing} tickerů nemá dostatek použitelných SEC faktů."
            )
        return AgentResult(
            status=status,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "analysed_tickers": len(evidence),
                "low_coverage_tickers": low_coverage,
                "missing_tickers": missing,
                "high_findings": sum(
                    item["severity"] == "HIGH"
                    for summary in summaries.values()
                    for item in summary["findings"]
                ),
                "warning_findings": sum(
                    item["severity"] == "WARN"
                    for summary in summaries.values()
                    for item in summary["findings"]
                ),
                "fraud_conclusion": False,
                "scoring_applied": False,
            },
            state_updates={"financial_forensics_by_ticker": summaries},
        )
