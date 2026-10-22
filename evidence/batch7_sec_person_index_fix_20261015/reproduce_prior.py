"""Reproduce both rejected invariants using exact archived prior source bytes."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from evidence.batch7_sec_20261015.build_review import EVENT_DAYS, load_inputs

ROOT = Path(__file__).resolve().parent


def reproduce():
    pins = json.loads((ROOT / "original_source_pins.json").read_bytes())
    for pin in pins["pins"].values():
        raw = (ROOT / pin["archived_as"]).read_bytes()
        if len(raw) != pin["bytes"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise ValueError("Preserved prior source/test bytes changed")
    spec = importlib.util.spec_from_file_location("prior_person_kind_index_reproduction", ROOT / "original/sec_person_kind_review.py")
    prior = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prior; spec.loader.exec_module(prior)
    descriptor, account, fact, primary, index, _ = load_inputs()
    def original_type(capture):
        fields = capture.summary(); fields["observed_at"] = capture.observed_at; fields["known_at"] = capture.known_at
        return prior.PersonKindCapture(body=capture.body, **fields)
    account, primary, index = [original_type(item) for item in (account, primary, index)]
    clock = max(item.available_at for item in (account, primary, index))
    def review(candidate, days):
        return prior.review_sec_person_kind(account_descriptor=descriptor, account_capture=account,
            person_fact=fact, primary_capture=primary, index_capture=candidate,
            knowledge_at=clock, requested_event_days=days)
    wrong_name = replace(index, source_url=index.source_url.replace("0001193125-25-245150-index.html", "0001193125-25-245151-index.html"))
    wrong_name_result = review(wrong_name, EVENT_DAYS)
    future_body = index.body.replace(b"2025-10-21", b"2099-10-21")
    future = replace(index, body=future_body, sha256=hashlib.sha256(future_body).hexdigest(), size_bytes=len(future_body))
    future_result = review(future, ("2099-10-21",))
    return {"prior_commit": pins["commit"], "fixture_kind": "SYNTHETIC_INDEX_MUTATIONS_OF_PINNED_ACTUAL_CAPTURE",
        "original_capture_http_requests_performed": 0, "source_originals_or_old_evidence_modified": False,
        "wrong_basename": {"index_url": wrong_name.source_url,
            "expected_invariant": "Index basename accession equals original accession directory",
            "prior_natural_person_fact_supported": wrong_name_result["natural_person_fact_supported"],
            "invariant_violated": wrong_name_result["natural_person_fact_supported"]},
        "future_filing_day": {"synthetic_index_sha256": future.sha256, "original_observed_at": future.observed_at.isoformat(),
            "expected_invariant": "Index filing day cannot be after original capture or knowledge",
            "prior_reported_filing_day": future_result["dated_person_fact"]["source_document_filing_day"],
            "prior_natural_person_fact_supported": future_result["natural_person_fact_supported"],
            "prior_requested_future_point_matched": future_result["requested_events"][0]["matches_disclosure_point_day"],
            "invariant_violated": future_result["natural_person_fact_supported"]},
        "invariant_violations": sum((wrong_name_result["natural_person_fact_supported"], future_result["natural_person_fact_supported"])),
        "person_cik_identity_was_verified": False}


if __name__ == "__main__":
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(), **reproduce()}
    with (ROOT / "prior_failed_invariants.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"reproduced_prior_invariant_violations": result["invariant_violations"], "http_requests": 0}))
