from __future__ import annotations


def merge_reasons(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                merged.append(item)
                seen.add(item)
    return merged[:8]


def merge_warnings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                merged.append(item)
                seen.add(item)
    return merged
