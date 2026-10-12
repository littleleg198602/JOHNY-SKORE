"""Offline replay of the archived primary healthcare responses.

This validates proposals against received bytes and the existing object resolver.
It does not fetch sources, write runtime checks, or certify issuer-wide coverage.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from market_checker_app.services.fda_recall_scout_service import (
    FdaRecallScoutService, OpenFdaRecallClient,
)
from market_checker_app.services.healthcare_coverage_service import (
    build_healthcare_coverage_report,
)
from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, normalize_application, resolve_fda_crl,
)


EVIDENCE_DIR = Path(__file__).resolve().parent
QUERY_CAPTURES = (
    "fda_crl_pfizer_exact_phrase", "fda_crl_merck_exact_phrase",
    "fda_crl_abbvie_exact_phrase", "fda_crl_abbvie_partial",
    "fda_crl_pfizer_2026_negative", "fda_crl_abbvie_2023_positive_control",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def capture(key: str, directory: Path = EVIDENCE_DIR) -> tuple[dict, bytes]:
    meta = read_json(directory / "captures" / f"{key}.metadata.json")
    archive = (directory / "captures" / meta["body_path"]).read_bytes()
    raw = gzip.decompress(archive) if meta["storage_encoding"] == "gzip" else archive
    if (hashlib.sha256(archive).hexdigest() != meta["archive_sha256"]
            or hashlib.sha256(raw).hexdigest() != meta["body_sha256"]
            or len(raw) != meta["body_bytes"]
            or len(archive) != meta["archived_bytes"]):
        raise ValueError(f"Archive integrity mismatch: {key}")
    if len(raw) > meta["byte_budget"] or not meta["body_complete_within_byte_budget"]:
        raise ValueError(f"Capture exceeds its byte budget: {key}")
    return meta, raw


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, value: str) -> None:
        self.parts.append(value)


def normalized_html(raw: bytes) -> str:
    parser = _Text()
    parser.feed(raw.decode("utf-8"))
    return " ".join(" ".join(parser.parts).split())


class _Response(BytesIO):
    def __init__(self, raw: bytes, url: str) -> None:
        super().__init__(raw)
        self.url = url

    def geturl(self) -> str:
        return self.url


def replay_fda_capture(key: str, directory: Path = EVIDENCE_DIR) -> dict:
    """Feed the real body/status to the existing client's HTTP/JSON handling."""
    meta, raw = capture(key, directory)
    parsed = urlsplit(meta["request_url"])
    if parsed.hostname != "api.fda.gov" or parsed.path != "/transparency/crl.json":
        raise ValueError("Replay is limited to the archived FDA CRL endpoint")
    query = parse_qs(parsed.query, keep_blank_values=True)

    def transport(request, *, timeout):
        requested = urlsplit(request.full_url)
        actual_query = parse_qs(requested.query, keep_blank_values=True)
        actual_query.setdefault("skip", ["0"])
        expected_query = dict(query)
        expected_query.setdefault("skip", ["0"])
        if ((requested.scheme, requested.hostname, requested.path)
                != (parsed.scheme, parsed.hostname, parsed.path)
                or actual_query != expected_query or timeout != 20):
            raise ValueError("Replay request differs from the captured request")
        if meta["http_status"] != 200:
            raise HTTPError(meta["response_url"], meta["http_status"],
                            "Archived primary response", {}, BytesIO(raw))
        return _Response(raw, meta["response_url"])

    with patch("market_checker_app.services.fda_recall_scout_service.urlopen", transport):
        payload = OpenFdaRecallClient()._query(
            "transparency/crl.json", query["search"][0],
            int(query["limit"][0]), int(query.get("skip", ["0"])[0]))
    limit = int(query["limit"][0])
    pages = FdaRecallScoutService(None, client=None, max_results=limit, max_pages=1,
                                  identities=HealthcareIdentityRegistry([]))
    rows, partial = pages._pages(lambda offset: deepcopy(payload),
                                 deadline=time.monotonic() + 5)
    status = ("PARTIAL_QUERY_POSITIVE" if partial else
              "COMPLETE_QUERY_POSITIVE" if rows else "COMPLETE_QUERY_NO_MATCH")
    return {
        "capture_key": key, "publisher": "FDA openFDA CRL dataset",
        "request_url": meta["request_url"], "search": query["search"][0],
        "http_status": meta["http_status"], "observed_at": meta["observed_at"],
        "original_body_sha256": meta["body_sha256"],
        "response_meta_last_updated": payload.get("meta", {}).get("last_updated"),
        "page_size": limit, "pages_replayed": 1, "skip": 0,
        "returned_count": len(rows), "publisher_query_total": payload["meta"]["results"]["total"],
        "partial": partial, "status": status, "issuer_wide_scope": False,
        "completeness_scope": "Only this exact search expression in this published CRL dataset snapshot.",
        "response": payload,
    }


