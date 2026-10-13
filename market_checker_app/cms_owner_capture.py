"""Explicit CMS primary capture for reviewed healthcare identity preparation."""
import argparse
import json
from pathlib import Path
import sqlite3

from market_checker_app.config import DEFAULT_DB_PATH
from market_checker_app.services.cms_owner_capture_service import capture_cms_owners
from market_checker_app.storage.scout_store import ScoutStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--owner-name", required=True)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--checkpoint-path", type=Path, required=True)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()
    try:
        result = capture_cms_owners(ScoutStore(args.db_path), subject_id=args.ticker,
            query_name=args.owner_name, checkpoint_path=args.checkpoint_path,
            page_size=args.page_size, max_pages=args.max_pages)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(json.dumps({"status": "ERROR", "error_type": type(error).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
