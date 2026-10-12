"""Independent, dated review and adoption of the three actual batch3 proposals.

Run with the three final source worktrees as arguments. This records the
original-byte hashes and exact supported scope before changing manifests.
It never creates new dates or promotes the separate withheld ownership link.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader

from market_checker_app.services.healthcare_identity_service import HealthcareIdentityRegistry, normalize_application
from market_checker_app.services.nhtsa_recall_scout_service import load_verified_models
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry


DIRECTORY = Path("evidence/batch3_identity_import_20261012")
DATA = Path("market_checker_app/data")


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def aware(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def norm(value):
    return " ".join(value.split())


def html_text(payload):
    soup = BeautifulSoup(payload, "html.parser")
    for node in soup(["head", "script", "style"]):
        node.decompose()
    return norm(" ".join(soup.stripped_strings)), soup


def checked_body(root, relative, expected_archive, expected_raw=None):
    path = root / relative
    archive = path.read_bytes()
    assert sha(archive) == expected_archive, relative
    raw = gzip.decompress(archive) if path.suffix == ".gz" else archive
    assert expected_raw is None or sha(raw) == expected_raw, relative
    return raw, {"path": relative, "archive_sha256": sha(archive), "raw_body_sha256": sha(raw), "raw_bytes": len(raw)}


def lane(root, proposal_relative):
    proposal_path = root / proposal_relative
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
    assert not status.strip(), f"Source lane is not finalized: {root.name}"
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "proposal_path": proposal_relative, "proposal_sha256": sha(proposal_path.read_bytes())}


def review_healthcare(root):
    relative = "evidence/batch3_healthcare_20261012/identity_proposal.json"
    provenance = lane(root, relative)
    proposal = read_json(root / relative)
    records, decisions = deepcopy(proposal["records"]), []
    captures = root / "evidence/batch3_healthcare_20261012/captures"
    metadata = [read_json(path) for path in captures.glob("*.metadata.json")]
    for record in records:
        day = date.fromisoformat(record["identifiers"]["letter_date"])
        assert record["time_basis"] == "EVENT_DATE" and record["relationship"] == "APPLICATION_HOLDER"
        assert record["effective_from"] == day.isoformat()
        assert record["effective_to"] == (day + timedelta(days=1)).isoformat()
        proof, issuer_texts = [], []
        for evidence in record["evidence"]:
            matches = [entry for entry in metadata if entry["request_url"] == evidence["url"]]
            assert len(matches) == 1 and matches[0]["http_status"] == 200
            meta = matches[0]
            assert meta["observed_at"] == evidence["observed_at"]
            assert aware(meta["observed_at"]) <= aware(record["known_at"])
            relative_body = str((captures / meta["body_path"]).relative_to(root))
            raw, checked = checked_body(root, relative_body, meta["archive_sha256"], meta["body_sha256"])
            checked.update({"url": evidence["url"], "locator": evidence["locator"], "purpose": evidence["purpose"],
                            "observed_at": meta["observed_at"]})
            if evidence["purpose"] == "OBJECT_RELATIONSHIP":
                index = int(re.search(r"results\[(\d+)\]", evidence["locator"])[1])
                source = json.loads(raw)["results"][index]
                assert source["company_name"] == record["entity_name"]
                assert source["file_name"] == record["identifiers"]["letter_file_name"]
                assert datetime.strptime(source["letter_date"], "%m/%d/%Y").date() == day
                assert [normalize_application(value) for value in source["application_number"]] == [record["identifiers"]["application_number"]]
                assert source["letter_type"] == "COMPLETE RESPONSE"
                text = norm(source["text"])
                assert record["entity_name"] in text and record["product_or_provider"] in text
                assert source["letter_date"] in text and "COMPLETE RESPONSE" in text
                checked["json_pointer"] = f"/results/{index}"
                checked["event_date"] = day.isoformat()
            else:
                assert urlsplit(evidence["url"]).path.startswith(f"/Archives/edgar/data/{int(record['issuer_cik'])}/")
                text, soup = html_text(raw)
                assert record["entity_name"].casefold() in text.casefold()
                issuer_texts.append(text)
                fact = soup.find(attrs={"name": re.compile(r"EntityCentralIndexKey$", re.I)})
                if fact is not None:
                    assert fact.get_text().strip().zfill(10) == record["issuer_cik"]
                    checked["inline_cik"] = fact.get_text().strip().zfill(10)
            proof.append(checked)
        assert any(re.search(r"\b" + record["ticker"] + r"\b", text) for text in issuer_texts)
        decisions.append({"record_id": record["record_id"], "decision": "ACCEPT_EXACT_LETTER_EVENT_ONLY",
                          "application": record["identifiers"]["application_number"], "event_date": day.isoformat(),
                          "known_at": record["known_at"], "issuer_cik": record["issuer_cik"], "proof": proof,
                          "continuous_ownership_verified": False, "issuer_wide_scope_verified": False})
    HealthcareIdentityRegistry(records)
    return records, {**provenance, "decisions": decisions,
                     "withheld": [{"entity": "Pfizer Ireland Pharmaceuticals", "reason": "c/o Pfizer Inc. address is not a dated ownership chain"}]}


def review_models(root):
    relative = "evidence/batch3_regulatory_20261012/proposal.json"
    provenance = lane(root, relative)
    proposal = read_json(root / relative)
    records, decisions = [], []
    for candidate in proposal["records"]:
        record = deepcopy(candidate["service_record"])
        assert record["model_year"] == 2026
        assert candidate["effective_knowledge"]["time_basis"] == "OBSERVED_SNAPSHOT"
        assert candidate["effective_knowledge"]["effective_from"] is None and candidate["effective_knowledge"]["effective_to"] is None
        assert not candidate["issuer_legal_liability_verified"] and not candidate["issuer_financial_impact_verified"]
        proof, raw_by_id = [], {}
        for evidence in candidate["evidence"]:
            raw, checked = checked_body(root, evidence["archive_path"], evidence["archive_sha256"], evidence["raw_response_sha256"])
            raw_by_id[evidence["capture_id"]] = raw
            metadata = read_json(root / f"evidence/batch3_regulatory_20261012/raw/{evidence['capture_id']}.metadata.json")
            assert metadata["http_status"] == 200 and metadata["requested_url"] == evidence["url"]
            assert metadata["observed_at"] == evidence["observed_at"]
            assert aware(evidence["observed_at"]) <= aware(record["known_at"])
            checked.update({"capture_id": evidence["capture_id"], "url": evidence["url"], "locator": evidence["locator"],
                            "purpose": evidence["purpose"], "observed_at": evidence["observed_at"]})
            proof.append(checked)
        summary = candidate["live_scope_summary"]
        recalls = json.loads(raw_by_id[summary["capture_id"]])
        assert len(recalls["results"]) == recalls["Count"] == summary["returned_count"]
        for recall in recalls["results"]:
            assert recall["Manufacturer"] == record["manufacturer"] and recall["Make"] == record["make"]
            assert recall["Model"] == record["model"] and int(recall["ModelYear"]) == 2026
        if record["ticker"] == "F":
            manufacturer = json.loads(raw_by_id["nhtsa_manufacturer_ford_976"])["Results"]
            assert len(manufacturer) == 1 and manufacturer[0]["Mfr_ID"] == 976 and manufacturer[0]["Mfr_Name"] == "FORD MOTOR COMPANY"
            assert any(row["Make_ID"] == 460 and row["Make_Name"] == "FORD" for row in json.loads(raw_by_id["nhtsa_makes_ford_976"])["Results"])
            assert any(row["Model_ID"] == 1801 and row["Model_Name"] == "F-150" for row in json.loads(raw_by_id["nhtsa_model_ford_f150_2026"])["Results"])
            text, soup = html_text(raw_by_id["sec_ford_2025_10k"])
            assert "Ford Motor Company".casefold() in text.casefold() and "F-150" in text
            fact = soup.find(attrs={"name": re.compile(r"EntityCentralIndexKey$", re.I)})
            assert fact.get_text().strip() == "0000037996"
            import io
            pdf = PdfReader(io.BytesIO(raw_by_id["nhtsa_ford_26v104_acknowledgement"]))
            text = norm(pdf.pages[0].extract_text())
            assert "Ford Motor Company" in text and "FORD/F-150/2021-2026" in text
        else:
            assert record["ticker"] == "GM" and record["manufacturer"] == "General Motors, LLC"
            manufacturer = json.loads(raw_by_id["nhtsa_manufacturer_gm_984"])["Results"]
            assert len(manufacturer) == 1 and manufacturer[0]["Mfr_ID"] == 984 and manufacturer[0]["Mfr_Name"] == "GENERAL MOTORS LLC"
            assert any(row["Make_ID"] == 467 and row["Make_Name"] == "CHEVROLET" for row in json.loads(raw_by_id["nhtsa_makes_gm_984"])["Results"])
            text, _ = html_text(raw_by_id["sec_gm_20260807_8k"])
            assert "General Motors LLC" in text and "a wholly owned subsidiary of the Company" in text and "August 7, 2026" in text
            import io
            pdf = PdfReader(io.BytesIO(raw_by_id["nhtsa_gm_25v815_part573"]))
            text = norm(" ".join(page.extract_text() or "" for page in pdf.pages[:2]))
            assert "Manufacturer Name: General Motors, LLC" in text and "2026-2026 CHEVROLET SILVERADO 1500" in text
        records.append(record)
        decisions.append({"ticker": record["ticker"], "decision": "ACCEPT_EXACT_2026_MODEL_OBSERVATION",
                          "service_record": record, "proof": proof,
                          "historical_parent_at_campaign_date_verified": False,
                          "issuer_legal_liability_verified": False, "financial_impact_verified": False})
    assert records == read_json(root / "evidence/batch3_regulatory_20261012/models.proposed.json")
    return records, {**provenance, "decisions": decisions,
                     "withheld": [{"model": "F-650 SD", "reason": "HTTP400 and unproven F-650 alias"},
                                  {"model": "CADILLAC CT4", "reason": "HTTP400 empty result is not a complete negative"}]}


def review_counterparties(root):
    relative = "evidence/batch3_sec_20261012/counterparty_proposal.json"
    provenance = lane(root, relative)
    proposal = read_json(root / relative)
    records, decisions = deepcopy(proposal["records"]), []
    for record in records:
        start, end = date.fromisoformat(record["effective_from"]), date.fromisoformat(record["effective_to"])
        assert end == start + timedelta(days=1)
        assert record["relationship_role"] in {"DIRECT_ISSUER", "PRIVATE_LEGAL_ENTITY"}
        proof = []
        for evidence in record["evidence"]:
            raw, checked = checked_body(root, evidence["archived_source_path"], evidence["archived_source_sha256"])
            assert aware(evidence["observed_at"]) <= aware(record["known_at"])
            if record["relationship_role"] == "DIRECT_ISSUER":
                text, soup = html_text(raw)
                # Exact source rows/cells are the locator; ellipses mark omissions,
                # rather than a fictitious contiguous passage in the filing.
                for fragment in evidence["quote_fragments"]:
                    if fragment == "Trading Symbol(s) NVDA":
                        assert "Trading Symbol(s)" in text and re.search(r"\bNVDA\b", text)
                        tables = [table for table in soup.find_all("table") if "Trading Symbol(s)" in table.get_text()]
                        assert any(re.search(r"Common Stock.*NVDA", norm(table.get_text(" ")), re.S) for table in tables)
                    else:
                        assert norm(fragment).casefold() in text.casefold()
                assert start.isoformat() == evidence["filing_clock_evidence"]["filing_date"]
                assert evidence["filing_clock_evidence"]["verified_first_publication_at"] is None
                assert evidence["publication_time_verified"] is False
            else:
                assert record["issuer_ticker"] is None and record["issuer_cik"] is None and record["issuer_legal_name"] is None
                document = json.loads(raw)
                source = document["data"]["attributes"]
                assert source["entity"]["legalName"]["name"] == record["legal_name"]
                assert source["lei"] == record["entity_identifier"]["value"]
                assert start.isoformat() == evidence["published_at"][:10]
                assert document["meta"]["goldenCopy"]["publishDate"] == evidence["published_at"]
                assert record["entity_register_status"] == source["entity"]["status"]
                assert record["lei_registration_status"] == source["registration"]["status"]
                assert record["lei_conformity_flag"] == source["conformityFlag"]
            checked.update({"url": evidence["url"], "locator": evidence["locator"], "purpose": evidence["purpose"],
                            "published_at": evidence["published_at"], "published_at_semantics": evidence["published_at_semantics"],
                            "publication_time_verified": evidence["publication_time_verified"], "observed_at": evidence["observed_at"]})
            proof.append(checked)
        decisions.append({"record_id": record["record_id"], "decision": "ACCEPT_EVENT_OR_REGISTER_SNAPSHOT_DAY_ONLY",
                          "effective_from": record["effective_from"], "effective_to": record["effective_to"],
                          "relationship_role": record["relationship_role"], "known_at": record["known_at"], "proof": proof,
                          "continuous_legal_interval_verified": False, "financial_attribution_allowed": False})
    DatedCounterpartyIdentityRegistry(records)
    return records, {**provenance, "decisions": decisions,
                     "withheld": [{"entity": "Sikorsky Aircraft Corporation → LMT", "reason": "No proven common effective day between LEI snapshot and dated parent exhibit"}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("healthcare", "regulatory", "sec"):
        parser.add_argument(f"--{name}-root", type=Path, required=True)
    args = parser.parse_args()
    assert not (DIRECTORY / "adoption.json").exists(), "Preserve the completed review and use a new dated review for any later import"
    records_health, health_review = review_healthcare(args.healthcare_root)
    records_models, models_review = review_models(args.regulatory_root)
    records_sec, sec_review = review_counterparties(args.sec_root)
    paths = [DATA / "verified_healthcare_identities.json", DATA / "verified_nhtsa_models.json", DATA / "verified_counterparty_identities.json"]
    baseline_bytes = {str(path): path.read_bytes() if path.exists() else None for path in paths}
    preserved_13f = (DATA / "verified_13f_securities.json").read_bytes()
    health = read_json(paths[0])
    old_health = deepcopy(health["records"])
    assert not {row["record_id"] for row in old_health}.intersection(row["record_id"] for row in records_health)
    health["records"].extend(records_health)
    health["record_count"] = len(health["records"])
    HealthcareIdentityRegistry(health["records"])
    models = read_json(paths[1])
    old_models = deepcopy(models)
    models.extend(records_models)
    sec = {"schema_version": 1, "scope": "Four exact SEC filing-day direct issuer identities and one GLEIF legal-entity snapshot day; no continuous legal intervals, parent ownership bridge or financial attribution.", "records": records_sec}
    assert baseline_bytes[str(paths[2])] is None, "Review existing counterparty versions before appending"
    DatedCounterpartyIdentityRegistry(sec["records"])
    with TemporaryDirectory(prefix="batch3-reviewed-models-") as directory:
        path = Path(directory) / "models.json"
        path.write_text(json.dumps(models), encoding="utf-8")
        load_verified_models(path)
    # Every new object/model/entity has now passed both primary review and its
    # existing consumer's schema. Preserve prior records and clocks verbatim.
    outputs = [health, models, sec]
    for path, output in zip(paths, outputs):
        assert (path.read_bytes() if path.exists() else None) == baseline_bytes[str(path)]
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert (DATA / "verified_13f_securities.json").read_bytes() == preserved_13f
    assert health["records"][:len(old_health)] == old_health and models[:len(old_models)] == old_models
    audit = {"schema_version": 1, "reviewer": "Codex sole registry owner batch3/identity-20261012",
             "reviewed_at": datetime.now(timezone.utc).isoformat(), "status": "PRIMARY_REVIEWED_IMPORT",
             "healthcare": health_review, "nhtsa": models_review, "counterparties": sec_review,
             "added_records": {"healthcare": len(records_health), "nhtsa_models": len(records_models), "counterparties": len(records_sec)},
             "baseline_preservation": {"healthcare_records": len(old_health), "nhtsa_records": len(old_models),
                                       "records_and_knowledge_clocks_preserved": True, "13f_records": 150,
                                       "13f_sha256_before": sha(preserved_13f), "13f_sha256_after": sha((DATA / "verified_13f_securities.json").read_bytes())},
             "production_manifests": [{"path": str(path), "sha256_before": sha(baseline_bytes[str(path)]) if baseline_bytes[str(path)] is not None else None,
                                       "sha256_after": sha(path.read_bytes())} for path in paths],
             "score_changed": False, "specialist_completion_changed": False, "automatic_trading_enabled": False,
             "scope": "Exact archived healthcare letter events; two 2026 model observations; four filing-day direct issuers; one private legal-entity snapshot. No issuer-wide coverage or continuous/historical ownership inferred."}
    (DIRECTORY / "adoption.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"added_records": audit["added_records"], "13f_unchanged": True}, indent=2))
