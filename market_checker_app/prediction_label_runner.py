from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

import pandas as pd

from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.price_methodology import (
    PRICE_METHOD_VERSION,
    YAHOO_ADJUSTMENT,
)
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.services.yahoo_corporate_action_history import (
    YahooCorporateActionHistoryClient,
)
from market_checker_app.storage.prediction_label_queue_store import (
    LabelQueueCandidate,
    PredictionLabelQueueStore,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore


PriceLoader = Callable[
    [str],
    pd.DataFrame | tuple[pd.DataFrame | None, str | None] | None,
]


class _SelectedSnapshotStore:
    """Narrow SQLiteStore view used to resolve exactly one fair queue page."""

    def __init__(self, store: SQLiteStore, snapshot_ids: list[str]) -> None:
        self._store = store
        self._snapshot_ids = set(snapshot_ids)
        self.db_path = store.db_path

    def read_prediction_snapshots(self, *args, **kwargs) -> pd.DataFrame:
        frame = self._store.read_prediction_snapshots(*args, **kwargs)
        if frame.empty or "snapshot_id" not in frame.columns:
            return frame
        return frame[frame["snapshot_id"].astype(str).isin(self._snapshot_ids)].copy()

    def update_prediction_snapshot_labels(self, labels: list[dict[str, object]]) -> int:
        return self._store.update_prediction_snapshot_labels(labels)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _candidate_requirements(
    candidates: list[LabelQueueCandidate],
) -> dict[str, tuple[date, ...]]:
    requirements: dict[str, set[date]] = defaultdict(set)
    for candidate in candidates:
        base, future = target_us_equity_window(
            candidate.as_of,
            candidate.horizon_trading_days,
        )
        sessions = {base.session_date, *(item.session_date for item in future)}
        requirements[candidate.ticker].update(sessions)
        requirements[candidate.benchmark_ticker].update(sessions)
    return {
        ticker: tuple(sorted(sessions))
        for ticker, sessions in requirements.items()
    }


def _pending_ids(store: SQLiteStore, snapshot_ids: list[str]) -> set[str]:
    if not snapshot_ids:
        return set()
    frame = store.read_prediction_snapshots()
    if frame.empty or "snapshot_id" not in frame.columns:
        return set()
    wanted = frame[frame["snapshot_id"].astype(str).isin(snapshot_ids)]
    if "label_status" not in wanted.columns:
        return set()
    pending = wanted[wanted["label_status"].astype(str).str.upper() == "PENDING"]
    return set(pending["snapshot_id"].astype(str))


def _load_page_frames(
    *,
    store: SQLiteStore,
    candidates: list[LabelQueueCandidate],
    clock: datetime,
    cache: YahooOhlcCacheStore,
    client: YahooCorporateActionHistoryClient,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    requirements = _candidate_requirements(candidates)
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    stats = {
        "cache_fresh": 0,
        "cache_stale": 0,
        "cache_incomplete": 0,
        "cache_retry_deferred": 0,
        "range_incomplete_after_refresh": 0,
        "downloaded_symbols": 0,
        "download_failures": 0,
    }

    for ticker, sessions in requirements.items():
        lookup = cache.get(ticker, now=clock, required_sessions=sessions)
        if lookup.usable and lookup.frame is not None:
            frames[ticker] = lookup.frame
            stats[f"cache_{lookup.state}"] += 1
            continue
        if lookup.state == "incomplete":
            stats["cache_incomplete"] += 1
        if not lookup.can_retry(clock):
            stats["cache_retry_deferred"] += 1
            continue
        missing.append(ticker)

    if missing:
        fetched, warnings = client.fetch_batch(
            missing,
            period="max",
            interval="1d",
            batch_size=50,
        )
        for ticker, frame in fetched.items():
            try:
                cache.upsert_success(ticker, frame, fetched_at=clock)
            except ValueError as exc:
                warning = f"Corporate-action cache odmítla {ticker}: {exc}"
                warnings[ticker] = warning
                cache.note_failure(ticker, warning, fetched_at=clock)
                continue
            refreshed = cache.get(
                ticker,
                now=clock,
                required_sessions=requirements[ticker],
            )
            if refreshed.usable and refreshed.frame is not None:
                frames[ticker] = refreshed.frame
            else:
                stats["range_incomplete_after_refresh"] += 1
        for ticker, warning in warnings.items():
            if ticker not in fetched:
                cache.note_failure(ticker, warning, fetched_at=clock)
        stats["downloaded_symbols"] = len(fetched)
        stats["download_failures"] = len(warnings)

    return frames, stats


def _metrics_payload(prefix: str, metrics) -> dict[str, object]:
    return {
        f"{prefix}_backlog": metrics.backlog,
        f"{prefix}_due": metrics.due,
        f"{prefix}_actionable": metrics.actionable,
        f"{prefix}_retry_deferred": metrics.deferred_retry,
        f"{prefix}_immature": metrics.immature,
        f"{prefix}_oldest_pending_as_of": (
            metrics.oldest_pending_as_of.isoformat()
            if metrics.oldest_pending_as_of is not None
            else None
        ),
        f"{prefix}_oldest_due_at": (
            metrics.oldest_due_at.isoformat()
            if metrics.oldest_due_at is not None
            else None
        ),
    }


def resolve_prediction_labels(
    *,
    store: SQLiteStore,
    limit: int = 1000,
    page_size: int = 120,
    time_budget_seconds: float = 240.0,
    as_of: datetime | None = None,
    price_loader: PriceLoader | None = None,
) -> dict[str, object]:
    """Drain due labels fairly without letting blocked rows starve the queue.

    ``limit`` is the maximum number of snapshots attempted in one invocation;
    pages are selected from the entire due queue. Pending failures receive a
    persisted retry checkpoint, so later pages can progress in the same run and
    restarts do not hammer the same unavailable source.
    """

    if limit < 1:
        raise ValueError("limit musí být kladné číslo")
    if page_size < 1:
        raise ValueError("page_size musí být kladné číslo")
    if time_budget_seconds <= 0:
        raise ValueError("time_budget_seconds musí být kladné číslo")

    store.ensure_schema()
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)

    queue = PredictionLabelQueueStore(store.db_path)
    queue.cleanup_finalized()
    before = queue.metrics(as_of=clock)
    started = time.monotonic()

    aggregate = {
        "resolved": 0,
        "unavailable": 0,
        "deferred": 0,
        "source_failures": 0,
        "cache_fresh": 0,
        "cache_stale": 0,
        "cache_incomplete": 0,
        "cache_retry_deferred": 0,
        "range_incomplete_after_refresh": 0,
        "downloaded_symbols": 0,
        "download_failures": 0,
    }
    processed = 0
    page_count = 0
    seen_symbols: set[str] = set()
    time_budget_exhausted = False

    strict_cache: YahooOhlcCacheStore | None = None
    action_client: YahooCorporateActionHistoryClient | None = None
    if price_loader is None:
        strict_cache = YahooOhlcCacheStore(
            store.db_path,
            adjustment=YAHOO_ADJUSTMENT,
            methodology_version=PRICE_METHOD_VERSION,
        )
        action_client = YahooCorporateActionHistoryClient()

    while processed < limit:
        if time.monotonic() - started >= time_budget_seconds:
            time_budget_exhausted = True
            break
        candidates = queue.select_candidates(
            as_of=clock,
            limit=min(page_size, limit - processed),
        )
        if not candidates:
            break

        page_count += 1
        ids = [candidate.snapshot_id for candidate in candidates]
        processed += len(candidates)
        for candidate in candidates:
            seen_symbols.add(candidate.ticker)
            seen_symbols.add(candidate.benchmark_ticker)
        queue.mark_attempted(ids, as_of=clock)

        active_loader = price_loader
        if active_loader is None:
            assert strict_cache is not None and action_client is not None
            frames, page_stats = _load_page_frames(
                store=store,
                candidates=candidates,
                clock=clock,
                cache=strict_cache,
                client=action_client,
            )
            for key, value in page_stats.items():
                aggregate[key] += int(value)

            def cached_loader(ticker: str) -> pd.DataFrame | None:
                return frames.get(str(ticker).strip().upper())

            active_loader = cached_loader

        selected_store = _SelectedSnapshotStore(store, ids)
        resolution = PredictionLabelService().resolve_pending_snapshots(
            store=selected_store,  # type: ignore[arg-type]
            price_loader=active_loader,
            as_of=clock,
            limit=None,
        )
        for key in ("resolved", "unavailable", "deferred", "source_failures"):
            aggregate[key] += int(resolution.get(key) or 0)

        still_pending = _pending_ids(store, ids)
        queue.defer_pending(
            {
                snapshot_id: "source_or_required_price_range_not_ready"
                for snapshot_id in still_pending
            },
            as_of=clock,
        )
        queue.cleanup_finalized()

    after = queue.metrics(as_of=clock)
    report: dict[str, object] = {
        "status": "SUCCESS",
        "as_of": clock.isoformat(),
        "capacity_limit": limit,
        "page_size": page_size,
        "time_budget_seconds": time_budget_seconds,
        "time_budget_exhausted": time_budget_exhausted,
        "page_count": page_count,
        "processed_candidates": processed,
        "candidate_symbols": len(seen_symbols),
        "pending_before": before.backlog,
        "pending_after": after.backlog,
        "backlog": after.backlog,
        "oldest_waiting_as_of": (
            after.oldest_pending_as_of.isoformat()
            if after.oldest_pending_as_of is not None
            else None
        ),
        "price_method_version": PRICE_METHOD_VERSION,
        "price_adjustment": YAHOO_ADJUSTMENT,
        **aggregate,
        **_metrics_payload("before", before),
        **_metrics_payload("after", after),
    }
    if (
        aggregate["source_failures"]
        or aggregate["download_failures"]
        or aggregate["range_incomplete_after_refresh"]
        or aggregate["deferred"]
        or time_budget_exhausted
    ):
        report["status"] = "PARTIAL"
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Spravedlivě uzavře zralé historické predikce bez nové analýzy trhu."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("outputs/market_checker_history.db"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("outputs/prediction_label_resolution_latest.json"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximální počet zralých snapshotů zpracovaných v jednom běhu.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=120,
        help="Velikost jedné obnovitelné stránky fronty.",
    )
    parser.add_argument(
        "--time-budget-seconds",
        type=float,
        default=240.0,
        help="Maximální wall-clock budget pro jeden resolver běh.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        report = resolve_prediction_labels(
            store=SQLiteStore(args.db_path),
            limit=args.limit,
            page_size=args.page_size,
            time_budget_seconds=args.time_budget_seconds,
        )
        _atomic_json(args.output_path, report)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"[LABEL CHYBA] {exc}") from exc
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
