"""Zero-HTTP original-byte DOW co-filer regression; no issuer registry export."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from market_checker_app.services.sec_corporate_action_bridge import _IssuerNameParser, issuer_names
from market_checker_app.services.sec13f_identity_bulk import MAX_SOURCE_BYTES
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEG_COMMIT = "e869fb60b12fcc850e5d2a51e73a3fa3b726377d"
MODULE = "market_checker_app/services/sec_corporate_action_bridge.py"
SOURCE_FOLDER = Path("evidence/batch6_regulatory_20261014/raw")
DOC = "sec_dow_2025_10k.html"
INDEX = "sec_dow_2025_10k_index.html"
PINNED = {DOC: "7df203af3404625494916fe8685535eb3548c6b4c1bc8a6c04a6c7ed7c291122",
          INDEX: "94dfcc4f3e55b4eed10b83f2934a791aca769acc3377162567a088eeb0bb04d2"}
URL = "https://www.sec.gov/Archives/edgar/data/1751788/000175178826000018/dow-20251231.htm"


@dataclass(frozen=True)
class RetainedFilingView:
    """Evidence-only complete 6 MB-approved capture; bulk's 4 MB cap is unchanged."""
    url: str
    observed_at: datetime
    payload: bytes
    published_on: date
    published_at: None = None


