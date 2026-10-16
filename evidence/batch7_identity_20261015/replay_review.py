"""Exact retained-source LEG/SGI control and dated tuple review; zero HTTP."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from evidence.batch4_identity_20261013.replay_review import captures as prior_captures
from evidence.batch6_identity_20261014.replay_review import build_inputs as leg_inputs, new_captures as leg_captures
from market_checker_app.services.sec13f_identity_bulk import Sec13fIdentityBulk, SourceSnapshot
from market_checker_app.services.sec_control_instrument_review import control_text, review_control_instrument_bridge

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEG_METADATA = "https://data.sec.gov/submissions/CIK0000058492.json"
SGI_METADATA = "https://data.sec.gov/submissions/CIK0001206264.json"
SGI_COVER = "https://www.sec.gov/Archives/edgar/data/1206264/000120626426000113/sgi-20260630.htm"
LEG_INSTRUMENT = "https://www.sec.gov/Archives/edgar/data/58492/000210012126000938/primary_doc.xml"
SGI_ANNOUNCEMENT = "https://www.sec.gov/Archives/edgar/data/1206264/000114036126014337/ef20070363_8k.htm"
LEG_AGREEMENT_FILING = "https://www.sec.gov/Archives/edgar/data/58492/000119312526151966/d38467d8k.htm"
LEG_AGREEMENT = "https://www.sec.gov/Archives/edgar/data/58492/000119312526151966/d38467dex21.htm"
SGI_RELEASE = "https://www.sec.gov/Archives/edgar/data/1206264/000120626426000121/exhibit991-somnigroupcompl.htm"
EXPECTED = {SGI_COVER, LEG_INSTRUMENT, SGI_ANNOUNCEMENT, LEG_AGREEMENT_FILING, LEG_AGREEMENT, SGI_RELEASE}


def reference(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def captures(directory=HERE):
    result, inventory = {}, []
    receipts = sorted((directory / "captures").glob("*.metadata.json"))
    if len(receipts) != 6:
        raise ValueError("The exact original six-attempt research set is required")
    for receipt in receipts:
        meta = json.loads(receipt.read_bytes())
        if meta.get("status") != "PUBLIC_ORIGINAL_BYTES_CAPTURED":
            raise ValueError("An unavailable/withheld/failed original body cannot become evidence")
        url = meta["requested_url"]
        if (meta.get("http_status") != 200 or meta.get("final_url") != url or url not in EXPECTED
                or meta.get("attempts") != 1 or meta.get("max_bytes") != 4_000_000
                or meta.get("redirects_allowed") is not False or meta.get("body_path") != meta["raw_sha256"] + ".bin"):
            raise ValueError("The source is not an exact bounded successful reserved primary response")
        if not datetime.fromisoformat(meta["started_at"]) <= datetime.fromisoformat(meta["observed_at"]):
            raise ValueError("Original request reservation/capture clocks are inconsistent")
        raw_path = receipt.with_name(meta["body_path"])
        raw = raw_path.read_bytes()
        if not 0 < len(raw) <= meta["max_bytes"] or len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != meta["raw_sha256"]:
            raise ValueError("Source bytes do not match their pinned original capture")
        if url in result:
            raise ValueError("Duplicate original response")
        result[url] = meta, raw
        if directory == HERE:
            inventory.extend((reference(receipt), reference(raw_path)))
    if set(result) != EXPECTED:
        raise ValueError("The original six-capture source set differs")
    return result, inventory


def _through(text, start, end):
    at = text.index(start)
    stop = text.index(end, at) + len(end)
    return text[at:stop]


def _edge(source, stock, excerpt, relation_excerpt, child_reference, parent_reference, relation, *, planned=False):
    return {"source_snapshot_id": source.snapshot_id, "subject_role": "SURVIVING_ISSUER",
            "scope": "PLANNED" if planned else "COMPLETED", "relation": relation,
            "effective_on": None if planned else stock["effective_on"],
            "effective_date_text": None if planned else stock["effective_date_text"],
            "excerpt": excerpt, "relation_excerpt": relation_excerpt,
            "child_name_text": stock["from_action_name_text"], "parent_name_text": stock["to_action_name_text"],
            "child_reference_text": child_reference, "parent_reference_text": parent_reference}


def build_inputs():
    prior_service, stock, _, prior_diagnostics = leg_inputs()
    new, inventory = captures()
    retained, old_new = prior_captures(), leg_captures()[0]
    metadata = {"0000058492": json.loads(retained[LEG_METADATA][1]), "0001206264": json.loads(old_new[SGI_METADATA][1])}
    clocks, additions = [], []
    for url, (receipt, raw) in sorted(new.items()):
        parts = urlsplit(url).path.split("/")
        cik, number = parts[4].zfill(10), parts[5]
        accession = f"{number[:10]}-{number[10:12]}-{number[12:]}"
        recent = metadata[cik]["filings"]["recent"]
        index = recent["accessionNumber"].index(accession)
        published = recent["acceptanceDateTime"][index]
        clocks.append({"url": url, "publication_at": published, "filed_on": recent["filingDate"][index],
                       "reported_on": recent["reportDate"][index], "form": recent["form"][index],
                       "observed_at": receipt["observed_at"], "started_at": receipt["started_at"],
                       "metadata_url": LEG_METADATA if cik == "0000058492" else SGI_METADATA,
                       "metadata_raw_sha256": hashlib.sha256(retained[LEG_METADATA][1] if cik == "0000058492" else old_new[SGI_METADATA][1]).hexdigest(),
                       "accession": accession, "multiple_files_are_independent_disclosures": False,
                       "historical_availability_reconstructed": False})
        kind = "ticker_filing" if url == SGI_COVER else "schedule13" if url == LEG_INSTRUMENT else "corporate_action_filing"
        additions.append(SourceSnapshot(kind, url, receipt["observed_at"], raw, published))
    sources = [*prior_service.snapshots.values(), *additions]
    as_of = max(source.observed_at for source in sources)
    service = Sec13fIdentityBulk(snapshots=sources, universe=prior_service.universe, quarter=prior_service.quarter,
                                existing=prior_service.existing, as_of=as_of)
    by_url = {source.url: source for source in additions}
    action, completion = (service.snapshots[stock[key]] for key in ("action_snapshot_id", "completion_snapshot_id"))
    action_relation = 'the Company became a wholly owned indirect subsidiary of the Parent'
    completion_relation = 'Leggett & Platt surviving and becoming a direct, wholly owned subsidiary of the Company'
    controls = [_edge(action, stock, stock["action_excerpt"], action_relation, "the Company", "the Parent", "INDIRECT"),
                _edge(completion, stock, stock["completion_excerpt"], completion_relation, "Leggett & Platt", "the Company", "DIRECT")]
    filing = by_url[LEG_AGREEMENT_FILING]
    text = control_text(filing)
    excerpt = _through(text, "On April 13, 2026", "with the Company surviving the Merger as a direct, wholly owned subsidiary of Parent.")
    controls.append(_edge(filing, stock, excerpt, 'the Company surviving the Merger as a direct, wholly owned subsidiary of Parent',
                          "the Company", "Parent", "DIRECT", planned=True))
    agreement = by_url[LEG_AGREEMENT]
    text = control_text(agreement)
    excerpt = _through(text, "AGREEMENT AND PLAN OF MERGER, dated", 'as amended (the “ GBCLM ”);')
    controls.append(_edge(agreement, stock, excerpt,
                          'the Company continuing as the surviving corporation in the Merger (the “ Surviving Corporation ”) and as a direct, wholly owned Subsidiary of Parent',
                          "the Company", "Parent", "DIRECT", planned=True))
    request = {"stock_merger": stock, "control_disclosures": controls,
               "instrument_disclosures": [{"role": "from", "anchor_on": "2026-06-30",
                   "instrument_snapshot_id": by_url[LEG_INSTRUMENT].snapshot_id,
                   "ticker_snapshot_id": stock["from_ticker_snapshot_id"]},
                   {"role": "to", "anchor_on": "2026-06-30", "instrument_snapshot_id": stock["to_instrument_snapshot_id"],
                    "ticker_snapshot_id": by_url[SGI_COVER].snapshot_id}]}
    report = review_control_instrument_bridge(service, request)
    inspections = []
    for source in additions:
        if source.kind == "corporate_action_filing":
            visible = control_text(source)
            inspections.append({"source": {**source.metadata(), "snapshot_id": source.snapshot_id},
                                "visible_literal_old_cusip": "524660107" in visible,
                                "visible_literal_recipient_cusip": "88023U101" in visible,
                                "cusip_absence_scope": "THIS_EXACT_CAPTURE_ONLY; NOT GLOBAL_ABSENCE_OR_DELISTING",
                                "independent_disclosure_key": [str(int(urlsplit(source.url).path.split('/')[4])), urlsplit(source.url).path.split('/')[5]],
                                "inspection_only": source.url in {SGI_ANNOUNCEMENT, SGI_RELEASE},
                                "completed_control_inferred_from_press_release": False})
    diagnostics = {"source_inventory": prior_diagnostics["source_inventory"] + inventory,
                   "new_primary_http_requests": 6, "new_public_original_raw_bodies": 6,
                   "publication_clock_links": clocks, "source_content_inspections": inspections,
                   "network_requested_during_replay": False, "app_quota_managed_live_claimed": False,
                   "historical_request_ledger_reconstructed": False, "historical_availability_reconstructed": False,
                   "canonical_tickers": len(service.universe), "stored_records": len(service.existing),
                   "registry_sha256": prior_diagnostics["registry_sha256"], "production_registry_changed": False,
                   "new_aliases": 0, "new_verified_identities": 0, "current_quarter_verified_coverage": 0}
    return service, request, report, diagnostics


def write_once(path, value):
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Current replay differs; retain the previous diagnostic version")
    else:
        with path.open("xb") as stream:
            stream.write(raw)


def runtime_plan(service, request, diagnostics):
    """An explicit immutable root-dispatch input; no production file glob."""
    by_hash = {item["sha256"]: item for item in diagnostics["source_inventory"]}
    descriptors = {}
    for index, source in enumerate(service.snapshots.values()):
        pin = by_hash[source.sha256]
        if pin["bytes"] != len(source.payload) or (ROOT / pin["path"]).read_bytes() != source.payload:
            raise ValueError("The original whole-body runtime pin does not match its source")
        descriptors[f"source_{index}"] = {"metadata": {**source.metadata(), "snapshot_id": source.snapshot_id},
                                           "body_available": True, "body_ref": pin}
    if len(descriptors) > 16 or sum(item["body_ref"]["bytes"] for item in descriptors.values()) > 24_000_000:
        raise ValueError("The explicit captured runtime plan exceeds its shared budget")
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_CONTROL_INSTRUMENT",
            "as_of": service.as_of.isoformat(),
            "request": {"quarter": service.quarter, "review_request": deepcopy_json(request)}, "captures": descriptors}


def deepcopy_json(value):
    return json.loads(json.dumps(value))


if __name__ == "__main__":
    service, request, report, diagnostics = build_inputs()
    for name, value in (("request.json", request), ("review.json", report), ("replay_diagnostics.json", diagnostics),
                        ("captured_review_plan.json", runtime_plan(service, request, diagnostics))):
        write_once(HERE / name, value)
    print(json.dumps({"status": report["status"], "control_conflicts": len(report["control_conflicts"]),
                      "control_edges": len(report["control_edges"]), "blockers": report["blockers"],
                      "fresh_http_requests_during_replay": 0}))
