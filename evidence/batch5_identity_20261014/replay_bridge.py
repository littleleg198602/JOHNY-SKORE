"""No-network replay of an independent, incomplete XOM instrument bridge."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from evidence.batch4_identity_20261013.replay_review import captures as historical_captures
from market_checker_app.services.sec13f_identity_bulk import (
    Sec13fIdentityBulk, SourceSnapshot, identity_version_sha256, load_snapshots, parse_ticker_filing,
)
from market_checker_app.services.sec_corporate_action_bridge import action_text
from market_checker_app.services.sec13f_scout_service import DEFAULT_SECURITIES, load_verified_securities
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from market_checker_app.utils.ticker_universe import load_canonical_tickers

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def new_captures(folder=HERE):
    results = {}
    paths = sorted((folder / "captures").glob("*.metadata.json"))
    if not 1 <= len(paths) <= 6:
        raise ValueError("Missing or unbounded new source captures")
    for path in paths:
        meta = json.loads(path.read_bytes())
        if meta.get("status") != "PUBLIC_RAW_CAPTURED":
            raise ValueError("WITHHELD or denied bodies cannot become bridge proof")
        if meta.get("http_status") != 200 or meta["requested_url"] != meta["final_url"]:
            raise ValueError("New source did not preserve exact successful public URL")
        if meta["body_path"] != meta["raw_sha256"] + ".bin":
            raise ValueError("New source has an invalid digest-named body")
        raw = path.with_name(meta["body_path"]).read_bytes()
        if len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != meta["raw_sha256"]:
            raise ValueError("New source original-byte check failed")
        results[meta["requested_url"]] = meta, raw
    return results


def build_inputs():
    captures = new_captures()
    new_metadata = json.loads(captures["https://data.sec.gov/submissions/CIK0002115436.json"][1])
    old_path = ROOT / "evidence" / "batch4_energy_20261013" / "raw" / "sec_xom_old_cik_submissions.json"
    old_meta = json.loads(old_path.with_suffix(".metadata.json").read_bytes())
    if hashlib.sha256(old_path.read_bytes()).hexdigest() != old_meta["raw_response_sha256"]:
        raise ValueError("Pinned historical issuer metadata bytes differ")
    metadata = {"0000034088": json.loads(old_path.read_bytes()), "0002115436": new_metadata}
    snapshots = load_snapshots(ROOT / "evidence" / "batch3_identity_bulk_20261012", kinds={"ticker_catalog", "13f_list"})
    clock_links, parse_results = [], []
    for url, (meta, raw) in captures.items():
        if "/Archives/" not in url:
            continue
        parts = urlsplit(url).path.split("/")
        cik, number = parts[4].zfill(10), parts[5]
        recent = metadata[cik]["filings"]["recent"]
        accession = f"{number[:10]}-{number[10:12]}-{number[12:]}"
        index = recent["accessionNumber"].index(accession)
        published = recent["acceptanceDateTime"][index]
        clock_links.append({"url": url, "accession": accession, "publication_at": published,
                            "observed_at": meta["observed_at"],
                            "source": "Exact SEC submissions acceptanceDateTime; no clock interpolation"})
        if url.endswith(".xml"):
            snapshots.append(SourceSnapshot("schedule13", url, meta["observed_at"], raw, published))
        else:
            snapshots.append(SourceSnapshot("corporate_action_filing", url, meta["observed_at"], raw, published))
            snapshot = SourceSnapshot("ticker_filing", url, meta["observed_at"], raw, published)
            try:
                facts = parse_ticker_filing(snapshot)
            except ValueError as error:
                parse_results.append({"url": url, "ticker_proof": "UNSUPPORTED", "reason": str(error)})
            else:
                snapshots.append(snapshot)
                parse_results.append({"url": url, "ticker_proof": "PARSED", "facts": facts})
    old_sources = historical_captures()
    action_url = "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm"
    action_meta, action_raw = old_sources[action_url]
    action = SourceSnapshot("corporate_action_filing", action_url, action_meta["observed_at"], action_raw,
                            "2026-07-01T16:36:49Z")
    snapshots.append(action)
    # Preserve the contradictory prior July7 class claim; selecting July31
    # as an independent positive cannot silently delete this older evidence.
    prior_url = "https://www.sec.gov/Archives/edgar/data/2115436/000211543626000003/xom-20260707.htm"
    prior_meta, prior_raw = old_sources[prior_url]
    snapshots.append(SourceSnapshot("ticker_filing", prior_url, prior_meta["observed_at"], prior_raw,
                                    "2026-07-07T20:19:57Z"))
    as_of = max(datetime.fromisoformat(meta["observed_at"]) for meta, _ in captures.values())
    baseline_bytes = DEFAULT_SECURITIES.read_bytes()
    service = Sec13fIdentityBulk(snapshots=snapshots, universe=load_canonical_tickers(), quarter="2026q3",
                                as_of=as_of, existing=load_verified_securities(DEFAULT_SECURITIES))
    prior = next(row for row in service.existing if row["ticker"] == "XOM")
    target = service.by_cusip["30233Q108"]
    text = action_text(action)
    start = text.index("On July 1, 2026, Exxon Mobil Corporation")
    end = text.index("As of the Effective Time, the rights of shareholders", start)
    excerpt = text[start:end].strip()
    if len(excerpt) > 4_000:
        raise ValueError("Action excerpt exceeds the review bound")
    by_kind_url = {(item.kind, item.url): item for item in snapshots}
    claim = {"ticker": "XOM", "from_identity_sha256": identity_version_sha256(prior),
             "to_identity": {"ticker": "XOM", "issuer_cik": "0002115436", "cusip": target["cusip"],
                             "issuer_name": target["issuer_name"], "class_description": target["class_description"]},
             "action_snapshot_id": action.snapshot_id, "effective_on": "2026-07-01",
             "effective_date_text": "July 1, 2026", "action_excerpt": excerpt,
             "from_name_text": "Exxon Mobil Corporation", "to_name_text": "ExxonMobil Holdings Corporation",
             "from_class_title": "common stock, without par value", "to_class_title": "common stock, par value $0.001 per share",
             "from_instrument_snapshot_id": by_kind_url[("schedule13", "https://www.sec.gov/Archives/edgar/data/34088/000009375126000461/primary_doc.xml")].snapshot_id,
             "from_ticker_snapshot_id": by_kind_url[("ticker_filing", "https://www.sec.gov/Archives/edgar/data/34088/000003408826000093/xom-20260630.htm")].snapshot_id,
             "to_instrument_snapshot_id": None,
             "to_ticker_snapshot_id": by_kind_url[("ticker_filing", "https://www.sec.gov/Archives/edgar/data/2115436/000211543626000006/xom-20260731.htm")].snapshot_id,
             "cusip_bridge_snapshot_id": None}
    exhibit = by_kind_url[("corporate_action_filing", "https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068dex4vi.htm")]
    exhibit_text = action_text(exhibit)
    report = instrument_transition_review(service, tickers=["XOM"], corporate_action_bridges=[claim])
    supplementary = {**exhibit.metadata(), "snapshot_id": exhibit.snapshot_id,
                     "visible_text_sha256": hashlib.sha256(exhibit_text.encode()).hexdigest(),
                     "scope": "Independent current issuer capital-stock description; no dated CUSIP bridge"}
    if DEFAULT_SECURITIES.read_bytes() != baseline_bytes:
        raise ValueError("Historical registry changed during read-only bridge review")
    return service, claim, report, {"new_primary_requests": len(captures), "new_public_raw": len(captures),
                                   "clock_links": clock_links, "parse_results": parse_results,
                                   "supplementary_class_exhibit": supplementary,
                                   "registry_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                                   "production_registry_changed": False, "network_requested_during_replay": False}


if __name__ == "__main__":
    _, claim, report, diagnostics = build_inputs()
    for name, value in (("bridge_proposal.json", claim), ("review.json", report), ("replay_diagnostics.json", diagnostics)):
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        path = HERE / name
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("Existing bridge replay differs; preserve and inspect the prior artifact")
        else:
            path.write_bytes(raw)
    print(json.dumps({"status": report["corporate_action_bridge_reviews"][0]["status"],
                      "blockers": report["corporate_action_bridge_reviews"][0]["blockers"],
                      "new_primary_requests": diagnostics["new_primary_requests"], "new_verified_identities": 0}))
