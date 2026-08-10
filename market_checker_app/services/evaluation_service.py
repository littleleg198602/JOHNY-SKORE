from __future__ import annotations

import pandas as pd


class EvaluationService:
    PREDICTION_FRAME_NAMES = (
        "prediction_overall",
        "prediction_summary",
        "prediction_details",
        "pending_predictions",
    )

    @staticmethod
    def _empty_prediction_frames() -> dict[str, pd.DataFrame]:
        return {name: pd.DataFrame() for name in EvaluationService.PREDICTION_FRAME_NAMES}

    @staticmethod
    def _normalize_prediction(signal: object) -> str:
        normalized = str(signal or "").strip().upper().replace("_", " ")
        if normalized in {"BUY", "STRONG BUY"}:
            return "BUY"
        if normalized in {"SELL", "STRONG SELL"}:
            return "SELL"
        if normalized == "HOLD":
            return "HOLD"
        return "UNKNOWN"

    def evaluate_predictions(
        self,
        history: pd.DataFrame,
        *,
        hold_tolerance_pct: float = 2.0,
        minimum_weekly_gap_days: float = 4.0,
        maximum_weekly_gap_days: float = 10.0,
        evaluation_timezone: str = "UTC",
    ) -> dict[str, pd.DataFrame]:
        """Evaluate one saved weekly signal against the next saved weekly price.

        The last snapshot in each local Monday-Sunday week is used, so repeated
        reruns on the same Monday cannot become a fake forward observation.
        BUY succeeds on a positive return, SELL on a negative return and HOLD
        when the absolute move stays inside ``hold_tolerance_pct``.  Only gaps
        close to one week enter the accuracy summary; longer/malformed gaps are
        kept in the detail table as ``IRREGULAR_GAP``.
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
        weekly["realized_return_pct"] = (
            (weekly["evaluation_price"] / weekly["current_price"]) - 1
        ) * 100
        weekly["prediction"] = weekly["signal"].map(self._normalize_prediction)

        has_next = weekly["evaluation_run_id"].notna()
        valid_prices = (
            weekly["current_price"].notna()
            & weekly["evaluation_price"].notna()
            & (weekly["current_price"] > 0)
            & (weekly["evaluation_price"] > 0)
        )
        valid_gap = weekly["holding_days"].between(
            minimum_weekly_gap_days,
            maximum_weekly_gap_days,
            inclusive="both",
        )
        known_prediction = weekly["prediction"] != "UNKNOWN"

        weekly["result"] = "PENDING"
        weekly.loc[has_next & ~valid_prices, "result"] = "NO_PRICE"
        weekly.loc[has_next & valid_prices & ~valid_gap, "result"] = "IRREGULAR_GAP"
        weekly.loc[has_next & valid_prices & valid_gap & ~known_prediction, "result"] = "UNKNOWN_SIGNAL"

        comparable = has_next & valid_prices & valid_gap & known_prediction
        buy_hit = (weekly["prediction"] == "BUY") & (weekly["realized_return_pct"] > 0)
        sell_hit = (weekly["prediction"] == "SELL") & (weekly["realized_return_pct"] < 0)
        hold_hit = (
            (weekly["prediction"] == "HOLD")
            & (weekly["realized_return_pct"].abs() <= hold_tolerance_pct)
        )
        weekly.loc[comparable, "result"] = "MISS"
        weekly.loc[comparable & (buy_hit | sell_hit | hold_hit), "result"] = "HIT"

        weekly["actual_move"] = ""
        weekly.loc[valid_prices & (weekly["realized_return_pct"] > 0), "actual_move"] = "UP"
        weekly.loc[valid_prices & (weekly["realized_return_pct"] < 0), "actual_move"] = "DOWN"
        weekly.loc[valid_prices & (weekly["realized_return_pct"] == 0), "actual_move"] = "FLAT"

        details = weekly.rename(
            columns={
                "run_id": "signal_run_id",
                "finished_at": "signal_at",
                "current_price": "signal_price",
                "current_price_source": "signal_price_source",
            }
        )[
            [
                "signal_run_id",
                "signal_at",
                "week_start",
                "ticker",
                "signal",
                "prediction",
                "signal_price",
                "signal_price_source",
                "evaluation_run_id",
                "evaluated_at",
                "evaluation_price",
                "evaluation_price_source",
                "holding_days",
                "realized_return_pct",
                "actual_move",
                "result",
            ]
        ].sort_values(["signal_at", "ticker"], ascending=[False, True])
        for column in ("signal_price", "evaluation_price", "holding_days", "realized_return_pct"):
            details[column] = pd.to_numeric(details[column], errors="coerce").round(4)

        scored = details[details["result"].isin(["HIT", "MISS"])].copy()
        summary_rows: list[dict[str, object]] = []
        for prediction in ("BUY", "HOLD", "SELL"):
            subset = scored[scored["prediction"] == prediction]
            hits = int((subset["result"] == "HIT").sum())
            evaluated = int(len(subset))
            summary_rows.append(
                {
                    "prediction": prediction,
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
                }
            )
        summary = pd.DataFrame(summary_rows)

        total_evaluated = int(len(scored))
        total_hits = int((scored["result"] == "HIT").sum())
        overall = pd.DataFrame(
            {
                "metric": [
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
        hist["next_return_pct"] = ((hist["next_price"] / hist["current_price"]) - 1) * 100
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
            valid.groupby("signal", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"signal": "new_signal", "next_return_pct": "avg_next_period_return_pct"})
        )
        by_signal_legacy = (
            valid.groupby("legacy_signal", as_index=False)["next_return_pct"]
            .mean()
            .rename(columns={"legacy_signal": "legacy_signal", "next_return_pct": "avg_next_period_return_pct"})
        )

        signal_transition = (
            valid.groupby(["legacy_signal", "signal"], as_index=False)
            .size()
            .rename(columns={"size": "count"})
            .sort_values("count", ascending=False)
        )

        def _hit(df: pd.DataFrame, signal_col: str) -> tuple[float, float]:
            buy = df[df[signal_col].isin(["BUY", "STRONG BUY"])]
            sell = df[df[signal_col].isin(["SELL", "STRONG SELL"])]
            return (
                float((buy["next_return_pct"] > 0).mean()) if not buy.empty else 0.0,
                float((sell["next_return_pct"] < 0).mean()) if not sell.empty else 0.0,
            )

        new_buy_hit, new_sell_hit = _hit(valid, "signal")
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
