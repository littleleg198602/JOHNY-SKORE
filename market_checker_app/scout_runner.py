from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from market_checker_app.config import DEFAULT_DB_PATH
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.services.fred_scout_service import FredApiClient, FredScoutService
from market_checker_app.services.eia_scout_service import EiaApiClient, EiaScoutService
from market_checker_app.services.fda_recall_scout_service import (
    FdaRecallScoutService, OpenFdaRecallClient,
)
from market_checker_app.services.finra_short_interest_scout_service import (
    FinraShortInterestClient, FinraShortInterestScoutService,
)
from market_checker_app.services.healthcare_scout_service import (
    CmsHospitalOwnerClient, ClinicalTrialsClient, HealthcareNameScoutService,
)
from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry,
)
from market_checker_app.services.ofac_scout_service import (
    OfacSdnClient, OfacSdnScoutService, OfacConsolidatedClient, OfacConsolidatedScoutService,
)
from market_checker_app.services.doj_scout_service import DojPressReleaseClient, DojPressReleaseScoutService
from market_checker_app.services.epa_echo_scout_service import EpaEchoFacilityClient, EpaEchoScoutService
from market_checker_app.services.specialist_identity_config import configured_regulatory_identities
from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.services.fdic_bank_scout_service import (
    FdicBankFindClient, FdicBankScoutService, load_verified_banks,
    DEFAULT_IDENTITIES as FDIC_IDENTITIES,
)
from market_checker_app.services.sec13f_scout_service import (
    Sec13fHttpClient, Sec13fScoutService, load_verified_securities,
    DEFAULT_SECURITIES as SEC13F_SECURITIES,
)
from market_checker_app.services.nhtsa_recall_scout_service import (
    NhtsaRecallClient, NhtsaRecallScoutService, load_verified_models,
    DEFAULT_MODELS as NHTSA_MODELS,
)
from market_checker_app.services.usaspending_scout_service import (
    UsaSpendingApiClient, UsaSpendingScoutService, load_verified_identities,
    DEFAULT_IDENTITIES,
)
from market_checker_app.services.usaspending_recipient_discovery import (
    UsaSpendingRecipientClient, UsaSpendingRecipientDiscovery,
)
from market_checker_app.services.award_transaction_history_service import AwardTransactionHistoryService
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256,
    DEFAULT_TICKER_UNIVERSE_PATH,
    load_canonical_ticker_records,
)
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.services.specialist_incremental_archive_service import (
    SUPPORTED_INCREMENTAL_SOURCES, capture_specialist_source_run,
)

FDA_DAILY_ISSUER_BUDGET = 40
FINRA_DAILY_ISSUER_BUDGET = 75


def _record_completed_source_run(store: ScoutStore, source: str, summary: dict[str, object], *,
                                 started_at: datetime, provider_as_of: datetime | None = None) -> dict[str, object]:
    completed = datetime.now(timezone.utc)
    store.record_source_run(source, as_of=completed, summary=summary)
    result = dict(summary)
    if source in SUPPORTED_INCREMENTAL_SOURCES:
        try:
            path = capture_specialist_source_run(
                store, source=source, summary=summary, observed_at=completed,
                started_at=started_at, provider_as_of=provider_as_of,
            )
            result.update(source_archive_status="SAVED", source_archive_filename=path.name)
        except Exception as exc:
            result.update(source_archive_status="ERROR", source_archive_error=type(exc).__name__)
    else:
        result["source_archive_status"] = "NOT_SUPPORTED"
    store.record_source_run(source, as_of=completed, summary=result)
    return result