def original_sources(source_dir=None):
    if source_dir is None:
        source_dir = ROOT / SOURCE_FOLDER
        if not source_dir.exists():
            source_dir = ROOT.with_name("JOHNY-batch6-regulatory-20261014") / SOURCE_FOLDER
    source_dir = Path(source_dir)
    result, inventory = {}, []
    for name, digest in PINNED.items():
        path = source_dir / name
        metadata_path = path.with_suffix(".metadata.json")
        metadata = json.loads(metadata_path.read_bytes())
        if (metadata.get("status") != "CAPTURED" or metadata.get("public_raw_available") is not True
                or metadata.get("http_status") != 200 or metadata.get("requested_url") != metadata.get("final_url")):
            raise ValueError("WITHHELD or unsupported original capture cannot be read")
        raw = path.read_bytes()
        if (hashlib.sha256(raw).hexdigest() != digest or metadata["raw_response_sha256"] != digest
                or metadata["raw_response_bytes"] != len(raw) or not 0 < len(raw) <= metadata["max_bytes"]
                or _has_sensitive_body(raw)):
            raise ValueError("Original captured source failed its bounded hash/body gate")
        result[name] = metadata, raw
        for file in (path, metadata_path):
            body = file.read_bytes()
            inventory.append({"path": (SOURCE_FOLDER / file.name).as_posix(),
                              "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
    return result, inventory


def _index_value(raw, label):
    pattern = r'<div class="infoHead">\s*' + re.escape(label) + r'\s*</div>\s*<div class="info">\s*([^<]+)\s*</div>'
    values = re.findall(pattern, raw.decode("utf-8"))
    if len(values) != 1:
        raise ValueError("Missing or ambiguous original index " + label)
    return values[0].strip()


def before_fix(source):
    # Original code fixture makes the regression reproducible in a fresh
    # public checkout without depending on unpublished local Git ancestry.
    fixture = HERE / "original_issuer_binder.py"
    if hashlib.sha256(fixture.read_bytes()).hexdigest() != "d31314babd779bd7a522c08343537e012226261fa2b334da83fb9b21ea9e0043":
        raise ValueError("Original binder diagnostic bytes changed")
    from evidence.batch6_cofiler_20261014.original_issuer_binder import issuer_names as original_binder
    return original_binder(source), "6222a1734caf76b0f7bd5216c62665a3fc08268be991c8750352ab317508d627"


def build_report(source_dir=None):
    sources, inventory = original_sources(source_dir)
    receipt, raw = sources[DOC]
    index_receipt, index_raw = sources[INDEX]
    if receipt["requested_url"] != URL:
        raise ValueError("Wrong retained exact DOW primary URL")
    filed = _index_value(index_raw, "Filing Date")
    accepted = _index_value(index_raw, "Accepted")
    datetime.strptime(accepted, "%Y-%m-%d %H:%M:%S")  # Display only; no timezone supplied.
    source = RetainedFilingView(URL, datetime.fromisoformat(receipt["observed_at"]), raw, date.fromisoformat(filed))
    prior, old_code_hash = before_fix(source)
    current = issuer_names(source)
    parser = _IssuerNameParser()
    parser.feed(raw.decode("utf-8")); parser.close()
    contexts = []
    for fact in current:
        cid = fact["context_ref"]
        identity = parser.contexts[cid]
        dimensions = [{"axis": item["axis"], "member": identity[item["key"]][0]} for item in parser.identity_dimensions.get(cid, [])]
        contexts.append({"context_ref": cid, "context_identifiers": identity["xbrli:identifier"],
                         "explicit_same_context_dei_ciks": parser.facts[cid]["dei:entitycentralindexkey"],
                         "name": fact["issuer_name"], "resolved_cik": fact["issuer_cik"], "anchor_date": fact["anchor_date"],
                         "legal_entity_dimensions": dimensions, "member_label_used_to_infer_cik": False})
    assert len(current) == 2
    assert {(fact["issuer_name"], fact["issuer_cik"], fact["anchor_date"]) for fact in current} == {
        ("Dow Inc.", "0001751788", "2025-12-31"), ("The Dow Chemical Company", "0000029915", "2025-12-31")}
    report = {"schema_version": 1, "status": "COFILER_EXPLICIT_CIK_NAME_DATE_CORRECTED", "scope": "PRIMARY_CONTEXT_FACTS_ONLY",
              "source_inventory": inventory, "original_bytes_unchanged": True, "source_url": URL,
              "before_fix": {"code_commit": LEG_COMMIT, "module_sha256": old_code_hash, "facts": prior,
                             "binder_fixture_sha256": "d31314babd779bd7a522c08343537e012226261fa2b334da83fb9b21ea9e0043",
                             "historical_wrong_parent_join_is_not_acceptance": True},
              "after_fix": {"facts": current, "context_bindings": contexts},
              "publisher_clock": {"filed_on": filed, "accepted_display": accepted, "accepted_timezone": "UNKNOWN",
                                  "published_at_utc": None, "publication_clock_reconstructed": False},
              "capture_clocks": {"document_observed_at": receipt["observed_at"], "index_observed_at": index_receipt["observed_at"]},
              "knowledge_backdated_to_report_day": False, "bulk_max_source_bytes_unchanged": MAX_SOURCE_BYTES,
              "retained_research_capture_max_bytes": receipt["max_bytes"], "new_http_requests": 0,
              "production_registry_changed": False, "verified_identities_added": 0, "score_changed": False}
    report["review_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def qualify_xom_review():
    """A second actual co-filer correction supersedes a historical name join."""
    from evidence.batch5_identity_20261014.replay_bridge import build_inputs
    service, claim, current, _ = build_inputs()
    original_path = ROOT / "evidence/batch5_identity_20261014/review.json"
    original_raw = original_path.read_bytes()
    original = json.loads(original_raw)
    old_bridge, new_bridge = original["corporate_action_bridge_reviews"][0], current["corporate_action_bridge_reviews"][0]
    source = service.snapshots[claim["from_ticker_snapshot_id"]]
    pairs = issuer_names(source)
    assert {(fact["issuer_name"], fact["issuer_cik"]) for fact in pairs} == {
        ("Exxon Mobil Corporation", "0000034088"), ("ExxonMobil Holdings Corporation", "0002115436")}
    assert old_bridge["blockers"] == new_bridge["blockers"] == ["TO_CIK_CUSIP_CLASS_BINDING_MISSING",
                                                               "CONFLICTING_TO_CLASS_SOURCE", "DATED_OLD_NEW_CUSIP_ACTION_BRIDGE_MISSING"]
    report = {"schema_version": 1, "status": "HISTORICAL_XOM_PARENT_JOIN_SUPERSEDED_BY_EXPLICIT_CIK",
              "historical_report": {"path": original_path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(original_raw).hexdigest(),
                                    "review_sha256": original["review_sha256"], "not_current_acceptance": True},
              "historical_from_issuer_names": old_bridge["proofs"]["from"]["independent_issuer_names"],
              "current_from_issuer_names": new_bridge["proofs"]["from"]["independent_issuer_names"],
              "actual_same_context_pairs": pairs, "source": {**source.metadata(), "snapshot_id": source.snapshot_id},
              "unchanged_blockers": new_bridge["blockers"], "current_review": current,
              "new_http_requests": 0, "registry_changed": False, "score_changed": False,
              "same_context_date_is_not_corporate_action_effective_date": True,
              "obsolete_assertion": "tests.test_batch5_identity_symbol.SameCikSymbolBridgeTests.test_actual_retained_sources_are_partial_and_first_xom_artifacts_replay_unchanged",
              "expected_change": "Assert immutable historical bytes separately; compare current names to their independent explicit DEI CIK/date pairs"}
    report["qualification_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


if __name__ == "__main__":
    report = build_report()
    for name, value in (("paired_identity_review.json", report), ("xom_review_qualification.json", qualify_xom_review())):
        output = HERE / name
        raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
        if output.exists() and output.read_bytes() != raw:
            raise ValueError("Preserve the prior original-byte diagnostic before changing its version")
        if not output.exists():
            output.write_bytes(raw)
    print(json.dumps({"status": report["status"], "pairs": 2, "fresh_http": 0,
                      "replayed_at": datetime.now(timezone.utc).isoformat()}))
