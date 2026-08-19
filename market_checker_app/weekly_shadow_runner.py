from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
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
    EvaluationAgentConfig,
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
from market_checker_app.services.short_report_manifest_service import (
    parse_short_report_sources,
)
from market_checker_app.services.stage3_manifest_service import (
    parse_commodity_energy_sources,
    parse_regulatory_contract_sources,
    parse_supply_chain_sources,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.text import normalize_ticker


DEFAULT_RSS_SOURCE = (
    "https://news.google.com/rss/search?"
    "q={ticker}%20stock&hl=en-US&gl=US&ceid=US:en"
)


class RuntimeConfigurationError(ValueError):
    pass


def _validated_sources(
    settings: AgentRuntimeSettings,
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
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
    errors = short_errors + supply_errors + commodity_errors + regulatory_errors
    if errors:
        raise RuntimeConfigurationError("\n".join(errors))
    if settings.supply_chain_enabled and not supply_chain:
        raise RuntimeConfigurationError(
            "SupplyChainAgent je zapnutý, ale trvalá konfigurace neobsahuje platný zdroj."
        )
    if settings.commodity_energy_enabled and not commodity_energy:
        raise RuntimeConfigurationError(
            "CommodityEnergyAgent je zapnutý, ale trvalá konfigurace neobsahuje platný zdroj."
        )
    return short_reports, supply_chain, commodity_energy, regulatory_contract


def build_runtime_config(
    settings: AgentRuntimeSettings,
    *,
    output_dir: Path,
    sqlite_path: Path,
    sec_user_agent: str,
) -> AppConfig:
    """Build the same safe agent configuration for unattended weekly runs."""

    (
        short_reports,
        supply_chain,
        commodity_energy,
        regulatory_contract,
    ) = _validated_sources(settings)
    if settings.sec_fundamentals_enabled and not sec_user_agent.strip():
        raise RuntimeConfigurationError(
            "SEC ingest je zapnutý, ale chybí JOHNY_SKORE_SEC_USER_AGENT."
        )
    return AppConfig(
        output_dir=output_dir,
        sqlite_path=sqlite_path,
        save_history=True,
        export_excel=False,
        compare_previous_run=True,
        agent_stage1_enabled=True,
        agent_shadow_mode=True,
        fundamental_ingestion=FundamentalIngestionConfig(
            enabled=settings.sec_fundamentals_enabled,
            user_agent=sec_user_agent.strip(),
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
            enabled=settings.supply_chain_enabled,
            sources=supply_chain,
            source_verification=Stage3SourceVerificationConfig(
                enabled=settings.supply_chain_enabled,
            ),
        ),
        commodity_energy=CommodityEnergyConfig(
            enabled=settings.commodity_energy_enabled,
            sources=commodity_energy,
            source_verification=Stage3SourceVerificationConfig(
                enabled=settings.commodity_energy_enabled,
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


def _tickers(explicit: Sequence[str], store: SQLiteStore) -> list[str]:
    raw = list(explicit) if explicit else store.list_tickers()
    normalized = [normalize_ticker(item) for item in raw]
    return list(dict.fromkeys(item for item in normalized if item))


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
    result = PipelineService(config).run(
        tickers,
        rss_sources,
        store,
        rss_enabled=rss_enabled,
        mt5_enabled=mt5_enabled,
        yahoo_metadata_enabled=yahoo_metadata_enabled,
    )
    summary: dict[str, object] = {
        "schema_version": 1,
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
        "evaluation_lift_pct_points": result.get("evaluation_lift_pct_points"),
        "evaluation_lift_lower_bound_pct_points": result.get(
            "evaluation_lift_lower_bound_pct_points"
        ),
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
    }
    _atomic_json(config.output_dir / "weekly_shadow_latest.json", summary)

    failures: list[str] = []
    if result.get("run_id") is None:
        failures.append("běh se neuložil do SQLite")
    if result.get("agent_status") != "SUCCESS":
        failures.append(f"agentní stav je {result.get('agent_status')}")
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
    if failures:
        raise RuntimeError("; ".join(failures))
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bezobslužný týdenní Stage 4 shadow běh s auditními kontrolami."
    )
    parser.add_argument("--tickers", nargs="*", default=[])
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
        tickers = _tickers(args.tickers, store)
        summary = run_weekly_shadow(
            config=config,
            tickers=tickers,
            rss_sources=args.rss_sources or [DEFAULT_RSS_SOURCE],
            rss_enabled=args.rss,
            mt5_enabled=args.mt5,
            yahoo_metadata_enabled=args.yahoo_metadata,
        )
    except (RuntimeConfigurationError, RuntimeError, OSError) as exc:
        raise SystemExit(f"[SHADOW CHYBA] {exc}") from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
