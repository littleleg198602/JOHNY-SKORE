"""Offline Windows-friendly healthcare diagnostic; never initializes a database."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Sequence

from market_checker_app.config import DEFAULT_DB_PATH
from market_checker_app.services.healthcare_coverage_service import build_healthcare_coverage_report
from market_checker_app.services.healthcare_identity_service import DEFAULT_HEALTHCARE_IDENTITIES
from market_checker_app.services.research_profile_service import PROFILE_PATH
from market_checker_app.utils.ticker_universe import DEFAULT_TICKER_UNIVERSE_PATH


DEFAULT_REPORT_PATH = Path("outputs/healthcare_coverage_latest.json")
PRODUCTION_DATA_DIRECTORY = DEFAULT_TICKER_UNIVERSE_PATH.parent
MAX_PATH_CHARACTERS = 4096


def _path(value: str | Path) -> Path:
    text = str(value)
    if not text or len(text) > MAX_PATH_CHARACTERS or any(char in text for char in ("\0", "\r", "\n")):
        raise ValueError("Cesta musi byt neprazdna, bez ridicich znaku a nejvyse 4096 znaku.")
    if "://" in text:
        raise ValueError("Pouzij cestu k souboru, nikoli webovou adresu.")
    return Path(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vytvori prehled zdravotnickych identit a ulozenych kontrol pro 687 tickeru.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Existujici SQLite DB; chybejici DB se nezalozi.")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH), help="Samostatny vystupni JSON report.")
    parser.add_argument("--as-of", help="ISO-8601 cas s explicitnim casovym pasmem, nejvyse 64 znaku.")
    return parser


def _clock(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not 1 <= len(value) <= 64:
        raise ValueError("Cas --as-of musi mit 1 az 64 znaku.")
    clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Cas --as-of musi obsahovat casove pasmo.")
    return clock.astimezone(timezone.utc)


def _guard_output(output: Path, *, db_path: Path, identities_path: Path) -> None:
    if output.suffix.lower() != ".json" or output.is_dir():
        raise ValueError("Vystup musi byt samostatny soubor JSON.")
    target = output.resolve()
    production = PRODUCTION_DATA_DIRECTORY.resolve()
    if target == production or production in target.parents:
        raise ValueError("Report nesmi prepsat produkcni datove registry.")
    protected = (db_path, DEFAULT_DB_PATH, identities_path, DEFAULT_HEALTHCARE_IDENTITIES,
                 DEFAULT_TICKER_UNIVERSE_PATH, PROFILE_PATH)
    for path in protected:
        if target == path.resolve():
            raise ValueError("Report nesmi prepsat databazi ani vstupni registr.")
        if output.exists() and path.exists() and output.samefile(path):
            raise ValueError("Vystupni soubor je odkaz na chranenou databazi nebo registr.")


def _save_report(output: Path, report: dict, *, db_path: Path, identities_path: Path) -> None:
    _guard_output(output, db_path=db_path, identities_path=identities_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".healthcare_coverage_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck after collection/serialization in case an existing link changed.
        _guard_output(output, db_path=db_path, identities_path=identities_path)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        db_path, output = _path(args.db_path), _path(args.output)
        configured = os.environ.get("JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE")
        identities_path = _path(configured if configured else DEFAULT_HEALTHCARE_IDENTITIES)
        clock = _clock(args.as_of)
        if db_path.exists() and not db_path.is_file():
            raise ValueError("Cesta k DB musi oznacovat soubor.")
        _guard_output(output, db_path=db_path, identities_path=identities_path)
        # The report only needs db_path. Constructing ScoutStore would create a
        # missing DB and run migrations, so pass a path handle without that class.
        report = build_healthcare_coverage_report(SimpleNamespace(db_path=db_path),
            as_of=clock, identities_path=identities_path)
        report["inputs"] = {"db_path": str(db_path), "healthcare_identities_path": str(identities_path),
                            "healthcare_identities_configuration": "JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE" if configured else "DEFAULT",
                            "canonical_ticker_path": str(DEFAULT_TICKER_UNIVERSE_PATH),
                            "database_initialized": False, "network_collectors_run": False}
        _save_report(output, report, db_path=db_path, identities_path=identities_path)
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"[HEALTHCARE COVERAGE ERROR] {exc}") from exc
    print(json.dumps({"status": "SAVED", "canonical_subjects": report["universe"]["subjects"],
                      "runtime_status": report["runtime"]["status"],
                      "identity_manifest_status": report["identity_manifest"]["status"],
                      "sources": {source: {key: facts[key] for key in
                          ("applicable_profile_subjects", "mapped_subjects", "queried_issuers", "current_complete_source_checks")}
                          for source, facts in report["sources"].items()},
                      "completion_verified": False, "output_path": str(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
