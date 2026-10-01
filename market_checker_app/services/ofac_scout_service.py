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


def parse_sdn_csv(body: bytes) -> OfacSnapshot:
    if not body or len(body) > 8_000_000:
        raise ValueError("OFAC export exceeds byte budget or is empty")
    entities, seen = [], set()
    total = 0
    text = body.decode("utf-8-sig").rstrip("\r\n")
    # The live OFAC flat file has a trailing DOS EOF marker. Only accept it
    # at EOF; an embedded or otherwise malformed row remains an error.
    if text.endswith("\x1a"):
        text = text[:-1]
    for row in csv.reader(io.StringIO(text)):
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
    return OfacSnapshot(tuple(entities), hashlib.sha256(body).hexdigest(), total)


class OfacSdnClient:
    def snapshot(self) -> OfacSnapshot:
        opener = build_opener(_OfacRedirects())
        request = Request(SDN_URL, headers={"Accept": "text/csv", "User-Agent": "JohnySkoreScout/1.0"})
        with opener.open(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname not in DOWNLOAD_HOSTS:
                raise ValueError("OFAC response left its approved export hosts")
            return parse_sdn_csv(response.read(8_000_001))


class OfacSdnScoutService:
    def __init__(self, store: ScoutStore, *, client: OfacSdnClient):
        self.store, self.client = store, client

    def run(self, *, as_of: datetime | None = None, universe: set[str] | None = None):
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("OFAC observation needs timezone")
        due = self.store.specialist_due("ofac", as_of=clock, limit=900, refresh_days=1)
        if universe is not None:
            due = [row for row in due if row["subject_id"] in universe]
        if not due:
            active = self.store.specialist_coverage("ofac", as_of=clock, subjects=universe)["active_identities"]
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
            by_name.setdefault(entity["name"].strip().casefold(), []).append(entity)
        checked = created = 0
        for issuer in due:
            matches = by_name.get(issuer["company_name"].strip().casefold(), [])
            for entity in matches:
                details = {"stage": "ofac_sdn_exact_name_candidate", **entity,
                           "query_issuer_name": issuer["company_name"], "issuer_sec_cik": issuer["cik"],
                           "issuer_identity_verified": False, "scoring_applied": False,
                           "scope": "SDN primary entity name only; no aliases, Non-SDN or ownership look-through",
                           "snapshot_source": SDN_URL}
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                details["snapshot_content_sha256"] = snapshot.content_sha256
                _, fresh = self.store.record_finding(
                    source="ofac", subject_id=issuer["subject_id"], source_object_id=entity["sdn_id"],
                    content_hash=digest, title=f"OFAC SDN name candidate: {entity['name']}",
                    source_url=SDN_URL, locator=f"SDN_ID:{entity['sdn_id']}",
                    published_at=clock, available_at=clock, observed_at=clock,
                    details=details, verification_status="UNVERIFIED")
                created += int(fresh)
            self.store.record_specialist_check("ofac", subject_id=issuer["subject_id"],
                                               identity_key=f"{issuer['cik']}:{issuer['company_name']}",
                                               as_of=clock, candidate_count=len(matches), truncated=False)
            checked += 1
        return {"status": "OK", "checked_issuers": checked, "new_findings": created,
                "snapshot_sha256": snapshot.content_sha256,
                "matched_rows": sum(len(by_name.get(i["company_name"].strip().casefold(), [])) for i in due)}
