"""Offline, read-only historical specialist report; no network collectors."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Sequence

from market_checker_app.config import DEFAULT_DB_PATH
from market_checker_app.services.specialist_evaluation_service import (
    build_specialist_evaluation_report, read_archived_prediction_snapshots,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vyhodnotí specialisty z uložených point-in-time snapshotů bez nové analýzy trhu.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-path", type=Path, default=Path("outputs/specialist_evaluation_latest.json"))
    parser.add_argument("--as-of", help="Čas s časovým pásmem ISO-8601; umožňuje reprodukovat historický cutoff.")
    parser.add_argument("--label-archive-directory", type=Path,
                        help="Archiv doložených cen pro vyhodnocení; výchozí je prediction_label_observations vedle databáze.")
    parser.add_argument("--minimum-training-samples", type=int, default=200)
    parser.add_argument("--minimum-evaluation-samples", type=int, default=200)
    parser.add_argument("--minimum-weeks", type=int, default=12)
    parser.add_argument("--iterations", type=int, default=400)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    temporary = None
    try:
        if args.output_path.suffix.lower() != ".json" or args.output_path.resolve() == args.db_path.resolve():
            raise ValueError("Výstup musí být samostatný soubor JSON.")
        clock = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else datetime.now(timezone.utc)
        snapshots = read_archived_prediction_snapshots(args.db_path)
        report = build_specialist_evaluation_report(snapshots, as_of=clock,
            label_archive_directory=(args.label_archive_directory or args.db_path.parent / "prediction_label_observations"),
            minimum_training_samples=args.minimum_training_samples,
            minimum_evaluation_samples=args.minimum_evaluation_samples,
            minimum_weeks=args.minimum_weeks, iterations=args.iterations)
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output_path.parent,
                                         prefix="specialist_evaluation_", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(args.output_path)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise SystemExit(f"[SPECIALIST EVALUATION ERROR] {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"status": report["status"], "reason": report["reason"],
                      "snapshot_rows": report["input"]["snapshot_rows"],
                      "resolved_labels_known_at_cutoff": report["input"]["resolved_labels_known_at_cutoff"],
                      "completion_verified": False, "output_path": str(args.output_path)}, sort_keys=True))


if __name__ == "__main__":
    main()
