from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_market_caps(path: str) -> tuple[dict[str, float], str | None]:
    if not path:
        return {}, "MarketCap soubor není vybrán."

    p = Path(path)
    if not p.exists():
        return {}, f"MarketCap soubor neexistuje: {path}"

    try:
        frame = pd.read_csv(p)
    except Exception as exc:
        return {}, f"MarketCap soubor nelze načíst: {exc}"

    required = {"ticker", "market_cap_usd"}
    if not required.issubset(set(frame.columns)):
        return {}, "MarketCap CSV musí obsahovat sloupce ticker, market_cap_usd"

    data = {str(r["ticker"]).upper(): float(r["market_cap_usd"]) for _, r in frame.iterrows()}
    return data, None
