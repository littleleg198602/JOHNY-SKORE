"""Cost-aware, analysis-only weekly portfolio simulation for OOS samples."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import math
from statistics import mean, stdev


PORTFOLIO_BACKTEST_VERSION = "weekly_top_n_cost_backtest_v1"


def _safe_mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _strategy_metrics(
    samples: Sequence[Mapping[str, object]],
    *,
    probability_key: str,
    top_fraction: float,
    max_positions: int,
    minimum_probability: float,
    round_trip_cost_bps: float,
) -> dict[str, object]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for sample in samples:
        grouped[str(sample.get("week") or sample.get("as_of") or "UNKNOWN")].append(sample)

    weekly_rows: list[dict[str, object]] = []
    previous_tickers: set[str] = set()
    turnover_values: list[float] = []
    for week in sorted(grouped):
        candidates = [
            row
            for row in grouped[week]
            if row.get(probability_key) is not None
            and float(row[probability_key]) >= minimum_probability
        ]
        candidates.sort(
            key=lambda row: (-float(row[probability_key]), str(row.get("ticker") or ""))
        )
        desired = max(1, math.ceil(len(grouped[week]) * top_fraction))
        selected = candidates[: min(max_positions, desired)]
        tickers = {str(row.get("ticker") or "") for row in selected}
        turnover = (
            0.0
            if not previous_tickers and not tickers
            else 1.0
            if not previous_tickers or not tickers
            else 1.0
            - len(previous_tickers & tickers)
            / max(len(previous_tickers), len(tickers))
        )
        if previous_tickers or tickers:
            turnover_values.append(turnover)
        previous_tickers = tickers
        gross = _safe_mean([float(row["target_value"]) for row in selected])
        cost = round_trip_cost_bps / 10_000.0 if selected else 0.0
        net = gross - cost if gross is not None else 0.0
        weekly_rows.append(
            {
                "week": week,
                "position_count": len(selected),
                "tickers": sorted(tickers),
                "gross_excess_return": gross,
                "estimated_cost": cost,
                "net_excess_return": net,
                "turnover": turnover,
            }
        )

    returns = [float(row["net_excess_return"]) for row in weekly_rows]
    gross_returns = [
        float(row["gross_excess_return"])
        for row in weekly_rows
        if row["gross_excess_return"] is not None
    ]
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        if peak > 0.0:
            max_drawdown = min(max_drawdown, equity / peak - 1.0)
    annualized_return = (
        equity ** (52.0 / len(returns)) - 1.0
        if returns and equity > 0.0
        else None
    )
    weekly_std = stdev(returns) if len(returns) >= 2 else None
    volatility = weekly_std * math.sqrt(52.0) if weekly_std is not None else None
    sharpe = (
        mean(returns) / weekly_std * math.sqrt(52.0)
        if weekly_std is not None and weekly_std > 1e-12
        else None
    )
    downside = [min(0.0, value) for value in returns]
    downside_deviation = (
        math.sqrt(mean([value * value for value in downside])) * math.sqrt(52.0)
        if downside
        else 0.0
    )
    sortino = (
        mean(returns) * 52.0 / downside_deviation
        if downside_deviation > 1e-12
        else None
    )
    calmar = (
        annualized_return / abs(max_drawdown)
        if annualized_return is not None and max_drawdown < -1e-12
        else None
    )
    active = [row for row in weekly_rows if int(row["position_count"]) > 0]
    return {
        "week_count": len(weekly_rows),
        "active_week_count": len(active),
        "position_observation_count": sum(
            int(row["position_count"]) for row in weekly_rows
        ),
        "mean_weekly_gross_excess_return": _safe_mean(gross_returns),
        "mean_weekly_net_excess_return": _safe_mean(returns),
        "cumulative_net_excess_return": equity - 1.0,
        "annualized_net_excess_return": annualized_return,
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "maximum_drawdown": max_drawdown,
        "positive_week_ratio": (
            mean([float(row["net_excess_return"]) > 0.0 for row in active])
            if active
            else None
        ),
        "mean_turnover": _safe_mean(turnover_values),
        "weekly": weekly_rows,
    }


def build_cost_aware_portfolio_backtest(
    samples: Sequence[Mapping[str, object]],
    *,
    top_fraction: float = 0.10,
    max_positions: int = 20,
    minimum_probability: float = 0.50,
    round_trip_cost_bps: float = 20.0,
) -> dict[str, object]:
    if not 0.0 < top_fraction <= 1.0:
        raise ValueError("top_fraction must be in (0, 1]")
    if max_positions < 1:
        raise ValueError("max_positions must be positive")
    if not 0.0 <= minimum_probability <= 1.0:
        raise ValueError("minimum_probability must be between 0 and 1")
    if round_trip_cost_bps < 0.0:
        raise ValueError("round_trip_cost_bps must not be negative")

    scenarios: dict[str, object] = {}
    for label, multiplier in (("0.5x", 0.5), ("1.0x", 1.0), ("2.0x", 2.0)):
        cost = round_trip_cost_bps * multiplier
        scenarios[label] = {
            "round_trip_cost_bps": cost,
            "momentum_baseline": _strategy_metrics(
                samples,
                probability_key="momentum_probability_up",
                top_fraction=top_fraction,
                max_positions=max_positions,
                minimum_probability=minimum_probability,
                round_trip_cost_bps=cost,
            ),
            "candidate": _strategy_metrics(
                samples,
                probability_key="candidate_probability_up",
                top_fraction=top_fraction,
                max_positions=max_positions,
                minimum_probability=minimum_probability,
                round_trip_cost_bps=cost,
            ),
        }
    return {
        "version": PORTFOLIO_BACKTEST_VERSION,
        "status": "EVALUATED" if samples else "INSUFFICIENT_DATA",
        "analysis_only": True,
        "automated_trading": False,
        "selection": {
            "frequency": "weekly",
            "top_fraction": top_fraction,
            "max_positions": max_positions,
            "minimum_probability": minimum_probability,
        },
        "base_round_trip_cost_bps": round_trip_cost_bps,
        "scenarios": scenarios,
    }


__all__ = [
    "PORTFOLIO_BACKTEST_VERSION",
    "build_cost_aware_portfolio_backtest",
]
