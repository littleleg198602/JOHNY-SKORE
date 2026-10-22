"""Rebuild a reviewable proposal only from the captured primary bytes.

No network access and no production registry writes. Quote excerpts retain
source spelling; an ellipsis separates independently located source fragments.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry


class SourceText(HTMLParser):
    """Preserve inline text, extract table cells and explicit inline XBRL facts."""
    def __init__(self, raw: bytes):
        super().__init__(convert_charrefs=True)
        self.parts, self.rows, self.row_stack, self.cell_stack = [], [], [], []
        self.facts, self.fact_stack = {}, []
        self.feed(raw.decode("utf-8"))
        self.text = " ".join("".join(self.parts).split())

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"div", "p", "tr", "br", "h1", "h2", "h3"}:
            self.parts.append(" ")
        if tag == "tr":
            self.row_stack.append([])
        if tag in {"td", "th"} and self.row_stack:
            self.cell_stack.append(([], self.row_stack[-1]))
        if tag == "ix:nonnumeric":
            self.fact_stack.append((attributes.get("name"), []))

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell_stack:
            parts, row = self.cell_stack.pop()
            row.append(" ".join("".join(parts).split()))
        if tag == "tr" and self.row_stack:
            self.rows.append(self.row_stack.pop())
        if tag == "ix:nonnumeric" and self.fact_stack:
            name, parts = self.fact_stack.pop()
            self.facts.setdefault(name, []).append(" ".join("".join(parts).split()))
        if tag in {"div", "p", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, value):
        self.parts.append(value)
        for parts, _ in self.cell_stack:
            parts.append(value)
        if self.fact_stack:
            self.fact_stack[-1][1].append(value)

    def field(self, label):
        matches = [row[1] for row in self.rows if len(row) >= 2 and row[0] == label]
        if not matches or len(set(matches)) != 1:
            raise ValueError(f"Unambiguous source field required: {label}")
        return matches[0]


def clock(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def source_manifest():
    manifest = json.loads((ROOT / "captures.json").read_text())
    sources = {}
    for source in manifest["captures"]:
        if source.get("http_status") != 200 or "failure_reason" in source:
            raise ValueError("Failed source capture cannot back a proposal")
        raw = (ROOT / source["path"]).read_bytes()
        if (len(raw) != source["size_bytes"]
                or hashlib.sha256(raw).hexdigest() != source["sha256"]):
            raise ValueError("Primary capture integrity failure")
        sources[Path(source["path"]).name] = source
    return sources


def filing_clock(sources, prefix, year, accession):
    name = f"{prefix}_submissions.json"
    data = json.loads((ROOT / sources[name]["path"]).read_bytes())
    recent = data["filings"]["recent"]
    index = recent["accessionNumber"].index(accession)
    accepted = recent["acceptanceDateTime"][index]
    index_source = sources[f"{prefix}_{year}_index.html"]
    raw = (ROOT / index_source["path"]).read_bytes()
    text = SourceText(raw).text
    # Index clocks remain verbatim; the timezone comparison is an explicitly
    # labelled interpretation, never a replacement for a publisher field.
    import re
    local = re.search(r"Accepted\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text).group(1)
    interpreted = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo("America/New_York"))
    conflict = interpreted.astimezone(timezone.utc) != clock(accepted)
    return {
        "accession_number": accession,
        "filing_date": recent["filingDate"][index],
        "report_date": recent["reportDate"][index],
        "reported_acceptance_at": accepted,
        "reported_acceptance_at_source": sources[name]["url"],
        "reported_acceptance_at_locator": f"filings.recent.acceptanceDateTime[{index}], accessionNumber[{index}]={accession}",
        "index_accepted_text": local,
        "index_source_url": index_source["url"],
        "index_accepted_timezone_interpretation": "America/New_York; source index has no explicit zone",
        "index_http_last_modified": index_source["last_modified"],
        "index_interpreted_utc": interpreted.astimezone(timezone.utc).isoformat(),
        "reported_acceptance_conflict": conflict,
        "publication_time_verified": False,
        "verified_first_publication_at": None,
        "publication_limitation": (
            "API UTC acceptance and interpreted index acceptance disagree; actual first publication unverified."
            if conflict else "SEC acceptance is documented; exact first dissemination of the renderer is unverified."
        ),
    }


def evidence(source, purpose, fragments, locator, *, filing=None, published_at=None):
    # At a conflict use actual observation as a conservative availability bound.
    # A reported source clock is retained but is never silently promoted to
    # verified historical publication.
    conflicted = filing and filing["reported_acceptance_conflict"]
    event = published_at or (source["observed_at"] if conflicted else filing["reported_acceptance_at"])
    item = {
        "purpose": purpose, "url": source["url"], "locator": locator,
        "quote": " … ".join(fragments),
        "quote_fragments": fragments,
        "quote_extraction": "Selected verbatim source fields/cells; whitespace normalized; ellipsis marks omitted material.",
        "publisher": "GLEIF" if published_at else "SEC EDGAR",
        "published_at": event, "observed_at": source["observed_at"],
        "archived_source_path": str((ROOT / source["path"]).relative_to(REPO)),
        "archived_source_sha256": source["sha256"],
        "http_last_modified": source["last_modified"],
        "publication_time_verified": False,
        "verified_first_publication_at": None,
        "published_at_semantics": (
            "GLEIF golden-copy publishDate" if published_at else
            "Conservative first-observed availability bound; original publication time conflicted" if conflicted else
            "Reported SEC UTC filing acceptance event; exact first renderer dissemination not verified"
        ),
    }
    if filing:
        item["reported_acceptance_at"] = filing["reported_acceptance_at"]
        item["reported_acceptance_conflict"] = filing["reported_acceptance_conflict"]
        item["filing_clock_evidence"] = filing
    return item


def main():
    sources = source_manifest()
    reviewed = datetime.now(timezone.utc)
    definitions = [
        ("MSFT", "msft", "2026", "0001193125-26-323660", "Microsoft Corporation", "0000789019"),
        ("LMT", "lmt", "2025", "0001628280-26-004195", "Lockheed Martin Corporation", "0000936468"),
        ("AAPL", "aapl", "2025", "0000320193-25-000079", "Apple Inc.", "0000320193"),
        ("NVDA", "nvda", "2026", "0001045810-26-000021", "NVIDIA Corporation", "0001045810"),
    ]
    canonical = {row["ticker"] for row in csv.DictReader((REPO / "market_checker_app/data/market_checker_687_tickers.csv").open())}
    if not {row[0] for row in definitions} <= canonical:
        raise ValueError("Proposal selection must be within the actual canonical universe")
    records, clocks = [], {}
    for ticker, prefix, year, accession, legal, cik in definitions:
        timing = filing_clock(sources, prefix, year, accession)
        clocks[ticker] = timing
        r1_source = sources[f"{prefix}_{year}_R1.htm"]
        r1 = SourceText((ROOT / r1_source["path"]).read_bytes())
        if r1.field("Entity Central Index Key") != cik or r1.field("Trading Symbol") != ticker:
            raise ValueError("Exact SEC legal identifier/ticker disagreement")
        if ticker == "NVDA":
            source = sources["nvda_2026_10k.htm"]
            cover = SourceText((ROOT / source["path"]).read_bytes())
            if ("NVIDIA CORPORATION (Exact name of registrant as specified in its charter)" not in cover.text
                    or cover.facts.get("dei:EntityCentralIndexKey") != [cik]):
                raise ValueError("NVIDIA full charter name and inline CIK must be captured")
            header = [r for r in cover.rows if "Trading Symbol(s)" in r]
            security = [r for r in cover.rows if "NVDA" in r and any("Common Stock" in c for c in r)]
            if len(header) != 1 or len(security) != 1 or header[0].index("Trading Symbol(s)") != security[0].index(ticker):
                raise ValueError("SEC ticker must be in its labelled cover column")
            entity = evidence(source, "ENTITY_IDENTITY", ["NVIDIA CORPORATION", cik],
                              "Cover exact charter-name heading; ix:nonnumeric[name=dei:EntityCentralIndexKey]", filing=timing)
            issuer = evidence(source, "ISSUER_IDENTITY", ["NVIDIA CORPORATION", "Trading Symbol(s) NVDA"],
                              "Cover exact charter-name heading; same-column header Trading Symbol(s) and Common Stock value NVDA", filing=timing)
            issuer["quote_extraction"] += " Ticker column transcript pairs the original header and its corresponding data cell."
        else:
            if r1.field("Entity Registrant Name").casefold() != legal.casefold():
                raise ValueError("Exact SEC registrant legal name disagreement")
            source = r1_source
            registered = r1.field("Entity Registrant Name")
            entity = evidence(source, "ENTITY_IDENTITY", [f"Entity Registrant Name {registered}", f"Entity Central Index Key {cik}"],
                              "R1 table: Entity Registrant Name and Entity Central Index Key rows", filing=timing)
            issuer = evidence(source, "ISSUER_IDENTITY", [f"Entity Registrant Name {registered}", f"Trading Symbol {ticker}"],
                              "R1 table: Entity Registrant Name and common-stock Trading Symbol rows", filing=timing)
        records.append({
            "record_id": f"batch3-sec-{ticker.lower()}-direct-{timing['filing_date']}",
            "disclosed_name": legal, "legal_name": legal, "relationship_role": "DIRECT_ISSUER",
            "entity_identifier": {"scheme": "CIK", "authority": "SEC", "value": cik},
            "issuer_ticker": ticker, "issuer_cik": cik, "issuer_legal_name": legal,
            "effective_from": timing["filing_date"],
            "effective_to": (datetime.fromisoformat(timing["filing_date"]).date() + timedelta(days=1)).isoformat(),
            "known_at": reviewed.isoformat(), "evidence": [entity, issuer],
            "identity_snapshot_date": timing["filing_date"],
            "review_window_semantics": "Date-granular declared SEC filing event-day snapshot only. The exclusive next-day schema boundary is not a legal change/expiry date; no continuous legal-effective interval is established.",
            "legal_effective_interval_verified": False,
        })
    gleif_source = sources["sikorsky_gleif.json"]
    gleif = json.loads((ROOT / gleif_source["path"]).read_bytes())
    attributes = gleif["data"]["attributes"]
    lei, legal = attributes["lei"], attributes["entity"]["legalName"]["name"]
    if lei != "54930054HRQM5ZB1WZ32" or legal != "SIKORSKY AIRCRAFT CORPORATION":
        raise ValueError("Independent exact Sikorsky register identity required")
    subsidiary_source = sources["lmt_2025_ex21.htm"]
    exhibit = SourceText((ROOT / subsidiary_source["path"]).read_bytes())
    title = "Subsidiaries of Lockheed Martin Corporation"
    if title not in exhibit.text or ["Sikorsky Aircraft Corporation", "Delaware"] not in [[cell for cell in row if cell] for row in exhibit.rows]:
        raise ValueError("Explicit exact SEC parent/subsidiary exhibit required")
    snapshot_date = clock(gleif["meta"]["goldenCopy"]["publishDate"]).date()
    entity_proof = evidence(gleif_source, "ENTITY_IDENTITY", [legal, lei],
                            "JSON data.attributes.entity.legalName.name and data.attributes.lei", published_at=gleif["meta"]["goldenCopy"]["publishDate"])
    child = {
        "record_id": f"batch3-sec-sikorsky-legal-entity-{snapshot_date.isoformat()}",
        "disclosed_name": "Sikorsky Aircraft Corporation", "legal_name": legal,
        "relationship_role": "PRIVATE_LEGAL_ENTITY",
        "entity_identifier": {"scheme": "LEI", "authority": "GLEIF", "value": lei},
        "issuer_ticker": None, "issuer_cik": None, "issuer_legal_name": None,
        "effective_from": snapshot_date.isoformat(),
        "effective_to": (snapshot_date + timedelta(days=1)).isoformat(),
        "known_at": reviewed.isoformat(),
        "identity_snapshot_date": snapshot_date.isoformat(),
        "review_window_semantics": "Date-granular GLEIF golden-copy snapshot day only. The exclusive next-day schema boundary is not a legal change/expiry date. The dated LMT parent bridge is withheld because the sources establish no common effective day.",
        "legal_effective_interval_verified": False,
        "entity_register_status": attributes["entity"]["status"],
        "lei_registration_status": attributes["registration"]["status"],
        "lei_conformity_flag": attributes["conformityFlag"],
        "lei_last_update_at": attributes["registration"]["lastUpdateDate"],
        "withheld_parent_bridge_path": "evidence/batch3_sec_20261012/withheld_subsidiary_candidate.json",
        "evidence": [entity_proof],
    }
    records.append(child)
    withheld = {
        "schema_version": 1,
        "candidate_status": "WITHHELD_DATES_NOT_ALIGNED",
        "legal_name": legal, "entity_identifier": child["entity_identifier"],
        "proposed_relationship_role": "SUBSIDIARY_OF_ISSUER",
        "proposed_issuer_cik": "0000936468", "proposed_issuer_ticker": "LMT",
        "proposed_issuer_legal_name": "Lockheed Martin Corporation",
        "entity_identity_snapshot_date": snapshot_date.isoformat(),
        "relationship_source_report_date": clocks["LMT"]["report_date"],
        "known_at": reviewed.isoformat(),
        "reason": "Exact current legal-entity register identity and exact dated SEC parent exhibit are separately supported, but no common effective day or continuous ownership interval is independently established. Current LMT attribution and historical backdating are withheld.",
        "issuer_attribution_allowed": False, "financial_attribution_allowed": False,
        "evidence": [entity_proof, records[1]["evidence"][1],
                     evidence(subsidiary_source, "ENTITY_TO_ISSUER", [title, "Sikorsky Aircraft Corporation Delaware"],
                              "Exhibit 21 heading and exact named subsidiary table row (Place of Formation: Delaware)", filing=clocks["LMT"])],
    }
    DatedCounterpartyIdentityRegistry(records)
    reviewed = datetime.now(timezone.utc)
    for row in records:
        row["known_at"] = reviewed.isoformat()
    withheld["known_at"] = reviewed.isoformat()
    DatedCounterpartyIdentityRegistry(records)
    proposal = {
        "schema_version": 1, "proposal_status": "PROPOSAL_REQUIRES_REGISTRY_OWNER_REVIEW",
        "reviewed_at": reviewed.isoformat(), "production_registry_rows_written": 0,
        "source_manifest_path": "evidence/batch3_sec_20261012/captures.json",
        "source_manifest_sha256": hashlib.sha256((ROOT / "captures.json").read_bytes()).hexdigest(),
        "scope": "Four exact canonical direct public issuers plus one independently identified private legal entity. Sikorsky parent attribution is withheld; no brands or inferred aliases.",
        "timing_policy": "known_at is actual local review time after byte capture. Effective dates limit resolution to the source event/snapshot day; exclusive next-day boundaries are schema gates, not independently established corporate legal-effective intervals. Conflicting publication clocks use first-observed availability bounds.",
        "historical_publication_clock_conflicts": [ticker for ticker, item in clocks.items() if item["reported_acceptance_conflict"]],
        "financial_attribution_allowed": False, "records": records,
        "withheld_parent_bridge_path": "evidence/batch3_sec_20261012/withheld_subsidiary_candidate.json",
    }
    (ROOT / "withheld_subsidiary_candidate.json").write_text(json.dumps(withheld, indent=2) + "\n", encoding="utf-8")
    (ROOT / "counterparty_proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "reviewed_at": reviewed.isoformat(), "clock_conflicts": proposal["historical_publication_clock_conflicts"]}))


if __name__ == "__main__":
    main()
