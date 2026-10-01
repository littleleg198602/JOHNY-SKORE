from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

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
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256,
    DEFAULT_TICKER_UNIVERSE_PATH,
    load_canonical_ticker_records,
)
from market_checker_app.storage.scout_store import ScoutStore

FDA_DAILY_ISSUER_BUDGET = 40
FINRA_DAILY_ISSUER_BUDGET = 75


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
    def recorded(source: str, summary: dict[str, object]) -> dict[str, object]:
        # Persist each completed source immediately. A later source can hang or
        # the process can stop without erasing the earlier progress in the UI.
        store.record_source_run(source, as_of=datetime.now(timezone.utc), summary=summary)
        return summary

    scout = SecScoutService(
        store, user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
    )
    scheduled = scout.schedule([record["ticker"] for record in records], as_of=now)
    batch = recorded("sec", scout.run_batch(limit=limit))
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
        fda_recalls = FdaRecallScoutService(
            store, client=OpenFdaRecallClient(os.getenv("JOHNY_SKORE_FDA_API_KEY", "")),
            max_subjects=FDA_DAILY_ISSUER_BUDGET,
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
    return {"scheduled_subjects": scheduled, "universe_snapshot": universe_snapshot, **batch,
            "macro_fred": macro, "energy_eia": energy, "contracts_usaspending": contracts,
            "recipient_discovery_usaspending": recipient_discovery,
            "fda_recall_candidates": fda_recalls,
            "finra_short_interest": finra_short_interest,
            "fdic_banks": fdic_banks,
            "sec13f_holdings": sec13f_holdings,
            "nhtsa_model_recalls": nhtsa_recalls,
            "queue": store.metrics(), "as_of": now.isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Průběžný sběr SEC stop pro Market Checker.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    result = run(db_path=args.db_path, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] in {"WAIT_ACCESS", "ACCESS_BLOCKED", "LEASE_LOST"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
