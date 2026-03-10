from __future__ import annotations

from market_checker_app.models import YahooAnalysisResult, YahooSnapshot


def _bounded(score: float) -> float:
    return max(0.0, min(100.0, score))


def analyze_yahoo(snapshot: YahooSnapshot) -> YahooAnalysisResult:
    data = snapshot.data
    missing: list[str] = []
    warnings: list[str] = []

    rec_mean = data.get("recommendationMean")
    rec_key = str(data.get("recommendationKey", "hold")).lower()
    analyst_n = int(data.get("numberOfAnalystOpinions") or 0)
    current = data.get("currentPrice")
    tgt_mean = data.get("targetMeanPrice")
    tgt_low = data.get("targetLowPrice")
    tgt_high = data.get("targetHighPrice")

    if current is None:
        missing.append("currentPrice")
    if tgt_mean is None:
        missing.append("targetMeanPrice")

    if isinstance(rec_mean, (int, float)):
        analyst_sent = _bounded(100 - (float(rec_mean) - 1) * 25)
    else:
        analyst_sent = {"strong_buy": 90, "buy": 75, "hold": 50, "underperform": 35, "sell": 20}.get(rec_key, 50)

    upside = ((float(tgt_mean) / float(current)) - 1) * 100 if isinstance(current, (int, float)) and isinstance(tgt_mean, (int, float)) and current else 0
    target_attr = _bounded(50 + upside * 2)

    revenue_growth = data.get("revenueGrowth")
    earnings_growth = data.get("earningsGrowth")
    margins = data.get("profitMargins")
    roe = data.get("returnOnEquity")
    debt_to_eq = data.get("debtToEquity")
    quality = 50.0
    for val, w in [(revenue_growth, 15), (earnings_growth, 20), (margins, 15), (roe, 20)]:
        if isinstance(val, (int, float)):
            quality += max(-w, min(w, float(val) * 100)) / 2
        else:
            missing.append(str(val))
    if isinstance(debt_to_eq, (int, float)):
        quality += 10 if debt_to_eq < 100 else -8

    trailing_pe = data.get("trailingPE")
    forward_pe = data.get("forwardPE")
    peg = data.get("pegRatio")
    valuation = 55.0
    if isinstance(forward_pe, (int, float)) and forward_pe > 0:
        valuation += 15 if forward_pe < 25 else -10
    else:
        missing.append("forwardPE")
    if isinstance(trailing_pe, (int, float)) and trailing_pe > 0:
        valuation += 10 if trailing_pe < 35 else -8
    if isinstance(peg, (int, float)) and peg > 0:
        valuation += 12 if peg < 2 else -6

    if isinstance(tgt_low, (int, float)) and isinstance(tgt_high, (int, float)) and isinstance(tgt_mean, (int, float)):
        if not (tgt_low <= tgt_mean <= tgt_high):
            warnings.append("inconsistent analyst targets")
            target_attr -= 12

    if analyst_n < 5:
        warnings.append("Yahoo analyst coverage weak")

    penalties = len(set(missing)) * 2 + (10 if analyst_n < 3 else 0)
    yahoo_score = _bounded(analyst_sent * 0.34 + target_attr * 0.26 + _bounded(quality) * 0.24 + _bounded(valuation) * 0.16 - penalties)

    completeness = 1 - min(1.0, len(set(missing)) / 12)
    confidence = _bounded(35 + completeness * 35 + min(1.0, analyst_n / 20) * 20 - (10 if "inconsistent analyst targets" in warnings else 0))

    reasons = [
        f"Analyst sentiment ({rec_key}) and recommendation mean influence score ({analyst_sent:.1f}).",
        f"Target upside estimate is {upside:.2f}% with {analyst_n} analysts.",
    ]

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
        missing_fields=sorted(set(missing)),
        warnings=warnings,
        reasons=reasons,
    )
