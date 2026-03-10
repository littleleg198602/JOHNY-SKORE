from __future__ import annotations

import re


def normalize_ticker(value: str) -> str:
    return value.strip().upper()


def normalize_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.lower()).strip()
    return re.sub(r"[^a-z0-9\s%.-]", "", normalized)
