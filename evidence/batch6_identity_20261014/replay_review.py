"""Exact LEG stock-conversion/withdrawal review; replay performs zero HTTP."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from evidence.batch4_identity_20261013.replay_review import captures as retained_captures
from market_checker_app.services.sec13f_identity_bulk import Sec13fIdentityBulk, SourceSnapshot, load_snapshots
from market_checker_app.services.sec_corporate_action_bridge import Form25Snapshot, action_text
from market_checker_app.services.sec13f_scout_service import DEFAULT_SECURITIES, load_verified_securities
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from market_checker_app.utils.ticker_universe import load_canonical_tickers

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GLOBAL = ROOT / "evidence" / "batch3_identity_bulk_20261012"
RETAINED = ROOT / "evidence" / "batch4_identity_20261013"
LEG_METADATA = "https://data.sec.gov/submissions/CIK0000058492.json"
SGI_METADATA = "https://data.sec.gov/submissions/CIK0001206264.json"
ACTION_URL = "https://www.sec.gov/Archives/edgar/data/58492/000119312526366694/d152590d8k.htm"
LEG_POST_URL = "https://www.sec.gov/Archives/edgar/data/58492/000031506626002283/primary_doc.xml"
LEG_INSTRUMENT = "https://www.sec.gov/Archives/edgar/data/58492/000031506626002256/primary_doc.xml"
LEG_COVER = "https://www.sec.gov/Archives/edgar/data/58492/000005849226000430/leg-20260630.htm"
SGI_ACTION = "https://www.sec.gov/Archives/edgar/data/1206264/000120626426000121/sgi-20260826.htm"
SGI_INSTRUMENT = "https://www.sec.gov/Archives/edgar/data/1206264/000031506626002146/primary_doc.xml"
WITHDRAWAL = "https://www.sec.gov/Archives/edgar/data/58492/000087666126000712/primary_doc.xml"
NEW_URLS = {SGI_METADATA, LEG_INSTRUMENT, LEG_COVER, SGI_ACTION, SGI_INSTRUMENT, WITHDRAWAL}


def reference(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def new_captures(directory=HERE):
    result, inventory = {}, []
    paths = sorted((directory / "captures").glob("*.metadata.json"))
    if not 1 <= len(paths) <= 6:
        raise ValueError("Missing or unbounded six-response capture set")
    for path in paths:
        meta = json.loads(path.read_bytes())
        if meta.get("status") != "PUBLIC_ORIGINAL_BYTES_CAPTURED":
            raise ValueError("WITHHELD/failed/reserved response cannot become a review source")
        if (meta["http_status"] != 200 or meta["requested_url"] != meta["final_url"]
                or meta["requested_url"] not in NEW_URLS or meta.get("body_path") != meta["raw_sha256"] + ".bin"):
            raise ValueError("Review source is not an exact successful assigned primary URL")
        raw_path = path.with_name(meta["body_path"])
        raw = raw_path.read_bytes()
        if len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != meta["raw_sha256"]:
            raise ValueError("Original primary bytes differ from their immutable capture")
        if meta["requested_url"] in result:
            raise ValueError("Duplicate exact primary response")
        result[meta["requested_url"]] = meta, raw
        if directory == HERE:
            inventory.extend((reference(path), reference(raw_path)))
    return result, inventory


def _cut(text, start, stop):
    at = text.index(start)
    return text[at:text.index(stop, at)].strip()


def build_inputs():
    fresh, inventory = new_captures()
    if set(fresh) != NEW_URLS:
        raise ValueError("Actual reviewed six-response set is incomplete")
    retained = retained_captures()
    old_urls = {LEG_METADATA, ACTION_URL, LEG_POST_URL}
    for path in sorted((RETAINED / "captures").glob("*.json")):
        meta = json.loads(path.read_bytes())
        if meta["requested_url"] in old_urls:
            inventory.extend((reference(path), reference(path.with_name(meta["raw_file"]))))
    metadata = {"0000058492": json.loads(retained[LEG_METADATA][1]), "0001206264": json.loads(fresh[SGI_METADATA][1])}
    snapshots = load_snapshots(GLOBAL, kinds={"ticker_catalog", "13f_list"})
    for snapshot in snapshots:
        inventory.extend((reference(GLOBAL / "snapshots" / (snapshot.snapshot_id + ".json")),
                          reference(GLOBAL / "snapshots" / (snapshot.sha256 + ".bin"))))
    clocks = []
    for url in (ACTION_URL, LEG_POST_URL, LEG_INSTRUMENT, LEG_COVER, SGI_ACTION, SGI_INSTRUMENT, WITHDRAWAL):
        receipt, raw = retained[url] if url in retained else fresh[url]
        parts = urlsplit(url).path.split("/")
        cik, number = parts[4].zfill(10), parts[5]
        recent = metadata[cik]["filings"]["recent"]
        accession = f"{number[:10]}-{number[10:12]}-{number[12:]}"
        index = recent["accessionNumber"].index(accession)
        published = recent["acceptanceDateTime"][index]
        clocks.append({"url": url, "form": recent["form"][index], "publication_at": published,
                       "reported_on": recent["reportDate"][index], "filed_on": recent["filingDate"][index],
                       "observed_at": receipt["observed_at"], "metadata_url": LEG_METADATA if cik == "0000058492" else SGI_METADATA,
                       "metadata_raw_sha256": hashlib.sha256(retained[LEG_METADATA][1] if cik == "0000058492" else fresh[SGI_METADATA][1]).hexdigest(),
                       "historical_availability_reconstructed": False})
        if url == WITHDRAWAL:
            if recent["form"][index] != "25-NSE":
                raise ValueError("Withdrawal capture does not have an exact SEC 25-NSE metadata row")
            snapshots.append(Form25Snapshot(url, receipt["observed_at"], raw, published))
        elif url.endswith(".xml"):
            snapshots.append(SourceSnapshot("schedule13", url, receipt["observed_at"], raw, published))
        elif url == LEG_COVER:
            snapshots.append(SourceSnapshot("ticker_filing", url, receipt["observed_at"], raw, published))
        else:
            snapshots.append(SourceSnapshot("corporate_action_filing", url, receipt["observed_at"], raw, published))
            if url == SGI_ACTION:
                snapshots.append(SourceSnapshot("ticker_filing", url, receipt["observed_at"], raw, published))
    as_of = max(datetime.fromisoformat(meta["observed_at"]) for meta, _ in fresh.values())
    baseline = DEFAULT_SECURITIES.read_bytes()
    service = Sec13fIdentityBulk(snapshots=snapshots, universe=load_canonical_tickers(), quarter="2026q3", as_of=as_of,
                                existing=load_verified_securities(DEFAULT_SECURITIES))
    by_source = {(source.kind, source.url): source for source in snapshots}
    action, completion = by_source[("corporate_action_filing", ACTION_URL)], by_source[("corporate_action_filing", SGI_ACTION)]
    action_visible, completion_visible = action_text(action), action_text(completion)
    old_row, target = service.by_cusip["524660107"], service.by_cusip["88023U101"]
    title = "common stock, par value $0.01 per share"
    claim = {"bridge_kind": "STOCK_MERGER_EXCHANGE", "ticker": "LEG",
             "from_identity": {"ticker": "LEG", "issuer_cik": "0000058492", "cusip": old_row["cusip"],
                               "issuer_name": old_row["issuer_name"], "class_description": old_row["class_description"]},
             "to_identity": {"ticker": "SGI", "issuer_cik": "0001206264", "cusip": target["cusip"],
                             "issuer_name": target["issuer_name"], "class_description": target["class_description"]},
             "effective_on": "2026-08-26", "effective_date_text": "August 26, 2026",
             "exchange_ratio": "0.1455", "fractional_cash_only": True,
             "from_action_name_text": "Leggett & Platt, Incorporated", "to_action_name_text": "Somnigroup International Inc.",
             "from_class_title": title, "to_class_title": title,
             "action_snapshot_id": action.snapshot_id,
             "action_excerpt": _cut(action_visible, "On August 26, 2026", "Capitalized terms used herein"),
             "exchange_excerpt": _cut(action_visible, "At the Effective Time, each share of", "The Parent common stock was registered"),
             "completion_snapshot_id": completion.snapshot_id,
             "completion_excerpt": _cut(completion_visible, "On August 26, 2026", "The Somnigroup common stock was registered"),
             "from_instrument_snapshot_id": by_source[("schedule13", LEG_INSTRUMENT)].snapshot_id,
             "to_instrument_snapshot_id": by_source[("schedule13", SGI_INSTRUMENT)].snapshot_id,
             "from_ticker_snapshot_id": by_source[("ticker_filing", LEG_COVER)].snapshot_id,
             "to_ticker_snapshot_id": by_source[("ticker_filing", SGI_ACTION)].snapshot_id,
             "withdrawal_snapshot_id": by_source[("form25_nse", WITHDRAWAL)].snapshot_id,
             "cusip_bridge_snapshot_id": None}
    report = instrument_transition_review(service, tickers=["LEG"], corporate_action_bridges=[claim])
    if DEFAULT_SECURITIES.read_bytes() != baseline:
        raise ValueError("Production instrument registry changed during read-only action review")
    diagnostics = {"source_inventory": inventory, "new_primary_http_requests": len(fresh), "new_public_original_raw_bodies": len(fresh),
                   "publication_clock_links": clocks, "app_quota_managed_live_claimed": False,
                   "historical_sec_request_history_reconstructed": False, "network_requested_during_replay": False,
                   "registry_sha256": hashlib.sha256(baseline).hexdigest(), "stored_records": len(service.existing),
                   "production_registry_changed": False, "new_verified_identities": 0, "new_aliases": 0,
                   "canonical_tickers": len(service.universe), "prior_LEG_registry_versions": len([row for row in service.existing if row["ticker"] == "LEG"])}
    return service, claim, report, diagnostics


if __name__ == "__main__":
    _, claim, report, diagnostics = build_inputs()
    for name, value in (("bridge_proposal.json", claim), ("review.json", report), ("replay_diagnostics.json", diagnostics)):
        payload = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
        path = HERE / name
        if path.exists() and path.read_bytes() != payload:
            raise ValueError("Existing review differs; preserve the original artifact and inspect inputs")
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(payload)
    bridge = report["corporate_action_bridge_reviews"][0]
    print(json.dumps({"bridge_status": bridge["status"], "blockers": bridge["blockers"],
                      "new_primary_responses": 6, "replay_http_requests": 0,
                      "replayed_at": datetime.now(timezone.utc).isoformat()}))
