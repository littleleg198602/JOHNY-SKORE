from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from market_checker_app.analysis.scoring import _build_decision_modules, _decision_from_modules
from market_checker_app.config import DecisionModuleWeights, DecisionThresholds


@dataclass(frozen=True)
class ValidationScenario:
    name: str
    ticker: str
    inputs: dict[str, float]
    expected: set[str]


def _run_scenario(s: ValidationScenario, weights: DecisionModuleWeights, thresholds: DecisionThresholds) -> dict[str, object]:
    modules = _build_decision_modules(
        news_score=s.inputs["news_score"],
        tech_score=s.inputs["tech_score"],
        analyst_score=s.inputs["analyst_score"],
        panic_score=s.inputs["panic_score"],
        news_confidence=s.inputs["news_confidence"],
        tech_confidence=s.inputs["tech_confidence"],
        analyst_confidence=s.inputs["analyst_confidence"],
        panic_confidence=s.inputs["panic_confidence"],
        context="validation",
    )

    signal, bull_score, bear_score, spread, _, _, _, _, blocked, _, _ = _decision_from_modules(
        modules,
        s.inputs["panic_score"],
        weights,
        thresholds,
    )

    module_directions = {m.module: m.direction for m in modules}
    module_confidences = {m.module: m.confidence for m in modules}

    return {
        "scenario": s.name,
        "ticker": s.ticker,
        "bull_score": round(bull_score, 2),
        "bear_score": round(bear_score, 2),
        "spread": round(spread, 2),
        "module_directions": module_directions,
        "module_confidences": module_confidences,
        "blocked_reasons": blocked,
        "final_signal": signal,
        "expected_signal_band": sorted(s.expected),
        "pass": signal in s.expected,
    }


def build_validation_rows() -> list[dict[str, object]]:
    scenarios = [
        ValidationScenario(
            name="A) technical bullish, news bullish, panic neutral, analysts mildly bullish",
            ticker="AAPL",
            inputs=dict(news_score=74, tech_score=78, panic_score=45, analyst_score=62, news_confidence=74, tech_confidence=82, analyst_confidence=66, panic_confidence=62),
            expected={"BUY", "STRONG BUY"},
        ),
        ValidationScenario(
            name="B) technical bearish, news bullish, panic elevated, analysts neutral",
            ticker="TSLA",
            inputs=dict(news_score=70, tech_score=33, panic_score=79, analyst_score=50, news_confidence=72, tech_confidence=81, analyst_confidence=58, panic_confidence=77),
            expected={"HOLD", "SELL"},
        ),
        ValidationScenario(
            name="C) technical bearish, news bearish, panic high, analysts bearish",
            ticker="NFLX",
            inputs=dict(news_score=30, tech_score=26, panic_score=86, analyst_score=34, news_confidence=78, tech_confidence=84, analyst_confidence=72, panic_confidence=83),
            expected={"SELL", "STRONG SELL"},
        ),
        ValidationScenario(
            name="D) technical bullish, news negative, panic neutral, analysts positive",
            ticker="MSFT",
            inputs=dict(news_score=38, tech_score=75, panic_score=44, analyst_score=67, news_confidence=70, tech_confidence=83, analyst_confidence=68, panic_confidence=62),
            expected={"BUY", "HOLD"},
        ),
        ValidationScenario(
            name="E) technical neutral, news mixed, panic neutral, analysts neutral",
            ticker="KO",
            inputs=dict(news_score=51, tech_score=50, panic_score=49, analyst_score=50, news_confidence=58, tech_confidence=60, analyst_confidence=56, panic_confidence=58),
            expected={"HOLD"},
        ),
        ValidationScenario(
            name="F) technical bullish, news bullish, panic extreme, analysts bullish",
            ticker="NVDA",
            inputs=dict(news_score=76, tech_score=79, panic_score=91, analyst_score=68, news_confidence=75, tech_confidence=84, analyst_confidence=67, panic_confidence=84),
            expected={"BUY", "HOLD", "SELL"},
        ),
        ValidationScenario(
            name="Edge-1) bullish news vs bearish technicals",
            ticker="AMD",
            inputs=dict(news_score=77, tech_score=31, panic_score=54, analyst_score=53, news_confidence=72, tech_confidence=79, analyst_confidence=58, panic_confidence=64),
            expected={"HOLD", "SELL"},
        ),
        ValidationScenario(
            name="Edge-2) bearish news vs bullish technicals",
            ticker="META",
            inputs=dict(news_score=34, tech_score=77, panic_score=46, analyst_score=62, news_confidence=70, tech_confidence=83, analyst_confidence=64, panic_confidence=63),
            expected={"BUY", "HOLD"},
        ),
        ValidationScenario(
            name="Edge-3) strong bullish setup blocked by panic",
            ticker="AMZN",
            inputs=dict(news_score=79, tech_score=82, panic_score=94, analyst_score=70, news_confidence=77, tech_confidence=85, analyst_confidence=69, panic_confidence=86),
            expected={"HOLD", "SELL", "BUY"},
        ),
    ]

    weights = DecisionModuleWeights()
    thresholds = DecisionThresholds()
    return [_run_scenario(s, weights, thresholds) for s in scenarios]


def build_driver_checks() -> dict[str, bool]:
    rows = {row["scenario"]: row for row in build_validation_rows()}

    return {
        "technical_primary_directional_driver": rows["Edge-1) bullish news vs bearish technicals"]["final_signal"] in {"HOLD", "SELL"}
        and rows["Edge-2) bearish news vs bullish technicals"]["final_signal"] in {"BUY", "HOLD"},
        "news_modifies_or_strengthens": rows["A) technical bullish, news bullish, panic neutral, analysts mildly bullish"]["final_signal"]
        in {"BUY", "STRONG BUY"}
        and rows["D) technical bullish, news negative, panic neutral, analysts positive"]["final_signal"] in {"BUY", "HOLD"},
        "panic_regime_filter": "panic_extreme_blocks_bullish_signal" in rows[
            "F) technical bullish, news bullish, panic extreme, analysts bullish"
        ]["blocked_reasons"],
        "analysts_secondary_confirmation": rows["A) technical bullish, news bullish, panic neutral, analysts mildly bullish"]["final_signal"]
        in {"BUY", "STRONG BUY"},
    }


if __name__ == "__main__":
    rows = build_validation_rows()
    for row in rows:
        status = "PASS" if row["pass"] else "FAIL"
        print(f"\n{row['scenario']} [{row['ticker']}]")
        print(f"  bull_score: {row['bull_score']}")
        print(f"  bear_score: {row['bear_score']}")
        print(f"  spread: {row['spread']}")
        print(f"  module_directions: {row['module_directions']}")
        print(f"  module_confidences: {row['module_confidences']}")
        print(f"  blocked_reasons: {row['blocked_reasons']}")
        print(f"  final_signal: {row['final_signal']}")
        print(f"  expected_signal_band: {row['expected_signal_band']}")
        print(f"  result: {status}")

    checks = build_driver_checks()
    print("\nDriver checks:")
    for key, value in checks.items():
        print(f"  {key}: {value}")
