"""Sole-owner read-only review: observed sponsor pins are not historical bridges."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path

from evidence.batch4_healthcare_20261013.replay import capture
from market_checker_app.services.healthcare_identity_service import (
    DEFAULT_HEALTHCARE_IDENTITIES, HealthcareIdentityRegistry, clinical_trial_version,
)
from market_checker_app.services.healthcare_scout_service import _candidate, _identity_details


ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = ROOT / "evidence" / "batch4_healthcare_20261013" / "identity_proposal.json"
PROPOSAL_SHA256 = "56a66e925af1931de67fca4ad7594751637b1d7d569a669a2bf1fbbd13188816"
STUDY_KEYS = {"NCT03987919": "trial_surpass2_complete_record",
              "NCT04847557": "trial_summit_complete_record"}
ISSUER_KEYS = ("sec_lly_2021_10k", "sec_lly_2024_10k", "sec_lly_2025_10k")


class IssuerDates(HTMLParser):
    """Extract displayed exact DEI facts and their source context dates.

    This local review supports no inferred legal interval or hidden-fact
    promotion. The complete original bodies are verified before inspection.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.context = None
        self.contexts, self.facts, self.captures = {}, [], []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "xbrli:context":
            if self.context is not None or not attributes.get("id"):
                raise ValueError("Ambiguous SEC context")
            self.context = attributes["id"]
            if self.context in self.contexts:
                raise ValueError("Duplicate SEC context ID")
            self.contexts[self.context] = {}
        if self.context and tag in {"xbrli:identifier", "xbrli:instant", "xbrli:enddate"}:
            if tag != "xbrli:identifier" or attributes.get("scheme") == "http://www.sec.gov/CIK":
                self.captures.append([tag, "context", self.context, tag, []])
        name = str(attributes.get("name", "")).casefold()
        if tag == "ix:nonnumeric" and name in {
            "dei:entityregistrantname", "dei:tradingsymbol", "dei:security12btitle",
        }:
            if not attributes.get("contextref") or attributes.get("continuedat"):
                raise ValueError("Incomplete DEI identity fact")
            self.captures.append([tag, "fact", attributes["contextref"], name, []])

    def handle_data(self, text):
        for capture in self.captures:
            capture[4].append(text)

    def handle_endtag(self, tag):
        for index in range(len(self.captures) - 1, -1, -1):
            capture = self.captures[index]
            if capture[0] == tag:
                _, kind, context, key, texts = self.captures.pop(index)
                value = " ".join("".join(texts).split())
                if kind == "context":
                    if key in self.contexts[context]:
                        raise ValueError("Duplicate SEC identity/date claim")
                    self.contexts[context][key] = value
                else:
                    self.facts.append({"name": key, "context_ref": context, "value": value})
                break
        if tag == "xbrli:context":
            self.context = None


def issuer_facts(key):
    meta, raw = capture(key)
    parser = IssuerDates()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    if parser.context is not None or parser.captures:
        raise ValueError("Incomplete SEC issuer/date facts")
    anchors = []
    for fact in parser.facts:
        context = parser.contexts.get(fact["context_ref"], {})
        identifier = context.get("xbrli:identifier", "")
        anchor = context.get("xbrli:instant", context.get("xbrli:enddate"))
        if identifier != "0000059478" or anchor is None:
            continue
        date.fromisoformat(anchor)
        if ((fact["name"] == "dei:entityregistrantname" and fact["value"] == "ELI LILLY AND COMPANY")
                or (fact["name"] == "dei:tradingsymbol" and fact["value"] == "LLY")
                or (fact["name"] == "dei:security12btitle" and fact["value"] == "Common Stock (no par value)")):
            anchors.append({**fact, "issuer_cik": identifier, "anchor_date": anchor})
    if {item["name"] for item in anchors} != {
        "dei:entityregistrantname", "dei:tradingsymbol", "dei:security12btitle",
    }:
        raise ValueError("SEC body does not bind the exact Lilly name, CIK, common stock and ticker")
    return {"capture_key": key, "source_url": meta["response_url"],
            "body_sha256": meta["body_sha256"], "archive_sha256": meta["archive_sha256"],
            "observed_at": meta["observed_at"], "facts": anchors,
            "legal_interval_inferred": False}


def study_fact(record, row):
    version = clinical_trial_version(row)
    identifiers = record["identifiers"]
    if (version["nct_id"] != identifiers["nct_id"]
            or version["lead_sponsor"] != record["entity_name"]
            or version["record_version_date"] != identifiers["record_version_date"]
            or version["relationship_snapshot_sha256"] != identifiers["relationship_snapshot_sha256"]
            or identifiers["intervention_name"] not in version["intervention_names"]):
        raise ValueError("Proposal does not match its exact sponsor/intervention snapshot")
    organization = row["protocolSection"]["identificationModule"]["organization"]
    if organization != {"fullName": "Eli Lilly and Company", "class": "INDUSTRY"}:
        raise ValueError("Unresolved exact study organization")
    return {"version": version, "organization": organization,
            "selected_intervention": identifiers["intervention_name"],
            "unattributed_interventions": [name for name in version["intervention_names"]
                                          if name != identifiers["intervention_name"]],
            "scope": "Displayed current response sponsorship only; no historical CIK bridge"}


