from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from market_checker_app.config import DEFAULT_DB_PATH
from market_checker_app.services.sec_scout_service import SecScoutService
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
    return {"scheduled_subjects": scheduled, "universe_snapshot": universe_snapshot, **batch,
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
