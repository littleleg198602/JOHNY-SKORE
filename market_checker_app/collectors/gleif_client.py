from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import json
from typing import Any, Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import zlib

from market_checker_app.utils.entity_identifiers import (
    normalize_country_code,
    normalize_isin,
    normalize_lei,
)


GLEIF_API_ROOT = "https://api.gleif.org/api/v1"


class GleifError(RuntimeError):
    """Raised when an exact GLEIF lookup cannot be safely normalized."""


@dataclass(frozen=True, slots=True)
class GleifIdentity:
    lei: str
    legal_name: str
    country_code: str | None
    jurisdiction: str | None
    registered_as: str | None
    registration_status: str | None
    entity_status: str | None
    parent_lei: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    isins: tuple[str, ...] = field(default_factory=tuple)
    source_url: str = ""


JsonTransport = Callable[[str, dict[str, str], float], dict[str, Any]]


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()
        encoding = str(response.headers.get("Content-Encoding", "")).lower()
        if encoding == "gzip":
            payload = gzip.decompress(payload)
        elif encoding == "deflate":
            payload = zlib.decompress(payload)
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise GleifError("GLEIF endpoint did not return a JSON object")
    return decoded


def _text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


class GleifClient:
    """Exact-identifier client for GLEIF; it deliberately never fuzzy-matches names."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        user_agent: str = "JohnySkore/2.1 entity-registry",
        transport: JsonTransport | None = None,
    ) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.user_agent = str(user_agent or "").strip() or "JohnySkore/2.1"
        self._transport = transport or _default_transport

    def _request(self, url: str) -> dict[str, Any]:
        return self._transport(
            url,
            {
                "User-Agent": self.user_agent,
                "Accept": "application/vnd.api+json",
                "Accept-Encoding": "gzip, deflate",
            },
            self.timeout_seconds,
        )

    @staticmethod
    def _data_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    @staticmethod
    def _mapped_isins(payload: dict[str, Any]) -> tuple[str, ...]:
        values: list[object] = []
        for item in GleifClient._data_items(payload):
            attributes = item.get("attributes")
            if isinstance(attributes, dict):
                for key in ("isin", "isins"):
                    raw = attributes.get(key)
                    if isinstance(raw, list):
                        values.extend(raw)
                    elif raw is not None and raw != "":
                        values.append(raw)
            item_id = _text(item.get("id"))
            item_type = str(item.get("type") or "").lower()
            if item_id and "isin" in item_type:
                values.append(item_id)
        normalized: list[str] = []
        for value in values:
            try:
                isin = normalize_isin(value)
            except ValueError:
                continue
            if isin and isin not in normalized:
                normalized.append(isin)
        return tuple(normalized)

    @staticmethod
    def _parse_identity(
        item: dict[str, Any],
        *,
        source_url: str,
        mapped_isins: tuple[str, ...] = (),
    ) -> GleifIdentity:
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            raise GleifError("GLEIF record has no attributes")
        lei = normalize_lei(attributes.get("lei") or item.get("id"))
        if lei is None:
            raise GleifError("GLEIF record has no valid LEI")
        entity = attributes.get("entity")
        entity = entity if isinstance(entity, dict) else {}
        legal_name_value = entity.get("legalName")
        legal_name_data = (
            legal_name_value if isinstance(legal_name_value, dict) else {}
        )
        legal_name = _text(legal_name_data.get("name"))
        if legal_name is None:
            raise GleifError(f"GLEIF record {lei} has no legal name")
        aliases: list[str] = []
        other_names = entity.get("otherNames")
        if isinstance(other_names, list):
            for raw_name in other_names:
                if not isinstance(raw_name, dict):
                    continue
                name = _text(raw_name.get("name"))
                if name and name != legal_name and name not in aliases:
                    aliases.append(name)
        legal_address = entity.get("legalAddress")
        legal_address = legal_address if isinstance(legal_address, dict) else {}
        country_code = normalize_country_code(legal_address.get("country"))
        registration = attributes.get("registration")
        registration = registration if isinstance(registration, dict) else {}
        parent_lei: str | None = None
        relationships = item.get("relationships")
        if isinstance(relationships, dict):
            for key in ("direct-parent", "direct-parent-relationship"):
                raw_relationship = relationships.get(key)
                if not isinstance(raw_relationship, dict):
                    continue
                raw_data = raw_relationship.get("data")
                if isinstance(raw_data, dict):
                    try:
                        parent_lei = normalize_lei(raw_data.get("id"))
                    except ValueError:
                        parent_lei = None
                if parent_lei:
                    break
        return GleifIdentity(
            lei=lei,
            legal_name=legal_name,
            country_code=country_code,
            jurisdiction=_text(entity.get("jurisdiction")),
            registered_as=_text(entity.get("registeredAs")),
            registration_status=_text(registration.get("status")),
            entity_status=_text(entity.get("status")),
            parent_lei=parent_lei,
            aliases=tuple(aliases),
            isins=mapped_isins,
            source_url=source_url,
        )

    def resolve(
        self,
        *,
        lei: str | None = None,
        isin: str | None = None,
    ) -> GleifIdentity | None:
        normalized_lei = normalize_lei(lei)
        normalized_isin = normalize_isin(isin)
        if normalized_lei is None and normalized_isin is None:
            return None

        if normalized_lei is not None:
            source_url = f"{GLEIF_API_ROOT}/lei-records/{quote(normalized_lei)}"
            items = self._data_items(self._request(source_url))
            if len(items) != 1:
                raise GleifError(
                    f"Exact LEI lookup {normalized_lei} returned {len(items)} records"
                )
            isin_url = f"{source_url}/isins"
            mapped_isins: tuple[str, ...] = ()
            try:
                mapped_isins = self._mapped_isins(self._request(isin_url))
            except Exception:
                # The LEI record itself remains authoritative even when an
                # optional mapping endpoint is temporarily unavailable.
                mapped_isins = ()
            return self._parse_identity(
                items[0],
                source_url=source_url,
                mapped_isins=mapped_isins,
            )

        query = urlencode({"filter[isin]": normalized_isin})
        source_url = f"{GLEIF_API_ROOT}/lei-records?{query}"
        items = self._data_items(self._request(source_url))
        if not items:
            return None
        if len(items) != 1:
            raise GleifError(
                f"Exact ISIN lookup {normalized_isin} is ambiguous: {len(items)} records"
            )
        return self._parse_identity(
            items[0],
            source_url=source_url,
            mapped_isins=(normalized_isin,) if normalized_isin else (),
        )
