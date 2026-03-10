from __future__ import annotations

from pathlib import Path

import pandas as pd

from market_checker_app.analysis.trend_analysis import score_distribution
from market_checker_app.services.comparison_service import ComparisonService
from market_checker_app.storage.sqlite_store import SQLiteStore


class HistoryService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def load_global_trends(self) -> dict[str, pd.DataFrame]:
        history = self.store.read_global_history()
        if history.empty:
            return {"avg_total": pd.DataFrame(), "signal_counts": pd.DataFrame(), "latest_distribution": pd.DataFrame(), "top_delta": pd.DataFrame()}

        history["finished_at"] = pd.to_datetime(history["finished_at"])
        avg_total = history.groupby(["run_id", "finished_at"], as_index=False)["final_total_score"].mean()
        signal_counts = history.groupby(["run_id", "signal"], as_index=False).size()
        latest_run = int(history["run_id"].max())
        latest = history[history["run_id"] == latest_run]
        latest_distribution = score_distribution(latest)

        prev_run = self.store.get_previous_run_id(latest_run)
        top_delta = pd.DataFrame()
        if prev_run is not None:
            prev = self.store.read_signals_for_run(prev_run)
            curr = self.store.read_signals_for_run(latest_run)
            top_delta = ComparisonService.compare_runs(curr, prev)
            if not top_delta.empty:
                top_delta = top_delta.reindex(top_delta.DeltaTotal.abs().sort_values(ascending=False).index).head(10)
        return {"avg_total": avg_total, "signal_counts": signal_counts, "latest_distribution": latest_distribution, "top_delta": top_delta}

    def load_ticker_history(self, ticker: str) -> pd.DataFrame:
        return self.store.read_ticker_history(ticker)

    def list_tickers(self) -> list[str]:
        return self.store.list_tickers()

    def build_delta_against_previous(self, current_run_id: int) -> pd.DataFrame:
        prev = self.store.get_previous_run_id(current_run_id)
        if prev is None:
            return pd.DataFrame()
        return ComparisonService.compare_runs(self.store.read_signals_for_run(current_run_id), self.store.read_signals_for_run(prev))

    def build_delta_with_excel_fallback(self, current: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
        for candidate in sorted(output_dir.glob("market_checker_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True):
            delta = ComparisonService.compare_with_previous_excel(current, candidate)
            if not delta.empty:
                return delta
        return pd.DataFrame()
