from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

import pandas as pd

from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.services.prediction_label_service import PredictionLabelService
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


def _pending_symbols(store: SQLiteStore, limit: int) -> list[str]:
    snapshots = store.read_prediction_snapshots()
    if snapshots.empty or "label_status" not in snapshots.columns:
        return []
    pending = snapshots[
        snapshots["label_status"].astype(str).str.upper() == "PENDING"
    ].head(limit)
    symbols: list[str] = []
    for row in pending.to_dict(orient="records"):
        for value in (row.get("ticker"), row.get("benchmark_ticker") or "SPY"):
            ticker = str(value or "").strip().upper()
            if ticker and ticker not in symbols:
                symbols.append(ticker)
    return symbols


def resolve_prediction_labels(
    *,
    store: SQLiteStore,
    limit: int = 120,
    as_of: datetime | None = None,
    price_loader: PriceLoader | None = None,
) -> dict[str, object]:
    """Resolve a bounded batch of mature labels without running the pipeline."""

    if limit < 1:
        raise ValueError("limit musí být kladné číslo")
    store.ensure_schema()
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)

    symbols = _pending_symbols(store, limit)
    report: dict[str, object] = {
        "status": "SUCCESS",
        "as_of": clock.isoformat(),
        "batch_limit": limit,
        "candidate_symbols": len(symbols),
        "cache_fresh": 0,
        "cache_stale": 0,
        "downloaded_symbols": 0,
        "download_failures": 0,
    }

    if price_loader is None:
        cache = YahooOhlcCacheStore(store.db_path)
        client = YahooClient()
        frames: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        for ticker in symbols:
            lookup = cache.get(ticker, now=clock)
            if lookup.usable and lookup.frame is not None:
                frames[ticker] = lookup.frame
                report[f"cache_{lookup.state}"] = int(
                    report.get(f"cache_{lookup.state}", 0)
                ) + 1
            else:
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
                frames[ticker] = frame
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
    if int(resolution["source_failures"]) or int(report["download_failures"]):
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
