from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from market_checker_app.config import AppConfig
from market_checker_app.exporters.dashboard_builder import build_dashboard_tables
from market_checker_app.exporters.excel_exporter import ExcelExporter
from market_checker_app.services.evaluation_service import EvaluationService
from market_checker_app.services.pipeline_service import PipelineService
from market_checker_app.storage.sqlite_store import SQLiteStore


def _require_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise RuntimeError(f"{label}: missing columns {missing}")


def run_smoke_test(tickers: list[str], runs: int, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "smoke_history.db"

    config = AppConfig(
        output_dir=output_dir,
        sqlite_path=db_path,
        save_history=True,
        export_excel=True,
        compare_previous_run=True,
    )
    store = SQLiteStore(config.sqlite_path)
    pipeline = PipelineService(config)

    rss_sources = ["https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"]

    run_ids: list[int] = []
    last_result: dict[str, object] | None = None
    for run_index in range(1, runs + 1):
        print(f"[SMOKE] Run {run_index}/{runs} | tickers={tickers}")
        result = pipeline.run(tickers, rss_sources, store)
        last_result = result

        run_id = result.get("run_id")
        if run_id is None:
            raise RuntimeError("Pipeline run_id is None; SQLite write likely failed.")
        run_ids.append(int(run_id))

        signals = result["signals"]
        if not isinstance(signals, pd.DataFrame) or signals.empty:
            raise RuntimeError("Pipeline returned empty signals dataframe.")

        _require_columns(
            signals,
            [
                "scoring_version",
                "legacy_total_score",
                "legacy_signal",
                "tech_source_used",
                "final_total_score",
                "signal",
            ],
            "signals",
        )

        stored = store.read_signals_for_run(int(run_id))
        if stored.empty:
            raise RuntimeError(f"SQLite has no signal rows for run_id={run_id}.")
        _require_columns(
            stored,
            [
                "scoring_version",
                "legacy_total_score",
                "legacy_signal",
                "tech_source_used",
                "final_total_score",
                "signal",
            ],
            "sqlite.signal_history",
        )
        print(f"[SMOKE] Run {run_id}: signals={len(signals)} sqlite_rows={len(stored)}")

    assert last_result is not None
    signals = last_result["signals"]
    assert isinstance(signals, pd.DataFrame)

    history = store.read_global_history()
    if history.empty:
        raise RuntimeError("Global history is empty after smoke runs.")

    eval_frames = EvaluationService().evaluate_snapshots(history)
    expected_eval_keys = {
        "score_comparison",
        "top_bottom_new",
        "top_bottom_legacy",
        "by_signal_new",
        "by_signal_legacy",
        "strategy_side_by_side",
        "signal_transition",
        "hit_rate_new_vs_legacy",
        "coverage",
    }
    if not expected_eval_keys.issubset(eval_frames):
        raise RuntimeError(f"Evaluation outputs missing keys: {sorted(expected_eval_keys - set(eval_frames))}")

    if eval_frames["score_comparison"].empty:
        raise RuntimeError("Evaluation score_comparison is empty.")

    dashboard_tables = build_dashboard_tables(signals)
    excel_path = output_dir / "smoke_runtime.xlsx"
    ExcelExporter().export(
        excel_path,
        signals,
        pd.DataFrame({"source": rss_sources}),
        pd.DataFrame(),
        dashboard_tables,
        pd.DataFrame(),
    )

    if not excel_path.exists() or excel_path.stat().st_size == 0:
        raise RuntimeError("Excel export was not created.")

    exported_signals = pd.read_excel(excel_path, sheet_name="Signals")
    _require_columns(
        exported_signals,
        ["scoring_version", "legacy_total_score", "legacy_signal", "tech_source_used"],
        "excel.Signals",
    )

    tech_source_distribution = {}
    if "tech_source_used" in signals.columns:
        tech_source_distribution = signals["tech_source_used"].value_counts(dropna=False).to_dict()

    summary = {
        "run_ids": run_ids,
        "tickers": tickers,
        "signals_rows": len(signals),
        "history_rows": len(history),
        "excel_path": str(excel_path),
        "tech_source_distribution": tech_source_distribution,
        "evaluation_non_empty": [k for k, v in eval_frames.items() if isinstance(v, pd.DataFrame) and not v.empty],
    }
    print("[SMOKE] SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Runtime smoke-test for Market Checker pipeline")
    parser.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT", "TSLA"], help="Ticker list (3-5 recommended)")
    parser.add_argument("--runs", type=int, default=2, help="Number of pipeline runs (2 recommended for forward-return proxy)")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/smoke_runtime"), help="Smoke output directory")
    args = parser.parse_args()

    tickers = list(dict.fromkeys([t.strip().upper() for t in args.tickers if t.strip()]))
    if len(tickers) < 3 or len(tickers) > 5:
        raise SystemExit("Use 3 to 5 tickers for this smoke-test.")
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")

    run_smoke_test(tickers=tickers, runs=args.runs, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
