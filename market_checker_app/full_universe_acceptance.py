from __future__ import annotations

import argparse
import json
from pathlib import Path

from market_checker_app.services.full_universe_acceptance_service import (
    FullUniverseAcceptanceError,
    validate_full_universe_report,
)
from market_checker_app.services.watchlist_service import load_watchlist


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ověří, že týdenní report účetně pokrývá kanonický ticker universe."
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("outputs/weekly_shadow_latest.json"),
    )
    parser.add_argument(
        "--ticker-file",
        type=Path,
        default=Path("market_checker_app/production_watchlist.txt"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("outputs/full_universe_acceptance_latest.json"),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise FullUniverseAcceptanceError("weekly summary není JSON objekt")
        report = validate_full_universe_report(
            summary,
            expected_tickers=load_watchlist(args.ticker_file),
        )
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (FullUniverseAcceptanceError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[UNIVERSE CHYBA] {exc}") from exc
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
