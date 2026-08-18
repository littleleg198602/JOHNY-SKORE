from __future__ import annotations

import pandas as pd


class EvaluationService:
    PREDICTION_FRAME_NAMES = (
        "prediction_overall",
        "prediction_summary",
        "forecast_summary",
        "prediction_weekly",
        "prediction_cumulative",
        "forecast_weekly",
        "forecast_cumulative",
        "prediction_by_version",
        "prediction_by_ticker",
        "prediction_details",
        "pending_predictions",
    )

    @staticmethod
    def _empty_prediction_frames() -> dict[str, pd.DataFrame]:
        return {name: pd.DataFrame() for name in EvaluationService.PREDICTION_FRAME_NAMES}

    @staticmethod
    def _normalize_action(signal: object) -> str:
        normalized = str(signal or "").strip().upper().replace("_", " ")
        if normalized in {"BUY", "STRONG BUY"}:
            return "BUY"
        if normalized in {"SELL", "STRONG SELL"}:
            return "SELL"
        if normalized in {"HOLD", "NO TRADE", "FLAT"}:
            return "NO_TRADE"
        return "UNKNOWN"

    @staticmethod
    def _normalize_forecast(value: object) -> str:
        normalized = str(value or "").strip().upper().replace("_", " ")
        if normalized in {"UP", "BUY", "STRONG BUY"}:
            return "UP"
        if normalized in {"DOWN", "SELL", "STRONG SELL"}:
            return "DOWN"
        if normalized in {"FLAT", "HOLD"}:
            return "FLAT"
        return "UNKNOWN"

    @staticmethod
    def _split_adjusted_observation(
        signal_price: float,
        evaluation_price: float,
    ) -> tuple[float, float, float, str]:
        """Return adjusted evaluation price, return, multiplier and audit note.

        Only ratios very close to common split factors are corrected, and the
        corrected weekly move must be plausible.  This catches obvious events
        such as a 2:1 split without treating an arbitrary large move as a split.
        """

        if signal_price <= 0 or evaluation_price <= 0:
            return evaluation_price, float("nan"), 1.0, ""
        ratio = evaluation_price / signal_price
        raw_return = (ratio - 1) * 100
        if abs(raw_return) < 35:
            return evaluation_price, raw_return, 1.0, ""

        candidates: list[tuple[float, float, str]] = []
        for factor in (1.25, 4 / 3, 1.5, 2.0, 3.0, 4.0, 5.0, 10.0):
            forward_target = 1 / factor
            forward_tolerance = max(0.012, forward_target * 0.04)
            if abs(ratio - forward_target) <= forward_tolerance:
                multiplier = factor
                corrected_return = (ratio * multiplier - 1) * 100
                if abs(corrected_return) <= 20:
                    candidates.append(
                        (abs(corrected_return), multiplier, f"probable_forward_split_{factor:g}:1")
                    )

            reverse_tolerance = max(0.04, factor * 0.04)
            if abs(ratio - factor) <= reverse_tolerance:
                multiplier = 1 / factor
                corrected_return = (ratio * multiplier - 1) * 100
                if abs(corrected_return) <= 20:
                    candidates.append(
                        (abs(corrected_return), multiplier, f"probable_reverse_split_1:{factor:g}")
                    )

        if not candidates:
            return evaluation_price, raw_return, 1.0, ""
        _, multiplier, note = min(candidates, key=lambda item: item[0])
        adjusted_price = evaluation_price * multiplier
        return adjusted_price, ((adjusted_price / signal_price) - 1) * 100, multiplier, note

    def evaluate_predictions(
        self,
        history: pd.DataFrame,
        *,
        hold_tolerance_pct: float = 2.0,
        minimum_weekly_gap_days: float = 4.0,
        maximum_weekly_gap_days: float = 10.0,
        evaluation_timezone: str = "UTC",
    ) -> dict[str, pd.DataFrame]:
        """Evaluate one saved weekly forecast/action against the next weekly price.

        The last snapshot in each local Monday-Sunday week is used, so repeated
        reruns cannot become fake forward observations.  BUY/SELL action hit
        rate is reported separately from UP/DOWN/FLAT forecast accuracy;
        NO_TRADE is explicit abstention and never counts as HIT or MISS.
        """

        if hold_tolerance_pct < 0:
            raise ValueError("hold_tolerance_pct must not be negative")
        if minimum_weekly_gap_days < 0 or maximum_weekly_gap_days < minimum_weekly_gap_days:
            raise ValueError("weekly gap bounds are invalid")

        required = {"run_id", "finished_at", "ticker", "current_price", "signal"}
        if history.empty or not required.issubset(history.columns):
            return self._empty_prediction_frames()

        hist = history.copy()
        hist["finished_at"] = pd.to_datetime(hist["finished_at"], utc=True, errors="coerce")
        hist["current_price"] = pd.to_numeric(hist["current_price"], errors="coerce")
        if "current_price_source" not in hist.columns:
            hist["current_price_source"] = "unknown"
        else:
            hist["current_price_source"] = hist["current_price_source"].fillna("unknown")
        hist["run_id"] = pd.to_numeric(hist["run_id"], errors="coerce")
        hist["ticker"] = hist["ticker"].astype(str).str.strip().str.upper()
        if "decision_signal" not in hist.columns:
            hist["decision_signal"] = hist["signal"]
        else:
            hist["decision_signal"] = hist["decision_signal"].fillna(hist["signal"])

        fallback_action = hist["signal"].map(self._normalize_action)
        if "action" in hist.columns:
            explicit_action = hist["action"].map(self._normalize_action)
            hist["normalized_action"] = explicit_action.where(
                explicit_action != "UNKNOWN", fallback_action
            )
        else:
            hist["normalized_action"] = fallback_action

        fallback_forecast = hist["decision_signal"].map(self._normalize_forecast)
        if "forecast" in hist.columns:
            explicit_forecast = hist["forecast"].map(self._normalize_forecast)
            hist["normalized_forecast"] = explicit_forecast.where(
                explicit_forecast != "UNKNOWN", fallback_forecast
            )
        else:
            hist["normalized_forecast"] = fallback_forecast
        if "action_reasons" not in hist.columns:
            hist["action_reasons"] = ""
        if "scoring_version" not in hist.columns:
            hist["scoring_version"] = "legacy_unversioned"
        else:
            hist["scoring_version"] = hist["scoring_version"].fillna("legacy_unversioned")
        hist = hist.dropna(subset=["finished_at", "run_id"])
        hist = hist[hist["ticker"] != ""]
        if hist.empty:
            return self._empty_prediction_frames()

        local_time = hist["finished_at"].dt.tz_convert(evaluation_timezone)
        hist["week_start"] = (
            local_time.dt.normalize()
            - pd.to_timedelta(local_time.dt.weekday, unit="D")
        ).dt.date
        hist = hist.sort_values(["ticker", "finished_at", "run_id"])
        same_week_rows_ignored = int(
            len(hist) - len(hist.drop_duplicates(["ticker", "week_start"], keep="last"))
        )
        weekly = hist.drop_duplicates(["ticker", "week_start"], keep="last").copy()
        weekly = weekly.sort_values(["ticker", "finished_at", "run_id"])

        grouped = weekly.groupby("ticker", sort=False)
        weekly["evaluation_run_id"] = grouped["run_id"].shift(-1)
        weekly["evaluated_at"] = grouped["finished_at"].shift(-1)
        weekly["evaluation_price"] = grouped["current_price"].shift(-1)
        weekly["evaluation_price_source"] = grouped["current_price_source"].shift(-1)
        weekly["holding_days"] = (
            weekly["evaluated_at"] - weekly["finished_at"]
        ).dt.total_seconds() / 86_400.0
        observations = [
            self._split_adjusted_observation(float(start), float(end))
            if pd.notna(start) and pd.notna(end)
            else (end, float("nan"), 1.0, "")
            for start, end in zip(weekly["current_price"], weekly["evaluation_price"])
        ]
        weekly["evaluation_price_adjusted"] = [item[0] for item in observations]
        weekly["realized_return_pct"] = [item[1] for item in observations]
        weekly["split_adjustment_multiplier"] = [item[2] for item in observations]
        weekly["corporate_action_note"] = [item[3] for item in observations]
        weekly["action"] = weekly["normalized_action"]
        weekly["forecast"] = weekly["normalized_forecast"]
        weekly["prediction"] = weekly["action"]

        has_next = weekly["evaluation_run_id"].notna()
        valid_prices = (
            weekly["current_price"].notna()
            & weekly["evaluation_price_adjusted"].notna()
            & (weekly["current_price"] > 0)
            & (weekly["evaluation_price_adjusted"] > 0)
        )
        valid_gap = weekly["holding_days"].between(
            minimum_weekly_gap_days,
            maximum_weekly_gap_days,
            inclusive="both",
        )
        known_action = weekly["action"] != "UNKNOWN"
        known_forecast = weekly["forecast"] != "UNKNOWN"

        weekly["result"] = "PENDING"
        weekly.loc[has_next & ~valid_prices, "result"] = "NO_PRICE"
        weekly.loc[has_next & valid_prices & ~valid_gap, "result"] = "IRREGULAR_GAP"
        weekly.loc[has_next & valid_prices & valid_gap & ~known_action, "result"] = "UNKNOWN_ACTION"

        comparable_action = has_next & valid_prices & valid_gap & known_action
        directional_action = weekly["action"].isin(["BUY", "SELL"])
        buy_hit = (weekly["action"] == "BUY") & (weekly["realized_return_pct"] > 0)
        sell_hit = (weekly["action"] == "SELL") & (weekly["realized_return_pct"] < 0)
        weekly.loc[comparable_action & (weekly["action"] == "NO_TRADE"), "result"] = "NO_TRADE"
        weekly.loc[comparable_action & directional_action, "result"] = "MISS"
        weekly.loc[comparable_action & (buy_hit | sell_hit), "result"] = "HIT"

        weekly["signed_return_pct"] = pd.NA
        weekly.loc[weekly["action"] == "BUY", "signed_return_pct"] = weekly.loc[
            weekly["action"] == "BUY", "realized_return_pct"
        ]
        weekly.loc[weekly["action"] == "SELL", "signed_return_pct"] = -weekly.loc[
            weekly["action"] == "SELL", "realized_return_pct"
        ]

        weekly["actual_move"] = ""
        weekly.loc[
            valid_prices & (weekly["realized_return_pct"].abs() <= hold_tolerance_pct),
            "actual_move",
        ] = "FLAT"
        weekly.loc[
            valid_prices & (weekly["realized_return_pct"] > hold_tolerance_pct),
            "actual_move",
        ] = "UP"
        weekly.loc[
            valid_prices & (weekly["realized_return_pct"] < -hold_tolerance_pct),
            "actual_move",
        ] = "DOWN"

        weekly["forecast_result"] = "PENDING"
        weekly.loc[has_next & ~valid_prices, "forecast_result"] = "NO_PRICE"
        weekly.loc[has_next & valid_prices & ~valid_gap, "forecast_result"] = "IRREGULAR_GAP"
        weekly.loc[
            has_next & valid_prices & valid_gap & ~known_forecast,
            "forecast_result",
        ] = "UNKNOWN_FORECAST"
        comparable_forecast = has_next & valid_prices & valid_gap & known_forecast
        weekly.loc[comparable_forecast, "forecast_result"] = "FORECAST_MISS"
        weekly.loc[
            comparable_forecast & (weekly["forecast"] == weekly["actual_move"]),
            "forecast_result",
        ] = "FORECAST_HIT"

        detail_source = weekly.rename(
            columns={
                "run_id": "signal_run_id",
                "finished_at": "signal_at",
                "current_price": "signal_price",
                "current_price_source": "signal_price_source",
            }
        )
        detail_columns = [
            "signal_run_id",
            "signal_at",
            "week_start",
            "ticker",
            "scoring_version",
            "decision_signal",
            "forecast",
            "action",
            "action_reasons",
            "signal",
            "prediction",
            "signal_price",
            "signal_price_source",
            "evaluation_run_id",
            "evaluated_at",
            "evaluation_price",
            "evaluation_price_adjusted",
            "evaluation_price_source",
            "split_adjustment_multiplier",
            "corporate_action_note",
            "holding_days",
            "realized_return_pct",
            "signed_return_pct",
            "actual_move",
            "result",
            "forecast_result",
        ]
        details = detail_source[
            [column for column in detail_columns if column in detail_source.columns]
        ].sort_values(["signal_at", "ticker"], ascending=[False, True])
        for column in (
            "signal_price",
            "evaluation_price",
            "evaluation_price_adjusted",
            "holding_days",
            "realized_return_pct",
            "signed_return_pct",
            "split_adjustment_multiplier",
        ):
            details[column] = pd.to_numeric(details[column], errors="coerce").round(4)

        scored = details[details["result"].isin(["HIT", "MISS"])].copy()
        eligible_actions = details[details["result"].isin(["HIT", "MISS", "NO_TRADE"])].copy()
        summary_rows: list[dict[str, object]] = []
        for prediction in ("BUY", "SELL", "NO_TRADE"):
            observations_for_action = eligible_actions[
                eligible_actions["action"] == prediction
            ]
            subset = scored[scored["action"] == prediction]
            hits = int(subset["result"].eq("HIT").sum())
            evaluated = int(len(subset))
            summary_rows.append(
                {
                    "prediction": prediction,
                    "observations": int(len(observations_for_action)),
                    "evaluated": evaluated,
                    "hits": hits,
                    "misses": evaluated - hits,
                    "hit_rate_pct": round(hits / evaluated * 100, 2) if evaluated else None,
                    "avg_realized_return_pct": round(float(subset["realized_return_pct"].mean()), 4)
                    if evaluated
                    else None,
                    "median_realized_return_pct": round(float(subset["realized_return_pct"].median()), 4)
                    if evaluated
                    else None,
                    "avg_signed_return_pct": round(float(subset["signed_return_pct"].mean()), 4)
                    if evaluated
                    else None,
                    "median_signed_return_pct": round(float(subset["signed_return_pct"].median()), 4)
                    if evaluated
                    else None,
                }
            )
        summary = pd.DataFrame(summary_rows)

        forecast_scored = details[
            details["forecast_result"].isin(["FORECAST_HIT", "FORECAST_MISS"])
        ].copy()
        forecast_rows: list[dict[str, object]] = []
        for forecast in ("UP", "FLAT", "DOWN"):
            subset = forecast_scored[forecast_scored["forecast"] == forecast]
            hits = int(subset["forecast_result"].eq("FORECAST_HIT").sum())
            evaluated = int(len(subset))
            forecast_rows.append(
                {
                    "forecast": forecast,
                    "evaluated": evaluated,
                    "hits": hits,
                    "misses": evaluated - hits,
                    "accuracy_pct": round(hits / evaluated * 100, 2) if evaluated else None,
                    "avg_realized_return_pct": round(float(subset["realized_return_pct"].mean()), 4)
                    if evaluated
                    else None,
                }
            )
        forecast_summary = pd.DataFrame(forecast_rows)

        version_rows: list[dict[str, object]] = []
        for version in sorted(details["scoring_version"].astype(str).unique()):
            version_details = details[details["scoring_version"].astype(str) == version]
            version_eligible = version_details[
                version_details["result"].isin(["HIT", "MISS", "NO_TRADE"])
            ]
            version_trades = version_details[version_details["result"].isin(["HIT", "MISS"])]
            version_forecasts = version_details[
                version_details["forecast_result"].isin(["FORECAST_HIT", "FORECAST_MISS"])
            ]
            trade_count = int(len(version_trades))
            trade_hits = int(version_trades["result"].eq("HIT").sum())
            forecast_count = int(len(version_forecasts))
            forecast_hits_for_version = int(
                version_forecasts["forecast_result"].eq("FORECAST_HIT").sum()
            )
            version_rows.append(
                {
                    "scoring_version": version,
                    "eligible_observations": int(len(version_eligible)),
                    "directional_trades": trade_count,
                    "directional_hits": trade_hits,
                    "directional_hit_rate_pct": round(trade_hits / trade_count * 100, 2)
                    if trade_count
                    else None,
                    "trade_coverage_pct": round(trade_count / len(version_eligible) * 100, 2)
                    if len(version_eligible)
                    else None,
                    "avg_signed_return_pct": round(
                        float(version_trades["signed_return_pct"].mean()), 4
                    )
                    if trade_count
                    else None,
                    "evaluated_forecasts": forecast_count,
                    "forecast_hits": forecast_hits_for_version,
                    "forecast_accuracy_pct": round(
                        forecast_hits_for_version / forecast_count * 100, 2
                    )
                    if forecast_count
                    else None,
                }
            )
        prediction_by_version = pd.DataFrame(version_rows)

        weekly_columns = [
            "week_start",
            "evaluated",
            "hits",
            "misses",
            "hit_rate_pct",
        ]
        cumulative_columns = weekly_columns + [
            "cumulative_evaluated",
            "cumulative_hits",
            "cumulative_misses",
            "cumulative_hit_rate_pct",
        ]
        ticker_columns = [
            "ticker",
            "evaluated",
            "hits",
            "misses",
            "hit_rate_pct",
            "first_signal_at",
            "last_evaluated_at",
        ]
        def _accuracy_history(
            frame: pd.DataFrame,
            *,
            result_column: str,
            hit_label: str,
        ) -> tuple[pd.DataFrame, pd.DataFrame]:
            if frame.empty:
                return (
                    pd.DataFrame(columns=weekly_columns),
                    pd.DataFrame(columns=cumulative_columns),
                )
            weekly_frame = (
                frame.assign(is_hit=frame[result_column].eq(hit_label).astype(int))
                .groupby("week_start", as_index=False)
                .agg(evaluated=(result_column, "size"), hits=("is_hit", "sum"))
                .sort_values("week_start")
            )
            weekly_frame["misses"] = weekly_frame["evaluated"] - weekly_frame["hits"]
            weekly_frame["hit_rate_pct"] = (
                weekly_frame["hits"] / weekly_frame["evaluated"] * 100
            ).round(2)
            weekly_frame = weekly_frame[weekly_columns]
            cumulative_frame = weekly_frame.copy()
            cumulative_frame["cumulative_evaluated"] = cumulative_frame["evaluated"].cumsum()
            cumulative_frame["cumulative_hits"] = cumulative_frame["hits"].cumsum()
            cumulative_frame["cumulative_misses"] = cumulative_frame["misses"].cumsum()
            cumulative_frame["cumulative_hit_rate_pct"] = (
                cumulative_frame["cumulative_hits"]
                / cumulative_frame["cumulative_evaluated"]
                * 100
            ).round(2)
            return weekly_frame, cumulative_frame[cumulative_columns]

        weekly_history, cumulative_history = _accuracy_history(
            scored,
            result_column="result",
            hit_label="HIT",
        )
        forecast_weekly, forecast_cumulative = _accuracy_history(
            forecast_scored,
            result_column="forecast_result",
            hit_label="FORECAST_HIT",
        )

        if scored.empty:
            by_ticker = pd.DataFrame(columns=ticker_columns)
        else:
            by_ticker = (
                scored.assign(is_hit=scored["result"].eq("HIT").astype(int))
                .groupby("ticker", as_index=False)
                .agg(
                    evaluated=("result", "size"),
                    hits=("is_hit", "sum"),
                    first_signal_at=("signal_at", "min"),
                    last_evaluated_at=("evaluated_at", "max"),
                )
            )
            by_ticker["misses"] = by_ticker["evaluated"] - by_ticker["hits"]
            by_ticker["hit_rate_pct"] = (
                by_ticker["hits"] / by_ticker["evaluated"] * 100
            ).round(2)
            by_ticker = by_ticker[ticker_columns].sort_values(
                ["evaluated", "hit_rate_pct", "ticker"],
                ascending=[False, False, True],
            )

        total_evaluated = int(len(scored))
        total_hits = int((scored["result"] == "HIT").sum())
        forecast_evaluated = int(len(forecast_scored))
        forecast_hits = int(forecast_scored["forecast_result"].eq("FORECAST_HIT").sum())
        eligible_action_count = int(len(eligible_actions))
        overall = pd.DataFrame(
            {
                "metric": [
                    "evaluated_directional_trades",
                    "correct_directional_trades",
                    "wrong_directional_trades",
                    "directional_hit_rate_pct",
                    "trade_coverage_pct",
                    "avg_signed_return_pct",
                    "median_signed_return_pct",
                    "no_trade_predictions",
                    "evaluated_forecasts",
                    "correct_forecasts",
                    "wrong_forecasts",
                    "forecast_accuracy_pct",
                    "corporate_action_adjustments",
                    "evaluated_weekly_predictions",
                    "correct_predictions",
                    "wrong_predictions",
                    "overall_hit_rate_pct",
                    "pending_predictions",
                    "irregular_gap_predictions",
                    "no_price_predictions",
                    "same_week_rows_ignored",
                    "hold_tolerance_pct",
                ],
                "value": [
                    total_evaluated,
                    total_hits,
                    total_evaluated - total_hits,
                    round(total_hits / total_evaluated * 100, 2) if total_evaluated else None,
                    round(total_evaluated / eligible_action_count * 100, 2)
                    if eligible_action_count
                    else None,
                    round(float(scored["signed_return_pct"].mean()), 4)
                    if total_evaluated
                    else None,
                    round(float(scored["signed_return_pct"].median()), 4)
                    if total_evaluated
                    else None,
                    int((details["result"] == "NO_TRADE").sum()),
                    forecast_evaluated,
                    forecast_hits,
                    forecast_evaluated - forecast_hits,
                    round(forecast_hits / forecast_evaluated * 100, 2)
                    if forecast_evaluated
                    else None,
                    int(details["corporate_action_note"].astype(str).ne("").sum()),
                    total_evaluated,
                    total_hits,
                    total_evaluated - total_hits,
                    round(total_hits / total_evaluated * 100, 2) if total_evaluated else None,
                    int((details["result"] == "PENDING").sum()),
                    int((details["result"] == "IRREGULAR_GAP").sum()),
                    int((details["result"] == "NO_PRICE").sum()),
                    same_week_rows_ignored,
                    float(hold_tolerance_pct),
                ],
            }
        )
        pending = details[details["result"] == "PENDING"].copy()
        return {
            "prediction_overall": overall,
            "prediction_summary": summary,
            "forecast_summary": forecast_summary,
            "prediction_weekly": weekly_history,
            "prediction_cumulative": cumulative_history,
            "forecast_weekly": forecast_weekly,
            "forecast_cumulative": forecast_cumulative,
            "prediction_by_version": prediction_by_version,
            "prediction_by_ticker": by_ticker,
            "prediction_details": details,
            "pending_predictions": pending,
        }

    def evaluate_snapshots(
        self,
        history: pd.DataFrame,
        *,
        hold_tolerance_pct: float = 2.0,
    ) -> dict[str, pd.DataFrame]:
        prediction_frames = self.evaluate_predictions(
            history,
            hold_tolerance_pct=hold_tolerance_pct,
        )
        if history.empty:
            return {
                **prediction_frames,
                "score_comparison": pd.DataFrame(),
                "top_bottom_new": pd.DataFrame(),
                "top_bottom_legacy": pd.DataFrame(),
                "by_signal_new": pd.DataFrame(),
                "by_signal_legacy": pd.DataFrame(),
                "strategy_side_by_side": pd.DataFrame(),
                "signal_transition": pd.DataFrame(),
                "hit_rate_new_vs_legacy": pd.DataFrame(),
                "coverage": pd.DataFrame(),
            }

        hist = history.sort_values(["ticker", "run_id"]).copy()
        hist["score_delta_new_minus_legacy"] = hist["final_total_score"] - hist["legacy_total_score"]
        score_comparison = pd.DataFrame(
            {
                "metric": ["avg_final_total", "avg_legacy_total", "avg_delta_new_minus_legacy", "score_correlation"],
                "value": [
                    float(hist["final_total_score"].mean()),
                    float(hist["legacy_total_score"].mean()),
                    float(hist["score_delta_new_minus_legacy"].mean()),
                    float(hist[["final_total_score", "legacy_total_score"]].corr().iloc[0, 1]) if len(hist) > 1 else 1.0,
                ],
            }
        )

        coverage = pd.DataFrame(
            {
                "metric": ["rows", "scoring_versions", "mt5_rows", "yfinance_fallback_rows"],
                "value": [
                    int(len(hist)),
                    int(hist["scoring_version"].nunique(dropna=True)) if "scoring_version" in hist.columns else 0,
                    int((hist["tech_source_used"] == "mt5").sum()) if "tech_source_used" in hist.columns else 0,
                    int((hist["tech_source_used"] == "yfinance_fallback").sum()) if "tech_source_used" in hist.columns else 0,
                ],
            }
        )

        hist["next_price"] = hist.groupby("ticker")["current_price"].shift(-1)
        adjusted_forward = [
            self._split_adjusted_observation(float(start), float(end))[1]
            if pd.notna(start) and pd.notna(end)
            else float("nan")
            for start, end in zip(hist["current_price"], hist["next_price"])
        ]
        hist["next_return_pct"] = adjusted_forward
        fallback_action = hist["signal"].map(self._normalize_action)
        if "action" in hist.columns:
            explicit_action = hist["action"].map(self._normalize_action)
            hist["new_action"] = explicit_action.where(
                explicit_action != "UNKNOWN", fallback_action
            )
        else:
            hist["new_action"] = fallback_action
        if "decision_signal" in hist.columns:
            hist["new_decision_signal"] = hist["decision_signal"].fillna(hist["signal"])
        else:
            hist["new_decision_signal"] = hist["signal"]
        valid = hist.dropna(subset=["next_return_pct"]).copy()
        if valid.empty:
            return {
                **prediction_frames,
                "score_comparison": score_comparison,
                "top_bottom_new": pd.DataFrame(),
                "top_bottom_legacy": pd.DataFrame(),
                "by_signal_new": pd.DataFrame(),
                "by_signal_legacy": pd.DataFrame(),
                "strategy_side_by_side": pd.DataFrame({"note": ["Forward return nelze spočítat: chybí current_price historie."]}),
                "signal_transition": pd.DataFrame(),
                "hit_rate_new_vs_legacy": pd.DataFrame(),
                "coverage": coverage,
            }

        valid["new_decile_group"] = pd.cut(valid["percentile_in_watchlist"], bins=[0, 10, 90, 100], labels=["bottom_decile", "middle", "top_decile"], include_lowest=True)
        valid["legacy_percentile"] = valid.groupby("run_id")["legacy_total_score"].rank(pct=True, ascending=True) * 100
        valid["legacy_decile_group"] = pd.cut(valid["legacy_percentile"], bins=[0, 10, 90, 100], labels=["bottom_decile", "middle", "top_decile"], include_lowest=True)

        top_bottom_new = (
            valid[valid["new_decile_group"].isin(["top_decile", "bottom_decile"])]
            .groupby("new_decile_group", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"new_decile_group": "decile_group", "next_return_pct": "avg_next_period_return_pct"})
        )

        top_bottom_legacy = (
            valid[valid["legacy_decile_group"].isin(["top_decile", "bottom_decile"])]
            .groupby("legacy_decile_group", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"legacy_decile_group": "decile_group", "next_return_pct": "avg_next_period_return_pct"})
        )

        by_signal_new = (
            valid.groupby("new_action", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"new_action": "new_signal", "next_return_pct": "avg_next_period_return_pct"})
        )
        by_signal_legacy = (
            valid.groupby("legacy_signal", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"legacy_signal": "legacy_signal", "next_return_pct": "avg_next_period_return_pct"})
        )

        signal_transition = (
            valid.groupby(["legacy_signal", "new_decision_signal"], as_index=False)
            .size()
            .rename(columns={"new_decision_signal": "signal", "size": "count"})
            .sort_values("count", ascending=False)
        )

        def _hit(df: pd.DataFrame, signal_col: str) -> tuple[float, float]:
            buy = df[df[signal_col].isin(["BUY", "STRONG BUY"])]
            sell = df[df[signal_col].isin(["SELL", "STRONG SELL"])]
            return (
                float((buy["next_return_pct"] > 0).mean()) if not buy.empty else 0.0,
                float((sell["next_return_pct"] < 0).mean()) if not sell.empty else 0.0,
            )

        new_buy_hit, new_sell_hit = _hit(valid, "new_action")
        legacy_buy_hit, legacy_sell_hit = _hit(valid, "legacy_signal")
        hit_rate_new_vs_legacy = pd.DataFrame(
            {
                "strategy": ["new", "legacy", "new", "legacy"],
                "bucket": ["BUY+STRONG_BUY", "BUY+STRONG_BUY", "SELL+STRONG_SELL", "SELL+STRONG_SELL"],
                "hit_rate": [new_buy_hit, legacy_buy_hit, new_sell_hit, legacy_sell_hit],
            }
        )

        strategy_side_by_side = pd.DataFrame(
            {
                "metric": [
                    "top_decile_avg_return_pct",
                    "bottom_decile_avg_return_pct",
                    "buy_hit_rate",
                    "sell_hit_rate",
                ],
                "new": [
                    float(top_bottom_new[top_bottom_new["decile_group"] == "top_decile"]["avg_next_period_return_pct"].mean()),
                    float(top_bottom_new[top_bottom_new["decile_group"] == "bottom_decile"]["avg_next_period_return_pct"].mean()),
                    new_buy_hit,
                    new_sell_hit,
                ],
                "legacy": [
                    float(top_bottom_legacy[top_bottom_legacy["decile_group"] == "top_decile"]["avg_next_period_return_pct"].mean()),
                    float(top_bottom_legacy[top_bottom_legacy["decile_group"] == "bottom_decile"]["avg_next_period_return_pct"].mean()),
                    legacy_buy_hit,
                    legacy_sell_hit,
                ],
            }
        )

        return {
            **prediction_frames,
            "score_comparison": score_comparison,
            "top_bottom_new": top_bottom_new,
            "top_bottom_legacy": top_bottom_legacy,
            "by_signal_new": by_signal_new,
            "by_signal_legacy": by_signal_legacy,
            "strategy_side_by_side": strategy_side_by_side,
            "signal_transition": signal_transition,
            "hit_rate_new_vs_legacy": hit_rate_new_vs_legacy,
            "coverage": coverage,
        }
