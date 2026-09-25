from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from market_checker_app.utils.text import normalize_ticker
from market_checker_app.utils.ticker_universe import load_canonical_tickers


PROFILE_PATH = Path(__file__).resolve().parents[1] / "data" / "research_profiles.json"


@dataclass(frozen=True, slots=True)
class ResearchProfile:
    code: str
    label: str
    tickers: tuple[str, ...]
    metrics: str
    sources: str


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    version: int
    profiles: tuple[ResearchProfile, ...]
    by_ticker: dict[str, ResearchProfile]
    source_only: tuple[str, ...]
    unmapped_input: tuple[str, ...]

    def for_ticker(self, ticker: str) -> ResearchProfile | None:
        """An absent profile means unresolved applicability, never a penalty."""
        return self.by_ticker.get(normalize_ticker(ticker))

    def applicability(self, ticker: str, profile_code: str) -> str:
        profile = self.for_ticker(ticker)
        if profile is None:
            return "UNKNOWN"
        return "APPLICABLE" if profile.code == profile_code else "NOT_APPLICABLE"


def load_research_profiles(path: Path = PROFILE_PATH) -> ProfileRegistry:
    """Validate the research taxonomy against the immutable production input.

    The research document has P where the actual source CSV has OKE. Keep that
    discrepancy visible. No provider alias or sector is inferred from it.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw["schema_version"] != 1 or len(raw["profiles"]) != 39:
        raise ValueError("Unsupported research profile catalogue")
    profiles = tuple(
        ResearchProfile(
            code=item["code"], label=item["label"],
            tickers=tuple(item["tickers"]), metrics=item["metrics"],
            sources=item["sources"],
        )
        for item in raw["profiles"]
    )
    if len({profile.code for profile in profiles}) != len(profiles):
        raise ValueError("Duplicate profile codes")
    by_ticker: dict[str, ResearchProfile] = {}
    for profile in profiles:
        for ticker in profile.tickers:
            if normalize_ticker(ticker) != ticker or ticker in by_ticker:
                raise ValueError(f"Duplicate or malformed research ticker: {ticker}")
            by_ticker[ticker] = profile
    production = set(load_canonical_tickers())
    source_only = tuple(sorted(set(by_ticker) - production))
    unmapped_input = tuple(sorted(production - set(by_ticker)))
    if source_only != tuple(raw["source_only_tickers"]):
        raise ValueError("Research-only ticker discrepancy changed")
    if unmapped_input != tuple(raw["unmapped_input_tickers"]):
        raise ValueError("Production ticker discrepancy changed")
    return ProfileRegistry(raw["schema_version"], profiles, by_ticker,
                           source_only, unmapped_input)
