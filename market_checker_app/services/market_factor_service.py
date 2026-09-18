from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Mapping

import pandas as pd

from market_checker_app.services.us_equity_calendar_service import last_completed_session, session_label, sessions_between


MARKET_FACTOR_VERSION = "market_factors_v2"
RETURN_HORIZONS = (1, 5, 20, 60, 120, 252)


def _close_series(history: pd.DataFrame | None, as_of: datetime) -> pd.Series:
    if history is None or history.empty or "Close" not in history.columns:
        return pd.Series(dtype=float)
    completed = last_completed_session(as_of)
    if completed is None:
        return pd.Series(dtype=float)
    values: dict[pd.Timestamp, float] = {}
    for timestamp, raw_close in history["Close"].items():
        label = session_label(timestamp)
        try:
            close = float(raw_close)
        except (TypeError, ValueError):
            continue
        if label is not None and label <= completed and close > 0.0 and math.isfinite(close):
            values[label] = close
    if values:
        allowed = set(sessions_between(min(values), min(max(values), completed)))
        values = {session: close for session, close in values.items() if session in allowed}
    if completed not in values:
        return pd.Series(dtype=float)
    # Reindex instead of dropping gaps: an absent session is not a shorter
    # horizon. Individual lookbacks below remain usable when complete.
    return pd.Series(values, dtype=float).reindex(sessions_between(min(values), completed))


def _return(series: pd.Series, days: int) -> float | None:
    if len(series) <= days or series.tail(days + 1).isna().any():
        return None
    base = float(series.iloc[-(days + 1)])
    latest = float(series.iloc[-1])
    return (latest / base) - 1.0 if base > 0.0 else None


def _relative_return(asset: pd.Series, benchmark: pd.Series, days: int) -> float | None:
    if asset.empty or benchmark.empty or len(asset) <= days or len(benchmark) <= days:
        return None
    if asset.index[-1] != benchmark.index[-1]:
        return None
    sessions = sessions_between(asset.index[0], asset.index[-1])
    if len(sessions) < days + 1:
        return None
    required = sessions[-(days + 1):]
    if any(session not in asset.index or session not in benchmark.index
           or pd.isna(asset.loc[session]) or pd.isna(benchmark.loc[session]) for session in required):
        return None
    asset_return = (float(asset.loc[required[-1]]) / float(asset.loc[required[0]])) - 1.0
    benchmark_return = (float(benchmark.loc[required[-1]]) / float(benchmark.loc[required[0]])) - 1.0
    return asset_return - benchmark_return

def _volatility(series: pd.Series, days: int) -> float | None:
    if len(series) <= days or series.tail(days + 1).isna().any():
        return None
    returns = series.tail(days + 1).pct_change(fill_method=None).dropna()
    if len(returns) < days:
        return None
    value = float(returns.std(ddof=1) * math.sqrt(252.0))
    return value if math.isfinite(value) else None


def _drawdown(series: pd.Series, days: int) -> float | None:
    if len(series) < days or series.tail(days).isna().any():
        return None
    window = series.tail(days)
    peak = float(window.max())
    latest = float(window.iloc[-1])
    return (latest / peak) - 1.0 if peak > 0.0 else None


def build_market_factor_snapshot(
    *,
    asset_history: pd.DataFrame | None,
    benchmark_history: pd.DataFrame | None,
    as_of: datetime,
    asset_source: str,
    benchmark_source: str | None,
) -> dict[str, object]:
    """Build point-in-time price factors; missing observations remain explicit.

    The function is deliberately score-free.  It only records reproducible
    features for later ablation against the frozen baseline.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    asset = _close_series(asset_history, as_of)
    benchmark = _close_series(benchmark_history, as_of)
    asset_returns = {f"{days}d": _return(asset, days) for days in RETURN_HORIZONS}
    benchmark_returns = {
        f"{days}d": _return(benchmark, days) for days in RETURN_HORIZONS
    }
    relative_returns = {
        f"{days}d": _relative_return(asset, benchmark, days)
        for days in RETURN_HORIZONS
    }
    missing = {
        "asset_history": not bool(len(asset)),
        "benchmark_history": not bool(len(benchmark)),
        "relative_returns": not any(value is not None for value in relative_returns.values()),
        "realized_volatility_20d": _volatility(asset, 20) is None,
        "realized_volatility_60d": _volatility(asset, 60) is None,
        "drawdown_252d": _drawdown(asset, 252) is None,
    }
    return {
        "version": MARKET_FACTOR_VERSION,
        "as_of": as_of.isoformat(),
        "asset_observations": int(len(asset)),
        "benchmark_observations": int(len(benchmark)),
        "asset_returns": asset_returns,
        "benchmark_returns": benchmark_returns,
        "relative_returns": relative_returns,
        "realized_volatility": {
            "20d_annualized": _volatility(asset, 20),
            "60d_annualized": _volatility(asset, 60),
        },
        "drawdown": {"252d": _drawdown(asset, 252)},
        "provenance": {
            "asset_source": asset_source,
            "benchmark_source": benchmark_source or "MISSING",
            "point_in_time": True,
        },
        "missingness": missing,
    }
