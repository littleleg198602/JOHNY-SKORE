"""SEC-only bulk identity preparation, checkpointing, and reviewed export."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from market_checker_app.collectors.sec_edgar_client import SecEdgarClient, SecEdgarError
from market_checker_app.services.sec13f_identity_bulk import (
    MAX_REVIEW_BYTES, MAX_UNIVERSE, SourceSnapshot, Sec13fIdentityBulk, TICKER_URL,
    _clock, atomic_json, guard_production_write, list_url, load_snapshots, official_url, read_checkpoint, save_snapshot,
)
from market_checker_app.services.sec13f_scout_service import DEFAULT_SECURITIES, load_verified_securities
from market_checker_app.utils.ticker_universe import load_canonical_tickers


def _configured_client() -> SecEdgarClient:
    user_agent = os.getenv("JOHNY_SKORE_SEC_USER_AGENT", "").strip()
    if not user_agent or "@" not in user_agent:
        raise ValueError("Chybí existující SEC kontakt JOHNY_SKORE_SEC_USER_AGENT; použij Nainstalovat_Patraci_Agenty.bat.")
    # Reuse the connector's pacing, redirect policy, 4MB document bound,
    # retries and immediate access-block stop behavior.
    return SecEdgarClient(user_agent=user_agent, max_attempts=2)


def capture_source(*, workspace: Path, kind: str, url: str,
                   published_at: datetime | None = None,
                   client: SecEdgarClient | None = None) -> SourceSnapshot:
    guard_production_write(workspace / "snapshots")
    official_url(kind, url)
    if kind in {"schedule13", "ticker_filing", "corporate_action_filing"} and published_at is None:
        raise ValueError("Filing snapshots require their SEC publication time before capture")
    if published_at is not None:
        published_at = _clock(published_at)
        if published_at > datetime.now(timezone.utc):
            raise ValueError("Filing publication time is in the future")
    client = client or _configured_client()
    payload = client._request_bytes(url)
    snapshot = SourceSnapshot(kind, url, datetime.now(timezone.utc), payload, published_at)
    save_snapshot(workspace, snapshot)
    return snapshot


def _guard_destinations(paths: list[Path], *, workspace: Path,
                        existing_path: Path, inputs: list[Path] | None = None) -> None:
    protected = {existing_path.resolve(), DEFAULT_SECURITIES.resolve(),
                 *(path.resolve() for path in inputs or [])}
    archive = (workspace / "snapshots").resolve()
    for path in paths:
        guard_production_write(path)
        resolved = path.resolve()
        if resolved in protected or resolved == archive or archive in resolved.parents:
            raise ValueError("Output must be a reviewable copy; production registry and input evidence cannot be overwritten")


def _latest_source(sources: list[SourceSnapshot]) -> SourceSnapshot:
    latest_time = max(source.observed_at for source in sources)
    latest = [source for source in sources if source.observed_at == latest_time]
    if len(latest) != 1:
        raise ValueError("Ambiguous latest SEC source snapshots")
    return latest[0]


def _selected_sources(snapshots: list[SourceSnapshot], *, quarter: str,
                      state: dict | None = None) -> list[SourceSnapshot]:
    if state is not None:
        indexed = {snapshot.snapshot_id: snapshot for snapshot in snapshots}
        ids = state.get("snapshot_ids")
        if not isinstance(ids, list) or any(key not in indexed for key in ids):
            raise ValueError("Checkpoint refers to missing archived sources")
        return [indexed[key] for key in ids]
    grouped = {}
    for snapshot in snapshots:
        if snapshot.kind == "13f_list" and snapshot.url != list_url(quarter):
            continue
        # One catalog across its two API variants, one copy per filing URL.
        key = snapshot.kind if snapshot.kind == "ticker_catalog" else (snapshot.kind, snapshot.url)
        grouped.setdefault(key, []).append(snapshot)
    return [_latest_source(group) for group in grouped.values()]


def run_preparation(*, workspace: Path, quarter: str, checkpoint: Path,
                    existing_path: Path = DEFAULT_SECURITIES,
                    batch_size: int = 100, one_batch: bool = False,
                    as_of: datetime | None = None, capture_global: bool = False) -> dict:
    _guard_destinations([checkpoint], workspace=workspace, existing_path=existing_path)
    guard_production_write(workspace / "snapshots")
    list_url(quarter)
    if as_of is not None:
        as_of = _clock(as_of)
    state = read_checkpoint(checkpoint)
    # Initial preparation needs only two global sources. Keep a cached
    # collection of hundreds of large filings out of this fast path.
    snapshots = load_snapshots(workspace,
                               snapshot_ids=set(state["snapshot_ids"]) if state else None,
                               kinds=None if state else {"ticker_catalog", "13f_list"})
    if capture_global and state is None:
        missing = [(kind, url) for kind, url in (("ticker_catalog", TICKER_URL), ("13f_list", list_url(quarter)))
                   if not any(snapshot.kind == kind and (kind == "ticker_catalog" or snapshot.url == url)
                              for snapshot in snapshots)]
        if missing:
            client = _configured_client()
            for kind, url in missing:
                snapshots.append(capture_source(workspace=workspace, kind=kind, url=url, client=client))
    snapshots = _selected_sources(snapshots, quarter=quarter, state=state)
    clock = as_of or (_clock(state["as_of"]) if state else datetime.now(timezone.utc))
    builder = Sec13fIdentityBulk(
        snapshots=snapshots, universe=load_canonical_tickers(), quarter=quarter,
        as_of=clock, existing=load_verified_securities(existing_path),
    )
    while True:
        result = builder.run_batch(checkpoint, batch_size=batch_size)
        print(f"SEC identity candidates: {result['next_index']}/{result['total_tickers']} saved", flush=True)
        if one_batch or result["next_index"] == result["total_tickers"]:
            return result


def export_reviewed(*, workspace: Path, quarter: str, review_path: Path,
                    output: Path, existing_path: Path = DEFAULT_SECURITIES,
                    as_of: datetime | None = None) -> dict:
    audit_path = output.with_name(output.stem + ".review.json")
    _guard_destinations([output, audit_path], workspace=workspace, existing_path=existing_path,
                        inputs=[review_path, workspace / "candidates.json"])
    if review_path.stat().st_size > MAX_REVIEW_BYTES:
        raise ValueError("Unbounded reviewed identity file")
    reviews = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(reviews, list) or not 0 < len(reviews) <= MAX_UNIVERSE:
        raise ValueError("Reviewed batch must be a nonempty bounded list")
    ids = set()
    for entry in reviews:
        if not isinstance(entry, dict) or not isinstance(entry.get("review"), dict):
            raise ValueError("Each proposed identity needs a dated explicit review")
        for key in ("list_snapshot_id", "instrument_snapshot_id", "ticker_snapshot_id"):
            reference = entry["review"].get(key)
            if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{64}", reference):
                raise ValueError("Reviewed identity has malformed snapshot references")
            ids.add(reference)
        transitions = entry["review"].get("identity_transitions", [])
        if not isinstance(transitions, list) or len(transitions) > 2:
            raise ValueError("Identity transitions must be a bounded list of temporal neighbours")
        for transition in transitions:
            reference = transition.get("source_snapshot_id") if isinstance(transition, dict) else None
            if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{64}", reference):
                raise ValueError("Identity transition has a malformed primary snapshot reference")
            ids.add(reference)
    clock = _clock(as_of) if as_of is not None else datetime.now(timezone.utc)
    catalogs = [source for source in load_snapshots(workspace, kinds={"ticker_catalog"})
                if _clock(source.observed_at) <= clock]
    if not catalogs:
        raise ValueError("No ticker snapshot known at export time")
    catalog = _latest_source(catalogs)
    sources = [catalog, *load_snapshots(workspace, snapshot_ids=ids)]
    baseline_sha256 = hashlib.sha256(existing_path.read_bytes()).hexdigest()
    existing = load_verified_securities(existing_path)
    builder = Sec13fIdentityBulk(
        snapshots=sources, universe=load_canonical_tickers(), quarter=quarter,
        as_of=clock, existing=existing,
    )
    accepted = builder.validate_reviewed(reviews)
    # Produce a proposal and its exact review/source provenance, not an edit
    # to production or a specialist-completion claim.
    proposal = [*existing, *accepted]
    with TemporaryDirectory(prefix="sec13f-reviewed-") as directory:
        validation_path = Path(directory) / "proposal.json"
        atomic_json(validation_path, proposal)
        load_verified_securities(validation_path)
    if hashlib.sha256(existing_path.read_bytes()).hexdigest() != baseline_sha256:
        raise ValueError("Existing registry changed during review; reload its exact identity versions")
    atomic_json(output, proposal)
    audit = {"schema_version": 1, "status": "REVIEWED_EXPORT_PROPOSAL",
             "new_identities": len(accepted), "total_identities": len(existing) + len(accepted),
             "production_registry_changed": False, "specialist_done_changed": False,
             "as_of": builder.as_of.isoformat(), "fingerprint": builder.fingerprint,
             "proposal_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
             "baseline_registry_sha256": baseline_sha256,
             "preserved_identity_versions": len(existing),
             "primary_identity_transitions": sum(len(entry["review"].get("identity_transitions", [])) for entry in reviews),
             "reviews": reviews, "sources": {key: source.metadata() for key, source in builder.snapshots.items()}}
    atomic_json(audit_path, audit)
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare all 687 SEC identity candidates; validate explicit reviews separately")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--workspace", type=Path, default=Path("outputs/sec13f_identity_bulk"))
    prepare.add_argument("--quarter", default="2026q2")
    prepare.add_argument("--checkpoint", type=Path)
    prepare.add_argument("--batch-size", type=int, default=100)
    prepare.add_argument("--one-batch", action="store_true")
    prepare.add_argument("--capture-global", action="store_true")
    prepare.add_argument("--as-of", type=_clock)
    capture = sub.add_parser("capture")
    capture.add_argument("--workspace", type=Path, default=Path("outputs/sec13f_identity_bulk"))
    capture.add_argument("--kind", choices=["ticker_catalog", "13f_list", "schedule13", "ticker_filing", "corporate_action_filing"], required=True)
    capture.add_argument("--url", required=True)
    capture.add_argument("--published-at", type=_clock)
    validate = sub.add_parser("export-reviewed")
    validate.add_argument("--workspace", type=Path, default=Path("outputs/sec13f_identity_bulk"))
    validate.add_argument("--quarter", default="2026q2")
    validate.add_argument("--reviews", type=Path, required=True)
    validate.add_argument("--output", type=Path, default=Path("outputs/sec13f_identity_bulk/reviewed_registry_proposal.json"))
    validate.add_argument("--existing", type=Path, default=DEFAULT_SECURITIES)
    validate.add_argument("--as-of", type=_clock)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            run_preparation(workspace=args.workspace, quarter=args.quarter,
                            checkpoint=args.checkpoint or args.workspace / "candidates.json",
                            batch_size=args.batch_size, one_batch=args.one_batch,
                            as_of=args.as_of, capture_global=args.capture_global)
        elif args.command == "capture":
            source = capture_source(workspace=args.workspace, kind=args.kind,
                                    url=args.url, published_at=args.published_at)
            print(f"Saved SEC source snapshot {source.snapshot_id}")
        else:
            result = export_reviewed(workspace=args.workspace, quarter=args.quarter,
                                     review_path=args.reviews, output=args.output,
                                     existing_path=args.existing, as_of=args.as_of)
            print(f"Reviewed registry proposal: {result['new_identities']} additions saved")
    except KeyboardInterrupt:
        print("SEC identity bulk interrupted; rerun the same preparation to resume its saved checkpoint.", flush=True)
        return 130
    except (OSError, ValueError, KeyError, TypeError, SecEdgarError) as exc:
        print(f"SEC identity bulk stopped: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
