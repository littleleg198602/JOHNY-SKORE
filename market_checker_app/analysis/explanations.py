from __future__ import annotations


def merge_reasons(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                merged.append(item)
                seen.add(item)
    return merged[:12]


def merge_warnings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                merged.append(item)
                seen.add(item)
    return merged


def build_key_drivers(news_score: float, tech_score: float, yahoo_score: float, behavioral_score: float, risk_score: float, regime: str) -> list[str]:
    drivers = [
        f"Regime: {regime}",
        f"News/Tech/Yahoo/Behavioral = {news_score:.1f}/{tech_score:.1f}/{yahoo_score:.1f}/{behavioral_score:.1f}",
    ]
    if risk_score > 65:
        drivers.append("Elevated risk profile penalizes final score")
    elif risk_score < 35:
        drivers.append("Contained risk profile supports final score")
    return drivers
