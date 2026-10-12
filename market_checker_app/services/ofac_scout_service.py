"""Exact-name SDN candidates. This is not comprehensive sanctions clearance."""
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
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
COMMENTS_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_COMMENTS.CSV"
CONSOLIDATED_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_PRIM.CSV"
CONSOLIDATED_ALT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ALT.CSV"
CONSOLIDATED_COMMENTS_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_COMMENTS.CSV"
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
    comments_content_sha256: str = ""
    comments_total_rows: int = 0
    comments_status: str = "NOT_FETCHED"


def _remarks_evidence(primary: str, spillover: str = "") -> dict:
    # Publisher spillover starts at character 1001, including mid-word. Do not
    # strip either part or insert a separator (OFAC's legacy-file tutorial).
    remarks = primary + spillover
    # FAQ 123 documents quoted a.k.a. text as weak aliases. Retain just that
    # explicit form for corroboration; arbitrary quoted prose is not a name.
    # This narrow extractor does not claim to cover every publisher format.
    pattern = r"(?:^|;\s*)a\.k\.a\.\s+(['\"])(.+?)\1(?=\s*(?:;|\.|$))"
    weak_names = []
    seen = set()
    for match in re.finditer(pattern, remarks, flags=re.DOTALL):
        name = match.group(2)
        if (not name.strip() or len(name) > 350 or "\n" in name or "\r" in name
                or name.casefold() in seen):
            continue
        seen.add(name.casefold())
        weak_names.append({"name": name, "alias_quality": "weak",
                           "source_field": "remarks", "screening_eligible": False})
    return {"remarks": remarks, "remarks_primary": primary,
            "remarks_spillover": spillover, "weak_aliases": weak_names}


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
                or not name or len(name) > 1000 or len(row[11]) > 1000):
            raise ValueError("OFAC export has invalid or duplicate identifiers")
        seen.add(entity_id)
        # The official legacy flat-file type -0- denotes an entity. Other
        # record types (individuals, vessels, aircraft) are not issuer names.
        if kind != "-0-":
            if kind.casefold() not in {"individual", "vessel", "aircraft"}:
                raise ValueError("Unknown OFAC entry type")
            continue
        entities.append({"sdn_id": entity_id, "name": name, "programs": programs,
                         **_remarks_evidence("" if row[11] == "-0-" else row[11])})
    if not entities:
        raise ValueError("OFAC snapshot contains no entity records")
    return OfacSnapshot(tuple(entities), hashlib.sha256(body).hexdigest(), total, frozenset(seen))


