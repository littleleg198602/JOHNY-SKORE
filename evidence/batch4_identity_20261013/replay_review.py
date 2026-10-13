"""Offline replay of eleven exact primary responses and the pinned Q3 inputs.

Run from the repository with:
python -m evidence.batch4_identity_20261013.replay_review
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from market_checker_app.services.sec13f_identity_bulk import (
    MAX_SOURCE_BYTES, Sec13fIdentityBulk, SourceSnapshot, _action_text,
    load_snapshots, parse_schedule13, parse_ticker_filing,
)
from market_checker_app.services.sec13f_scout_service import DEFAULT_SECURITIES, load_verified_securities
from market_checker_app.services.sec13f_transition_review import instrument_transition_review
from market_checker_app.utils.ticker_universe import load_canonical_tickers


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GLOBAL_ARCHIVE = ROOT / "evidence" / "batch3_identity_bulk_20261012"


def captures():
    entries = {}
    paths = sorted((HERE / "captures").glob("*.json"))
    if not 1 <= len(paths) <= 12:
        raise ValueError("Missing or unbounded primary capture set")
    for path in paths:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("status") != "RAW_CAPTURED" or receipt.get("http_status") != 200:
            raise ValueError("Incomplete primary capture cannot become a replay input")
        digest = receipt["sha256"]
        if receipt["raw_file"] != f"{digest}.bin" or len(digest) != 64:
            raise ValueError("Capture refers to an invalid digest-named source")
        payload = path.with_name(receipt["raw_file"]).read_bytes()
        if (not 0 < len(payload) <= MAX_SOURCE_BYTES or len(payload) != receipt["bytes"]
                or hashlib.sha256(payload).hexdigest() != digest
                or receipt["final_url"] != receipt["requested_url"]):
            raise ValueError("Primary capture failed its original-byte or URL check")
        url = receipt["requested_url"]
        if url in entries:
            raise ValueError("Duplicate primary capture URL")
        entries[url] = (receipt, payload)
    return entries


def _excerpt(text, start, stop):
    begin = text.index(start)
    return text[begin:text.index(stop, begin)].strip()


def build_review():
    entries = captures()
    metadata = {}
    metadata_sources = {}
    for url, (receipt, payload) in entries.items():
        if url.startswith("https://data.sec.gov/submissions/CIK"):
            document = json.loads(payload)
            if url != f"https://data.sec.gov/submissions/CIK{str(document['cik']).zfill(10)}.json":
                raise ValueError("Submissions response has a conflicting exact issuer CIK")
            metadata[str(document["cik"]).zfill(10)] = document
            metadata_sources[str(document["cik"]).zfill(10)] = {"url": url, "sha256": receipt["sha256"],
                                                                "observed_at": receipt["observed_at"]}
    snapshots = load_snapshots(GLOBAL_ARCHIVE, kinds={"ticker_catalog", "13f_list"})
    primary = {}
    parse_results = []
    filing_clock_links = []
    for url, (receipt, payload) in entries.items():
        if not url.startswith("https://www.sec.gov/Archives/edgar/data/"):
            continue
        parts = urlsplit(url).path.split("/")
        cik, number = parts[4].zfill(10), parts[5]
        accession = f"{number[:10]}-{number[10:12]}-{number[12:]}"
        recent = metadata[cik]["filings"]["recent"]
        index = recent["accessionNumber"].index(accession)
        published = recent["acceptanceDateTime"][index]
        filing_clock_links.append({
            "filing_url": url, "accession": accession, "filing_date": recent["filingDate"][index],
            "report_date": recent["reportDate"][index], "publication_time": published,
            "clock_basis": "Exact archived SEC submissions acceptanceDateTime; no inferred clock reconciliation",
            "metadata_source": metadata_sources[cik], "historical_available_at_not_reconstructed": True,
        })
        if url.endswith(".xml"):
            snapshot = SourceSnapshot("schedule13", url, receipt["observed_at"], payload, published)
            fact = parse_schedule13(snapshot)
            snapshots.append(snapshot)
            parse_results.append({"url": url, "status": "STRUCTURED_INSTRUMENT_PARSED", "fact": fact})
        else:
            snapshot = SourceSnapshot("corporate_action_filing", url, receipt["observed_at"], payload, published)
            snapshots.append(snapshot)
            try:
                text = _action_text(snapshot)
            except ValueError as error:
                parse_results.append({"url": url, "status": "NO_COMPLETE_VISIBLE_ACTION_PROOF",
                                      "error": str(error), "original_bytes_not_repaired": True})
            else:
                primary[url] = (snapshot, text)
            ticker_snapshot = SourceSnapshot("ticker_filing", url, receipt["observed_at"], payload, published)
            try:
                contexts = parse_ticker_filing(ticker_snapshot)
            except ValueError as error:
                parse_results.append({"url": url, "status": "NO_SUPPORTED_SAME_CONTEXT_DEI_PROOF",
                                      "error": str(error), "plain_cover_table_not_promoted": True})
            else:
                snapshots.append(ticker_snapshot)
                parse_results.append({"url": url, "status": "SAME_CONTEXT_DEI_PARSED", "contexts": contexts})
    xom, xom_text = primary["https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm"]
    leg, leg_text = primary["https://www.sec.gov/Archives/edgar/data/58492/000119312526366694/d152590d8k.htm"]
    pstg, pstg_text = primary["https://www.sec.gov/Archives/edgar/data/1474432/000147443226000061/pstg-20260503.htm"]
    statements = [
        {"ticker": "XOM", "source_snapshot_id": xom.snapshot_id,
         "excerpt": _excerpt(xom_text, "On July 1, 2026, Exxon Mobil Corporation", "The foregoing description"),
         "effective_on": "2026-07-01", "effective_date_text": "July 1, 2026"},
        {"ticker": "LEG", "source_snapshot_id": leg.snapshot_id,
         "excerpt": _excerpt(leg_text, "On August 26, 2026", "As a result of the Merger"),
         "effective_on": "2026-08-26", "effective_date_text": "August 26, 2026"},
        {"ticker": "LEG", "source_snapshot_id": leg.snapshot_id,
         "excerpt": _excerpt(leg_text, "On the Closing Date, in connection with the consummation", "Item 3.03"),
         "effective_on": None, "effective_date_text": None},
        {"ticker": "PSTG", "source_snapshot_id": pstg.snapshot_id,
         "excerpt": _excerpt(pstg_text, "In April 2026, we also updated our ticker symbol", "• In May 2026"),
         "effective_on": None, "effective_date_text": None},
    ]
    as_of = max(datetime.fromisoformat(receipt["observed_at"]) for receipt, _ in entries.values())
    baseline_bytes = DEFAULT_SECURITIES.read_bytes()
    service = Sec13fIdentityBulk(snapshots=snapshots, universe=load_canonical_tickers(), quarter="2026q3",
                                as_of=as_of, existing=load_verified_securities(DEFAULT_SECURITIES))
    report = instrument_transition_review(service, tickers=["XOM", "LEG", "PSTG"], primary_statements=statements)
    if DEFAULT_SECURITIES.read_bytes() != baseline_bytes:
        raise ValueError("Registry changed during read-only transition review")
    diagnostics = {"raw_capture_count": len(entries), "original_bytes_verified": True,
                   "selected_snapshot_count": len(snapshots), "filing_parse_results": parse_results,
                   "publication_clock_links": filing_clock_links,
                   "baseline_registry_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                   "production_registry_changed": False, "network_requested_during_replay": False,
                   "configured_edgar_contact_guard_unchanged": True}
    return report, diagnostics


if __name__ == "__main__":
    report, diagnostics = build_review()
    for name, value in (("review.json", report), ("replay_diagnostics.json", diagnostics)):
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        path = HERE / name
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError("Existing replay differs; preserve the prior artifact and inspect inputs")
        else:
            with path.open("xb") as stream:
                stream.write(payload)
    print(json.dumps({"status": report["status"], "review_sha256": report["review_sha256"],
                      "verified_identities_added": 0, "raw_captures": diagnostics["raw_capture_count"],
                      "network_requested": False}))
