from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import ast
import json
from pathlib import Path
import unittest

import pandas as pd

from market_checker_app.prediction_contract import PRIMARY_TARGET_VERSION
from market_checker_app.services.candidate_model_service import build_candidate_model_report, _training_rows
from market_checker_app.services.candidate_model_evaluation_service import summarize_candidate_samples
from market_checker_app.services.layer_ablation_service import evaluate_layer_ablation
from market_checker_app.services.macro_regime_service import build_macro_regime_report, parse_macro_observations
from market_checker_app.services.market_factor_service import build_market_factor_snapshot
from market_checker_app.services.prediction_label_service import PredictionLabelService
from market_checker_app.services.resource_margin_scenario_service import build_resource_margin_scenario
from market_checker_app.services.sec_fundamental_feature_service import build_sec_fundamental_feature_snapshots
from market_checker_app.services.us_equity_calendar import us_equity_session
from tests.test_sec_fundamental_feature_service import _fact, _dt
from tests.test_candidate_model_service import _row, _at
from tests.test_candidate_model_evaluation_service import _snapshots
from tests.test_market_factor_service import _history
from tests.test_resource_margin_scenario_service import _source

UTC = timezone.utc


class FinalReleaseRegressions(unittest.TestCase):
    def test_total_debt_is_complete_order_independent_and_preserves_lineage(self):
        facts = [_fact("Assets", 500), _fact("CashAndCashEquivalentsAtCarryingValue", 50),
                 _fact("LongTermDebtCurrent", 10), _fact("LongTermDebtNoncurrent", 90), _fact("ShortTermBorrowings", 5)]
        for ordered in (facts, list(reversed(facts))):
            snapshot = build_sec_fundamental_feature_snapshots({"ACME": ordered}, as_of=_dt("2025-08-01"))[0]
            self.assertEqual(105, snapshot.values["total_debt"])
            self.assertEqual(2.1, snapshot.values["debt_to_cash_ratio"])
            self.assertEqual({fact.fact_id for fact in facts[2:]}, set(snapshot.source_fact_ids["total_debt"]))
            self.assertEqual(4, len(snapshot.source_fact_ids["debt_to_cash_ratio"]))

    def test_debt_does_not_double_count_aggregate_or_invent_missing_components(self):
        facts = [_fact("Assets", 500), _fact("LongTermDebt", 100),
                 _fact("LongTermDebtCurrent", 10), _fact("LongTermDebtNoncurrent", 90)]
        missing = build_sec_fundamental_feature_snapshots({"ACME": facts}, as_of=_dt("2025-08-01"))[0]
        self.assertNotIn("total_debt", missing.values)
        complete = build_sec_fundamental_feature_snapshots({"ACME": facts + [_fact("ShortTermBorrowings", 5)]}, as_of=_dt("2025-08-01"))[0]
        self.assertEqual(105, complete.values["total_debt"])

    def test_labels_become_available_at_official_close_not_midnight(self):
        index = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"], utc=True)
        prices = pd.DataFrame({"Close": [100, 101, 102, 103, 104, 105]}, index=index)
        snapshot_at = datetime(2026, 3, 2, 22, tzinfo=UTC)
        result = PredictionLabelService._common_price_windows(prices, prices, snapshot_as_of=snapshot_at, evaluation_as_of=datetime(2026, 3, 9, 21, tzinfo=UTC), horizon=5)
        self.assertIsNotNone(result)
        self.assertEqual(datetime(2026, 3, 9, 20, tzinfo=UTC), result[2])
        row = _row(1, label=.01, as_of_day=0)
        row.update(as_of=snapshot_at.isoformat(), target_observed_at=result[2].isoformat())
        self.assertEqual([], _training_rows(pd.DataFrame([row]), as_of=datetime(2026, 3, 9, 12, tzinfo=UTC), target_version=PRIMARY_TARGET_VERSION))

    def test_calendar_handles_new_year_exception_and_early_close(self):
        self.assertIsNotNone(us_equity_session(date(2021, 12, 31)))
        self.assertEqual(datetime(2026, 11, 27, 18, tzinfo=UTC), us_equity_session(date(2026, 11, 27)).close_at)

    def test_twelve_daily_cohorts_are_not_twelve_weeks(self):
        samples = [{"week": day.isoformat(), "ticker": str(i), "target_value": .01,
                    "outcome_up": 1., "momentum_probability_up": .4, "candidate_probability_up": .6}
                   for i, day in enumerate(pd.bdate_range("2026-01-05", periods=12))]
        result = summarize_candidate_samples(samples)
        self.assertEqual(3, result["distinct_weeks"])

    def test_training_deduplicates_ticker_horizons_and_excludes_legacy(self):
        rows = [_row(i, label=.01 if i % 2 else -.01, as_of_day=i) for i in range(12)]
        for row in rows:
            row["ticker"] = "AAPL"
        legacy = _row(100, label=.01, as_of_day=0)
        legacy["target_version"] = "excess_return_5d_nyse_split_price_v3"
        training = _training_rows(pd.DataFrame([*rows, legacy]), as_of=_at(30), target_version=PRIMARY_TARGET_VERSION)
        self.assertEqual(2, len(training))
        self.assertNotIn("snapshot-100", [row["snapshot_id"] for row in training])

    def test_missing_or_constant_columns_do_not_disable_all_available_features(self):
        rows = [_row(i, label=.01 if i % 2 else -.01, as_of_day=0) for i in range(8)]
        rows.append(_row(90, label=None, as_of_day=30))
        for row in rows:
            payload = json.loads(row["feature_payload_json"])
            payload["market_factors"]["relative_returns"] = {}
            payload["market_factors"]["drawdown"] = {"252d": 0}
            row["feature_payload_json"] = json.dumps(payload)
        report = build_candidate_model_report(pd.DataFrame(rows), prediction_snapshot_ids=["snapshot-90"], as_of=_at(30), minimum_training_samples=8, iterations=20)
        self.assertEqual("TRAINED", report["status"])
        self.assertEqual(4, sum(report["artifact"]["parameters"]["active_features"]))
        self.assertIsNotNone(report["predictions"][0]["candidate_probability_up"])

    def test_stale_prices_and_missing_sessions_cannot_form_returns(self):
        history = _history([100 + i for i in range(270)])
        common = dict(as_of=datetime(2026, 2, 1, tzinfo=UTC), asset_source="fixture", benchmark_source="fixture")
        stale = build_market_factor_snapshot(asset_history=history.iloc[:-1], benchmark_history=history, **common)
        self.assertIsNone(stale["asset_returns"]["1d"])
        gap = build_market_factor_snapshot(asset_history=history.drop(history.index[-4]), benchmark_history=history, **common)
        self.assertIsNotNone(gap["asset_returns"]["1d"])
        self.assertIsNone(gap["asset_returns"]["5d"])
        self.assertIsNone(gap["realized_volatility"]["20d_annualized"])

    def test_macro_units_dates_and_freshness_are_enforced(self):
        template = "CPI_YOY | GLOBAL | 2026-01 | {} | {} | 2026-01-01 | 2026-01-02 | 2026-01-02 | https://example.com/cpi"
        percent, errors = parse_macro_observations(template.format(4, "percent"))
        fraction, errors2 = parse_macro_observations(template.format(.04, "fraction"))
        self.assertEqual([], errors + errors2)
        self.assertEqual(percent[0]["value"], fraction[0]["value"])
        invalid = template.format(4, "percent").replace("2026-01-01", "2027-01-01")
        self.assertTrue(parse_macro_observations(invalid)[1])
        self.assertTrue(parse_macro_observations(template.format(4, "USD"))[1])
        expired = build_macro_regime_report(percent, as_of=datetime(2026, 9, 1, tzinfo=UTC))
        self.assertEqual([], expired["selected_observations"])

    def test_invalid_resource_scenario_keeps_schema_and_output_is_not_cost(self):
        invalid = replace(_source(), hedged_share_pct=80, fixed_price_share_pct=50)
        result = build_resource_margin_scenario(invalid, as_of=_at(5))
        self.assertIn("price_series_attached", result)
        self.assertIn("HEDGED_AND_FIXED", result["reason"])
        output = build_resource_margin_scenario(replace(_source(), exposure_type="COMMODITY_OUTPUT"), as_of=_at(5))
        self.assertIn("OUTPUT_REVENUE", output["reason"])
        self.assertNotIn("sensitivity", output)
        bad_url = build_resource_margin_scenario(replace(_source(), url="http://example.com",), as_of=_at(5))
        self.assertIn("DISCLOSURE_SOURCE_URL_INVALID", bad_url["reason"])

    def test_ablation_compares_exact_matching_ids_and_reports_missing_layers(self):
        frame = _snapshots()
        for index, row in frame.iterrows():
            payload = json.loads(row["feature_payload_json"])
            payload["news"] = {"news_count_total": 2, "weighted_sentiment_avg": index % 3 - 1, "news_confidence": .8}
            frame.loc[index, "feature_payload_json"] = json.dumps(payload)
        report = evaluate_layer_ablation(frame, minimum_training_samples=8, minimum_evaluation_samples=1, minimum_weeks=1, iterations=20)
        self.assertEqual("EVALUATED", report["variants"]["news"]["status"])
        self.assertEqual("INSUFFICIENT_DATA", report["variants"]["sec"]["status"])
        ids = report["variants"]["news"]["matched_snapshot_ids"]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(report["activation_allowed"])

    def test_no_broker_execution_calls_exist_in_production_python(self):
        forbidden = {"order_send", "placeOrder", "place_order", "submit_order", "create_order", "order_modify"}
        root = Path(__file__).resolve().parents[1] / "market_checker_app"
        findings = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                    if name in forbidden:
                        findings.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
