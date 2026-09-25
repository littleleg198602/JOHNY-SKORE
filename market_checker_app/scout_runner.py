from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from market_checker_app.config import DEFAULT_DB_PATH
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.utils.ticker_universe import load_canonical_tickers
from market_checker_app.storage.scout_store import ScoutStore


def run(*, db_path: Path = DEFAULT_DB_PATH, limit: int = 100) -> dict[str, object]:
    store = ScoutStore(db_path)
    now = datetime.now(timezone.utc)
    scout = SecScoutService(
        store, user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
    )
    scheduled = scout.schedule(load_canonical_tickers(), as_of=now)
    batch = scout.run_batch(limit=limit)
    return {"scheduled_subjects": scheduled, **batch,
            "queue": store.metrics(), "as_of": now.isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Průběžný sběr SEC stop pro Market Checker.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    result = run(db_path=args.db_path, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
