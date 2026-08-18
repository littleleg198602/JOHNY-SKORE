from __future__ import annotations

import unittest

import pandas as pd

from market_checker_app.services.evaluation_service import EvaluationService


def _row(
    run_id: int,
    finished_at: str,
    ticker: str,
    price: float | None,
    signal: str,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "finished_at": finished_at,
        "ticker": ticker,
        "current_price": price,
        "signal": signal,
    }


class WeeklyPredictionEvaluationTests(unittest.TestCase):
    def test_actions_and_forecasts_are_scored_separately(self) -> None:
        history = pd.DataFrame(
            [
                _row(1, "2026-08-03T18:00:00Z", "BUY_OK", 100, "BUY"),
                _row(2, "2026-08-10T18:00:00Z", "BUY_OK", 105, "HOLD"),
                _row(1, "2026-08-03T18:00:00Z", "BUY_BAD", 100, "STRONG BUY"),
                _row(2, "2026-08-10T18:00:00Z", "BUY_BAD", 90, "HOLD"),
                _row(1, "2026-08-03T18:00:00Z", "SELL_OK", 100, "SELL"),
                _row(2, "2026-08-10T18:00:00Z", "SELL_OK", 95, "HOLD"),
                _row(1, "2026-08-03T18:00:00Z", "SELL_BAD", 100, "STRONG SELL"),
                _row(2, "2026-08-10T18:00:00Z", "SELL_BAD", 105, "HOLD"),
                _row(1, "2026-08-03T18:00:00Z", "HOLD_OK", 100, "HOLD"),
                _row(2, "2026-08-10T18:00:00Z", "HOLD_OK", 101.5, "HOLD"),
                _row(1, "2026-08-03T18:00:00Z", "HOLD_BAD", 100, "HOLD"),
                _row(2, "2026-08-10T18:00:00Z", "HOLD_BAD", 103, "HOLD"),
            ]
        )

        frames = EvaluationService().evaluate_predictions(history, hold_tolerance_pct=2.0)
        details = frames["prediction_details"]
        evaluated = details[details["result"].isin(["HIT", "MISS"])]
        results = evaluated.set_index("ticker")["result"].to_dict()

        self.assertEqual(
            {
                "BUY_OK": "HIT",
                "BUY_BAD": "MISS",
                "SELL_OK": "HIT",
                "SELL_BAD": "MISS",
            },
            results,
        )
        no_trade = details[details["result"] == "NO_TRADE"].set_index("ticker")
        self.assertEqual({"HOLD_OK", "HOLD_BAD"}, set(no_trade.index))
        forecast_results = details[
            details["forecast_result"].isin(["FORECAST_HIT", "FORECAST_MISS"])
        ].set_index("ticker")["forecast_result"].to_dict()
        self.assertEqual("FORECAST_HIT", forecast_results["HOLD_OK"])
        self.assertEqual("FORECAST_MISS", forecast_results["HOLD_BAD"])
        summary = frames["prediction_summary"].set_index("prediction")
        self.assertEqual(2, int(summary.loc["BUY", "evaluated"]))
        self.assertEqual(1, int(summary.loc["BUY", "hits"]))
        self.assertEqual(50.0, float(summary.loc["SELL", "hit_rate_pct"]))
        self.assertEqual(2, int(summary.loc["NO_TRADE", "observations"]))
        self.assertEqual(0, int(summary.loc["NO_TRADE", "evaluated"]))
        overall = dict(
            zip(frames["prediction_overall"]["metric"], frames["prediction_overall"]["value"])
        )
        self.assertEqual(4, int(overall["evaluated_directional_trades"]))
        self.assertEqual(2, int(overall["correct_directional_trades"]))
        self.assertEqual(50.0, float(overall["directional_hit_rate_pct"]))
        self.assertEqual(66.67, float(overall["trade_coverage_pct"]))
        self.assertEqual(2, int(overall["no_trade_predictions"]))
        self.assertEqual(6, int(overall["evaluated_forecasts"]))
        self.assertEqual(3, int(overall["correct_forecasts"]))
        self.assertEqual(6, int(overall["pending_predictions"]))

    def test_explicit_no_trade_can_keep_a_directional_forecast(self) -> None:
        history = pd.DataFrame(
            [
                {
                    **_row(1, "2026-08-03T18:00:00Z", "AAPL", 100, "NO_TRADE"),
                    "decision_signal": "HOLD",
                    "action": "NO_TRADE",
                    "forecast": "UP",
                    "scoring_version": "v2.1_guarded_consensus",
                },
                {
                    **_row(2, "2026-08-10T18:00:00Z", "AAPL", 105, "NO_TRADE"),
                    "decision_signal": "HOLD",
                    "action": "NO_TRADE",
                    "forecast": "UP",
                    "scoring_version": "v2.1_guarded_consensus",
                },
            ]
        )

        frames = EvaluationService().evaluate_predictions(history)
        details = frames["prediction_details"]
        evaluated = details[details["signal_run_id"] == 1].iloc[0]
        self.assertEqual("NO_TRADE", evaluated["result"])
        self.assertEqual("FORECAST_HIT", evaluated["forecast_result"])
        by_version = frames["prediction_by_version"].set_index("scoring_version")
        self.assertEqual(
            0,
            int(by_version.loc["v2.1_guarded_consensus", "directional_trades"]),
        )
        self.assertEqual(
            100.0,
            float(by_version.loc["v2.1_guarded_consensus", "forecast_accuracy_pct"]),
        )

    def test_probable_stock_split_is_adjusted_before_scoring(self) -> None:
        history = pd.DataFrame(
            [
                _row(1, "2026-08-03T18:00:00Z", "SPLIT_TEST", 100, "BUY"),
                _row(2, "2026-08-10T18:00:00Z", "SPLIT_TEST", 50.7, "HOLD"),
            ]
        )

        frames = EvaluationService().evaluate_predictions(history)
        evaluated = frames["prediction_details"].query("signal_run_id == 1").iloc[0]
        self.assertEqual("HIT", evaluated["result"])
        self.assertAlmostEqual(1.4, float(evaluated["realized_return_pct"]), places=4)
        self.assertEqual(2.0, float(evaluated["split_adjustment_multiplier"]))
        self.assertIn("probable_forward_split_2:1", evaluated["corporate_action_note"])
        overall = dict(
            zip(frames["prediction_overall"]["metric"], frames["prediction_overall"]["value"])
        )
        self.assertEqual(1, int(overall["corporate_action_adjustments"]))

    def test_latest_same_week_rerun_is_used_once(self) -> None:
        history = pd.DataFrame(
            [
                _row(1, "2026-08-03T08:00:00Z", "AAPL", 95, "SELL"),
                _row(2, "2026-08-03T18:00:00Z", "AAPL", 100, "BUY"),
                _row(3, "2026-08-10T18:00:00Z", "AAPL", 110, "HOLD"),
            ]
        )

        frames = EvaluationService().evaluate_predictions(history)
        details = frames["prediction_details"]
        evaluated = details[details["result"] == "HIT"]

        self.assertEqual(1, len(evaluated))
        self.assertEqual(2, int(evaluated.iloc[0]["signal_run_id"]))
        self.assertEqual("BUY", evaluated.iloc[0]["prediction"])
        self.assertEqual(10.0, float(evaluated.iloc[0]["realized_return_pct"]))
        overall = dict(
            zip(frames["prediction_overall"]["metric"], frames["prediction_overall"]["value"])
        )
        self.assertEqual(1, int(overall["same_week_rows_ignored"]))

    def test_irregular_gap_and_missing_price_do_not_distort_hit_rate(self) -> None:
        history = pd.DataFrame(
            [
                _row(1, "2026-08-03T18:00:00Z", "AAPL", 100, "BUY"),
                _row(3, "2026-08-17T18:00:00Z", "AAPL", 120, "HOLD"),
                _row(1, "2026-08-03T18:00:00Z", "MSFT", None, "BUY"),
                _row(2, "2026-08-10T18:00:00Z", "MSFT", 200, "HOLD"),
            ]
        )

        frames = EvaluationService().evaluate_predictions(history)
        results = frames["prediction_details"].set_index(["ticker", "signal_run_id"])["result"]

        self.assertEqual("IRREGULAR_GAP", results.loc[("AAPL", 1)])
        self.assertEqual("NO_PRICE", results.loc[("MSFT", 1)])
        overall = dict(
            zip(frames["prediction_overall"]["metric"], frames["prediction_overall"]["value"])
        )
        self.assertEqual(0, int(overall["evaluated_weekly_predictions"]))
        self.assertEqual(1, int(overall["irregular_gap_predictions"]))
        self.assertEqual(1, int(overall["no_price_predictions"]))

    def test_empty_or_incomplete_history_returns_empty_frames(self) -> None:
        service = EvaluationService()
        for history in (pd.DataFrame(), pd.DataFrame({"ticker": ["AAPL"]})):
            frames = service.evaluate_predictions(history)
            self.assertEqual(set(service.PREDICTION_FRAME_NAMES), set(frames))
            self.assertTrue(all(frame.empty for frame in frames.values()))

    def test_negative_hold_tolerance_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "hold_tolerance_pct"):
            EvaluationService().evaluate_predictions(
                pd.DataFrame(),
                hold_tolerance_pct=-0.1,
            )

    def test_cumulative_history_weights_every_prediction_not_every_week(self) -> None:
        rows = [
            _row(1, "2026-08-03T18:00:00Z", "FIRST", 100, "BUY"),
            _row(2, "2026-08-10T18:00:00Z", "FIRST", 90, "BUY"),
            _row(3, "2026-08-17T18:00:00Z", "FIRST", 100, "HOLD"),
        ]
        for index in range(99):
            ticker = f"HIT_{index:03d}"
            rows.extend(
                [
                    _row(2, "2026-08-10T18:00:00Z", ticker, 100, "BUY"),
                    _row(3, "2026-08-17T18:00:00Z", ticker, 101, "HOLD"),
                ]
            )

        frames = EvaluationService().evaluate_predictions(pd.DataFrame(rows))
        weekly = frames["prediction_weekly"].reset_index(drop=True)
        cumulative = frames["prediction_cumulative"].reset_index(drop=True)

        self.assertEqual(2, len(weekly))
        self.assertEqual([1, 100], weekly["evaluated"].astype(int).tolist())
        self.assertEqual([0, 100], weekly["hits"].astype(int).tolist())
        self.assertEqual([0.0, 100.0], weekly["hit_rate_pct"].astype(float).tolist())
        self.assertEqual(101, int(cumulative.iloc[-1]["cumulative_evaluated"]))
        self.assertEqual(100, int(cumulative.iloc[-1]["cumulative_hits"]))
        self.assertEqual(99.01, float(cumulative.iloc[-1]["cumulative_hit_rate_pct"]))

        by_ticker = frames["prediction_by_ticker"].set_index("ticker")
        self.assertEqual(2, int(by_ticker.loc["FIRST", "evaluated"]))
        self.assertEqual(1, int(by_ticker.loc["FIRST", "hits"]))
        self.assertEqual(50.0, float(by_ticker.loc["FIRST", "hit_rate_pct"]))


if __name__ == "__main__":
    unittest.main()