def run(*, db_path: Path = DEFAULT_DB_PATH, limit: int = 100) -> dict[str, object]:
    store = ScoutStore(db_path)
    now = datetime.now(timezone.utc)
    try:
        records = load_canonical_ticker_records()
    except ValueError as exc:
        # A hash mismatch is an audit event, never permission to analyze the
        # new list. Show the position-level difference from the archived list.
        try:
            candidate = load_canonical_ticker_records(DEFAULT_TICKER_UNIVERSE_PATH)
            differences = store.preview_universe_changes(candidate)
        except ValueError:
            raise exc
        if differences:
            raise ValueError(
                f"{exc} Změněných pozic: {len(differences)}; "
                f"první rozdíly: {differences[:10]}"
            ) from exc
        raise
    universe_snapshot = store.record_universe_snapshot(
        source_name=DEFAULT_TICKER_UNIVERSE_PATH.name,
        source_sha256=CANONICAL_CSV_SHA256,
        records=records,
        as_of=now,
    )
    source_started = datetime.now(timezone.utc)

    def recorded(source: str, summary: dict[str, object], *,
                 provider_as_of: datetime | None = now) -> dict[str, object]:
        nonlocal source_started
        # Persist each completed source immediately. A later source can hang or
        # the process can stop without erasing the earlier progress in the UI.
        result = _record_completed_source_run(
            store, source, summary, started_at=source_started, provider_as_of=provider_as_of,
        )
        source_started = datetime.now(timezone.utc)
        return result

    scout = SecScoutService(
        store, user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
    )
    scheduled = scout.schedule([record["ticker"] for record in records], as_of=now)
    batch = recorded("sec", scout.run_batch(limit=limit), provider_as_of=None)
    fred_key = os.getenv("JOHNY_SKORE_FRED_API_KEY", "")
    if fred_key:
        try:
            macro = FredScoutService(store, client=FredApiClient(fred_key)).run(as_of=now)
        except Exception as exc:
            macro = {"status": "ERROR", "error": type(exc).__name__}
    else:
        macro = {"status": "WAIT_ACCESS", "new_findings": 0}
    macro = recorded("fred", macro)
    eia_key = os.getenv("JOHNY_SKORE_EIA_API_KEY", "")
    if eia_key:
        try:
            energy = EiaScoutService(store, client=EiaApiClient(eia_key)).run(as_of=now)
        except Exception as exc:
            energy = {"status": "ERROR", "error": type(exc).__name__}
    else:
        energy = {"status": "WAIT_ACCESS", "new_findings": 0}
    energy = recorded("eia", energy)
    try:
        identity_path = Path(os.getenv("JOHNY_SKORE_USASPENDING_UEI_FILE") or DEFAULT_IDENTITIES)
        contracts = UsaSpendingScoutService(
            store, client=UsaSpendingApiClient(),
            identities=load_verified_identities(identity_path),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        contracts = {"status": "ERROR", "error": type(exc).__name__}
    contracts = recorded("usaspending", contracts)
    try:
        recipient_discovery = UsaSpendingRecipientDiscovery(
            store, client=UsaSpendingRecipientClient(),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        recipient_discovery = {"status": "ERROR", "error": type(exc).__name__}
    recipient_discovery = recorded("recipient_discovery", recipient_discovery)
    try:
        healthcare_identities = HealthcareIdentityRegistry.load(Path(
            os.getenv("JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE") or DEFAULT_HEALTHCARE_IDENTITIES))
    except (OSError, ValueError, KeyError, TypeError):
        healthcare_identities = None
    try:
        if healthcare_identities is None:
            raise ValueError("Invalid healthcare identity manifest")
        fda_recalls = FdaRecallScoutService(
            store, client=OpenFdaRecallClient(os.getenv("JOHNY_SKORE_FDA_API_KEY", "")),
            max_subjects=FDA_DAILY_ISSUER_BUDGET,
            identities=healthcare_identities,
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        fda_recalls = {"status": "ERROR", "error": type(exc).__name__}
    fda_recalls = recorded("fda", fda_recalls)
    finra_id = os.getenv("JOHNY_SKORE_FINRA_CLIENT_ID", "")
    finra_secret = os.getenv("JOHNY_SKORE_FINRA_CLIENT_SECRET", "")
    if finra_id and finra_secret:
        try:
            finra_short_interest = FinraShortInterestScoutService(
                store, client=FinraShortInterestClient(finra_id, finra_secret),
                max_subjects=FINRA_DAILY_ISSUER_BUDGET,
            ).run(as_of=now, universe={record["ticker"] for record in records})
        except Exception as exc:
            finra_short_interest = {"status": "ERROR", "error": type(exc).__name__}
    else:
        finra_short_interest = {"status": "WAIT_ACCESS", "new_findings": 0}
    finra_short_interest = recorded("finra", finra_short_interest)
    try:
        bank_path = Path(os.getenv("JOHNY_SKORE_FDIC_BANKS_FILE") or FDIC_IDENTITIES)
        fdic_banks = FdicBankScoutService(
            store, client=FdicBankFindClient(), identities=load_verified_banks(bank_path),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        fdic_banks = {"status": "ERROR", "error": type(exc).__name__}
    fdic_banks = recorded("fdic", fdic_banks)
    sec_user_agent = os.getenv("JOHNY_SKORE_SEC_USER_AGENT", "")
    if sec_user_agent:
        try:
            security_path = Path(os.getenv("JOHNY_SKORE_SEC13F_SECURITIES_FILE") or SEC13F_SECURITIES)
            sec13f_holdings = Sec13fScoutService(
                store, client=Sec13fHttpClient(sec_user_agent),
                securities=load_verified_securities(security_path),
            ).run(as_of=now, universe={record["ticker"] for record in records})
        except Exception as exc:
            sec13f_holdings = {"status": "ERROR", "error": type(exc).__name__}
    else:
        sec13f_holdings = {"status": "WAIT_ACCESS", "new_findings": 0}
    sec13f_holdings = recorded("sec13f", sec13f_holdings)
    try:
        model_path = Path(os.getenv("JOHNY_SKORE_NHTSA_MODELS_FILE") or NHTSA_MODELS)
        nhtsa_recalls = NhtsaRecallScoutService(
            store, client=NhtsaRecallClient(), models=load_verified_models(model_path),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        nhtsa_recalls = {"status": "ERROR", "error": type(exc).__name__}
    nhtsa_recalls = recorded("nhtsa", nhtsa_recalls)
    healthcare_results = {}
    for client in (CmsHospitalOwnerClient(), ClinicalTrialsClient()):
        try:
            if healthcare_identities is None:
                raise ValueError("Invalid healthcare identity manifest")
            summary = HealthcareNameScoutService(store, client=client, identities=healthcare_identities).run(
                as_of=now, universe={record["ticker"] for record in records})
        except Exception as exc:
            summary = {"status": "ERROR", "error": type(exc).__name__}
        healthcare_results[client.source] = recorded(client.source, summary)
    try:
        ofac = OfacSdnScoutService(store, client=OfacSdnClient()).run(
            as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        ofac = {"status": "ERROR", "error": type(exc).__name__}
    ofac = recorded("ofac", ofac)
    try:
        non_sdn = OfacConsolidatedScoutService(store, client=OfacConsolidatedClient()).run(
            as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        non_sdn = {"status": "ERROR", "error": type(exc).__name__}
    non_sdn = recorded("ofac_non_sdn", non_sdn)
    try:
        doj = DojPressReleaseScoutService(store, client=DojPressReleaseClient()).run(
            as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        doj = {"status": "ERROR", "error": type(exc).__name__}
    doj = recorded("doj", doj)
    profiles = load_research_profiles()
    epa_subjects = {ticker for ticker, profile in profiles.by_ticker.items()
                    if profile.code in {"CHEMICALS", "METALS", "INDUSTRIAL", "HOME", "PACKAGING"}}
    try:
        regulatory_identities, regulatory_manifest_status = configured_regulatory_identities()
        if regulatory_manifest_status == "INVALID_IDENTITY_MANIFEST":
            raise ValueError("Invalid regulatory identity manifest")
        epa = EpaEchoScoutService(store, client=EpaEchoFacilityClient(), identities=regulatory_identities).run(
            as_of=now, universe={record["ticker"] for record in records} & epa_subjects)
        epa["identity_manifest_status"] = regulatory_manifest_status
    except Exception as exc:
        epa = {"status":"ERROR", "error":type(exc).__name__}
    epa = recorded("epa", epa)
    return {"scheduled_subjects": scheduled, "universe_snapshot": universe_snapshot, **batch,
            "macro_fred": macro, "energy_eia": energy, "contracts_usaspending": contracts,
            "recipient_discovery_usaspending": recipient_discovery,
            "fda_recall_candidates": fda_recalls,
            "finra_short_interest": finra_short_interest,
            "fdic_banks": fdic_banks,
            "sec13f_holdings": sec13f_holdings,
            "nhtsa_model_recalls": nhtsa_recalls,
            "cms_hospital_owner_candidates": healthcare_results["cms"],
            "clinical_trial_candidates": healthcare_results["clinicaltrials"],
            "ofac_sdn_candidates": ofac,
            "ofac_non_sdn_candidates": non_sdn,
            "doj_press_release_candidates": doj,
            "epa_echo_facility_candidates": epa,
            "queue": store.metrics(), "as_of": now.isoformat()}


def run_award_transactions(*, db_path: Path = DEFAULT_DB_PATH,
                           subject_id: str, award_finding_id: str) -> dict[str, object]:
    """Explicit one-award collection sharing the existing daily HTTP budget."""
    store = ScoutStore(db_path)
    try:
        path = Path(os.getenv("JOHNY_SKORE_USASPENDING_UEI_FILE") or DEFAULT_IDENTITIES)
        result = AwardTransactionHistoryService(
            store, archive_directory=db_path.parent / "award_transaction_captures",
            identities=load_verified_identities(path),
        ).run(subject_id=subject_id, award_finding_id=award_finding_id)
    except Exception as exc:
        result = {"status": "ERROR", "error": type(exc).__name__, "new_findings": 0}
    captures = result.pop("capture_manifests", [])
    result["source_archive_status"] = "SAVED" if captures else "NOT_CREATED"
    store.record_source_run("usaspending_transactions", as_of=datetime.now(timezone.utc), summary=result)
    return result


def run_primary_ir(*, db_path: Path, ticker: str, feed_url: str, authority_url: str,
                   publisher_hosts: tuple[str, ...], candidate_id: str | None = None,
                   original_url: str | None = None, requested_as_of: datetime | None = None,
                   max_requests: int = 6) -> dict[str, object]:
    """Explicit bounded source review, sharing the retained RSS quota ledger."""
    from market_checker_app.services.ir_primary_scout_service import IRPrimaryScoutService

    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError("Primary IR collection requires the existing shared database")
    store = ScoutStore(db_path)
    started = datetime.now(timezone.utc)
    try:
        result = IRPrimaryScoutService(store, max_requests=max_requests).run(
            ticker, feed_url=feed_url, authority_url=authority_url,
            publisher_hosts=publisher_hosts, candidate_id=candidate_id,
            original_url=original_url, requested_as_of=requested_as_of,
        )
    except Exception as exc:
        result = {"status": "ERROR", "error": type(exc).__name__,
                  "issuer_attribution_allowed": False, "scoring_applied": False}
    return _record_completed_source_run(store, "rss", result, started_at=started)


def run_award_capture_audit(*, db_path: Path, subject_id: str, award_finding_id: str,
                           baseline_manifest_names: list[str], current_manifest_names: list[str],
                           as_of: datetime | None = None) -> dict[str, object]:
    """Read original selected-award captures; persist a separate physical audit."""
    db_path = Path(db_path)
    store = ScoutStore(db_path)
    directory = db_path.parent / "award_transaction_captures"
    root = directory.resolve()

    def selected(names: list[str]) -> list[Path]:
        if not 1 <= len(names) <= 5 or len(set(names)) != len(names):
            raise ValueError("Choose one to five distinct manifests per traversal")
        paths = []
        for name in names:
            if not isinstance(name, str) or not re.fullmatch(r"capture-[A-Za-z0-9_-]{1,100}\.json", name):
                raise ValueError("Choose a retained capture manifest filename")
            path = directory / name
            if path.is_symlink() or path.resolve().parent != root or not path.is_file():
                raise ValueError("Capture manifests must remain in their retained directory")
            paths.append(path)
        return paths

    try:
        baseline, current = selected(baseline_manifest_names), selected(current_manifest_names)
        path = Path(os.getenv("JOHNY_SKORE_USASPENDING_UEI_FILE") or DEFAULT_IDENTITIES)
        result = AwardTransactionHistoryService(
            store, archive_directory=directory, identities=load_verified_identities(path),
        ).audit_capture_versions(subject_id=subject_id, award_finding_id=award_finding_id,
                                 baseline_manifests=baseline, current_manifests=current, as_of=as_of)
    except Exception as exc:
        result = {"status": "INVALID_COMPARISON", "error": type(exc).__name__,
                  "audit_recorded_at": datetime.now(timezone.utc).isoformat(),
                  "publisher_deletion_verified": False, "scoring_applied": False}
    # This is a new audit of earlier observations. No source body, finding,
    # quota counter or source availability is changed by the comparison.
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    destination = db_path.parent / "award_transaction_audits" / f"audit-{digest}.json"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise ValueError("Stored audit does not match its content hash")
        else:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        result.update(audit_archive_status="SAVED", audit_archive_filename=destination.name,
                      audit_archive_sha256=digest)
    except Exception as exc:
        result.update(audit_archive_status="ERROR", audit_archive_error=type(exc).__name__)
    store.record_source_run("usaspending_transaction_audit", as_of=datetime.now(timezone.utc), summary=result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Průběžný sběr SEC stop pro Market Checker.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--award-subject", help="Ticker uložené doložené prime zakázky")
    parser.add_argument("--award-finding-id", help="ID uloženého nálezu prime zakázky")
    parser.add_argument("--award-baseline-manifest", action="append", default=[], help="Uložená stránka prvního průchodu (1–5)")
    parser.add_argument("--award-current-manifest", action="append", default=[], help="Uložená stránka druhého průchodu (1–5)")
    args = parser.parse_args()
    if bool(args.award_subject) != bool(args.award_finding_id):
        parser.error("--award-subject a --award-finding-id vyžadují společné zadání")
    comparison = bool(args.award_baseline_manifest or args.award_current_manifest)
    if comparison and not (args.award_subject and args.award_baseline_manifest and args.award_current_manifest):
        parser.error("Porovnání vyžaduje vybranou zakázku a oba průchody")
    if comparison:
        result = run_award_capture_audit(db_path=args.db_path, subject_id=args.award_subject,
            award_finding_id=args.award_finding_id, baseline_manifest_names=args.award_baseline_manifest,
            current_manifest_names=args.award_current_manifest)
    elif args.award_subject:
        result = run_award_transactions(db_path=args.db_path, subject_id=args.award_subject,
                                        award_finding_id=args.award_finding_id)
    else:
        result = run(db_path=args.db_path, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if comparison:
        if result["audit_archive_status"] != "SAVED" or result["status"] not in {
            "SAME_SOURCE_CAPTURE", "COMPLETE_CAPTURE_COMPARISON",
        }:
            raise SystemExit(1)
        return
    if result["status"] in {"WAIT_ACCESS", "ACCESS_BLOCKED", "LEASE_LOST"}:
        raise SystemExit(2)
    if args.award_subject and result["status"] in {"WAIT_QUOTA", "BUSY", "RATE_LIMITED"}:
        raise SystemExit(2)
    if args.award_subject and result["status"] not in {"COMPLETE", "NO_MATCH"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