def build_review(proposal_path=PROPOSAL):
    proposal_bytes = Path(proposal_path).read_bytes()
    if hashlib.sha256(proposal_bytes).hexdigest() != PROPOSAL_SHA256:
        raise ValueError("Proposal changed after the owner's exact-content handoff")
    proposal = json.loads(proposal_bytes)
    proposed_registry = HealthcareIdentityRegistry(proposal["records"])
    baseline_bytes = DEFAULT_HEALTHCARE_IDENTITIES.read_bytes()
    baseline = HealthcareIdentityRegistry.load(DEFAULT_HEALTHCARE_IDENTITIES).records()
    old_seed = next(row for row in baseline if row["record_id"] == "lly-trial-nct03954834-version-2021-10-20")
    issuers = [issuer_facts(key) for key in ISSUER_KEYS]
    rows = []
    for record in proposed_registry.records():
        nct = record["identifiers"]["nct_id"]
        metadata, raw = capture(STUDY_KEYS[nct])
        payload = json.loads(raw)
        fact = study_fact(record, payload)
        event = date.fromisoformat(record["effective_from"])
        if event.isoformat() != record["identifiers"]["record_version_date"]:
            raise ValueError("Proposal event interval differs from its publisher update day")
        exact_anchors = [item for issuer in issuers for item in issuer["facts"]
                         if item["anchor_date"] == event.isoformat()]
        clock = datetime.fromisoformat(record["known_at"])
        if any(datetime.fromisoformat(item["observed_at"]) > clock for item in record["evidence"]):
            raise ValueError("Proposal backdates knowledge before observation")
        candidate = _candidate("clinicaltrials", payload, record["entity_name"], clock)
        if candidate is None:
            raise ValueError("Captured study is not an exact displayed sponsor candidate")
        issuer = {"subject_id": record["ticker"], "cik": record["issuer_cik"], "company_name": record["entity_name"]}
        unsafe = _identity_details(proposed_registry, "clinicaltrials", candidate[1], issuer, clock)
        production = _identity_details(HealthcareIdentityRegistry(baseline), "clinicaltrials", candidate[1], issuer, clock)
        rows.append({
            "record_id": record["record_id"], "nct_id": nct, "source_body_sha256": metadata["body_sha256"],
            "source_url": metadata["response_url"], "source_observed_at": metadata["observed_at"],
            "source_snapshot": fact, "issuer_evidence": deepcopy(issuers),
            "requested_time_basis": record["time_basis"],
            "requested_effective_interval": [record["effective_from"], record["effective_to"]],
            "known_at": record["known_at"], "exact_event_day_issuer_facts": exact_anchors,
            "historical_sponsor_to_cik_bridge_proven": False,
            "publisher_posting_day_is_immutable_historical_version": False,
            "schema_valid_proposal_resolution": unsafe["identity_resolution"],
            "unchanged_production_resolution": production["identity_resolution"],
            "decision": "WITHHOLD", "adopted": False,
            "reasons": ["NO_DATED_SPONSOR_CIK_BRIDGE_FOR_EFFECTIVE_DAY",
                        "OBSERVED_RELATIONSHIP_PIN_IS_NOT_AN_IMMUTABLE_HISTORICAL_VERSION"],
            "future_work": "Define a separate observed-snapshot sponsor attribution contract without historical EVENT_DATE semantics",
        })
    if DEFAULT_HEALTHCARE_IDENTITIES.read_bytes() != baseline_bytes:
        raise ValueError("Production manifest changed during read-only owner review")
    return {"schema_version": 1, "status": "WITHHELD_TEMPORAL_ISSUER_SPONSOR_BINDING",
            "source_dependency_commits": ["edaba8cef597102d6f9e54d6b013abd45ab85676", "d1811851a8d78ef8a2a3fcaf80cdf6325e0de381"],
            "proposal_sha256": PROPOSAL_SHA256, "proposal_review_clock": proposal["reviewed_at"],
            "records": rows, "proposed_records": len(rows), "adopted_records": 0, "withheld_records": len(rows),
            "preserved_production_records": len(baseline),
            "production_sha256_before": hashlib.sha256(baseline_bytes).hexdigest(),
            "production_sha256_after": hashlib.sha256(DEFAULT_HEALTHCARE_IDENTITIES.read_bytes()).hexdigest(),
            "old_lly_trial_seed": deepcopy(old_seed), "old_seed_rewritten": False,
            "production_registry_changed": False, "comparators_attributed": False,
            "issuer_wide_coverage_claimed": False, "historical_legal_continuity_claimed": False,
            "score_changed": False, "specialist_done_changed": False,
            "network_requested": False}


if __name__ == "__main__":
    report = build_review()
    report["owner_review_recorded_at"] = datetime.now(timezone.utc).isoformat()
    target = Path(__file__).with_name("healthcare_review.json")
    with target.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "adopted": 0, "withheld": report["withheld_records"],
                      "production_records_preserved": report["preserved_production_records"]}))
