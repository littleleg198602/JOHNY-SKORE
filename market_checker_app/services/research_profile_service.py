from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from market_checker_app.utils.text import normalize_ticker
from market_checker_app.utils.ticker_universe import load_canonical_tickers


PROFILE_PATH = Path(__file__).resolve().parents[1] / "data" / "research_profiles.json"
RULE_CATALOGUE_VERSION = 1


@dataclass(frozen=True, slots=True)
class ResearchRule:
    """A research question, not a verified fact or a score contribution."""

    rule_id: str
    profile_code: str
    kind: str
    description: str


def _rules(code: str, kind: str, source: str) -> tuple[ResearchRule, ...]:
    descriptions = [part.strip() for part in source.split(";")]
    if not descriptions or any(not part for part in descriptions):
        raise ValueError(f"Empty {kind} rule in {code}")
    return tuple(
        ResearchRule(
            rule_id=(f"research-v{RULE_CATALOGUE_VERSION}:{code}:{kind}:"
                     f"{position}:{hashlib.sha256(description.encode('utf-8')).hexdigest()[:12]}"),
            profile_code=code, kind=kind, description=description,
        )
        for position, description in enumerate(descriptions, start=1)
    )


@dataclass(frozen=True, slots=True)
class ResearchProfile:
    code: str
    label: str
    tickers: tuple[str, ...]
    metrics: str
    sources: str

    @property
    def metric_rules(self) -> tuple[ResearchRule, ...]:
        return _rules(self.code, "metric", self.metrics)

    @property
    def source_rules(self) -> tuple[ResearchRule, ...]:
        return _rules(self.code, "source", self.sources)


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    version: int
    profiles: tuple[ResearchProfile, ...]
    by_ticker: dict[str, ResearchProfile]
    verified_overrides: dict[str, ResearchProfile]
    source_only: tuple[str, ...]
    unmapped_input: tuple[str, ...]

    def for_ticker(self, ticker: str) -> ResearchProfile | None:
        """An absent profile means unresolved applicability, never a penalty."""
        ticker = normalize_ticker(ticker)
        if ticker in self.source_only:
            return None
        return self.by_ticker.get(ticker) or self.verified_overrides.get(ticker)

    def applicability(self, ticker: str, profile_code: str) -> str:
        profile = self.for_ticker(ticker)
        if profile is None:
            return "UNKNOWN"
        return "APPLICABLE" if profile.code == profile_code else "NOT_APPLICABLE"

    def rule_status(self, ticker: str, rule_id: str) -> str:
        """Scope a proposed question without treating missing evidence as bad news.

        A matching profile only makes a question a candidate. Qualifiers such as
        'only for banking subsidiaries' still require entity-level evidence.
        """
        profile = self.for_ticker(ticker)
        if profile is None:
            return "UNKNOWN"
        rules = (rule for item in self.profiles
                 for rule in item.metric_rules + item.source_rules)
        selected = next((rule for rule in rules if rule.rule_id == rule_id), None)
        if selected is None:
            return "UNKNOWN"
        return ("CANDIDATE" if selected.profile_code == profile.code
                else "NOT_APPLICABLE")


def load_research_profiles(path: Path = PROFILE_PATH) -> ProfileRegistry:
    """Validate the research taxonomy against the immutable production input.

    The research document has P where the actual source CSV has OKE. Keep that
    discrepancy visible. The independently sourced OKE mapping is an explicit
    taxonomy override, not an alias for research-only P.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw["schema_version"] != 2 or len(raw["profiles"]) != 39:
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
    # Parse every profile into stable, typed questions at load time, including
    # qualifiers inside their descriptions. No phrase becomes a numeric score.
    for profile in profiles:
        if not profile.metric_rules or not profile.source_rules:
            raise ValueError(f"Profile {profile.code} has no research rules")
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
    by_code = {profile.code: profile for profile in profiles}
    verified_overrides: dict[str, ResearchProfile] = {}
    for ticker, record in raw["verified_profile_overrides"].items():
        if (ticker not in unmapped_input or ticker in by_ticker
                or record["profile"] not in by_code
                or not record["source_url"].startswith("https://www.oneok.com/")
                or not record["sec_10k_url"].startswith("https://www.sec.gov/Archives/")
                or not record["verified_at"]):
            raise ValueError(f"Invalid independently verified profile: {ticker}")
        verified_overrides[ticker] = by_code[record["profile"]]
    if set(verified_overrides) != set(unmapped_input):
        raise ValueError("Unresolved production profile discrepancy")
    return ProfileRegistry(raw["schema_version"], profiles, by_ticker,
                           verified_overrides, source_only, unmapped_input)
