from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Mapping

import pandas as pd

from market_checker_app.services.us_equity_calendar_service import last_completed_session, session_label, sessions_between


MARKET_FACTOR_VERSION = "market_factors_v3"
SUPPORTED_MARKET_FACTOR_VERSIONS = frozenset(
    {"market_factors_v2", MARKET_FACTOR_VERSION}
)
FEATURE_CONTRACT_VERSION = "pdf_feature_contract_v1"
RETURN_HORIZONS = (1, 5, 20, 60, 120, 252)

MARKET_FEATURE_CONTRACTS: dict[str, dict[str, object]] = {
    "trend.sma_20_distance": {
        "formula": "close / SMA20 - 1",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "trend.sma_50_distance": {
        "formula": "close / SMA50 - 1",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "trend.sma_200_distance": {
        "formula": "close / SMA200 - 1",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "trend.ema_20_50_spread": {
        "formula": "EMA20 / EMA50 - 1",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "price_position.high_52w_distance": {
        "formula": "close / rolling_max_252(close) - 1",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "gaps.overnight": {
        "formula": "open_t / close_t-1 - 1",
        "source_fields": ["Open", "Close"],
        "point_in_time": True,
    },
    "gaps.intraday": {
        "formula": "close_t / open_t - 1",
        "source_fields": ["Open", "Close"],
        "point_in_time": True,
    },
    "volume.volume_zscore_20d": {
        "formula": "(volume_t - mean20(volume)) / std20(volume)",
        "source_fields": ["Volume"],
        "point_in_time": True,
    },
    "liquidity.dollar_adv_20d": {
        "formula": "mean20(close * volume)",
        "source_fields": ["Close", "Volume"],
        "point_in_time": True,
    },
    "liquidity.amihud_20d": {
        "formula": "mean20(abs(return) / dollar_volume)",
        "source_fields": ["Close", "Volume"],
        "point_in_time": True,
    },
    "volatility.downside_20d_annualized": {
        "formula": "std20(negative daily returns) * sqrt(252)",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "volatility.ewma_20d_annualized": {
        "formula": "EWMA20 daily variance(lambda=0.94)^0.5 * sqrt(252)",
        "source_fields": ["Close"],
        "point_in_time": True,
    },
    "volatility.parkinson_20d_annualized": {
        "formula": "sqrt(mean20(log(high/low)^2)/(4*ln(2))*252)",
        "source_fields": ["High", "Low"],
        "point_in_time": True,
    },
    "volatility.garman_klass_20d_annualized": {
        "formula": "sqrt(mean20(0.5*log(H/L)^2-(2ln2-1)*log(C/O)^2)*252)",
        "source_fields": ["Open", "High", "Low", "Close"],
        "point_in_time": True,
    },
    "risk.atr_pct_14d": {
        "formula": "ATR14 / close",
        "source_fields": ["High", "Low", "Close"],
        "point_in_time": True,
    },
    "risk.beta_60d": {
        "formula": "cov60(asset_return, benchmark_return)/var60(benchmark_return)",
        "source_fields": ["Close", "benchmark.Close"],
        "point_in_time": True,
    },
    "risk.idiosyncratic_volatility_60d_annualized": {
        "formula": "std60(asset_return-beta*benchmark_return)*sqrt(252)",
        "source_fields": ["Close", "benchmark.Close"],
        "point_in_time": True,
    },
}
FEATURE_CONTRACT_DEFINITION_HASH = hashlib.sha256(
    json.dumps(
        MARKET_FEATURE_CONTRACTS,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


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


def _field_series(
    history: pd.DataFrame | None,
    field: str,
    as_of: datetime,
) -> pd.Series:
    """Return a session-aligned numeric field without filling missing sessions."""

    if history is None or history.empty or field not in history.columns:
        return pd.Series(dtype=float)
    completed = last_completed_session(as_of)
    if completed is None:
        return pd.Series(dtype=float)
    values: dict[pd.Timestamp, float] = {}
    for timestamp, raw_value in history[field].items():
        label = session_label(timestamp)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if (
            label is not None
            and label <= completed
            and math.isfinite(value)
            and value >= 0.0
        ):
            values[label] = value
    if not values:
        return pd.Series(dtype=float)
    allowed = set(sessions_between(min(values), min(max(values), completed)))
    values = {session: value for session, value in values.items() if session in allowed}
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


def _log_return(series: pd.Series, days: int) -> float | None:
    simple = _return(series, days)
    return math.log1p(simple) if simple is not None and simple > -1.0 else None


def _moving_average_distance(series: pd.Series, days: int) -> float | None:
    if len(series) < days or series.tail(days).isna().any():
        return None
    average = float(series.tail(days).mean())
    latest = float(series.iloc[-1])
    return (latest / average) - 1.0 if average > 0.0 else None


def _ema_spread(series: pd.Series, fast: int, slow: int) -> float | None:
    if len(series) < slow or series.tail(slow).isna().any():
        return None
    window = series.tail(slow)
    fast_value = float(window.ewm(span=fast, adjust=False).mean().iloc[-1])
    slow_value = float(window.ewm(span=slow, adjust=False).mean().iloc[-1])
    return (fast_value / slow_value) - 1.0 if slow_value > 0.0 else None


def _latest_gap(open_series: pd.Series, close: pd.Series) -> tuple[float | None, float | None]:
    if open_series.empty or close.empty or open_series.index[-1] != close.index[-1]:
        return None, None
    latest_open = open_series.iloc[-1]
    latest_close = close.iloc[-1]
    if pd.isna(latest_open) or pd.isna(latest_close) or float(latest_open) <= 0.0:
        return None, None
    intraday = (float(latest_close) / float(latest_open)) - 1.0
    if len(close) < 2 or pd.isna(close.iloc[-2]) or float(close.iloc[-2]) <= 0.0:
        return None, intraday
    overnight = (float(latest_open) / float(close.iloc[-2])) - 1.0
    return overnight, intraday


def _volume_features(close: pd.Series, volume: pd.Series) -> dict[str, float | None]:
    aligned = pd.concat({"close": close, "volume": volume}, axis=1).dropna()
    aligned = aligned[(aligned["close"] > 0.0) & (aligned["volume"] > 0.0)]
    result: dict[str, float | None] = {
        "volume_zscore_20d": None,
        "dollar_adv_20d": None,
        "dollar_adv_60d": None,
        "log_dollar_adv_20d": None,
        "amihud_20d": None,
    }
    if len(aligned) >= 20:
        window = aligned.tail(20)
        volume_std = float(window["volume"].std(ddof=1))
        if volume_std > 0.0 and math.isfinite(volume_std):
            result["volume_zscore_20d"] = (
                float(window["volume"].iloc[-1]) - float(window["volume"].mean())
            ) / volume_std
        dollar_volume = window["close"] * window["volume"]
        adv20 = float(dollar_volume.mean())
        if adv20 > 0.0 and math.isfinite(adv20):
            result["dollar_adv_20d"] = adv20
            result["log_dollar_adv_20d"] = math.log(adv20)
        returns = window["close"].pct_change(fill_method=None)
        ratios = returns.abs() / dollar_volume
        ratios = ratios.replace([math.inf, -math.inf], pd.NA).dropna()
        if not ratios.empty:
            result["amihud_20d"] = float(ratios.mean())
    if len(aligned) >= 60:
        adv60 = float((aligned.tail(60)["close"] * aligned.tail(60)["volume"]).mean())
        if adv60 > 0.0 and math.isfinite(adv60):
            result["dollar_adv_60d"] = adv60
    return result


def _downside_volatility(series: pd.Series, days: int) -> float | None:
    if len(series) <= days or series.tail(days + 1).isna().any():
        return None
    returns = series.tail(days + 1).pct_change(fill_method=None).dropna()
    negative = returns[returns < 0.0]
    if len(negative) < 2:
        return 0.0 if len(negative) == 0 else None
    value = float(negative.std(ddof=1) * math.sqrt(252.0))
    return value if math.isfinite(value) else None


def _ewma_volatility(series: pd.Series, days: int, decay: float = 0.94) -> float | None:
    if len(series) <= days or series.tail(days + 1).isna().any():
        return None
    returns = series.tail(days + 1).pct_change(fill_method=None).dropna()
    if len(returns) < days:
        return None
    weights = [(1.0 - decay) * decay ** index for index in range(days - 1, -1, -1)]
    total = sum(weights)
    variance = sum(weight * float(value) ** 2 for weight, value in zip(weights, returns)) / total
    value = math.sqrt(max(0.0, variance) * 252.0)
    return value if math.isfinite(value) else None


def _range_volatility(
    open_series: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    days: int,
) -> tuple[float | None, float | None]:
    frame = pd.concat(
        {"open": open_series, "high": high, "low": low, "close": close}, axis=1
    ).dropna()
    frame = frame[
        (frame["open"] > 0.0)
        & (frame["high"] > 0.0)
        & (frame["low"] > 0.0)
        & (frame["close"] > 0.0)
        & (frame["high"] >= frame["low"])
    ]
    if len(frame) < days:
        return None, None
    window = frame.tail(days)
    log_hl = (window["high"] / window["low"]).map(math.log)
    parkinson_variance = float((log_hl.pow(2).mean()) / (4.0 * math.log(2.0)))
    log_co = (window["close"] / window["open"]).map(math.log)
    gk_series = 0.5 * log_hl.pow(2) - (2.0 * math.log(2.0) - 1.0) * log_co.pow(2)
    gk_variance = max(0.0, float(gk_series.mean()))
    return (
        math.sqrt(max(0.0, parkinson_variance) * 252.0),
        math.sqrt(gk_variance * 252.0),
    )


def _atr_percent(high: pd.Series, low: pd.Series, close: pd.Series, days: int) -> float | None:
    frame = pd.concat({"high": high, "low": low, "close": close}, axis=1).dropna()
    if len(frame) < days + 1:
        return None
    window = frame.tail(days + 1)
    previous_close = window["close"].shift(1)
    true_range = pd.concat(
        [
            window["high"] - window["low"],
            (window["high"] - previous_close).abs(),
            (window["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1).iloc[1:]
    latest = float(window["close"].iloc[-1])
    atr = float(true_range.mean())
    return atr / latest if latest > 0.0 and math.isfinite(atr) else None


def _market_risk(asset: pd.Series, benchmark: pd.Series, days: int) -> tuple[float | None, float | None]:
    frame = pd.concat({"asset": asset, "benchmark": benchmark}, axis=1).dropna()
    if len(frame) < days + 1:
        return None, None
    returns = frame.tail(days + 1).pct_change(fill_method=None).dropna()
    if len(returns) < days:
        return None, None
    benchmark_variance = float(returns["benchmark"].var(ddof=1))
    if benchmark_variance <= 0.0 or not math.isfinite(benchmark_variance):
        return None, None
    beta = float(returns["asset"].cov(returns["benchmark"]) / benchmark_variance)
    residuals = returns["asset"] - beta * returns["benchmark"]
    idiosyncratic = float(residuals.std(ddof=1) * math.sqrt(252.0))
    return (
        beta if math.isfinite(beta) else None,
        idiosyncratic if math.isfinite(idiosyncratic) else None,
    )


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
    open_series = _field_series(asset_history, "Open", as_of)
    high = _field_series(asset_history, "High", as_of)
    low = _field_series(asset_history, "Low", as_of)
    volume = _field_series(asset_history, "Volume", as_of)
    asset_returns = {f"{days}d": _return(asset, days) for days in RETURN_HORIZONS}
    benchmark_returns = {
        f"{days}d": _return(benchmark, days) for days in RETURN_HORIZONS
    }
    relative_returns = {
        f"{days}d": _relative_return(asset, benchmark, days)
        for days in RETURN_HORIZONS
    }
    overnight_gap, intraday_return = _latest_gap(open_series, asset)
    volume_features = _volume_features(asset, volume)
    parkinson, garman_klass = _range_volatility(
        open_series, high, low, asset, 20
    )
    beta, idiosyncratic = _market_risk(asset, benchmark, 60)
    extended_values = {
        "trend.sma_20_distance": _moving_average_distance(asset, 20),
        "trend.sma_50_distance": _moving_average_distance(asset, 50),
        "trend.sma_200_distance": _moving_average_distance(asset, 200),
        "trend.ema_20_50_spread": _ema_spread(asset, 20, 50),
        "price_position.high_52w_distance": _drawdown(asset, 252),
        "gaps.overnight": overnight_gap,
        "gaps.intraday": intraday_return,
        "volume.volume_zscore_20d": volume_features["volume_zscore_20d"],
        "liquidity.dollar_adv_20d": volume_features["dollar_adv_20d"],
        "liquidity.amihud_20d": volume_features["amihud_20d"],
        "volatility.downside_20d_annualized": _downside_volatility(asset, 20),
        "volatility.ewma_20d_annualized": _ewma_volatility(asset, 20),
        "volatility.parkinson_20d_annualized": parkinson,
        "volatility.garman_klass_20d_annualized": garman_klass,
        "risk.atr_pct_14d": _atr_percent(high, low, asset, 14),
        "risk.beta_60d": beta,
        "risk.idiosyncratic_volatility_60d_annualized": idiosyncratic,
    }
    missing = {
        "asset_history": not bool(len(asset)),
        "benchmark_history": not bool(len(benchmark)),
        "relative_returns": not any(value is not None for value in relative_returns.values()),
        "realized_volatility_20d": _volatility(asset, 20) is None,
        "realized_volatility_60d": _volatility(asset, 60) is None,
        "drawdown_252d": _drawdown(asset, 252) is None,
        "ohlc_extended": not any(
            extended_values[path] is not None
            for path in extended_values
            if path.startswith(("trend.", "price_position.", "gaps."))
        ),
        "volume_liquidity": not any(
            extended_values[path] is not None
            for path in extended_values
            if path.startswith(("volume.", "liquidity."))
        ),
        "range_volatility": parkinson is None or garman_klass is None,
        "market_risk": beta is None or idiosyncratic is None,
    }
    return {
        "version": MARKET_FACTOR_VERSION,
        "as_of": as_of.isoformat(),
        "asset_observations": int(len(asset)),
        "benchmark_observations": int(len(benchmark)),
        "asset_returns": asset_returns,
        "asset_log_returns": {
            f"{days}d": _log_return(asset, days) for days in RETURN_HORIZONS
        },
        "benchmark_returns": benchmark_returns,
        "relative_returns": relative_returns,
        "realized_volatility": {
            "20d_annualized": _volatility(asset, 20),
            "60d_annualized": _volatility(asset, 60),
        },
        "drawdown": {"252d": _drawdown(asset, 252)},
        "trend": {
            "sma_20_distance": extended_values["trend.sma_20_distance"],
            "sma_50_distance": extended_values["trend.sma_50_distance"],
            "sma_200_distance": extended_values["trend.sma_200_distance"],
            "ema_20_50_spread": extended_values["trend.ema_20_50_spread"],
        },
        "price_position": {
            "high_52w_distance": extended_values[
                "price_position.high_52w_distance"
            ],
        },
        "gaps": {
            "overnight": overnight_gap,
            "intraday": intraday_return,
        },
        "volume": {
            "volume_zscore_20d": volume_features["volume_zscore_20d"],
        },
        "liquidity": {
            "dollar_adv_20d": volume_features["dollar_adv_20d"],
            "dollar_adv_60d": volume_features["dollar_adv_60d"],
            "log_dollar_adv_20d": volume_features["log_dollar_adv_20d"],
            "amihud_20d": volume_features["amihud_20d"],
        },
        "volatility": {
            "downside_20d_annualized": extended_values[
                "volatility.downside_20d_annualized"
            ],
            "ewma_20d_annualized": extended_values[
                "volatility.ewma_20d_annualized"
            ],
            "parkinson_20d_annualized": parkinson,
            "garman_klass_20d_annualized": garman_klass,
        },
        "risk": {
            "atr_pct_14d": extended_values["risk.atr_pct_14d"],
            "beta_60d": beta,
            "idiosyncratic_volatility_60d_annualized": idiosyncratic,
        },
        "feature_contract": {
            "version": FEATURE_CONTRACT_VERSION,
            "observed_at": as_of.isoformat(),
            "available_at": as_of.isoformat(),
            "missing_policy": "explicit_null_no_forward_fill",
            "definition_hash": FEATURE_CONTRACT_DEFINITION_HASH,
            "feature_names": sorted(MARKET_FEATURE_CONTRACTS),
            "implemented_count": len(MARKET_FEATURE_CONTRACTS),
            "available_count": sum(
                value is not None for value in extended_values.values()
            ),
        },
        "provenance": {
            "asset_source": asset_source,
            "benchmark_source": benchmark_source or "MISSING",
            "point_in_time": True,
        },
        "missingness": missing,
    }
