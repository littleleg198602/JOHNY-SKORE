from __future__ import annotations

from market_checker_app.models import ConfidenceResult


def combine_confidence(news_conf: float, tech_conf: float, yahoo_conf: float) -> ConfidenceResult:
    data_quality = max(0.0, min(100.0, news_conf * 0.34 + tech_conf * 0.36 + yahoo_conf * 0.30))
    final_conf = max(0.0, min(100.0, data_quality * 0.85 + min(news_conf, tech_conf, yahoo_conf) * 0.15))
    return ConfidenceResult(
        news_confidence=round(news_conf, 2),
        tech_confidence=round(tech_conf, 2),
        yahoo_confidence=round(yahoo_conf, 2),
        data_quality_score=round(data_quality, 2),
        final_confidence=round(final_conf, 2),
    )
