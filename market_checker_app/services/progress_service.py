from __future__ import annotations

from collections import deque
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from market_checker_app.models import AnalysisLogEvent, AnalysisProgressState


class ProgressService:
    def __init__(
        self,
        total_symbols: int,
        max_logs: int = 25,
        on_update: Callable[[AnalysisProgressState], None] | None = None,
    ) -> None:
        self._state = AnalysisProgressState(total_symbols=total_symbols)
        self._events: deque[AnalysisLogEvent] = deque(maxlen=max_logs)
        self._on_update = on_update

    def snapshot(self) -> AnalysisProgressState:
        self._state.recent_logs = [asdict(event) for event in self._events]
        return self._state

    def _emit(self) -> None:
        if self._on_update:
            self._on_update(self.snapshot())

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def log(self, event_type: str, message: str, ticker: str = "") -> None:
        event = AnalysisLogEvent(timestamp=self._now(), ticker=ticker, event_type=event_type, message=message)
        self._events.appendleft(event)
        prefixed = f"[{ticker}] {message}" if ticker else message
        if event_type == "WARNING":
            self._state.warnings.append(prefixed)
        elif event_type == "FALLBACK":
            self._state.fallbacks.append(prefixed)
        elif event_type == "ERROR":
            self._state.errors.append(prefixed)
        self._emit()

    def set_current(self, ticker: str, position: int, step: str, message: str) -> None:
        self._state.current_symbol = ticker
        self._state.current_position = position
        self._state.current_step = step
        self._state.current_message = message
        self._state.processed_symbols = max(self._state.processed_symbols, position - 1)
        if self._state.total_symbols > 0:
            done_before_current = max(0, position - 1)
            self._state.overall_progress = min(0.99, done_before_current / self._state.total_symbols)
        self._emit()

    def set_step(self, ticker: str, step: str, message: str, ticker_progress: float | None = None) -> None:
        self._state.current_symbol = ticker
        self._state.current_step = step
        self._state.current_message = message
        if ticker_progress is not None:
            self._state.ticker_progress = max(0.0, min(1.0, ticker_progress))
        self._emit()

    def add_completed_row(self, row: dict[str, object]) -> None:
        self._state.completed_rows.append(row)
        self._state.processed_symbols += 1
        self._state.ticker_progress = 1.0
        if self._state.total_symbols > 0:
            self._state.overall_progress = min(1.0, self._state.processed_symbols / self._state.total_symbols)
        self._emit()

    def set_global_step(self, step: str, message: str, progress: float) -> None:
        self._state.current_step = step
        self._state.current_message = message
        self._state.ticker_progress = 0.0
        self._state.overall_progress = max(self._state.overall_progress, min(1.0, progress))
        self._emit()

    def finalize(self, message: str) -> None:
        self._state.current_step = "done"
        self._state.current_message = message
        self._state.ticker_progress = 1.0
        self._state.overall_progress = 1.0
        self._emit()
