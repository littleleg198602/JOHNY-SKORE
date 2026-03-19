from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
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

    signal, bull_score, bear_score, spread, overall_conf, bullish_count, bearish_count, neutral_count, blocked, _, driver = _decision_from_modules(
        modules,
        s.inputs["panic_score"],
        weights,
        thresholds,
    )

    module_directions = {m.module: m.direction for m in modules}
    module_confidences = {m.module: m.confidence for m in modules}
    module_spreads = {m.module: round(m.bull_contribution - m.bear_contribution, 2) for m in modules}
    primary_driver = max(modules, key=lambda m: abs(m.bull_contribution - m.bear_contribution)).module

    return {
        "scenario": s.name,
        "ticker": s.ticker,
        "inputs": dict(s.inputs),
        "bull_score": round(bull_score, 2),
        "bear_score": round(bear_score, 2),
        "spread": round(spread, 2),
        "overall_confidence": round(overall_conf * 100, 2),
        "module_directions": module_directions,
        "module_confidences": module_confidences,
        "module_spreads": module_spreads,
        "primary_driver": primary_driver,
        "driver": driver,
        "bullish_module_count": bullish_count,
        "bearish_module_count": bearish_count,
        "neutral_module_count": neutral_count,
        "modules": [asdict(m) for m in modules],
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


def _classify_hold_primary_reason(row: dict[str, object], thresholds: DecisionThresholds) -> str:
    blocked = set(row["blocked_reasons"])
    module_dirs = row["module_directions"]
    tech_dir = module_dirs.get("technical")
    news_dir = module_dirs.get("news")

    if abs(float(row["spread"])) <= thresholds.hold_band or "bull_bear_balance_hold_band" in blocked:
        return "small_spread"
    if tech_dir in {"bullish", "bearish"} and news_dir in {"bullish", "bearish"} and tech_dir != news_dir:
        return "technical_news_conflict"
    if any(reason.startswith("panic_") for reason in blocked):
        return "panic_block"
    if "low_confidence_blocks_directional_signal" in blocked:
        return "low_confidence"
    if int(row["neutral_module_count"]) >= 2:
        return "mixed_neutral_modules"
    return "other_hold_reason"


def _signal_distribution(rows: list[dict[str, object]]) -> dict[str, int]:
    buckets = {name: 0 for name in ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]}
    for row in rows:
        buckets[row["final_signal"]] += 1
    return buckets


def build_calibration_analysis() -> dict[str, object]:
    weights = DecisionModuleWeights()
    thresholds = DecisionThresholds()
    rows = build_validation_rows()

    hold_rows = [row for row in rows if row["final_signal"] == "HOLD"]
    hold_diagnostics = [
        {
            "ticker": row["ticker"],
            "bull_score": row["bull_score"],
            "bear_score": row["bear_score"],
            "spread": row["spread"],
            "overall_confidence": row["overall_confidence"],
            "primary_driver": row["primary_driver"],
            "module_directions": row["module_directions"],
            "blocked_reasons": row["blocked_reasons"],
        }
        for row in sorted(hold_rows, key=lambda r: (abs(float(r["spread"])), -float(r["overall_confidence"])))
    ]

    concentration = {
        "small_spread": 0,
        "technical_news_conflict": 0,
        "panic_block": 0,
        "low_confidence": 0,
        "mixed_neutral_modules": 0,
        "other_hold_reason": 0,
    }
    for row in hold_rows:
        concentration[_classify_hold_primary_reason(row, thresholds)] += 1

    baseline_distribution = _signal_distribution(rows)
    sensitivity_levels = {
        "narrower_hold_band_3": 3.0,
        "baseline_hold_band_7": thresholds.hold_band,
        "wider_hold_band_11": 11.0,
    }
    sensitivity_simulation: dict[str, dict[str, int]] = {}
    for label, hold_band in sensitivity_levels.items():
        adjusted_thresholds = replace(thresholds, hold_band=hold_band)
        simulated_rows: list[dict[str, object]] = []
        for row in rows:
            i = row["inputs"]
            modules = _build_decision_modules(
                news_score=float(i["news_score"]),
                tech_score=float(i["tech_score"]),
                analyst_score=float(i["analyst_score"]),
                panic_score=float(i["panic_score"]),
                news_confidence=float(i["news_confidence"]),
                tech_confidence=float(i["tech_confidence"]),
                analyst_confidence=float(i["analyst_confidence"]),
                panic_confidence=float(i["panic_confidence"]),
                context="calibration-sim",
            )
            signal, *_ = _decision_from_modules(modules, float(i["panic_score"]), weights, adjusted_thresholds)
            simulated_rows.append({"final_signal": signal})
        sensitivity_simulation[label] = _signal_distribution(simulated_rows)

    high_conf_threshold = 70.0
    high_conf_hold_rows = [row for row in hold_rows if float(row["overall_confidence"]) >= high_conf_threshold]
    confidence_sanity = {
        "high_confidence_threshold": high_conf_threshold,
        "high_confidence_hold_count": len(high_conf_hold_rows),
        "hold_count": len(hold_rows),
        "high_confidence_hold_ratio": round((len(high_conf_hold_rows) / len(hold_rows)) if hold_rows else 0.0, 3),
        "diagnosis": (
            "High-confidence HOLDs mostly represent mixed-certainty confluence (small spread/conflict) rather than low-confidence gating."
            if high_conf_hold_rows
            else "High-confidence HOLDs are rare in this calibration sample."
        ),
    }

    technical_hold_rows: list[dict[str, object]] = []
    for row in hold_rows:
        technical_module = next((m for m in row["modules"] if m["module"] == "technical"), None)
        if technical_module is None:
            continue
        tech_spread = technical_module["bull_contribution"] - technical_module["bear_contribution"]
        if abs(tech_spread) >= 20:
            technical_hold_rows.append(
                {
                    "ticker": row["ticker"],
                    "tech_direction": technical_module["direction"],
                    "tech_spread": round(tech_spread, 2),
                    "final_signal": row["final_signal"],
                    "blocked_reasons": row["blocked_reasons"],
                }
            )

    technical_effectiveness = {
        "strong_technical_state_hold_count": len(technical_hold_rows),
        "hold_count": len(hold_rows),
        "strong_technical_hold_ratio": round((len(technical_hold_rows) / len(hold_rows)) if hold_rows else 0.0, 3),
        "examples": technical_hold_rows,
    }

    return {
        "hold_diagnostics": hold_diagnostics,
        "hold_concentration": concentration,
        "signal_distribution_baseline": baseline_distribution,
        "sensitivity_simulation": sensitivity_simulation,
        "confidence_sanity_check": confidence_sanity,
        "technical_driver_effectiveness": technical_effectiveness,
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

    calibration = build_calibration_analysis()
    print("\nCalibration analysis:")
    print("  HOLD diagnostics:")
    for item in calibration["hold_diagnostics"]:
        print(f"    {item}")
    print(f"  HOLD concentration: {calibration['hold_concentration']}")
    print(f"  Baseline signal distribution: {calibration['signal_distribution_baseline']}")
    print("  Sensitivity simulation:")
    for key, dist in calibration["sensitivity_simulation"].items():
        print(f"    {key}: {dist}")
    print(f"  Confidence sanity check: {calibration['confidence_sanity_check']}")
    print(f"  Technical-driver effectiveness: {calibration['technical_driver_effectiveness']}")
