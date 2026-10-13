"""No-network replay of the exact archived ClinicalTrials requests and scope."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import gzip
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from market_checker_app.services.healthcare_identity_service import (
    HealthcareIdentityRegistry, clinical_trial_version,
)
from market_checker_app.services.healthcare_scout_service import (
    ClinicalTrialsClient, HealthcareNameScoutService, _candidate, _identity_details,
)
from market_checker_app.storage.scout_store import ScoutStore


ROOT = Path(__file__).resolve().parent
STUDY_IDS = ("NCT03987919", "NCT04847557")
ISSUER = {"subject_id": "LLY", "cik": "0000059478", "company_name": "Eli Lilly and Company"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def capture(key, root=ROOT):
    meta = read_json(root / "captures" / f"{key}.metadata.json")
    if not isinstance(meta["body_path"], str) or Path(meta["body_path"]).name != meta["body_path"]:
        raise ValueError("Invalid capture path")
    archive = (root / "captures" / meta["body_path"]).read_bytes()
    raw = gzip.decompress(archive) if meta["storage_encoding"] == "gzip" else archive
    if (hashlib.sha256(raw).hexdigest() != meta["body_sha256"]
            or hashlib.sha256(archive).hexdigest() != meta["archive_sha256"]
            or len(raw) != meta["body_bytes"] or len(archive) != meta["archived_bytes"]
            or len(raw) > meta["byte_budget"] or not meta["body_complete_within_byte_budget"]):
        raise ValueError("Capture integrity/budget failure")
    if meta["http_status"] != 200:
        raise ValueError("A denied capture cannot prove a source relationship")
    requested, response = urlsplit(meta["request_url"]), urlsplit(meta["response_url"])
    if (requested.scheme != "https" or response.scheme != "https"
            or requested.hostname not in {"clinicaltrials.gov", "www.sec.gov", "data.sec.gov"}
            or requested.hostname != response.hostname):
        raise ValueError("Capture publisher mismatch")
    start, end = map(datetime.fromisoformat, (meta["requested_at"], meta["observed_at"]))
    if start.utcoffset() is None or end.utcoffset() is None or start > end:
        raise ValueError("Capture observation clocks are invalid")
    return meta, raw


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def normalized_html(raw):
    parser = Text()
    parser.feed(raw.decode("utf-8"))
    return " ".join(" ".join(parser.parts).split())


class Response(BytesIO):
    def __init__(self, raw, url):
        super().__init__(raw)
        self.url = url

    def geturl(self):
        return self.url


class ReplayTransport:
    def __init__(self, root=ROOT):
        self.root, self.calls = root, []
        self.queries = []
        for entry in read_json(root / "capture_index.json")["captures"]:
            parsed = urlsplit(entry["request_url"])
            if parsed.hostname == "clinicaltrials.gov" and parsed.path == "/api/v2/studies":
                self.queries.append((entry["capture_key"], parse_qs(parsed.query)))

    def __call__(self, request, *, timeout):
        parsed = urlsplit(request.full_url)
        if parsed.scheme != "https" or parsed.hostname != "clinicaltrials.gov" or parsed.path != "/api/v2/studies" or timeout != 20:
            raise ValueError("Replay attempted another source request")
        matches = [key for key, query in self.queries if query == parse_qs(parsed.query)]
        if len(matches) != 1:
            raise ValueError("Replay request does not match one archived request")
        meta, raw = capture(matches[0], self.root)
        self.calls.append(matches[0])
        return Response(raw, meta["response_url"])


def build_replay_report(root=ROOT):
    manifest = read_json(root / "identity_proposal.json")
    registry = HealthcareIdentityRegistry.load(root / "identity_proposal.json")
    clock = datetime.fromisoformat(manifest["reviewed_at"]) + timedelta(seconds=1)
    index = read_json(root / "capture_index.json")
    verified = []
    for entry in index["captures"]:
        meta, raw = capture(entry["capture_key"], root)
        verified.append({"capture_key": meta["capture_key"], "body_sha256": meta["body_sha256"],
                         "body_bytes": len(raw), "observed_at": meta["observed_at"]})
    transport = ReplayTransport(root)
    client = ClinicalTrialsClient(study_ids=STUDY_IDS)
    cursor, rows, seen = None, [], set()
    with patch("market_checker_app.services.healthcare_scout_service.urlopen", transport):
        for page in range(4):
            batch, token = client.page(ISSUER["company_name"], size=1, cursor=cursor)
            rows.extend(batch)
            if token is None:
                break
            if token in seen:
                raise ValueError("Replay cursor repeated")
            seen.add(token)
            cursor = token
        else:
            raise ValueError("Replay traversal exceeded page budget")
        negative, token = client.page("Pfizer", size=2)
    positive_pages = [key for key in transport.calls if key.startswith("trial_lilly_positive_page_")]
    meta, raw = capture(positive_pages[0], root)
    total = read_json(root / "captures" / meta["body_path"])["totalCount"]
    if len(rows) != total or {clinical_trial_version(row)["nct_id"] for row in rows} != set(STUDY_IDS):
        raise ValueError("Archived traversal does not prove its bounded count")
    if negative or token is not None:
        raise ValueError("Archived negative query is not complete")
    objects = []
    for row in rows:
        candidate = _candidate("clinicaltrials", row, ISSUER["company_name"], clock)
        if candidate is None:
            raise ValueError("Real row failed sponsor/version validation")
        _, detail = candidate
        result = _identity_details(registry, "clinicaltrials", detail, ISSUER, clock)
        event = date.fromisoformat(detail["last_update_posted"])
        record = next(record for record in registry.records()
                      if record["identifiers"]["nct_id"] == detail["nct_id"])
        args = {"source": "clinicaltrials", "kind": "clinical_trial_sponsor", "ticker": "LLY",
                "cik": ISSUER["cik"], "entity_name": ISSUER["company_name"],
                "identifiers": record["identifiers"], "event_date": event}
        changed = deepcopy(row)
        changed["protocolSection"]["sponsorCollaboratorsModule"]["collaborators"] = [{"name": "Unmapped collaborator"}]
        _, changed_detail = _candidate("clinicaltrials", changed, ISSUER["company_name"], clock)
        changed_result = _identity_details(registry, "clinicaltrials", changed_detail, ISSUER, clock)
        objects.append({"nct_id": detail["nct_id"], "publisher_record_version": detail["publisher_record_version"],
                        "identity_resolution": result["identity_resolution"],
                        "attributed_intervention_names": result.get("attributed_intervention_names", []),
                        "unattributed_intervention_names": result.get("unattributed_intervention_names", []),
                        "relationship_snapshot_pinned": result.get("relationship_snapshot_pinned", False),
                        "before_knowledge_resolution": registry.resolve(
                            as_of=datetime.fromisoformat(record["known_at"]) - timedelta(microseconds=1),
                            **args)["identity_resolution"],
                        "same_day_changed_relationship_resolution": changed_result["identity_resolution"],
                        "current_event_resolution": registry.resolve(
                            as_of=clock, **dict(args, event_date=clock.date()))["identity_resolution"],
                        "sponsor_role_establishes_product_ownership": False})
    with TemporaryDirectory() as temporary:
        store = ScoutStore(Path(temporary) / "replay.db")
        store.observe_sec_identity(subject_id="LLY", cik=ISSUER["cik"],
                                   company_name=ISSUER["company_name"], as_of=clock)
        collector = HealthcareNameScoutService(store, client=client, identities=registry,
                                                max_subjects=1, page_size=1, max_pages=4)
        with patch("market_checker_app.services.healthcare_scout_service.urlopen", ReplayTransport(root)):
            first = collector.run(as_of=clock, universe={"LLY"})
        with patch("market_checker_app.services.healthcare_scout_service.urlopen", ReplayTransport(root)):
            replay = collector.run(as_of=clock + timedelta(days=1), universe={"LLY"})
        versions = registry.identity_versions(store, "clinicaltrials", as_of=clock + timedelta(days=1))
        coverage = store.specialist_coverage("clinicaltrials", as_of=clock + timedelta(days=1),
                                            subjects={"LLY"}, identity_versions=versions)
        before = store.latest_findings(["LLY"], as_of=clock - timedelta(microseconds=1), source="clinicaltrials")
        with store._connect() as conn:
            findings = [dict(row) for row in conn.execute(
                "SELECT verification_status,available_at,first_observed_at,details_json FROM scout_findings")]
    return {"schema_version": 1, "replay_as_of": clock.isoformat(), "network_requests": 0,
            "verified_captures": verified,
            "bounded_positive_query": {"study_ids": list(STUDY_IDS), "pages": len(positive_pages),
                                       "publisher_total": total, "returned_count": len(rows), "complete": True},
            "bounded_negative_query": {"study_ids": list(STUDY_IDS), "sponsor_query": "Pfizer",
                                       "returned_count": 0, "http_status": 200, "complete": True},
            "issuer_wide_completeness": False, "objects": objects,
            "first_collector_run": first, "idempotent_replay": replay, "broad_source_coverage": coverage,
            "finding_count": len(findings), "finding_statuses": [row["verification_status"] for row in findings],
            "findings_available_before_observation": len(before),
            "scoring_applied": any(json.loads(row["details_json"])["scoring_applied"] for row in findings),
            "historical_source_availability_established": False, "OOS_verified": False,
            "user_windows_run_verified": False, "full_specialist_acceptance": False}


if __name__ == "__main__":
    print(json.dumps(build_replay_report(), indent=2))
