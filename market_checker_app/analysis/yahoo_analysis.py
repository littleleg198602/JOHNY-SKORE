from __future__ import annotations

from market_checker_app.models import YahooAnalysisResult, YahooSnapshot


CORE_FIELDS = [
    "currentPrice",
    "targetMeanPrice",
    "targetMedianPrice",
    "recommendationMean",
    "numberOfAnalystOpinions",
    "forwardPE",
    "profitMargins",
    "revenueGrowth",
    "earningsGrowth",
    "debtToEquity",
]


def _bounded(score: float) -> float:
    return max(0.0, min(100.0, score))


def analyze_yahoo(snapshot: YahooSnapshot) -> YahooAnalysisResult:
    data = snapshot.data
    missing = [field for field in CORE_FIELDS if data.get(field) is None]
    warnings: list[str] = []

    rec_mean = data.get("recommendationMean")
    rec_key = str(data.get("recommendationKey", "hold")).lower()
    analyst_n = int(data.get("numberOfAnalystOpinions") or 0)
    current = data.get("currentPrice")
    tgt_mean = data.get("targetMeanPrice")
    tgt_median = data.get("targetMedianPrice")
    tgt_low = data.get("targetLowPrice")
    tgt_high = data.get("targetHighPrice")

    analyst_sent = _bounded(100 - (float(rec_mean) - 1) * 25) if isinstance(rec_mean, (int, float)) else {"strong_buy": 90, "buy": 76, "hold": 50, "underperform": 34, "sell": 20}.get(rec_key, 50)
    upside = ((float(tgt_mean) / float(current)) - 1) * 100 if isinstance(current, (int, float)) and isinstance(tgt_mean, (int, float)) and current else 0.0
    target_attr = _bounded(50 + upside * 2.2)

    quality = 50.0
    for field, weight in [("profitMargins", 12), ("operatingMargins", 10), ("revenueGrowth", 15), ("earningsGrowth", 15), ("returnOnEquity", 12)]:
        val = data.get(field)
        if isinstance(val, (int, float)):
            quality += max(-weight, min(weight, float(val) * 120)) / 2
    debt_to_eq = data.get("debtToEquity")
    if isinstance(debt_to_eq, (int, float)):
        quality += 8 if debt_to_eq < 120 else -8

    valuation = 55.0
    forward_pe = data.get("forwardPE")
    trailing_pe = data.get("trailingPE")
    peg = data.get("pegRatio") or data.get("PEG")
    for pe, bonus, penalty, threshold in [(forward_pe, 12, -10, 25), (trailing_pe, 10, -8, 35)]:
        if isinstance(pe, (int, float)) and pe > 0:
            valuation += bonus if pe < threshold else penalty
    if isinstance(peg, (int, float)) and peg > 0:
        valuation += 10 if peg < 2 else -8

    if analyst_n < 5:
        warnings.append("Yahoo analyst coverage weak")
    if all(isinstance(v, (int, float)) for v in [tgt_low, tgt_mean, tgt_high]) and not (float(tgt_low) <= float(tgt_mean) <= float(tgt_high)):
        warnings.append("inconsistent analyst targets")
        target_attr -= 10
    if isinstance(tgt_mean, (int, float)) and isinstance(tgt_median, (int, float)) and abs(float(tgt_mean) - float(tgt_median)) / max(float(current or 1), 1) > 0.25:
        warnings.append("target dispersion unusually high")

    penalties = len(missing) * 1.8 + (8 if analyst_n < 3 else 0) + (6 if "inconsistent analyst targets" in warnings else 0)
    yahoo_score = _bounded(analyst_sent * 0.32 + target_attr * 0.24 + _bounded(quality) * 0.26 + _bounded(valuation) * 0.18 - penalties)

    completeness = 1 - min(1.0, len(missing) / len(CORE_FIELDS))
    confidence = _bounded(30 + completeness * 38 + min(1.0, analyst_n / 20) * 20 - (8 if "inconsistent analyst targets" in warnings else 0))

    if missing:
        warnings.append("key Yahoo fields missing")

    return YahooAnalysisResult(
        ticker=snapshot.ticker,
        yahoo_score=round(yahoo_score, 2),
        yahoo_confidence=round(confidence, 2),
        analyst_sentiment_score=round(analyst_sent, 2),
        target_attractiveness_score=round(target_attr, 2),
        fundamental_quality_score=round(_bounded(quality), 2),
        valuation_sanity_score=round(_bounded(valuation), 2),
        number_of_analyst_opinions=analyst_n,
        missing_fields=missing,
        warnings=warnings,
        reasons=[f"Analyst sentiment {analyst_sent:.1f}, upside {upside:.2f}%.", f"Fundamentals {quality:.1f}, valuation {valuation:.1f}, analyst count {analyst_n}."],
    )
