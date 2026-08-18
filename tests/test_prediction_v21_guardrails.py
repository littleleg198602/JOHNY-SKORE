from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import pandas as pd

from market_checker_app.analysis.news_analysis import analyze_news
from market_checker_app.analysis.scoring import (
    _build_decision_modules,
    _decision_from_modules,
    build_v21_action,
)
from market_checker_app.analysis.tech_analysis import analyze_tech
from market_checker_app.config import (
    DecisionModuleWeights,
    DecisionThresholds,
    PredictionV21Config,
)
from market_checker_app.models import NewsItem


class PredictionV21GuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = PredictionV21Config()

    def test_legacy_consensus_allows_directional_action(self) -> None:
        action, reasons = build_v21_action(
            decision_signal="BUY",
            legacy_signal="STRONG BUY",
            forecast="UP",
            signal_strength="moderate",
            decision_confidence=0.42,
            panic_score=40,
            risk_flags=[],
            blocked_reasons=[],
            config=self.config,
        )

        self.assertEqual("BUY", action)
        self.assertIn("v21_legacy_consensus", reasons)

    def test_guarded_strong_forecast_can_trade_without_legacy_direction(self) -> None:
        action, reasons = build_v21_action(
            decision_signal="HOLD",
            legacy_signal="HOLD",
            forecast="DOWN",
            signal_strength="strong",
            decision_confidence=0.44,
            panic_score=55,
            risk_flags=[],
            blocked_reasons=[],
            config=self.config,
        )

        self.assertEqual("SELL", action)
        self.assertIn("v21_guarded_strong_signal", reasons)

    def test_atr_or_module_conflict_is_a_hard_veto(self) -> None:
        for risk_flag in ("high_atr_ratio", "conflicting_module_signals"):
            with self.subTest(risk_flag=risk_flag):
                action, reasons = build_v21_action(
                    decision_signal="BUY",
                    legacy_signal="BUY",
                    forecast="UP",
                    signal_strength="strong",
                    decision_confidence=0.7,
                    panic_score=35,
                    risk_flags=[risk_flag],
                    blocked_reasons=[],
                    config=self.config,
                )
                self.assertEqual("NO_TRADE", action)
                self.assertTrue(any(risk_flag in reason for reason in reasons))

    def test_technical_only_state_no_longer_promotes_hold(self) -> None:
        modules = _build_decision_modules(
            news_score=40,
            tech_score=70,
            analyst_score=45,
            panic_score=45,
            news_confidence=70,
            tech_confidence=80,
            analyst_confidence=60,
            panic_confidence=65,
            context="test",
        )

        signal, *_, blocked, _, _ = _decision_from_modules(
            modules,
            45,
            DecisionModuleWeights(),
            DecisionThresholds(),
        )
        self.assertEqual("HOLD", signal)
        self.assertNotIn("technical_override_promoted_to_buy", blocked)

    def test_technical_confidence_has_calibration_headroom(self) -> None:
        index = pd.date_range("2025-01-01", periods=260, freq="B", tz="UTC")
        close = pd.Series([100 + idx * 0.12 for idx in range(len(index))], index=index)
        ohlc = pd.DataFrame(
            {
                "Open": close - 0.3,
                "High": close + 0.7,
                "Low": close - 0.8,
                "Close": close,
                "Volume": 1_000_000,
            }
        )

        result = analyze_tech("TEST", ohlc, source="mt5")
        self.assertGreater(result.tech_confidence, 50)
        self.assertLessEqual(result.tech_confidence, 88)
        self.assertNotEqual(100, result.tech_confidence)

    def test_single_news_source_cannot_claim_high_confidence(self) -> None:
        now = datetime.now(timezone.utc)
        articles = [
            NewsItem(
                ticker="TEST",
                source="Google News RSS",
                title=f"TEST growth update {idx}",
                summary="TEST beat expectations",
                published_at=now - timedelta(hours=idx),
                sentiment_weight=1.0,
                url=f"https://example.com/{idx}",
            )
            for idx in range(20)
        ]

        result = analyze_news("TEST", articles)
        self.assertEqual(1, result.unique_sources_count)
        self.assertLessEqual(result.news_confidence, 55)


if __name__ == "__main__":
    unittest.main()
