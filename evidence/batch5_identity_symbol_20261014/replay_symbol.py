"""Zero-HTTP review of retained PSTG/P exact primary bytes; no aliases."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from evidence.batch4_identity_20261013.replay_review import captures
from market_checker_app.services.sec13f_identity_bulk import (
    Sec13fIdentityBulk, SourceSnapshot, load_snapshots, parse_ticker_filing,
)
from market_checker_app.services.sec_corporate_action_bridge import action_text, issuer_names
from market_checker_app.services.sec13f_scout_service import DEFAULT_SECURITIES, load_verified_securities
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from market_checker_app.utils.ticker_universe import load_canonical_tickers

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD_ARCHIVE = ROOT / "evidence" / "batch4_identity_20261013"
GLOBAL_ARCHIVE = ROOT / "evidence" / "batch3_identity_bulk_20261012"
METADATA_URL = "https://data.sec.gov/submissions/CIK0001474432.json"
OLD_COVER_URL = "https://www.sec.gov/Archives/edgar/data/1474432/000147443226000011/pstg-20260223.htm"
ACTION_URL = "https://www.sec.gov/Archives/edgar/data/1474432/000147443226000061/pstg-20260503.htm"
NEW_COVER_URL = "https://www.sec.gov/Archives/edgar/data/1474432/000147443226000088/pstg-20260802.htm"
INSTRUMENT_URL = "https://www.sec.gov/Archives/edgar/data/1474432/000210011926000481/primary_doc.xml"


def _reference(path):
    raw = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def build_inputs():
    entries = captures()
    metadata_receipt, metadata_raw = entries[METADATA_URL]
    metadata = json.loads(metadata_raw)
    if str(metadata["cik"]).zfill(10) != "0001474432":
        raise ValueError("Retained issuer metadata has the wrong exact CIK")
    selected_urls = {METADATA_URL, OLD_COVER_URL, ACTION_URL, NEW_COVER_URL, INSTRUMENT_URL}
    source_inventory = []
    for path in sorted((OLD_ARCHIVE / "captures").glob("*.json")):
        receipt = json.loads(path.read_bytes())
        if receipt["requested_url"] in selected_urls:
            source_inventory.extend((_reference(path), _reference(path.with_name(receipt["raw_file"]))))
    if len(source_inventory) != 2 * len(selected_urls):
        raise ValueError("Retained source inventory is incomplete")
    snapshots = load_snapshots(GLOBAL_ARCHIVE, kinds={"ticker_catalog", "13f_list"})
    for snapshot in snapshots:
        source_inventory.extend((_reference(GLOBAL_ARCHIVE / "snapshots" / (snapshot.snapshot_id + ".json")),
                                 _reference(GLOBAL_ARCHIVE / "snapshots" / (snapshot.sha256 + ".bin"))))
    recent = metadata["filings"]["recent"]
    clocks = []
    for url in (OLD_COVER_URL, ACTION_URL, NEW_COVER_URL, INSTRUMENT_URL):
        receipt, raw = entries[url]
        number = urlsplit(url).path.split("/")[5]
        accession = f"{number[:10]}-{number[10:12]}-{number[12:]}"
        index = recent["accessionNumber"].index(accession)
        published = recent["acceptanceDateTime"][index]
        clocks.append({"url": url, "accession": accession, "publication_at": published,
                       "reported_at": recent["reportDate"][index], "filed_on": recent["filingDate"][index],
                       "observed_at": receipt["observed_at"], "metadata_url": METADATA_URL,
                       "metadata_sha256": metadata_receipt["sha256"],
                       "historical_knowledge_reconstructed": False})
        if url == INSTRUMENT_URL:
            snapshots.append(SourceSnapshot("schedule13", url, receipt["observed_at"], raw, published))
        else:
            snapshots.append(SourceSnapshot("ticker_filing", url, receipt["observed_at"], raw, published))
            if url == ACTION_URL:
                snapshots.append(SourceSnapshot("corporate_action_filing", url, receipt["observed_at"], raw, published))
    as_of = max(datetime.fromisoformat(entries[url][0]["observed_at"]) for url in selected_urls)
    baseline_bytes = DEFAULT_SECURITIES.read_bytes()
    service = Sec13fIdentityBulk(snapshots=snapshots, universe=load_canonical_tickers(), quarter="2026q3",
                                as_of=as_of, existing=load_verified_securities(DEFAULT_SECURITIES))
    by_kind_url = {(snapshot.kind, snapshot.url): snapshot for snapshot in snapshots}
    old_cover = by_kind_url[("ticker_filing", OLD_COVER_URL)]
    new_cover = by_kind_url[("ticker_filing", NEW_COVER_URL)]
    old_fact, new_fact = parse_ticker_filing(old_cover)[0], parse_ticker_filing(new_cover)[0]
    old_name = next(item["issuer_name"] for item in issuer_names(old_cover)
                    if item["context_ref"] == old_fact["context_ref"] and item["anchor_date"] == old_fact["anchor_date"])
    new_name = next(item["issuer_name"] for item in issuer_names(new_cover)
                    if item["context_ref"] == new_fact["context_ref"] and item["anchor_date"] == new_fact["anchor_date"])
    target = service.by_cusip["74624M102"]
    action = by_kind_url[("corporate_action_filing", ACTION_URL)]
    text = action_text(action)
    start = text.index("In April 2026, we also updated our ticker symbol")
    end = text.index("• In May 2026", start)
    excerpt = text[start:end].strip()
    claim = {"bridge_kind": "SAME_CIK_SYMBOL_RENAME", "ticker": "PSTG",
             "from_identity": {"ticker": old_fact["ticker"], "issuer_cik": old_fact["issuer_cik"],
                               "cusip": target["cusip"], "issuer_name": old_name,
                               "class_description": old_fact["class_title"]},
             "to_identity": {"ticker": new_fact["ticker"], "issuer_cik": new_fact["issuer_cik"],
                             "cusip": target["cusip"], "issuer_name": target["issuer_name"],
                             "class_description": target["class_description"]},
             "from_tuple_status": "UNVERIFIED: old CUSIP-to-Class-A binding is missing; no production PSTG version exists",
             "action_snapshot_id": action.snapshot_id, "action_excerpt": excerpt,
             "effective_on": None, "effective_date_text": None,
             "from_name_text": old_name, "to_name_text": new_name,
             "from_class_title": old_fact["class_title"], "to_class_title": new_fact["class_title"],
             "from_ticker_snapshot_id": old_cover.snapshot_id, "to_ticker_snapshot_id": new_cover.snapshot_id,
             "from_instrument_snapshot_id": by_kind_url[("schedule13", INSTRUMENT_URL)].snapshot_id,
             "to_instrument_snapshot_id": None, "cusip_bridge_snapshot_id": None}
    report = instrument_transition_review(service, tickers=["PSTG"], corporate_action_bridges=[claim])
    if DEFAULT_SECURITIES.read_bytes() != baseline_bytes:
        raise ValueError("Registry changed during read-only symbol review")
    return service, claim, report, {"new_http_requests": 0, "network_requested_during_replay": False,
                                   "retained_primary_responses_selected": len(selected_urls),
                                   "source_inventory": source_inventory, "publication_clock_links": clocks,
                                   "registry_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                                   "prior_PSTG_registry_versions": len([row for row in service.existing if row["ticker"] == "PSTG"]),
                                   "canonical_symbols": len(service.universe), "production_registry_changed": False,
                                   "new_verified_identities": 0, "new_aliases": 0,
                                   "scope": "Current catalog, formerNames, quarterly list and first/last covers do not establish effective day"}


if __name__ == "__main__":
    _, claim, report, diagnostics = build_inputs()
    for name, value in (("symbol_proposal.json", claim), ("review.json", report), ("replay_diagnostics.json", diagnostics)):
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        path = HERE / name
        if path.exists() and path.read_bytes() != payload:
            raise ValueError("Existing symbol review differs; preserve the prior artifact")
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(payload)
    bridge = report["corporate_action_bridge_reviews"][0]
    print(json.dumps({"status": bridge["status"], "blockers": bridge["blockers"], "new_http_requests": 0,
                      "new_aliases": 0, "review_executed_at": datetime.now(timezone.utc).isoformat()}))
