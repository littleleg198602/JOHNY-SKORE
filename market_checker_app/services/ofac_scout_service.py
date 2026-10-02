"""Exact-name SDN candidates. This is not comprehensive sanctions clearance."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.storage.scout_store import ScoutStore


SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
ALT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"
CONSOLIDATED_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_PRIM.CSV"
CONSOLIDATED_ALT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ALT.CSV"
DOWNLOAD_HOSTS = {"sanctionslistservice.ofac.treas.gov",
                  "wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com"}


class _OfacRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != "https" or target.hostname not in DOWNLOAD_HOSTS
                or target.port not in {None, 443} or target.username or target.password):
            raise ValueError("OFAC export redirected outside its observed official delivery hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class OfacSnapshot:
    entities: tuple[dict, ...]
    content_sha256: str
    total_rows: int
    record_ids: frozenset[str] = frozenset()
    alias_content_sha256: str = ""
    alias_total_rows: int = 0


def _csv_text(body: bytes, *, byte_budget: int) -> str:
    if not body or len(body) > byte_budget:
        raise ValueError("OFAC export exceeds byte budget or is empty")
    text = body.decode("utf-8-sig").rstrip("\r\n")
    # The publisher's legacy files can end with DOS EOF, but nowhere else.
    if text.endswith("\x1a"):
        text = text[:-1]
    if "\x1a" in text:
        raise ValueError("Embedded EOF in OFAC export")
    return text


def parse_sdn_csv(body: bytes) -> OfacSnapshot:
    entities, seen = [], set()
    total = 0
    text = _csv_text(body, byte_budget=8_000_000)
    for row in csv.reader(io.StringIO(text), strict=True):
        total += 1
        if total > 100_000 or len(row) != 12:
            raise ValueError("Malformed OFAC CSV export")
        entity_id, name, kind, programs = (value.strip() for value in row[:4])
        if (not re.fullmatch(r"[1-9]\d{0,11}", entity_id) or entity_id in seen
                or not name or len(name) > 1000):
            raise ValueError("OFAC export has invalid or duplicate identifiers")
        seen.add(entity_id)
        # The official legacy flat-file type -0- denotes an entity. Other
        # record types (individuals, vessels, aircraft) are not issuer names.
        if kind != "-0-":
            if kind.casefold() not in {"individual", "vessel", "aircraft"}:
                raise ValueError("Unknown OFAC entry type")
            continue
        entities.append({"sdn_id": entity_id, "name": name, "programs": programs})
    if not entities:
        raise ValueError("OFAC snapshot contains no entity records")
    return OfacSnapshot(tuple(entities), hashlib.sha256(body).hexdigest(), total, frozenset(seen))


def attach_sdn_aliases(snapshot: OfacSnapshot, body: bytes) -> OfacSnapshot:
    """Join publisher ALT.CSV by ENT_NUM, never by name or row position.

    Weak aliases in primary remarks, addresses and Non-SDN are outside this
    contract. Unknown parents fail closed: the exports may have changed during
    download. Separate fingerprints do not assert an atomic publisher release.
    """
    text = _csv_text(body, byte_budget=2_000_000)
    by_id = {entity["sdn_id"]: {**entity, "aliases": []} for entity in snapshot.entities}
    seen, total = set(), 0
    for row in csv.reader(io.StringIO(text), strict=True):
        total += 1
        if total > 100_000 or len(row) != 5:
            raise ValueError("Malformed OFAC alias export")
        parent, alias_id, kind, name, remarks = (value.strip() for value in row)
        if (not re.fullmatch(r"[1-9]\d{0,11}", parent)
                or not re.fullmatch(r"[1-9]\d{0,11}", alias_id)
                or alias_id in seen or parent not in snapshot.record_ids
                or kind not in {"aka", "fka", "nka"}
                or not name or name == "-0-" or len(name) > 350 or len(remarks) > 200):
            raise ValueError("Invalid, duplicate or orphan OFAC alias")
        seen.add(alias_id)
        # Validate every row, but do not retain individuals/vessels/aircraft.
        if parent in by_id:
            by_id[parent]["aliases"].append({"alias_id": alias_id, "alias_type": kind,
                                             "name": name, "remarks": remarks})
    if not total:
        raise ValueError("OFAC alias export contains no rows")
    return OfacSnapshot(tuple(by_id.values()), snapshot.content_sha256, snapshot.total_rows,
                        snapshot.record_ids, hashlib.sha256(body).hexdigest(), total)


class OfacSdnClient:
    def _download(self, url: str, *, byte_budget: int) -> bytes:
        opener = build_opener(_OfacRedirects())
        request = Request(url, headers={"Accept": "text/csv", "User-Agent": "JohnySkoreScout/1.0"})
        with opener.open(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname not in DOWNLOAD_HOSTS:
                raise ValueError("OFAC response left its approved export hosts")
            body = response.read(byte_budget + 1)
            if not body or len(body) > byte_budget:
                raise ValueError("OFAC export exceeds byte budget or is empty")
            return body

    def snapshot(self) -> OfacSnapshot:
        primary = parse_sdn_csv(self._download(SDN_URL, byte_budget=8_000_000))
        return attach_sdn_aliases(primary, self._download(ALT_URL, byte_budget=2_000_000))


def parse_consolidated_csv(body: bytes) -> OfacSnapshot:
    """The publisher uses the same 12-field contract for the Non-SDN list.

    Program tags and remarks describe different restrictions; a Non-SDN name
    match must never imply SDN blocking or comprehensive sanctions clearance.
    """
    text = _csv_text(body, byte_budget=1_000_000)
    primary = parse_sdn_csv(body)
    remarks = {row[0].strip(): row[11].strip() for row in csv.reader(io.StringIO(text), strict=True)}
    entities = tuple({**entity, "remarks": remarks[entity["sdn_id"]]} for entity in primary.entities)
    return OfacSnapshot(entities, primary.content_sha256, primary.total_rows, primary.record_ids)


class OfacConsolidatedClient(OfacSdnClient):
    def snapshot(self) -> OfacSnapshot:
        primary = parse_consolidated_csv(self._download(CONSOLIDATED_URL, byte_budget=1_000_000))
        return attach_sdn_aliases(primary, self._download(CONSOLIDATED_ALT_URL, byte_budget=1_000_000))


class OfacSdnScoutService:
    source = "ofac"
    primary_url, aliases_url = SDN_URL, ALT_URL
    list_kind, id_field = "SDN", "sdn_id"
    stage = "ofac_sdn_exact_name_candidate"
    scope = "SDN primary and ALT entity names only; no weak aliases, Non-SDN or ownership look-through"

    def __init__(self, store: ScoutStore, *, client: OfacSdnClient):
        self.store, self.client = store, client

    def run(self, *, as_of: datetime | None = None, universe: set[str] | None = None):
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("OFAC observation needs timezone")
        due = self.store.specialist_due(self.source, as_of=clock, limit=900, refresh_days=1)
        if universe is not None:
            due = [row for row in due if row["subject_id"] in universe]
        if not due:
            active = self.store.specialist_coverage(self.source, as_of=clock, subjects=universe)["active_identities"]
            return {"status": "WAIT_IDENTITY" if not active else "OK", "checked_issuers": 0,
                    "new_findings": 0}
        try:
            snapshot = self.client.snapshot()
        except (OSError, ValueError, UnicodeError, csv.Error):
            # A failed download is never a negative screening result.
            return {"status": "ERROR", "checked_issuers": 0, "new_findings": 0,
                    "error": "OFAC_EXPORT_UNAVAILABLE_OR_INVALID"}
        by_name = {}
        for entity in snapshot.entities:
            names = [{"name": entity["name"], "name_type": "primary"}]
            names.extend({**alias, "name_type": "alternate"} for alias in entity.get("aliases", []))
            for name in names:
                key = name["name"].strip().casefold()
                by_name.setdefault(key, {}).setdefault(entity["sdn_id"], (entity, []))[1].append(name)
        checked = created = 0
        for issuer in due:
            matches = by_name.get(issuer["company_name"].strip().casefold(), {})
            for entity, matched_names in matches.values():
                details = {"stage": self.stage,
                           self.id_field: entity["sdn_id"], "name": entity["name"], "programs": entity["programs"],
                           "matched_names": sorted(matched_names, key=lambda row: (row["name_type"], row.get("alias_id", ""))),
                           "query_issuer_name": issuer["company_name"], "issuer_sec_cik": issuer["cik"],
                           "issuer_identity_verified": False, "scoring_applied": False,
                           "scope": self.scope,
                           "snapshot_source": self.primary_url, "alias_snapshot_source": self.aliases_url}
                if self.source == "ofac_non_sdn":
                    details.update(list_kind=self.list_kind, remarks=entity.get("remarks", ""),
                                   blocking_status_determined=False,
                                   restrictions_require_program_review=True)
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                details["snapshot_content_sha256"] = snapshot.content_sha256
                details["alias_snapshot_content_sha256"] = snapshot.alias_content_sha256
                _, fresh = self.store.record_finding(
                    source=self.source, subject_id=issuer["subject_id"], source_object_id=entity["sdn_id"],
                    content_hash=digest, title=f"OFAC {self.list_kind} name candidate: {entity['name']}",
                    source_url=self.primary_url, locator=f"{self.id_field.upper()}:{entity['sdn_id']}",
                    published_at=clock, available_at=clock, observed_at=clock,
                    details=details, verification_status="UNVERIFIED")
                created += int(fresh)
            self.store.record_specialist_check(self.source, subject_id=issuer["subject_id"],
                                               identity_key=f"{issuer['cik']}:{issuer['company_name']}",
                                               as_of=clock, candidate_count=len(matches),
                                               truncated=not bool(snapshot.alias_content_sha256))
            checked += 1
        return {"status": "OK" if snapshot.alias_content_sha256 else "PARTIAL",
                "checked_issuers": checked, "new_findings": created,
                "snapshot_sha256": snapshot.content_sha256,
                "alias_snapshot_sha256": snapshot.alias_content_sha256,
                "alias_rows": snapshot.alias_total_rows,
                "matched_rows": sum(len(by_name.get(i["company_name"].strip().casefold(), [])) for i in due)}


class OfacConsolidatedScoutService(OfacSdnScoutService):
    source = "ofac_non_sdn"
    primary_url, aliases_url = CONSOLIDATED_URL, CONSOLIDATED_ALT_URL
    list_kind, id_field = "NON_SDN", "non_sdn_id"
    stage = "ofac_non_sdn_exact_name_candidate"
    scope = "Consolidated Non-SDN primary and ALT entity names; program-specific restrictions, no weak aliases or ownership look-through"
