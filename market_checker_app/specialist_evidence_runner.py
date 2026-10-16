"""Explicit offline evidence actions; never starts a provider collection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from market_checker_app.services.captured_identity_review_service import (
    MAX_PLAN_BYTES, _json_bytes, _publish, retained_file, run_captured_identity_review,
)

PRIME_PLAN_VERSION = "prime_archive_recovery_plan_v1"
_HASH = re.compile(r"[a-f0-9]{64}")


def _existing_database(path: Path) -> Path:
    if path is None:
        raise ValueError("Choose the existing retained database")
    path = Path(path)
    if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Choose the existing retained database")
    return path


def run_prime_archive_recovery(*, db_path: Path | None, source_root: Path, plan_name: str,
                               archive_root: Path, validate_only: bool = True) -> dict:
    from market_checker_app.services.usaspending_prime_archive_recovery import recover_prime_awards_from_archives
    from market_checker_app.storage.scout_store import ScoutStore
    if type(validate_only) is not bool:
        raise ValueError("Choose explicit validation or import")
    plan_path = retained_file(source_root, plan_name)
    with plan_path.open("rb") as stream:
        body = stream.read(MAX_PLAN_BYTES + 1)
    if len(body) > MAX_PLAN_BYTES:
        raise ValueError("Recovery plan byte budget exceeded")
    plan = _json_bytes(body)
    if (set(plan) != {"schema_version", "subject_id", "manifests"}
            or plan["schema_version"] != PRIME_PLAN_VERSION
            or not isinstance(plan["subject_id"], str)
            or not isinstance(plan["manifests"], list) or not 1 <= len(plan["manifests"]) <= 20):
        raise ValueError("Invalid bounded prime recovery plan")
    paths, pins = [], {}
    for item in plan["manifests"]:
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or not isinstance(item["sha256"], str) or not _HASH.fullmatch(item["sha256"])):
            raise ValueError("Recovery requires original manifest hashes")
        path = retained_file(source_root, item["path"])
        if str(path) in pins:
            raise ValueError("Duplicate recovery manifest")
        paths.append(path); pins[str(path)] = item["sha256"]
    # Validation can inspect the original corpus without constructing a store.
    # Import is explicit and uses the existing database; no fresh source quota.
    now = datetime.now(timezone.utc)
    review = recover_prime_awards_from_archives(None, manifests=paths,
        expected_manifest_sha256=pins, subject_id=plan["subject_id"], as_of=now,
        validate_only=True)
    if not validate_only and review.get("status") == "VALIDATED":
        store = ScoutStore(_existing_database(db_path))
        review = recover_prime_awards_from_archives(store, manifests=paths,
            expected_manifest_sha256=pins, subject_id=plan["subject_id"], as_of=now,
            validate_only=False)
    result = {"schema_version": "prime_archive_recovery_action_v1", "review": review,
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "plan_sha256": hashlib.sha256(body).hexdigest(), "validate_only": validate_only,
              "source_http_requests": 0, "quota_reconstructed": False,
              "registry_adoptions": 0, "scoring_applied": False,
              "full_specialist_acceptance": False}
    name, digest = _publish(result, archive_root)
    result.update(archive_status="SAVED", archive_filename=name, archive_sha256=digest)
    return result


def run_prospective_history_action(*, action: str, history_directory: Path,
                                   db_path: Path | None = None, snapshot_id: str | None = None,
                                   source_root: Path | None = None, source_archive_names: tuple[str, ...] = (),
                                   label_observation_name: str | None = None,
                                   maximum: int = 200) -> dict:
    from market_checker_app.services.prospective_specialist_history import ProspectiveSpecialistHistory
    if action not in {"register", "attach", "report"}:
        raise ValueError("Choose an explicit history action")
    history = ProspectiveSpecialistHistory(history_directory)
    if action == "report":
        if snapshot_id or source_archive_names or label_observation_name:
            raise ValueError("History report cannot mutate a forecast")
        return history.read_report(as_of=datetime.now(timezone.utc), maximum=maximum)
    if not isinstance(snapshot_id, str) or not _HASH.fullmatch(snapshot_id):
        raise ValueError("Select the exact stored forecast ID")
    database = _existing_database(db_path)
    if action == "register":
        if label_observation_name or len(source_archive_names) > 24 or len(set(source_archive_names)) != len(source_archive_names):
            raise ValueError("Registration requires distinct bounded original sources")
        if source_archive_names and source_root is None:
            raise ValueError("Select the retained source directory")
        paths = [retained_file(source_root, name) for name in source_archive_names]
        return history.register_stored_snapshot(database, snapshot_id, source_archive_paths=paths)
    if source_archive_names or source_root is None or not label_observation_name:
        raise ValueError("Attachment requires one exact retained label observation")
    return history.attach_label(database, snapshot_id,
        label_observation_path=retained_file(source_root, label_observation_name))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Kontrola původních uložených důkazů bez nového stahování.")
    commands = parser.add_subparsers(dest="action", required=True)
    for kind in ("review", "prime"):
        command = commands.add_parser(kind)
        command.add_argument("--source-root", type=Path, required=True)
        command.add_argument("--plan", required=True, help="Relativní název uloženého plánu")
        command.add_argument("--archive-root", type=Path, required=True)
        if kind == "prime":
            command.add_argument("--db-path", type=Path)
            command.add_argument("--import-targets", action="store_true", help="Uložit jen skutečně doložené prime nálezy")
    for kind in ("register", "attach", "report"):
        command = commands.add_parser(kind)
        command.add_argument("--history-directory", type=Path, required=True)
        if kind != "report":
            command.add_argument("--db-path", type=Path, required=True)
            command.add_argument("--snapshot-id", required=True)
            command.add_argument("--source-root", type=Path)
        if kind == "register":
            command.add_argument("--source-archive", action="append", default=[])
        if kind == "attach":
            command.add_argument("--label-observation", required=True)
        if kind == "report":
            command.add_argument("--maximum", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        if args.action == "review":
            result = run_captured_identity_review(source_root=args.source_root,
                plan_name=args.plan, archive_root=args.archive_root)
        elif args.action == "prime":
            if args.import_targets and args.db_path is None:
                parser.error("Import vyžaduje původní databázi")
            result = run_prime_archive_recovery(db_path=args.db_path, source_root=args.source_root,
                plan_name=args.plan, archive_root=args.archive_root, validate_only=not args.import_targets)
        else:
            result = run_prospective_history_action(action=args.action, history_directory=args.history_directory,
                db_path=getattr(args, "db_path", None), snapshot_id=getattr(args, "snapshot_id", None),
                source_root=getattr(args, "source_root", None),
                source_archive_names=tuple(getattr(args, "source_archive", ())),
                label_observation_name=getattr(args, "label_observation", None), maximum=getattr(args, "maximum", 200))
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "INVALID_RETAINED_EVIDENCE", "source_http_requests": 0,
                          "scoring_applied": False}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    if result.get("review", {}).get("status") == "REJECTED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