def attach_sdn_aliases(snapshot: OfacSnapshot, body: bytes) -> OfacSnapshot:
    """Join publisher ALT.CSV by ENT_NUM, never by name or row position.

    Weak aliases stay in remarks and are never promoted into this name index.
    Unknown parents fail closed: the exports may have changed during
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
    return replace(snapshot, entities=tuple(by_id.values()),
                   alias_content_sha256=hashlib.sha256(body).hexdigest(), alias_total_rows=total)


def attach_sdn_comments(snapshot: OfacSnapshot, body: bytes) -> OfacSnapshot:
    """Reconstruct primary remarks by exact ENT_NUM, never by name/order.

    SDN_COMMENTS/CONS_COMMENTS have one spillover per publisher record. Reject
    the entire supplement on duplicate/orphan/malformed rows, so independent
    exports changing during download cannot silently produce full coverage.
    The primary and ALT snapshot remains usable separately.
    """
    text = _csv_text(body, byte_budget=2_000_000)
    by_id = {entity["sdn_id"]: dict(entity) for entity in snapshot.entities}
    seen, total = set(), 0
    for row in csv.reader(io.StringIO(text), strict=True):
        total += 1
        if total > 100_000 or len(row) != 2:
            raise ValueError("Malformed OFAC remarks supplement")
        parent, spillover = row
        parent = parent.strip()
        if (not re.fullmatch(r"[1-9]\d{0,11}", parent) or parent in seen
                or parent not in snapshot.record_ids or not spillover.strip()
                or spillover == "-0-" or len(spillover) > 100_000):
            raise ValueError("Invalid, duplicate or orphan OFAC remarks supplement")
        seen.add(parent)
        # Individuals/vessels/aircraft are validated but do not become issuers.
        if parent in by_id:
            primary = by_id[parent].get("remarks_primary", by_id[parent].get("remarks", ""))
            # A supplement for an untruncated primary record is inconsistent.
            if len(primary) != 1000:
                raise ValueError("OFAC supplement requires a truncated primary remarks field")
            by_id[parent].update(_remarks_evidence(primary, spillover))
    return replace(snapshot, entities=tuple(by_id.values()),
                   comments_content_sha256=hashlib.sha256(body).hexdigest(),
                   comments_total_rows=total, comments_status="COMPLETE")


class OfacSdnClient:
    comments_url = COMMENTS_URL

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
        snapshot = attach_sdn_aliases(primary, self._download(ALT_URL, byte_budget=2_000_000))
        return self._attach_comments(snapshot)

    def _attach_comments(self, snapshot: OfacSnapshot) -> OfacSnapshot:
        try:
            return attach_sdn_comments(snapshot, self._download(self.comments_url, byte_budget=2_000_000))
        except (OSError, ValueError, UnicodeError, csv.Error):
            # Do not erase successful primary/ALT observations or represent the
            # missing supplement as an empty, complete set of remarks.
            return replace(snapshot, comments_status="UNAVAILABLE_OR_INVALID")


def parse_consolidated_csv(body: bytes) -> OfacSnapshot:
    """The publisher uses the same 12-field contract for the Non-SDN list.

    Program tags and remarks describe different restrictions; a Non-SDN name
    match must never imply SDN blocking or comprehensive sanctions clearance.
    """
    _csv_text(body, byte_budget=1_000_000)
    return parse_sdn_csv(body)


class OfacConsolidatedClient(OfacSdnClient):
    comments_url = CONSOLIDATED_COMMENTS_URL

    def snapshot(self) -> OfacSnapshot:
        primary = parse_consolidated_csv(self._download(CONSOLIDATED_URL, byte_budget=1_000_000))
        snapshot = attach_sdn_aliases(primary, self._download(CONSOLIDATED_ALT_URL, byte_budget=1_000_000))
        return self._attach_comments(snapshot)


class OfacSdnScoutService:
    source = "ofac"
    primary_url, aliases_url, comments_url = SDN_URL, ALT_URL, COMMENTS_URL
    list_kind, id_field = "SDN", "sdn_id"
    stage = "ofac_sdn_exact_name_candidate"
    scope = ("SDN primary and ALT entity names; remarks and weak aliases are corroboration only; "
             "no address verification, Non-SDN or ownership look-through")

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
        remarks_complete = (snapshot.comments_status == "COMPLETE"
                            and bool(snapshot.comments_content_sha256))
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
                           "remarks": entity.get("remarks", ""),
                           "remarks_primary": entity.get("remarks_primary", entity.get("remarks", "")),
                           "remarks_spillover": entity.get("remarks_spillover", ""),
                           "weak_aliases": entity.get("weak_aliases", []),
                           "weak_aliases_screened": False,
                           "weak_alias_extraction_scope": "explicit quoted a.k.a. text in publisher remarks only",
                           "weak_alias_extraction_complete": False,
                           "publisher_remarks_complete": remarks_complete,
                           "remarks_supplement_status": snapshot.comments_status,
                           "ownership_look_through_performed": False,
                           "publication_time_verified": False,
                           "snapshot_release_atomicity_verified": False,
                           "scope": self.scope,
                           "snapshot_source": self.primary_url, "alias_snapshot_source": self.aliases_url,
                           "comments_snapshot_source": self.comments_url}
                if self.source == "ofac_non_sdn":
                    details.update(list_kind=self.list_kind,
                                   blocking_status_determined=False,
                                   restrictions_require_program_review=True)
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                details["snapshot_content_sha256"] = snapshot.content_sha256
                details["alias_snapshot_content_sha256"] = snapshot.alias_content_sha256
                details["comments_snapshot_content_sha256"] = snapshot.comments_content_sha256
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
                                               truncated=not bool(snapshot.alias_content_sha256) or not remarks_complete)
            checked += 1
        return {"status": "OK" if snapshot.alias_content_sha256 and remarks_complete else "PARTIAL",
                "checked_issuers": checked, "new_findings": created,
                "snapshot_sha256": snapshot.content_sha256,
                "alias_snapshot_sha256": snapshot.alias_content_sha256,
                "alias_rows": snapshot.alias_total_rows,
                "comments_snapshot_sha256": snapshot.comments_content_sha256,
                "comments_rows": snapshot.comments_total_rows,
                "remarks_supplement_status": snapshot.comments_status,
                "publisher_remarks_complete": remarks_complete,
                "weak_aliases_screened": False,
                "weak_alias_extraction_complete": False,
                "ownership_look_through_performed": False,
                "publication_time_verified": False,
                "snapshot_release_atomicity_verified": False,
                "matched_rows": sum(len(by_name.get(i["company_name"].strip().casefold(), [])) for i in due)}


class OfacConsolidatedScoutService(OfacSdnScoutService):
    source = "ofac_non_sdn"
    primary_url, aliases_url, comments_url = CONSOLIDATED_URL, CONSOLIDATED_ALT_URL, CONSOLIDATED_COMMENTS_URL
    list_kind, id_field = "NON_SDN", "non_sdn_id"
    stage = "ofac_non_sdn_exact_name_candidate"
    scope = ("Consolidated Non-SDN primary and ALT entity names; program-specific restrictions; "
             "remarks and weak aliases are corroboration only; no address verification or ownership look-through")