def resolve_record(registry: HealthcareIdentityRegistry, row: dict, *, event_date: date,
                   as_of: datetime, cik: str | None = None, identifiers: dict | None = None) -> dict:
    return registry.resolve(source=row["source"], kind=row["kind"], ticker=row["ticker"],
                            cik=cik or row["issuer_cik"], entity_name=row["entity_name"],
                            identifiers=identifiers or row["identifiers"],
                            event_date=event_date, as_of=as_of)


def build_replay_report(directory: Path = EVIDENCE_DIR) -> dict:
    """Return bounded validation evidence without changing a production registry."""
    manifest = read_json(directory / "identity_proposal.json")
    review = read_json(directory / "source_review.json")
    index = read_json(directory / "capture_index.json")
    registry = HealthcareIdentityRegistry.load(directory / "identity_proposal.json")
    records = registry.records()
    if (manifest.get("proposal_status") != "REVIEWED_PROPOSAL"
            or manifest.get("record_count") != len(records)
            or manifest.get("distinct_issuer_count") != len({r["ticker"] for r in records})
            or manifest.get("distinct_application_count")
            != len({r["identifiers"]["application_number"] for r in records})):
        raise ValueError("Proposal review/count metadata mismatch")
    verified_captures = []
    for entry in index["captures"]:
        meta, raw = capture(entry["capture_key"], directory)
        if (meta["body_sha256"] != entry["body_sha256"]
                or meta["archive_sha256"] != entry["archive_sha256"]):
            raise ValueError("Capture index hash mismatch")
        verified_captures.append({"capture_key": entry["capture_key"],
                                  "http_status": meta["http_status"],
                                  "original_body_sha256": meta["body_sha256"],
                                  "body_bytes": len(raw)})
    for source in review["source_reviews"]:
        meta, raw = capture(source["capture_key"], directory)
        text = normalized_html(raw)
        if meta["http_status"] != 200 or meta["body_sha256"] != source["original_body_sha256"]:
            raise ValueError("SEC issuer evidence is not a complete archived success")
        for excerpt in source["excerpts"]:
            if text[excerpt["normalized_text_start"]:excerpt["normalized_text_end"]] != excerpt["text"]:
                raise ValueError("SEC reviewed excerpt differs from its archived body")
    clock = max(datetime.fromisoformat(row["known_at"]) for row in records) + timedelta(seconds=1)
    review_by_id = {row["record_id"]: row for row in review["record_reviews"]}
    events = []
    for record in records:
        reviewed = review_by_id[record["record_id"]]
        meta, raw = capture(reviewed["primary_capture_key"], directory)
        pointer = reviewed["source_record_json_pointer"]
        position = int(pointer.removeprefix("/results/"))
        source_row = json.loads(raw)["results"][position]
        signed = datetime.strptime(source_row["letter_date"], "%m/%d/%Y").date()
        normalized_text = " ".join(source_row["text"].split())
        if (meta["body_sha256"] != reviewed["primary_body_sha256"]
                or source_row["company_name"] != record["entity_name"]
                or source_row["file_name"] != record["identifiers"]["letter_file_name"]
                or source_row["letter_type"] != "COMPLETE RESPONSE"
                or [normalize_application(v) for v in source_row["application_number"]]
                != [record["identifiers"]["application_number"]]
                or signed.isoformat() != record["identifiers"]["letter_date"]
                or record["product_or_provider"] not in normalized_text):
            raise ValueError("Proposal object does not match its archived FDA row")
        matched = resolve_fda_crl(registry, ticker=record["ticker"], cik=record["issuer_cik"],
                                  row=source_row, signed=signed, as_of=clock)
        before_known = resolve_record(registry, record, event_date=signed,
                                       as_of=datetime.fromisoformat(record["known_at"]) - timedelta(microseconds=1))
        expired = resolve_record(registry, record,
                                  event_date=date.fromisoformat(record["effective_to"]), as_of=clock)
        current = resolve_record(registry, record, event_date=clock.date(), as_of=clock)
        wrong_issuer = resolve_record(registry, record, event_date=signed, as_of=clock,
                                      cik="0000000001")
        identifiers = dict(record["identifiers"], letter_file_name="different-letter.pdf")
        wrong_letter = resolve_record(registry, record, event_date=signed, as_of=clock,
                                      identifiers=identifiers)
        events.append({
            "record_id": record["record_id"], "ticker": record["ticker"],
            "application_number": record["identifiers"]["application_number"],
            "letter_date": signed.isoformat(), "effective_from": record["effective_from"],
            "effective_to": record["effective_to"], "known_at": record["known_at"],
            "primary_capture_key": reviewed["primary_capture_key"],
            "source_record_json_pointer": pointer, "primary_body_sha256": meta["body_sha256"],
            "object_resolution": matched["identity_resolution"],
            "product_attribution_allowed_at_event": matched["product_attribution_allowed"],
            "before_known_resolution": before_known["identity_resolution"],
            "exclusive_end_resolution": expired["identity_resolution"],
            "current_event_resolution": current["identity_resolution"],
            "wrong_issuer_resolution": wrong_issuer["identity_resolution"],
            "wrong_letter_resolution": wrong_letter["identity_resolution"],
            "ownership_history_established": False,
        })
    queries = []
    for key in QUERY_CAPTURES:
        query = replay_fda_capture(key, directory)
        query.pop("response")
        queries.append(query)
    diagnostic = build_healthcare_coverage_report(
        identities_path=directory / "identity_proposal.json", as_of=clock)
    fda = diagnostic["sources"]["fda"]
    return {
        "schema_version": 1, "report_type": "archived_healthcare_proposal_replay",
        "generated_at": datetime.now(timezone.utc).isoformat(), "replay_as_of": clock.isoformat(),
        "network_requests": 0, "runtime_store_provided": False,
        "production_registry_mutated": False, "verified_capture_count": len(verified_captures),
        "verified_captures": verified_captures, "event_replays": events, "queries": queries,
        "proposal_coverage_diagnostic": {
            "identity_manifest": diagnostic["identity_manifest"], "runtime": diagnostic["runtime"],
            "fda": {key: fda[key] for key in (
                "documented_issuer_object_subjects", "mapped_subjects", "mapped_objects",
                "queried_issuers", "current_complete_source_checks", "current_partial_source_checks",
                "issuer_wide_complete_checks", "negative_issuer_completeness_verified",
                "object_status_counts", "expired_interval_records", "historical_event_only_records")},
            "acceptance": diagnostic["acceptance"],
        },
        "limitations": [
            "Five direct-issuer letter events, four applications, three canonical issuers only.",
            "One-day effective intervals bound each event and are not actual ownership begin/end dates.",
            "Letter dates are not first publication dates; actual capture and review clocks govern availability.",
            "FDA may release archive batches and does not expose a complete issuer-wide inventory.",
            "The letter_year filter is exercised by the real 2023 positive control but is absent from the captured public searchable-fields page.",
            "Pfizer Ireland BLA761184 is withheld because a c/o address is insufficient dated ownership evidence.",
            "An optional SEC index request returned 403; it was not retried or used as identity evidence.",
            "This is an offline byte replay, not a runtime source check, historical impact evaluation, or Windows acceptance.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(build_replay_report(), indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
