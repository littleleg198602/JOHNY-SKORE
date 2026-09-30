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
    scout = SecScoutService(
        store, user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
    )
    scheduled = scout.schedule([record["ticker"] for record in records], as_of=now)
    batch = scout.run_batch(limit=limit)
    fred_key = os.getenv("JOHNY_SKORE_FRED_API_KEY", "")
    if fred_key:
        try:
            macro = FredScoutService(store, client=FredApiClient(fred_key)).run(as_of=now)
        except Exception as exc:
            macro = {"status": "ERROR", "error": type(exc).__name__}
    else:
        macro = {"status": "WAIT_ACCESS", "new_findings": 0}
    eia_key = os.getenv("JOHNY_SKORE_EIA_API_KEY", "")
    if eia_key:
        try:
            energy = EiaScoutService(store, client=EiaApiClient(eia_key)).run(as_of=now)
        except Exception as exc:
            energy = {"status": "ERROR", "error": type(exc).__name__}
    else:
        energy = {"status": "WAIT_ACCESS", "new_findings": 0}
    try:
        identity_path = Path(os.getenv("JOHNY_SKORE_USASPENDING_UEI_FILE") or DEFAULT_IDENTITIES)
        contracts = UsaSpendingScoutService(
            store, client=UsaSpendingApiClient(),
            identities=load_verified_identities(identity_path),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        contracts = {"status": "ERROR", "error": type(exc).__name__}
    try:
        recipient_discovery = UsaSpendingRecipientDiscovery(
            store, client=UsaSpendingRecipientClient(),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        recipient_discovery = {"status": "ERROR", "error": type(exc).__name__}
    try:
        fda_recalls = FdaRecallScoutService(
            store, client=OpenFdaRecallClient(os.getenv("JOHNY_SKORE_FDA_API_KEY", "")),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        fda_recalls = {"status": "ERROR", "error": type(exc).__name__}
    finra_id = os.getenv("JOHNY_SKORE_FINRA_CLIENT_ID", "")
    finra_secret = os.getenv("JOHNY_SKORE_FINRA_CLIENT_SECRET", "")
    if finra_id and finra_secret:
        try:
            finra_short_interest = FinraShortInterestScoutService(
                store, client=FinraShortInterestClient(finra_id, finra_secret),
            ).run(as_of=now, universe={record["ticker"] for record in records})
        except Exception as exc:
            finra_short_interest = {"status": "ERROR", "error": type(exc).__name__}
    else:
        finra_short_interest = {"status": "WAIT_ACCESS", "new_findings": 0}
    try:
        bank_path = Path(os.getenv("JOHNY_SKORE_FDIC_BANKS_FILE") or FDIC_IDENTITIES)
        fdic_banks = FdicBankScoutService(
            store, client=FdicBankFindClient(), identities=load_verified_banks(bank_path),
        ).run(as_of=now, universe={record["ticker"] for record in records})
    except Exception as exc:
        fdic_banks = {"status": "ERROR", "error": type(exc).__name__}
    return {"scheduled_subjects": scheduled, "universe_snapshot": universe_snapshot, **batch,
            "macro_fred": macro, "energy_eia": energy, "contracts_usaspending": contracts,
            "recipient_discovery_usaspending": recipient_discovery,
            "fda_recall_candidates": fda_recalls,
            "finra_short_interest": finra_short_interest,
            "fdic_banks": fdic_banks,
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
