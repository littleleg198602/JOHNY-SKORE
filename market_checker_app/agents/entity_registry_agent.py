from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from typing import Any, Protocol

from market_checker_app.agents.base import BaseAgent
from market_checker_app.agents.contracts import (
    AgentContext,
    AgentResult,
    AgentStatus,
    EntityRecord,
    IdentityConflictRecord,
    utc_now,
)
from market_checker_app.collectors.gleif_client import GleifIdentity
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
from market_checker_app.services.reviewed_identity_service import (
    ReviewedIdentityRegistry, apply_decision, clock,
)
from market_checker_app.services.issuer_catalog_service import IssuerCatalog
from market_checker_app.services.primary_listing_service import PrimaryListingRegistry
from market_checker_app.services.nasdaq_listing_service import NasdaqListingRegistry


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


def _dated_ticker_aliases(value: object, current_ticker: str) -> list[dict[str, str | None]]:
    """Accept only sourced, time-bounded ticker aliases for this instrument."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("dated_ticker_aliases must be a list")
    aliases: list[dict[str, str | None]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("A dated ticker alias must be a mapping")
        ticker = normalize_ticker(str(item.get("ticker") or ""))
        start = _utc_datetime(item.get("valid_from"), "alias valid_from")
        end = _utc_datetime(item.get("valid_to"), "alias valid_to")
        source = _optional_text(item.get("source_url"))
        if not ticker or ticker == current_ticker or start is None or source is None:
            raise ValueError("Alias needs a different ticker, valid_from and source_url")
        if end is not None and end <= start:
            raise ValueError("Alias valid_to must be later than valid_from")
        aliases.append({
            "ticker": ticker, "valid_from": start.isoformat(),
            "valid_to": end.isoformat() if end else None,
            "source_url": public_https_reference(source),
        })
    if len({(a["ticker"], a["valid_from"]) for a in aliases}) != len(aliases):
        raise ValueError("Duplicate dated ticker alias interval")
    return aliases


class PrimaryIdentityResolver(Protocol):
    def resolve(
        self,
        *,
        lei: str | None = None,
        isin: str | None = None,
    ) -> GleifIdentity | None: ...


def _stable_id(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class EntityRegistryAgent(BaseAgent):
    name = "entity_registry"
    version = "2.1"
    required = True

    def __init__(
        self,
        identity_records: Mapping[str, EntityRecord | Mapping[str, Any]] | None = None,
        *,
        primary_registry_client: PrimaryIdentityResolver | None = None,
        reviewed_registry: ReviewedIdentityRegistry | None = None,
        listing_registry: PrimaryListingRegistry | None = None,
    ) -> None:
        self._identity_records = dict(identity_records or {})
        self._primary_registry_client = primary_registry_client
        self._reviewed_registry = reviewed_registry
        self._listing_registry = listing_registry

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
        declared_ticker = normalize_ticker(raw.get("ticker") or "")
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
        if "dated_ticker_aliases" in metadata:
            raise ValueError("Use the validated dated_ticker_aliases identity field")
        metadata["dated_ticker_aliases"] = _dated_ticker_aliases(
            raw.get("dated_ticker_aliases"), ticker,
        )
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
            metadata["dated_ticker_aliases"] = _dated_ticker_aliases(
                metadata.get("dated_ticker_aliases"), ticker,
            )
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

    @staticmethod
    def _primary_conflicts(
        entity: EntityRecord,
        identity: GleifIdentity,
        *,
        observed_at: datetime,
    ) -> list[IdentityConflictRecord]:
        candidates: list[tuple[str, str, str]] = []
        if entity.lei and entity.lei != identity.lei:
            candidates.append(("lei", entity.lei, identity.lei))
        if entity.isin and identity.isins and entity.isin not in identity.isins:
            candidates.append(("isin", entity.isin, ",".join(identity.isins)))
        conflicts: list[IdentityConflictRecord] = []
        for field_name, existing_value, candidate_value in candidates:
            conflicts.append(
                IdentityConflictRecord(
                    conflict_id=_stable_id(
                        entity.entity_id,
                        field_name,
                        existing_value,
                        candidate_value,
                        identity.source_url,
                    ),
                    ticker=entity.ticker,
                    entity_id=entity.entity_id,
                    legal_entity_id=entity.legal_entity_id,
                    field_name=field_name,
                    existing_value=existing_value,
                    candidate_value=candidate_value,
                    existing_source=entity.source,
                    candidate_source="GLEIF Global LEI Index",
                    observed_at=observed_at,
                    existing_source_url=entity.source_url,
                    candidate_source_url=identity.source_url,
                    reason=(
                        "Primární identifikátory se neshodují; kandidát byl "
                        "oddělen do karantény a nesmí přepsat aktivní identitu."
                    ),
                    metadata={"automatic_resolution": False},
                )
            )
        return conflicts

    @staticmethod
    def _enrich_from_primary(
        entity: EntityRecord,
        identity: GleifIdentity,
    ) -> EntityRecord:
        selected_isin = entity.isin
        if selected_isin is None and len(identity.isins) == 1:
            selected_isin = identity.isins[0]
        legal_entity_id = f"lei:{identity.lei}"
        metadata = dict(entity.metadata)
        metadata.update(
            {
                "identity_resolution": "RESOLVED",
                "primary_registry": "GLEIF",
                "gleif_registration_status": identity.registration_status,
                "gleif_entity_status": identity.entity_status,
                "gleif_jurisdiction": identity.jurisdiction,
                "gleif_registered_as": identity.registered_as,
                "mapped_isin_count": len(identity.isins),
                "mapped_isin_ambiguous": (
                    entity.isin is None and len(identity.isins) > 1
                ),
            }
        )
        return replace(
            entity,
            name=identity.legal_name or entity.name,
            isin=selected_isin,
            lei=identity.lei,
            legal_entity_id=legal_entity_id,
            issuer_id=entity.issuer_id or legal_entity_id,
            instrument_id=(
                f"isin:{selected_isin}"
                if selected_isin
                else entity.instrument_id
            ),
            parent_entity_id=(
                f"lei:{identity.parent_lei}"
                if identity.parent_lei
                else entity.parent_entity_id
            ),
            country_code=identity.country_code or entity.country_code,
            aliases=list(
                dict.fromkeys(entity.aliases + list(identity.aliases))
            ),
            source="gleif_primary_registry",
            source_url=identity.source_url,
            confidence=max(entity.confidence, 1.0),
            metadata=metadata,
        )

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

        conflicts: list[IdentityConflictRecord] = []
        warnings: list[str] = []
        reviewed = self._reviewed_registry or ReviewedIdentityRegistry.load()
        catalog = IssuerCatalog.load(as_of=context.started_at)
        listings = self._listing_registry or PrimaryListingRegistry.load()
        exchange = NasdaqListingRegistry.load_optional()
        dated_entities = []
        for entity in entities:
            # Fill only unconfigured identities. An explicit user's registry
            # is never replaced by the present-day ticker catalog.
            if entity.ticker not in configured:
                binding = catalog.lookup(entity.ticker, as_of=context.started_at)
                if binding:
                    entity = replace(entity, cik=binding['cik'], name=binding['name'],
                        legal_entity_id='cik:'+binding['cik'], issuer_id='cik:'+binding['cik'],
                        source='sec_issuer_catalog', source_url=binding['source_url'],
                        valid_from=clock(binding['known_at']), confidence=0.75,
                        metadata={**entity.metadata, 'identity_resolution':'ISSUER_ONLY',
                            'known_at':binding['known_at'],'issuer_catalog_sha256':binding['source_sha256'],
                            'instrument_identity_verified':False})
            decision = reviewed.decision(entity.ticker, as_of=context.started_at)
            if decision and entity.cik not in {None, decision['previous_cik'], decision['cik']}:
                conflicts.append(IdentityConflictRecord(
                    conflict_id=_stable_id(entity.entity_id, entity.cik, decision['cik'], decision['id']),
                    ticker=entity.ticker, entity_id=entity.entity_id, field_name='cik',
                    existing_value=entity.cik, candidate_value=decision['cik'],
                    existing_source=entity.source, candidate_source='reviewed_primary_identity',
                    observed_at=context.started_at, candidate_source_url=decision['primary_url'],
                    reason='Configured CIK contradicts the reviewed primary identity.'))
                entity = replace(entity, metadata={**entity.metadata, 'identity_resolution':'QUARANTINED'})
            elif decision:
                entity = apply_decision(entity, decision)
            else:
                when = clock(context.started_at)
                unavailable = ((entity.valid_from and entity.valid_from > when)
                               or (entity.valid_to and entity.valid_to <= when)
                               or (entity.metadata.get('known_at') and clock(entity.metadata['known_at']) > when))
                if unavailable:
                    entity = replace(entity, cik=None, isin=None, lei=None,
                        legal_entity_id=None, issuer_id=None, instrument_id=None,
                        confidence=0.0, metadata={**entity.metadata, 'identity_resolution':'UNAVAILABLE_AT_ASOF'})
            # Explicit instrument registries remain authoritative. Reviewed
            # succession may replace the old issuer before selecting a class.
            if (entity.ticker not in configured
                    and entity.metadata.get('identity_resolution') not in {'QUARANTINED','INACTIVE'}):
                proof=listings.lookup(entity.ticker, symbol=entity.yahoo_ticker,
                    cik=entity.cik, as_of=context.started_at, catalog=catalog)
                if proof is None and exchange:
                    proof=exchange.lookup(entity.yahoo_ticker,cik=entity.cik,
                        as_of=context.started_at,catalog=catalog)
                if proof:
                    entity=replace(entity, share_class=proof['class_title'],
                        instrument_id=f"issuer:{entity.cik}:symbol:{proof['ticker']}:class:{_stable_id(proof['class_title'])}",
                        source=entity.source if decision else ('nasdaq_primary_listing' if proof.get('scope') else 'sec_primary_listing'),
                        source_url=entity.source_url if decision else proof['source_url'],
                        valid_from=(entity.valid_from if decision else
                            max(entity.valid_from or clock(proof['known_at']),clock(proof['known_at']))),
                        confidence=1.0,
                        metadata={**entity.metadata,'identity_resolution':'RESOLVED_AS_REPORTED',
                            'instrument_identity_verified':True,
                            'instrument_identifier_scope':proof.get('scope','AS_REPORTED_REGISTERED_CLASS')+'; no inferred CUSIP, ISIN or LEI',
                            'class_as_reported_at':proof['anchor_date'],
                            'class_known_at':proof['known_at'],
                            'class_source_sha256':proof['sha256'],
                            'class_source_url':proof['source_url'],
                            'class_snapshot_id':proof['snapshot_id'],
                            'class_context_ref':proof['context_ref'],
                            'class_context_source':proof.get('context_source'),
                            'known_at':max(clock(entity.metadata.get('known_at',proof['known_at'])),clock(proof['known_at'])).isoformat()})
            dated_entities.append(entity)
        entities = dated_entities
        primary_attempted = 0
        primary_resolved = 0
        lookup_failures = 0
        if self._primary_registry_client is not None:
            enriched: list[EntityRecord] = []
            for entity in entities:
                if (entity.metadata.get('instrument_status') == 'INACTIVE'
                        or entity.metadata.get('identity_resolution') == 'QUARANTINED'
                        or (not entity.lei and not entity.isin)):
                    enriched.append(entity)
                    continue
                primary_attempted += 1
                try:
                    identity = self._primary_registry_client.resolve(
                        lei=entity.lei,
                        isin=entity.isin,
                    )
                except Exception as exc:
                    lookup_failures += 1
                    warnings.append(
                        f"GLEIF {entity.ticker}: {type(exc).__name__}: {exc}"
                    )
                    enriched.append(entity)
                    continue
                if identity is None:
                    lookup_failures += 1
                    warnings.append(
                        f"GLEIF {entity.ticker}: přesný LEI/ISIN nebyl nalezen."
                    )
                    enriched.append(entity)
                    continue
                candidate_conflicts = self._primary_conflicts(
                    entity,
                    identity,
                    observed_at=utc_now(),
                )
                if candidate_conflicts:
                    conflicts.extend(candidate_conflicts)
                    metadata = dict(entity.metadata)
                    metadata["identity_resolution"] = "QUARANTINED"
                    enriched.append(replace(entity, metadata=metadata))
                    continue
                primary_resolved += 1
                enriched.append(self._enrich_from_primary(entity, identity))
            entities = enriched

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
            and entity.metadata.get('identity_resolution') not in {'QUARANTINED', 'INACTIVE', 'ISSUER_ONLY'}
        )
        unresolved_tickers = sorted(
            entity.ticker
            for entity in entities
            if not (entity.legal_entity_id and entity.instrument_id)
            or entity.metadata.get('identity_resolution') in {'QUARANTINED', 'INACTIVE', 'ISSUER_ONLY'}
        )
        return AgentResult(
            status=(
                AgentStatus.PARTIAL
                if conflicts or lookup_failures
                else AgentStatus.SUCCESS
            ),
            entities=entities,
            identity_conflicts=conflicts,
            warnings=warnings,
            metadata={
                "unique_entities": len(entities),
                "resolved_identities": resolved,
                "unresolved_identities": len(entities) - resolved,
                "primary_registry_attempted": primary_attempted,
                "primary_registry_resolved": primary_resolved,
                "primary_registry_lookup_failures": lookup_failures,
                "quarantined_conflicts": len(conflicts),
            },
            state_updates={
                "entities_by_ticker": by_ticker,
                "entities_by_id": by_id,
                "entities_by_legal_entity_id": by_legal_entity,
                "identity_conflicts": conflicts,
                "unresolved_identity_tickers": unresolved_tickers,
                "quarantined_identity_tickers": sorted(
                    {item.ticker for item in conflicts}
                ),
            },
        )
