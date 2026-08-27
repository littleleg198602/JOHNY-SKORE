from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import tempfile
from typing import Sequence

from market_checker_app.config import (
    AppConfig,
    ClaimVerificationConfig,
    CommodityEnergyConfig,
    DecisionAgentConfig,
    EntityRegistryConfig,
    EvaluationAgentConfig,
    EuropeanFilingConfig,
    FinancialForensicsConfig,
    FundamentalIngestionConfig,
    RegulatoryContractConfig,
    ShortReportConfig,
    Stage3SourceVerificationConfig,
    SupplyChainConfig,
)
from market_checker_app.services.agent_runtime_service import (
    AgentRuntimeService,
    AgentRuntimeSettings,
)
from market_checker_app.services.company_intelligence_manifest_service import (
    parse_european_allowed_hosts,
    parse_european_filing_feeds,
    parse_european_filing_sources,
    parse_identity_records,
)
from market_checker_app.services.short_report_manifest_service import (
    parse_short_report_sources,
)
from market_checker_app.services.stage3_manifest_service import (
    parse_commodity_energy_sources,
    parse_regulatory_contract_sources,
    parse_supply_chain_sources,
)
from market_checker_app.services.watchlist_service import (
    WatchlistError,
    load_watchlist,
    normalize_watchlist,
    select_watchlist_pilot,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.models import AnalysisProgressState


DEFAULT_RSS_SOURCE = (
    "https://news.google.com/rss/search?"
    "q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en"
)
DEFAULT_SHORT_REPORT_RSS_SOURCE = "https://muddywatersresearch.com/feed/"
DEFAULT_RSS_SOURCES = (
    DEFAULT_RSS_SOURCE,
    DEFAULT_SHORT_REPORT_RSS_SOURCE,
)


class RuntimeConfigurationError(ValueError):
    pass


def _console_progress(state: AnalysisProgressState) -> None:
    """Render live progress for the local Windows launcher."""

    progress = max(0.0, min(1.0, float(state.overall_progress)))
    width = 28
    filled = int(round(progress * width))
    bar = "#" * filled + "." * (width - filled)
    position = ""
    if state.current_symbol:
        position = f" | {state.current_symbol} {state.current_position}/{state.total_symbols}"
    print(
        f"\\r[PROGRESS] [{bar}] {progress * 100:5.1f}%{position} | {state.current_message}   ",
        end="",
        flush=True,
    )


def _validated_sources(
    settings: AgentRuntimeSettings,
) -> tuple[
    dict[str, dict[str, object]],
    tuple[object, ...],
    tuple[object, ...],
    tuple[str, ...],
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
    tuple[object, ...],
]:
    identity_records, identity_errors = parse_identity_records(
        settings.identity_records_text
    )
    european_filings, european_filing_errors = parse_european_filing_sources(
        settings.european_filing_sources_text
    )
    european_feeds, european_feed_errors = parse_european_filing_feeds(
        settings.european_filing_feeds_text
    )
    european_allowed_hosts, european_host_errors = parse_european_allowed_hosts(
        settings.european_allowed_hosts_text
    )
    short_reports, short_errors = parse_short_report_sources(
        settings.short_report_sources_text
    )
    supply_chain, supply_errors = parse_supply_chain_sources(
        settings.supply_chain_sources_text
    )
    commodity_energy, commodity_errors = parse_commodity_energy_sources(
        settings.commodity_energy_sources_text
    )
    regulatory_contract, regulatory_errors = parse_regulatory_contract_sources(
        settings.regulatory_contract_sources_text
    )
    errors = (
        identity_errors
        + european_filing_errors
        + european_feed_errors
        + european_host_errors
        + short_errors
        + supply_errors
        + commodity_errors
        + regulatory_errors
    )
    if errors:
        raise RuntimeConfigurationError("\n".join(errors))
    if (
        settings.supply_chain_enabled
        and not supply_chain
        and not (
            settings.sec_fundamentals_enabled
            and settings.auto_discover_supply_chain_from_sec
        )
    ):
        raise RuntimeConfigurationError(
            "SupplyChainAgent je zapnutý, ale trvalá konfigurace neobsahuje platný zdroj."
        )
    if (
        settings.commodity_energy_enabled
        and not commodity_energy
        and not (
            settings.sec_fundamentals_enabled
            and settings.auto_discover_commodity_energy_from_sec
        )
    ):
        raise RuntimeConfigurationError(
            "CommodityEnergyAgent je zapnutý, ale trvalá konfigurace neobsahuje platný zdroj."
        )
    if settings.european_filings_enabled and not (
        european_filings or european_feeds
    ):
        raise RuntimeConfigurationError(
            "EuropeanFilingsAgent je zapnutý, ale trvalá konfigurace neobsahuje dokument ani feed."
        )
    identity_required_tickers = (
        {
            str(getattr(source, "ticker", "")).strip().upper()
            for source in european_filings + european_feeds
        }
        if settings.european_filings_enabled
        else set()
    )
    identity_required_tickers.update(
        str(getattr(source, "ticker", "")).strip().upper()
        for source in regulatory_contract
        if str(getattr(source, "source_type", "")).strip().lower()
        != "media_article"
    )
    missing_identity = sorted(
        ticker
        for ticker in identity_required_tickers
        if ticker and ticker not in identity_records
    )
    if missing_identity:
        raise RuntimeConfigurationError(
            "Identity manifest neobsahuje přesnou identitu pro: "
            + ", ".join(missing_identity)
        )
    return (
        identity_records,
        european_filings,
        european_feeds,
        european_allowed_hosts,
        short_reports,
        supply_chain,
        commodity_energy,
        regulatory_contract,
    )


def build_runtime_config(
    settings: AgentRuntimeSettings,
    *,
    output_dir: Path,
    sqlite_path: Path,
    sec_user_agent: str,
) -> AppConfig:
    """Build the same safe agent configuration for unattended weekly runs."""

    (
        identity_records,
        european_filings,
        european_feeds,
        european_allowed_hosts,
        short_reports,
        supply_chain,
        commodity_energy,
        regulatory_contract,
    ) = _validated_sources(settings)
    if settings.sec_fundamentals_enabled and (
        not sec_user_agent.strip() or "@" not in sec_user_agent
    ):
        raise RuntimeConfigurationError(
            "SEC ingest je zapnutý, ale JOHNY_SKORE_SEC_USER_AGENT neobsahuje "
            "název aplikace a kontaktní e-mail."
        )
    return AppConfig(
        output_dir=output_dir,
        sqlite_path=sqlite_path,
        save_history=True,
        export_excel=False,
        compare_previous_run=True,
        agent_stage1_enabled=True,
        agent_shadow_mode=True,
        entity_registry=EntityRegistryConfig(
            identity_records=identity_records,
        ),
        fundamental_ingestion=FundamentalIngestionConfig(
            enabled=settings.sec_fundamentals_enabled,
            user_agent=sec_user_agent.strip(),
        ),
        european_filings=EuropeanFilingConfig(
            enabled=settings.european_filings_enabled,
            sources=european_filings,
            feeds=european_feeds,
            allowed_local_exchange_hosts=european_allowed_hosts,
        ),
        financial_forensics=FinancialForensicsConfig(
            enabled=(
                settings.sec_fundamentals_enabled
                and settings.financial_forensics_enabled
            ),
        ),
        short_reports=ShortReportConfig(
            enabled=settings.short_reports_enabled,
            sources=short_reports,
            auto_discover_from_news=settings.auto_discover_short_reports,
        ),
        claim_verification=ClaimVerificationConfig(
            enabled=(
                settings.verify_short_report_claims
                and settings.sec_fundamentals_enabled
                and settings.financial_forensics_enabled
                and (
                    settings.short_reports_enabled
                    or settings.auto_discover_short_reports
                )
            ),
        ),
        supply_chain=SupplyChainConfig(
            enabled=(
                settings.supply_chain_enabled
                or (
                    settings.sec_fundamentals_enabled
                    and settings.auto_discover_supply_chain_from_sec
                )
            ),
            sources=supply_chain,
            auto_discover_from_sec_filings=(
                settings.auto_discover_supply_chain_from_sec
            ),
            source_verification=Stage3SourceVerificationConfig(
                enabled=(
                    settings.supply_chain_enabled
                    or (
                        settings.sec_fundamentals_enabled
                        and settings.auto_discover_supply_chain_from_sec
                    )
                ),
            ),
        ),
        commodity_energy=CommodityEnergyConfig(
            enabled=(
                settings.commodity_energy_enabled
                or (
                    settings.sec_fundamentals_enabled
                    and settings.auto_discover_commodity_energy_from_sec
                )
            ),
            sources=commodity_energy,
            auto_discover_from_sec_filings=(
                settings.auto_discover_commodity_energy_from_sec
            ),
            source_verification=Stage3SourceVerificationConfig(
                enabled=(
                    settings.commodity_energy_enabled
                    or (
                        settings.sec_fundamentals_enabled
                        and settings.auto_discover_commodity_energy_from_sec
                    )
                ),
            ),
        ),
        regulatory_contract=RegulatoryContractConfig(
            enabled=settings.regulatory_contract_enabled,
            sources=regulatory_contract,
            auto_discover_from_news=settings.auto_discover_regulatory_events,
            source_verification=Stage3SourceVerificationConfig(
                enabled=(
                    settings.regulatory_contract_enabled
                    or settings.auto_discover_regulatory_events
                ),
            ),
        ),
        decision_agent=DecisionAgentConfig(
            enabled=settings.stage4_shadow_enabled,
            live_application_enabled=False,
            live_policy_allowlist=(),
        ),
        evaluation_agent=EvaluationAgentConfig(
            enabled=settings.stage4_shadow_enabled,
            enable_after_gate=False,
            enabled_policy_allowlist=(),
        ),
    )


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _tickers(
    explicit: Sequence[str],
    store: SQLiteStore,
    *,
    ticker_file: Path | None = None,
    ticker_limit: int | None = None,
    required_tickers: Sequence[str] = (),
) -> list[str]:
    if explicit:
        normalized = normalize_watchlist(explicit)
    elif ticker_file is not None:
        normalized = load_watchlist(ticker_file)
    else:
        persisted = store.list_tickers()
        normalized = normalize_watchlist(persisted) if persisted else []
    if not normalized:
        return []
    return select_watchlist_pilot(
        normalized,
        ticker_limit,
        required_tickers=required_tickers,
    )


def _required_runtime_tickers(config: AppConfig) -> list[str]:
    sources = (
        tuple(config.short_reports.sources)
        + tuple(config.european_filings.sources)
        + tuple(config.european_filings.feeds)
        + tuple(config.supply_chain.sources)
        + tuple(config.commodity_energy.sources)
        + tuple(config.regulatory_contract.sources)
    )
    return list(
        dict.fromkeys(
            str(getattr(source, "ticker", "")).strip().upper()
            for source in sources
            if str(getattr(source, "ticker", "")).strip()
        )
    )


def _readiness_summary(
    result: dict[str, object],
    config: AppConfig,
) -> dict[str, object]:
    """Build a fail-closed, machine-readable Stage 4 readiness verdict."""

    activation_state = str(result.get("activation_state") or "INSUFFICIENT_DATA")
    gate_passed = bool(result.get("evaluation_gate_passed"))
    consecutive_passes = int(result.get("evaluation_consecutive_passes") or 0)
    required_passes = int(
        result.get("evaluation_required_consecutive_passes")
        or config.evaluation_agent.required_consecutive_passes
    )
    accuracy_improvement_proven = bool(
        gate_passed
        and consecutive_passes >= required_passes
        and activation_state in {"ELIGIBLE", "ENABLED"}
    )
    live_buy_sell_ready = bool(
        accuracy_improvement_proven
        and result.get("quality_gate_decision") == "PASS"
    )
    live_buy_sell_enabled = bool(
        live_buy_sell_ready
        and result.get("live_application_authorized")
        and not config.agent_shadow_mode
        and int(result.get("decision_applied_count") or 0) > 0
    )

    blockers = [
        str(reason)
        for reason in result.get("evaluation_activation_reasons", [])
        if str(reason).strip()
    ]
    if int(result.get("evaluation_sample_count") or 0) < int(
        config.evaluation_agent.minimum_oos_samples
    ):
        blockers.append(
            "minimum_oos_samples:"
            f"{int(result.get('evaluation_sample_count') or 0)}/"
            f"{config.evaluation_agent.minimum_oos_samples}"
        )
    if int(result.get("evaluation_distinct_weeks") or 0) < int(
        config.evaluation_agent.minimum_distinct_weeks
    ):
        blockers.append(
            "minimum_distinct_weeks:"
            f"{int(result.get('evaluation_distinct_weeks') or 0)}/"
            f"{config.evaluation_agent.minimum_distinct_weeks}"
        )
    if consecutive_passes < required_passes:
        blockers.append(
            f"independent_gate_passes:{consecutive_passes}/{required_passes}"
        )
    if result.get("quality_gate_decision") != "PASS":
        blockers.append(
            f"quality_gate:{result.get('quality_gate_decision') or 'MISSING'}"
        )
    if not result.get("live_application_authorized"):
        blockers.append("explicit_live_authorization_not_granted")
    if config.agent_shadow_mode:
        blockers.append("shadow_mode_active")

    return {
        "accuracy_improvement_proven": accuracy_improvement_proven,
        "live_buy_sell_ready": live_buy_sell_ready,
        "live_buy_sell_enabled": live_buy_sell_enabled,
        "activation_state": activation_state,
        "evaluation_gate_passed": gate_passed,
        "consecutive_gate_passes": consecutive_passes,
        "required_consecutive_gate_passes": required_passes,
        "shadow_mode": config.agent_shadow_mode,
        "blockers": list(dict.fromkeys(blockers)),
    }


def _source_health_summary(
    result: dict[str, object],
    config: AppConfig,
) -> dict[str, object]:
    return {
        "entity_registry": {
            "configured_records": len(config.entity_registry.identity_records),
            "status": result.get("entity_registry_status"),
            "unresolved": int(
                result.get("entity_unresolved_identity_count") or 0
            ),
            "conflicts": int(
                result.get("entity_identity_conflict_count") or 0
            ),
        },
        "sec_edgar": {
            "configured": config.fundamental_ingestion.enabled,
            "status": result.get("fundamental_ingestion_status"),
            "documents": int(result.get("fundamental_document_count") or 0),
            "facts": int(result.get("fundamental_fact_count") or 0),
            "filing_text_documents": int(
                result.get("fundamental_filing_text_document_count") or 0
            ),
            "filing_text_failures": int(
                result.get("fundamental_filing_text_failure_count") or 0
            ),
        },
        "financial_forensics": {
            "configured": config.financial_forensics.enabled,
            "status": result.get("financial_forensics_status"),
            "evidence": int(
                result.get("financial_forensics_evidence_count") or 0
            ),
        },
        "european_filings": {
            "configured": config.european_filings.enabled,
            "status": result.get("european_filings_status"),
            "documents": int(result.get("european_filing_document_count") or 0),
            "direct_sources": len(config.european_filings.sources),
            "feeds": len(config.european_filings.feeds),
        },
        "source_resolution": {
            "status": result.get("source_resolution_status"),
            "canonical_events": int(result.get("source_resolution_count") or 0),
            "conflicts_resolved": int(
                result.get("source_resolution_conflict_count") or 0
            ),
        },
        "short_reports": {
            "configured": bool(
                config.short_reports.enabled
                or config.short_reports.auto_discover_from_news
            ),
            "status": result.get("short_report_status"),
            "documents": int(result.get("short_report_document_count") or 0),
            "claims": int(result.get("short_report_claim_count") or 0),
            "auto_discovered": int(
                result.get("auto_discovered_short_reports") or 0
            ),
        },
        "supply_chain": {
            "configured": config.supply_chain.enabled,
            "status": result.get("supply_chain_status"),
            "relationships": int(
                result.get("supply_chain_relationship_count") or 0
            ),
            "auto_discovered": int(
                result.get("auto_discovered_supply_chain_relationships") or 0
            ),
        },
        "commodity_energy": {
            "configured": config.commodity_energy.enabled,
            "status": result.get("commodity_energy_status"),
            "exposures": int(
                result.get("commodity_energy_exposure_count") or 0
            ),
            "auto_discovered": int(
                result.get("auto_discovered_commodity_energy_exposures") or 0
            ),
        },
        "regulatory_contracts": {
            "configured": bool(
                config.regulatory_contract.enabled
                or config.regulatory_contract.auto_discover_from_news
            ),
            "status": result.get("regulatory_contract_status"),
            "events": int(
                result.get("regulatory_contract_event_count") or 0
            ),
            "auto_discovered": int(
                result.get("auto_discovered_regulatory_events") or 0
            ),
        },
    }


_SIGNAL_DETAIL_COLUMNS = (
    "ticker",
    "raw_total_score",
    "final_total_score",
    "final_confidence",
    "data_quality_score",
    "news_confidence",
    "tech_confidence",
    "yahoo_confidence",
    "decision_signal",
    "forecast",
    "action",
    "action_reasons",
    "signal_strength",
    "blocked_reasons",
    "reasons",
    "warnings",
)


def _json_safe(value: object) -> object:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _signal_detail_records(result: dict[str, object]) -> list[dict[str, object]]:
    signals = result.get("signals")
    if signals is None or not hasattr(signals, "to_dict"):
        return []
    columns = set(getattr(signals, "columns", []))
    selected = [column for column in _SIGNAL_DETAIL_COLUMNS if column in columns]
    return [
        {
            column: _json_safe(record.get(column))
            for column in selected
        }
        for record in signals.to_dict(orient="records")
    ]


_DECISION_DETAIL_FIELDS = (
    "decision_id",
    "ticker",
    "policy_name",
    "policy_version",
    "baseline_action",
    "baseline_forecast",
    "proposed_action",
    "proposed_forecast",
    "baseline_p_up",
    "baseline_p_flat",
    "baseline_p_down",
    "p_up",
    "p_flat",
    "p_down",
    "confidence",
    "hard_veto",
    "activation_state",
    "applied_to_prediction",
    "reasons",
    "conflicts",
    "evidence_ids",
    "claim_ids",
    "regulatory_event_ids",
    "metadata",
)


def _decision_detail_records(result: dict[str, object]) -> list[dict[str, object]]:
    report = result.get("agent_report")
    decisions = getattr(report, "decisions", []) if report is not None else []
    return [
        {
            field: _json_safe(getattr(decision, field, None))
            for field in _DECISION_DETAIL_FIELDS
        }
        for decision in decisions
    ]


def run_weekly_shadow(
    *,
    config: AppConfig,
    tickers: list[str],
    rss_sources: list[str],
    rss_enabled: bool,
    mt5_enabled: bool,
    yahoo_metadata_enabled: bool | None,
) -> dict[str, object]:
    from market_checker_app.services.pipeline_service import PipelineService

    if not config.agent_shadow_mode:
        raise RuntimeConfigurationError("Týdenní runner smí běžet pouze v shadow režimu.")
    if not tickers:
        raise RuntimeConfigurationError(
            "Chybí tickery: zadejte --tickers nebo nejprve vytvořte historii v SQLite."
        )

    config.ensure_output_dir()
    store = SQLiteStore(config.sqlite_path)
    store.ensure_schema()
    print(f"[INFO] Začínám zpracování {len(tickers)} tickerů; průběh se bude zobrazovat níže.", flush=True)
    result = PipelineService(config).run(
        tickers,
        rss_sources,
        store,
        progress_callback=_console_progress,
        rss_enabled=rss_enabled,
        mt5_enabled=mt5_enabled,
        yahoo_metadata_enabled=yahoo_metadata_enabled,
    )
    print("", flush=True)
    readiness = _readiness_summary(result, config)
    summary: dict[str, object] = {
        "schema_version": 2,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "run_id": result.get("run_id"),
        "ticker_count": len(tickers),
        "agent_status": result.get("agent_status"),
        "quality_gate_decision": result.get("quality_gate_decision"),
        "decision_count": result.get("decision_count"),
        "decision_suppressed_count": result.get("decision_suppressed_count"),
        "decision_applied_count": result.get("decision_applied_count"),
        "activation_state": result.get("activation_state"),
        "evaluation_sample_count": result.get("evaluation_sample_count"),
        "evaluation_distinct_weeks": result.get("evaluation_distinct_weeks"),
        "evaluation_baseline_accuracy_pct": result.get(
            "evaluation_baseline_accuracy_pct"
        ),
        "evaluation_candidate_accuracy_pct": result.get(
            "evaluation_candidate_accuracy_pct"
        ),
        "evaluation_lift_pct_points": result.get("evaluation_lift_pct_points"),
        "evaluation_lift_lower_bound_pct_points": result.get(
            "evaluation_lift_lower_bound_pct_points"
        ),
        "evaluation_positive_week_ratio": result.get(
            "evaluation_positive_week_ratio"
        ),
        "evaluation_gate_passed": result.get("evaluation_gate_passed"),
        "evaluation_gate_results": result.get("evaluation_gate_results"),
        "evaluation_consecutive_passes": result.get(
            "evaluation_consecutive_passes"
        ),
        "evaluation_required_consecutive_passes": result.get(
            "evaluation_required_consecutive_passes"
        ),
        "accuracy_improvement_proven": readiness[
            "accuracy_improvement_proven"
        ],
        "live_buy_sell_ready": readiness["live_buy_sell_ready"],
        "live_buy_sell_enabled": readiness["live_buy_sell_enabled"],
        "readiness": readiness,
        "source_health": _source_health_summary(result, config),
        "auto_discovered_short_reports": result.get(
            "auto_discovered_short_reports"
        ),
        "auto_discovered_regulatory_events": result.get(
            "auto_discovered_regulatory_events"
        ),
        "warning_count": len(result.get("warnings", [])),
        "error_count": len(result.get("errors", [])),
        "warnings": list(result.get("warnings", [])),
        "errors": list(result.get("errors", [])),
        "detail_schema_version": 1,
        "ticker_results": _signal_detail_records(result),
        "decision_results": _decision_detail_records(result),
    }
    failures: list[str] = []
    if result.get("run_id") is None:
        failures.append("běh se neuložil do SQLite")
    report = result.get("agent_report")
    executions = getattr(report, "executions", [])
    hard_agent_failures = [
        f"{execution.agent_name}:{execution.status.value}"
        for execution in executions
        if execution.status.value in {"FAILED", "BLOCKED"}
    ]
    if result.get("agent_status") == "FAILED" or hard_agent_failures:
        failures.append(
            "agentní běh obsahuje tvrdé selhání"
            + (
                ": " + ", ".join(hard_agent_failures)
                if hard_agent_failures
                else ""
            )
        )
    if config.fundamental_ingestion.enabled:
        if result.get("fundamental_ingestion_status") not in {
            "SUCCESS",
            "PARTIAL",
        }:
            failures.append("SEC ingest není provozuschopný")
        if int(result.get("fundamental_document_count") or 0) == 0:
            failures.append("SEC ingest neuložil žádný filing")
        if int(result.get("fundamental_fact_count") or 0) == 0:
            failures.append("SEC ingest neuložil žádný XBRL fakt")
    if (
        config.fundamental_ingestion.enabled
        and (
            config.supply_chain.auto_discover_from_sec_filings
            or config.commodity_energy.auto_discover_from_sec_filings
        )
    ):
        if int(result.get("fundamental_filing_text_document_count") or 0) == 0:
            failures.append("nebyl bezpečně načten žádný text výročního filingu")
        if int(result.get("fundamental_filing_text_failure_count") or 0) > 0:
            failures.append("některý text výročního filingu se nepodařilo načíst")
    if config.short_reports.enabled and int(
        result.get("short_report_document_count") or 0
    ) == 0:
        failures.append("short-report canary nebyl načten")
    if config.supply_chain.enabled and result.get("supply_chain_status") != "SUCCESS":
        failures.append(
            f"SupplyChainAgent skončil {result.get('supply_chain_status')}"
        )
    if (
        config.commodity_energy.enabled
        and result.get("commodity_energy_status") != "SUCCESS"
    ):
        failures.append(
            f"CommodityEnergyAgent skončil {result.get('commodity_energy_status')}"
        )
    if result.get("quality_gate_decision") != "PASS":
        failures.append(
            f"QualityGate skončil {result.get('quality_gate_decision')}"
        )
    if int(result.get("decision_applied_count") or 0) != 0:
        failures.append("shadow běh nepovoleně aplikoval rozhodnutí do predikce")
    if config.decision_agent.enabled and int(result.get("decision_count") or 0) != len(
        tickers
    ):
        failures.append("DecisionAgent nevytvořil právě jedno rozhodnutí pro každý ticker")
    if result.get("errors"):
        failures.append("pipeline ohlásila produkční chyby")
    summary["pipeline_status"] = "FAILED" if failures else "SUCCESS"
    summary["evaluation_status"] = (
        "PASS" if summary.get("evaluation_gate_passed") else "PENDING"
    )
    summary["pipeline_failures"] = list(failures)
    _atomic_json(config.output_dir / "weekly_shadow_latest.json", summary)

    if failures:
        raise RuntimeError("; ".join(failures))
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bezobslužný týdenní Stage 4 shadow běh s auditními kontrolami."
    )
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument(
        "--ticker-file",
        type=Path,
        help=(
            "Verzovaný ticker universe; použije se, pokud nejsou zadány "
            "--tickers. Bez něj runner obnoví tickery ze SQLite historie."
        ),
    )
    parser.add_argument(
        "--ticker-limit",
        type=int,
        help="Volitelný pilotní limit zachovávající pořadí ticker souboru.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("outputs/market_checker_history.db"),
    )
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--rss-source", action="append", dest="rss_sources")
    parser.add_argument(
        "--rss",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--mt5",
        action=argparse.BooleanOptionalAction,
        default=platform.system() == "Windows",
    )
    parser.add_argument(
        "--yahoo-metadata",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    runtime_service = AgentRuntimeService(args.runtime_config)
    settings, warning = runtime_service.load()
    if warning:
        raise SystemExit(warning)
    try:
        config = build_runtime_config(
            settings,
            output_dir=args.output_dir,
            sqlite_path=args.db_path,
            sec_user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
        )
        store = SQLiteStore(config.sqlite_path)
        store.ensure_schema()
        # The primary pilot is selected from the requested watchlist only.
        # Auxiliary source manifests must never displace pilot tickers.
        tickers = _tickers(
            args.tickers,
            store,
            ticker_file=args.ticker_file,
            ticker_limit=args.ticker_limit,
        )
        summary = run_weekly_shadow(
            config=config,
            tickers=tickers,
            rss_sources=args.rss_sources or list(DEFAULT_RSS_SOURCES),
            rss_enabled=args.rss,
            mt5_enabled=args.mt5,
            yahoo_metadata_enabled=args.yahoo_metadata,
        )
    except (
        RuntimeConfigurationError,
        RuntimeError,
        WatchlistError,
        OSError,
    ) as exc:
        raise SystemExit(f"[SHADOW CHYBA] {exc}") from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
