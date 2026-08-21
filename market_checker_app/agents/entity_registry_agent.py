from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import AgentContext, AgentResult, EntityRecord
from market_checker_app.utils.symbols import normalize_yahoo_symbol
from market_checker_app.utils.entity_identifiers import (
    normalize_cik,
    normalize_country_code,
    normalize_isin,
    normalize_lei,
    normalize_mic,
)
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.text import normalize_ticker


def _optional_text(value: object, *, uppercase: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.upper() if uppercase else text


def _utc_datetime(value: object, label: str) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aliases(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        raise ValueError("Entity aliases must be a string or a sequence of strings")
    return list(
        dict.fromkeys(
            text
            for item in values
            if (text := str(item).strip())
        )
    )


class EntityRegistryAgent(BaseAgent):
    name = "entity_registry"
    version = "2.0"
    required = True

    def __init__(
        self,
        identity_records: Mapping[str, EntityRecord | Mapping[str, Any]] | None = None,
    ) -> None:
        self._identity_records = dict(identity_records or {})

    @staticmethod
    def _normalized_records(
        records: Mapping[str, EntityRecord | Mapping[str, Any]],
    ) -> dict[str, EntityRecord | Mapping[str, Any]]:
        normalized: dict[str, EntityRecord | Mapping[str, Any]] = {}
        for raw_ticker, record in records.items():
            ticker = normalize_ticker(raw_ticker)
            if not ticker:
                raise ValueError(f"Invalid identity registry ticker: {raw_ticker!r}")
            if ticker in normalized and normalized[ticker] != record:
                raise ValueError(f"Conflicting identity records for {ticker}")
            normalized[ticker] = record
        return normalized

    @staticmethod
    def _record_from_mapping(
        ticker: str,
        raw: Mapping[str, Any],
        observed_aliases: list[str],
    ) -> EntityRecord:
        declared_ticker = normalize_ticker(raw.get("ticker"))
        if declared_ticker and declared_ticker != ticker:
            raise ValueError(
                f"Identity record ticker {declared_ticker!r} does not match {ticker}"
            )
        cik = normalize_cik(raw.get("cik"))
        isin = normalize_isin(raw.get("isin"))
        lei = normalize_lei(raw.get("lei"))
        mic = normalize_mic(raw.get("mic"))
        country_code = normalize_country_code(raw.get("country_code"))
        source_url_value = _optional_text(raw.get("source_url"))
        source_url = (
            public_https_reference(source_url_value)
            if source_url_value is not None
            else None
        )
        legal_entity_id = _optional_text(raw.get("legal_entity_id"))
        if legal_entity_id is None:
            if lei is not None:
                legal_entity_id = f"lei:{lei}"
            elif cik is not None:
                legal_entity_id = f"cik:{cik}"
        issuer_id = _optional_text(raw.get("issuer_id")) or legal_entity_id
        instrument_id = _optional_text(raw.get("instrument_id"))
        if instrument_id is None:
            if isin is not None:
                instrument_id = f"isin:{isin}"
            elif mic is not None:
                instrument_id = f"mic:{mic}:ticker:{ticker}"
            else:
                instrument_id = f"ticker:{ticker}"
        metadata_value = raw.get("metadata")
        if metadata_value is None:
            metadata: dict[str, Any] = {}
        elif isinstance(metadata_value, Mapping):
            metadata = dict(metadata_value)
        else:
            raise ValueError("Entity metadata must be a mapping")
        metadata.update(
            {
                "registry_stage": "5.1",
                "identity_resolution": (
                    "RESOLVED"
                    if legal_entity_id is not None and instrument_id is not None
                    else "PARTIAL"
                ),
            }
        )
        return EntityRecord(
            entity_id=_optional_text(raw.get("entity_id")) or f"ticker:{ticker}",
            ticker=ticker,
            yahoo_ticker=(
                _optional_text(raw.get("yahoo_ticker"))
                or normalize_yahoo_symbol(ticker)
            ),
            name=_optional_text(raw.get("name")),
            exchange=_optional_text(raw.get("exchange")),
            cik=cik,
            isin=isin,
            lei=lei,
            sector=_optional_text(raw.get("sector")),
            industry=_optional_text(raw.get("industry")),
            aliases=list(
                dict.fromkeys(observed_aliases + _aliases(raw.get("aliases")))
            ),
            source=_optional_text(raw.get("source")) or "entity_registry_manifest",
            metadata=metadata,
            legal_entity_id=legal_entity_id,
            issuer_id=issuer_id,
            instrument_id=instrument_id,
            parent_entity_id=_optional_text(raw.get("parent_entity_id")),
            security_type=_optional_text(raw.get("security_type"), uppercase=True),
            share_class=_optional_text(raw.get("share_class")),
            mic=mic,
            country_code=country_code,
            valid_from=_utc_datetime(raw.get("valid_from"), "valid_from"),
            valid_to=_utc_datetime(raw.get("valid_to"), "valid_to"),
            source_url=source_url,
            confidence=float(raw.get("confidence", 1.0)),
        )

    @classmethod
    def _build_record(
        cls,
        ticker: str,
        raw: EntityRecord | Mapping[str, Any] | None,
        observed_aliases: list[str],
    ) -> EntityRecord:
        if raw is None:
            return EntityRecord(
                entity_id=f"ticker:{ticker}",
                ticker=ticker,
                yahoo_ticker=normalize_yahoo_symbol(ticker),
                instrument_id=f"ticker:{ticker}",
                aliases=observed_aliases,
                confidence=0.0,
                metadata={
                    "registry_stage": "5.1",
                    "identity_resolution": "UNRESOLVED",
                },
            )
        if isinstance(raw, EntityRecord):
            record_ticker = normalize_ticker(raw.ticker)
            if record_ticker != ticker:
                raise ValueError(
                    f"Identity record ticker {raw.ticker!r} does not match {ticker}"
                )
            source_url = (
                public_https_reference(raw.source_url)
                if raw.source_url is not None
                else None
            )
            metadata = dict(raw.metadata)
            metadata.setdefault("registry_stage", "5.1")
            metadata.setdefault(
                "identity_resolution",
                "RESOLVED"
                if raw.legal_entity_id and raw.instrument_id
                else "PARTIAL",
            )
            return replace(
                raw,
                ticker=ticker,
                yahoo_ticker=raw.yahoo_ticker or normalize_yahoo_symbol(ticker),
                cik=normalize_cik(raw.cik),
                isin=normalize_isin(raw.isin),
                lei=normalize_lei(raw.lei),
                mic=normalize_mic(raw.mic),
                country_code=normalize_country_code(raw.country_code),
                aliases=list(
                    dict.fromkeys(observed_aliases + list(raw.aliases))
                ),
                source_url=source_url,
                metadata=metadata,
            )
        if not isinstance(raw, Mapping):
            raise ValueError(f"Unsupported identity record for {ticker}")
        return cls._record_from_mapping(ticker, raw, observed_aliases)

    def run(self, context: AgentContext) -> AgentResult:
        configured = self._normalized_records(self._identity_records)
        state_records = context.state.get("entity_identity_by_ticker", {})
        if state_records is not None and not isinstance(state_records, Mapping):
            raise ValueError("entity_identity_by_ticker must be a mapping")
        dynamic = self._normalized_records(dict(state_records or {}))
        for ticker, record in dynamic.items():
            if ticker in configured and configured[ticker] != record:
                raise ValueError(
                    f"Conflicting configured and runtime identity records for {ticker}"
                )
            configured[ticker] = record

        ordered_tickers: list[str] = []
        aliases_by_ticker: dict[str, list[str]] = {}

        for raw_ticker in context.watchlist:
            ticker = normalize_ticker(raw_ticker)
            if not ticker:
                continue
            aliases = aliases_by_ticker.setdefault(ticker, [])
            raw_clean = str(raw_ticker).strip()
            if raw_clean and raw_clean != ticker and raw_clean not in aliases:
                aliases.append(raw_clean)
            if ticker not in ordered_tickers:
                ordered_tickers.append(ticker)

        entities = [
            self._build_record(
                ticker,
                configured.get(ticker),
                aliases_by_ticker[ticker],
            )
            for ticker in ordered_tickers
        ]

        by_id: dict[str, EntityRecord] = {}
        for entity in entities:
            existing = by_id.get(entity.entity_id)
            if existing is not None and existing.ticker != entity.ticker:
                raise ValueError(
                    f"Entity ID {entity.entity_id!r} maps to both "
                    f"{existing.ticker} and {entity.ticker}"
                )
            by_id[entity.entity_id] = entity

        by_ticker = {entity.ticker: entity for entity in entities}
        by_legal_entity: dict[str, list[EntityRecord]] = {}
        for entity in entities:
            if entity.legal_entity_id:
                by_legal_entity.setdefault(entity.legal_entity_id, []).append(entity)
        resolved = sum(
            1
            for entity in entities
            if entity.legal_entity_id and entity.instrument_id
        )
        return AgentResult(
            entities=entities,
            metadata={
                "unique_entities": len(entities),
                "resolved_identities": resolved,
                "unresolved_identities": len(entities) - resolved,
            },
            state_updates={
                "entities_by_ticker": by_ticker,
                "entities_by_id": by_id,
                "entities_by_legal_entity_id": by_legal_entity,
            },
        )
