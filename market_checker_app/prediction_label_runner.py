from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

import pandas as pd

from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.prediction_contract import PRIMARY_TARGET_VERSION
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.us_equity_calendar import target_us_equity_window
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.storage.yahoo_ohlc_cache_store import YahooOhlcCacheStore


PriceLoader = Callable[[str], pd.DataFrame | tuple[pd.DataFrame | None, str | None] | None]


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


def _as_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pending_requirements(
    store: SQLiteStore,
    limit: int,
    *,
    clock: datetime,
) -> dict[str, tuple[date, ...]]:
    """Return exact calendar sessions needed by due snapshots in the batch."""

    snapshots = store.read_prediction_snapshots()
    if snapshots.empty or "label_status" not in snapshots.columns:
        return {}
    pending = snapshots[
        snapshots["label_status"].astype(str).str.upper() == "PENDING"
    ].head(limit)
    requirements: dict[str, set[date]] = defaultdict(set)
    for row in pending.to_dict(orient="records"):
        if str(row.get("target_version") or "") != PRIMARY_TARGET_VERSION:
            continue
        snapshot_as_of = _as_utc(row.get("as_of"))
        if snapshot_as_of is None:
            continue
        try:
            horizon = int(row.get("horizon_trading_days") or 5)
        except (TypeError, ValueError):
            continue
        if horizon < 1:
            continue
        base, future = target_us_equity_window(snapshot_as_of, horizon)
        if future[-1].close_at > clock:
            continue
        sessions = {base.session_date, *(item.session_date for item in future)}
        for value in (row.get("ticker"), row.get("benchmark_ticker") or "SPY"):
            ticker = str(value or "").strip().upper()
            if ticker:
                requirements[ticker].update(sessions)
    return {
        ticker: tuple(sorted(sessions))
        for ticker, sessions in requirements.items()
    }


def resolve_prediction_labels(
    *,
    store: SQLiteStore,
    limit: int = 120,
    as_of: datetime | None = None,
    price_loader: PriceLoader | None = None,
) -> dict[str, object]:
    """Resolve a bounded batch without accepting incomplete cached ranges."""

    if limit < 1:
        raise ValueError("limit musí být kladné číslo")
    store.ensure_schema()
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)

    requirements = _pending_requirements(store, limit, clock=clock)
    symbols = list(requirements)
    report: dict[str, object] = {
        "status": "SUCCESS",
        "as_of": clock.isoformat(),
        "batch_limit": limit,
        "candidate_symbols": len(symbols),
        "cache_fresh": 0,
        "cache_stale": 0,
        "cache_incomplete": 0,
        "cache_retry_deferred": 0,
        "range_incomplete_after_refresh": 0,
        "downloaded_symbols": 0,
        "download_failures": 0,
    }

    if price_loader is None:
        cache = YahooOhlcCacheStore(store.db_path)
        client = YahooClient()
        frames: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for ticker in symbols:
            lookup = cache.get(
                ticker,
                now=clock,
                required_sessions=requirements[ticker],
            )
            if lookup.usable and lookup.frame is not None:
                frames[ticker] = lookup.frame
                report[f"cache_{lookup.state}"] = int(
                    report.get(f"cache_{lookup.state}", 0)
                ) + 1
                continue
            if lookup.state == "incomplete":
                report["cache_incomplete"] = int(report["cache_incomplete"]) + 1
            if not lookup.can_retry(clock):
                report["cache_retry_deferred"] = int(
                    report["cache_retry_deferred"]
                ) + 1
                continue
            missing.append(ticker)

        if missing:
            fetched, warnings = client.fetch_ohlc_batch(
                missing,
                period="1y",
                interval="1d",
                batch_size=50,
            )
            for ticker, frame in fetched.items():
                cache.upsert_success(ticker, frame, fetched_at=clock)
                refreshed = cache.get(
                    ticker,
                    now=clock,
                    required_sessions=requirements[ticker],
                )
                if refreshed.usable and refreshed.frame is not None:
                    frames[ticker] = refreshed.frame
                else:
                    report["range_incomplete_after_refresh"] = int(
                        report["range_incomplete_after_refresh"]
                    ) + 1
            for ticker, warning in warnings.items():
                cache.note_failure(ticker, warning, fetched_at=clock)
            report["downloaded_symbols"] = len(fetched)
            report["download_failures"] = len(warnings)

        def loader(ticker: str) -> pd.DataFrame | None:
            return frames.get(str(ticker).strip().upper())

        price_loader = loader

    resolution = PredictionLabelService().resolve_pending_snapshots(
        store=store,
        price_loader=price_loader,
        as_of=clock,
        limit=limit,
    )
    report.update(resolution)
    if (
        int(resolution["source_failures"])
        or int(report["download_failures"])
        or int(report["range_incomplete_after_refresh"])
    ):
        report["status"] = "PARTIAL"
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dávkově uzavře zralé historické predikce bez nové analýzy trhu."
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
        default=120,
        help="Maximální počet PENDING snapshotů v jednom obnovitelném běhu.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        report = resolve_prediction_labels(
            store=SQLiteStore(args.db_path),
            limit=args.limit,
        )
        _atomic_json(args.output_path, report)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"[LABEL CHYBA] {exc}") from exc
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
