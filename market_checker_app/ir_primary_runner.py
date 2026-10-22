"""Explicit selected primary IR review; uses an existing shared ScoutStore DB."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--feed", required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--publisher-host", action="append", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--candidate-id")
    selection.add_argument("--original-url")
    parser.add_argument("--requested-as-of", type=datetime.fromisoformat)
    parser.add_argument("--max-requests", type=int, default=6)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error("--db must be the existing shared ScoutStore database; no fresh quota ledger")
    # The coordinator's wrapper also archives the completed RSS source run.
    from market_checker_app.scout_runner import run_primary_ir
    result = run_primary_ir(
        db_path=args.db, ticker=args.ticker, feed_url=args.feed, authority_url=args.authority,
        publisher_hosts=tuple(args.publisher_host), candidate_id=args.candidate_id,
        original_url=args.original_url, requested_as_of=args.requested_as_of,
        max_requests=args.max_requests)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "SOURCE_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
