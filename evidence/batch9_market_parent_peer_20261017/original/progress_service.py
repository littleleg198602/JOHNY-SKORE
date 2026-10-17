from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import time
from typing import Callable

from market_checker_app.models import AnalysisLogEvent, AnalysisProgressState


class ProgressService:
    def __init__(
        self,
        total_symbols: int,
        max_logs: int = 25,
        on_update: Callable[[AnalysisProgressState], None] | None = None,
        symbol_start_progress: float = 0.08,
        symbol_end_progress: float = 0.96,
        emit_interval_seconds: float = 0.0,
    ) -> None:
        self._state = AnalysisProgressState(total_symbols=total_symbols)
        self._events: deque[AnalysisLogEvent] = deque(maxlen=max_logs)
        self._on_update = on_update
        self._symbol_start_progress = max(0.0, min(1.0, symbol_start_progress))
        self._symbol_end_progress = max(self._symbol_start_progress, min(1.0, symbol_end_progress))
        self._emit_interval_seconds = max(0.0, emit_interval_seconds)
        self._last_emit_at: float | None = None
        self._flat_completed_rows: list[dict[str, object]] = []

    def snapshot(self) -> AnalysisProgressState:
        self._state.recent_logs = [asdict(event) for event in self._events]
        # Callbacks and Streamlit session state must receive an immutable-in-
        # practice point-in-time view. Returning the live object made all
        # previously collected progress samples change together.
        # Owned rows containing only built-in immutable values can be copied
        # by dict.copy instead of recursively dispatching every scalar for
        # every update. Unknown/nested values still use normal deepcopy.
        memo = {id(row): row.copy() for row in self._flat_completed_rows}
        return deepcopy(self._state, memo)

    def _emit(self, *, force: bool = False) -> None:
        if self._on_update is None:
            return
        now = time.monotonic()
        if (
            not force
            and self._last_emit_at is not None
            and now - self._last_emit_at < self._emit_interval_seconds
        ):
            return
        # Throttle before copying the growing row history. Skipped updates
        # still change the internal state; every delivered callback and an
        # explicit snapshot retain independent deep-copied data.
        self._last_emit_at = now
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
            fraction = done_before_current / self._state.total_symbols
            span = self._symbol_end_progress - self._symbol_start_progress
            self._state.overall_progress = max(
                self._state.overall_progress,
                self._symbol_start_progress + span * fraction,
            )
        self._emit()

    def set_step(self, ticker: str, step: str, message: str, ticker_progress: float | None = None) -> None:
        self._state.current_symbol = ticker
        self._state.current_step = step
        self._state.current_message = message
        if ticker_progress is not None:
            self._state.ticker_progress = max(0.0, min(1.0, ticker_progress))
        self._emit()

    def add_completed_row(self, row: dict[str, object]) -> None:
        owned_row = deepcopy(row)
        self._state.completed_rows.append(owned_row)
        immutable_types = (str, int, float, bool, bytes, type(None))
        if type(owned_row) is dict and all(
            type(key) in immutable_types and type(value) in immutable_types
            for key, value in owned_row.items()
        ):
            self._flat_completed_rows.append(owned_row)
        self._state.processed_symbols += 1
        self._state.ticker_progress = 1.0
        if self._state.total_symbols > 0:
            fraction = self._state.processed_symbols / self._state.total_symbols
            span = self._symbol_end_progress - self._symbol_start_progress
            self._state.overall_progress = max(
                self._state.overall_progress,
                min(self._symbol_end_progress, self._symbol_start_progress + span * fraction),
            )
        self._emit()

    def set_global_step(self, step: str, message: str, progress: float) -> None:
        self._state.current_step = step
        self._state.current_message = message
        self._state.current_symbol = ""
        self._state.current_position = 0
        self._state.ticker_progress = 0.0
        self._state.overall_progress = max(self._state.overall_progress, min(1.0, progress))
        self._emit()

    def finalize(self, message: str) -> None:
        self._state.current_step = "done"
        self._state.current_message = message
        self._state.ticker_progress = 1.0
        self._state.overall_progress = 1.0
        self._emit(force=True)
