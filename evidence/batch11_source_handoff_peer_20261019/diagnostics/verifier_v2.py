"""Independent local byte/content audit; never imports producers or runs suites.

Only the five declared batch11 source lanes and their identifiable retained pins
are read. Git operations inspect local objects only. No HTTP, SQL, original quota
database, unavailable body, registry mutation or interpretation replay is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import xml.etree.ElementTree as ET

SNAPSHOT = "970861da7309048a71e54c102ac482e1d24b07f8"
BASE = "6ba4f44bc3e1ca240615c91dbec5bf52c93f3846"
LIMIT = 8_000_000
HASH = re.compile(r"[a-f0-9]{64}")
LANES = {
    "identity": ("batch11_identity_20261018", "handoff.json", "captured_review_plan.json", "actual_review.json",
                 "sec_dated_instrument_point_review.py", "test_sec_dated_instrument_point_review.py", 22),
    "healthcare": ("batch11_healthcare_20261018", "handoff.json", "retained_review_plan.json", "source_review.json",
                   "fda_applicant_chronology_review.py", "test_fda_applicant_chronology_review.py", 27),
    "regulatory": ("batch11_regulatory_20261018", "interest_array_guard/handoff.json", "actual_review_plan.json",
                   "interest_array_guard/actual_review.json", "epa_frs_program_scope_review.py", "test_epa_frs_program_scope_review.py", 32),
    "contracts": ("batch11_contracts_20261018", "handoff_final.json", "actual_review_plan.json", "actual_current_review.json",
                  "usaspending_prime_scope_review.py", "test_usaspending_prime_scope_review.py", 35),
    "sec": ("batch11_sec_20261018", "handoff.json", "actual_review_plan.json", "person_role_temporal_review_v1.json",
            "sec_person_role_temporal_review.py", "test_sec_person_role_temporal_review.py", 37),
}
WITHHELD = {
    "evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html": "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
    "evidence/batch4_ir_20261013/primary/ir_authority.html": "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
    "evidence/batch5_ir_20261014/primary/ir_authority.html": "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1",
    "evidence/batch8_regulatory_20261015/raw/njd_case_page.html": "27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def walk(value, pointer="", parent=None, name=None):
    if isinstance(value, dict):
        yield pointer, value, parent, name
        for key, child in value.items():
            yield from walk(child, pointer+"/"+str(key).replace("~", "~0").replace("/", "~1"), value, key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, pointer+"/"+str(index), value, index)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Unqualified audit clock")
    return parsed.astimezone(timezone.utc)


class Audit:
    def __init__(self, root, snapshot):
        self.root, self.snapshot = root.resolve(), snapshot
        self.started = datetime.now(timezone.utc)
        self.findings, self.checks, self.declarations = [], [], []
        self.cache, self.documents, self.plan_inputs, self.derived = {}, {}, {}, defaultdict(list)
        self.tracked = self.git("ls-tree", "-r", "--name-only", snapshot).decode().splitlines()
        self.by_basename = defaultdict(list)
        for path in self.tracked:
            self.by_basename[PurePosixPath(path).name].append(path)
        self.scoped_files = [path for path in self.tracked if any(path.startswith("evidence/"+x[0]+"/") for x in LANES.values())]
        self.historical = defaultdict(list)
        self.inputs = []

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root)

    def safe(self, name):
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise ValueError("Invalid declared local pin")
        path = PurePosixPath(name)
        if path.is_absolute() or any(x in {"", ".", ".."} for x in name.split("/")):
            raise ValueError("Local pin escapes the selected repository")
        if (name in WITHHELD or name.startswith("outputs/") or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                or any(x in name.lower() for x in (".db-wal", ".db-shm", ".db-journal"))):
            raise ValueError("Forbidden unavailable-body/database path")
        target = self.root.joinpath(*path.parts)
        if any(x.is_symlink() for x in (target, *target.parents)) or not target.resolve().is_relative_to(self.root):
            raise ValueError("Declared local pin is a symlink/escape")
        return target

    def read(self, name):
        if name not in self.cache:
            target = self.safe(name)
            with target.open("rb") as stream:
                raw = stream.read(LIMIT+1)
            if len(raw) > LIMIT:
                raise ValueError("Audit retained-file bound exceeded")
            if sha(raw) in WITHHELD.values():
                raise ValueError("Known whole-withheld hash encountered; no substitute accepted")
            self.cache[name] = raw
        return self.cache[name]

    def pin(self, name):
        raw = self.read(name)
        return {"path": name, "bytes": len(raw), "sha256": sha(raw)}

    def check(self, label, condition, **evidence):
        record = {"check": label, "passed": bool(condition), **evidence}
        self.checks.append(record)
        if not condition:
            self.findings.append(record)
        return condition

    def load(self):
        self.check("EXACT_LOCAL_INPUT_SNAPSHOT", self.git("rev-parse", "HEAD").decode().strip() == self.snapshot)
        for path in self.scoped_files:
            if path.endswith(".json"):
                self.documents[path] = json.loads(self.read(path))
            elif "/source_versions/" in path or "/producer_versions/" in path or path.endswith(".py.txt"):
                self.historical[sha(self.read(path))].append(path)
        # Explicit path strings are a discovery index only; no arbitrary file or
        # original database is opened. Basenames resolve only when unambiguous.
        for path, value in list(self.documents.items()):
            for _, row, _, _ in walk(value):
                for key, item in row.items():
                    candidates = [key, item] if isinstance(item, str) else [key]
                    for candidate in candidates:
                        if isinstance(candidate, str) and candidate.startswith(("evidence/", "market_checker_app/", "tests/")):
                            if candidate in self.tracked:
                                self.read(candidate)
        for lane, config in LANES.items():
            folder, handoff, plan_name, report_name, module, tests, count = config
            prefix = "evidence/"+folder+"/"
            plan = self.documents[prefix+plan_name]
            report = self.documents[prefix+report_name]
            self.plan_inputs[lane] = plan
            for key, descriptor in plan["captures"].items():
                ref = descriptor["body_ref"]
                if descriptor["body_available"]:
                    self.check("PINNED_PLAN_BODY", ref is not None and self.pin(ref["path"]) == ref,
                               lane=lane, capture=key, pin=ref)
                    self.inputs.append({"lane": lane, "capture": key, **ref,
                        "body_role": descriptor["metadata"].get("body_role", "RETAINED_SOURCE_INPUT")})
                else:
                    self.check("UNAVAILABLE_PLAN_BODY_HAS_NO_SUBSTITUTE", ref is None, lane=lane, capture=key)
                self.derived[sha(canonical(descriptor["metadata"]))].append({"kind": "CANONICAL_PLAN_METADATA",
                    "document": prefix+plan_name, "capture": key})
            self.content(lane, plan, report)
            self.qualification(lane)

    def qualification(self, lane):
        folder, _, _, _, module, test, count = LANES[lane]
        prefix = "evidence/"+folder+"/"
        locations = {
            "identity": ("initial_tests.json", "initial_tests.log", "source_sha256"),
            "healthcare": ("qualified2.json", "qualified2.log", "source_sha256"),
            "regulatory": ("interest_array_guard/focused.json", "interest_array_guard/focused.log", "source_test_sha256"),
            "contracts": ("tests_current.json", "tests_current.log", "source_and_test_sha256"),
            "sec": ("focused_01.json", "focused_01.log", "source_pins"),
        }
        record_name, log_name, code_key = locations[lane]
        record = self.documents[prefix+record_name]
        log = self.read(prefix+log_name)
        pins = record[code_key]
        actual = {"market_checker_app/services/"+module: sha(self.read("market_checker_app/services/"+module)),
                  "tests/"+test: sha(self.read("tests/"+test))}
        if lane == "healthcare":
            wanted = {"market_checker_app/services/"+module: pins["producer"], "tests/"+test: pins["tests"]}
        else:
            wanted = {key: pins[key]["sha256"] if isinstance(pins[key], dict) else pins[key] for key in actual}
        self.check("CURRENT_QUALIFIED_PRODUCER_AND_TEST", actual == wanted, lane=lane, current=actual, declared=wanted)
        declared_log_hash = record.get("log_sha256")
        if lane == "contracts":
            declared_log_hash = self.documents[prefix+"handoff.json"]["pins"][prefix+log_name]["sha256"]
        self.check("CURRENT_COMPLETE_LOG_CONTENT", sha(log) == declared_log_hash
            and len(re.findall(rb"^test_[^\n]+ \.\.\. ok$", log, re.M)) == count
            and re.search(rb"Ran "+str(count).encode()+rb" tests? in ", log) is not None
            and log.rstrip().endswith(b"OK")
            and record["discovered"] == record["executed"] == count
            and all(record[key] == 0 for key in ("failures", "errors", "skips", "expected_failures", "unexpected_successes")),
            lane=lane, qualified_methods=count, log=self.pin(prefix+log_name), suites_rerun=0)

    def content(self, lane, plan, outer):
        review = outer.get("review", outer)
        if lane == "identity":
            self.check("IDENTITY_POINT_IS_PARTIAL_NO_CONTINUITY", review["status"] == "DATED_INSTRUMENT_POINT_PARTIAL"
                and review["point"] is None and not review["point_supported"]
                and review["requested_anchor_on"] == "2026-08-26"
                and review["schedule_instrument_fact"]["event_on"] == "2026-03-13"
                and review["cover_security_fact"]["symbol"] == "P"
                and review["cover_security_fact"]["class_title"].startswith("Class A Common Stock")
                and review["schedule_instrument_fact"]["class_title"] == "Common Stock"
                and all(not review[key] for key in ("continuity_verified", "issued_validity_verified", "automatic_alias_allowed",
                    "interval_extension_allowed", "historical_availability_inferred", "production_registry_changed",
                    "registry_adoptions", "new_verified_identities", "current_q3_verified_instrument_coverage", "scoring_allowed", "trading_allowed")))
            original = dict(review); digest = original.pop("review_sha256")
            self.check("IDENTITY_CANONICAL_REPORT_DIGEST", sha(canonical(original)) == digest)
            self.derived[digest].append({"kind": "CANONICAL_REPORT_WITHOUT_OWN_DIGEST"})
            for key, pin in review["capture_pins"].items():
                self.check("IDENTITY_CAPTURE_HASH_AND_CANONICAL_METADATA", pin["sha256"] == plan["captures"][key]["body_ref"]["sha256"]
                    and pin["metadata_sha256"] == sha(canonical(plan["captures"][key]["metadata"])), capture=key)
            source = plan["captures"]["cover"]["body_ref"]["path"]
            for name in ("header_pin", "security_pin", "registration_section"):
                span = review["cover_security_fact"][name]
                self.range_hash(source, span["byte_start"], span["byte_end"], span["sha256"], "IDENTITY_"+name)
            self.check("IDENTITY_ROUTE_CONFLICT_NOT_ERASED", "CAPTURED_SUBJECT_ROUTE_DIFFERS_FROM_INDEX_FILER_ROUTE" in review["blockers"]
                and review["schedule_publication_proof"]["publication_at"] is None)
        elif lane == "healthcare":
            point = review["roster_points"][0]
            self.check("HEALTHCARE_MISSING_ENDPOINTS_STAY_UNKNOWN", review["status"] == "PARTIAL"
                and [row["source_roster_as_of"] for row in review["roster_points"]] == ["2017-12-31", None, None]
                and len(point["entities"][0]["literal_listed_rows"]) == 0
                and len(point["entities"][1]["literal_listed_rows"]) == 1
                and point["entities"][1]["literal_listed_rows"][0]["legal_entity_name_as_displayed"] == "Pfizer Ireland Pharmaceuticals"
                and point["entities"][0]["universe_state"] == "UNKNOWN_IN_INCOMPLETE_ROSTER"
                and not point["universe"]["complete"]
                and all(not review[key] for key in ("ownership_start_verified", "continuous_control_verified", "product_owner_verified",
                    "issuer_attribution_allowed", "last_observation_carried_forward", "spelling_aliases_created",
                    "approval_date_is_ownership_since", "historical_first_availability_inferred", "registry_adoptions", "trading_allowed")))
            app = review["application_points"]
            self.check("HEALTHCARE_ROLE_AND_UNCLOSED_SOURCE_LIMITS", [row["source_party_role"] for row in app] == ["BLA_APPLICANT", "COMPANY_AS_DISPLAYED"]
                and app[0]["original_container_explicitly_closed"] is False
                and app[1]["original_container_explicitly_closed"] is None
                and [row["product_number"] for row in app[0]["product_rows"]] == ["001", "002"]
                and app[1]["product_rows"][0]["product_number"] is None
                and review["application_comparisons"] == [])
        elif lane == "regulatory":
            descriptor = next(iter(plan["captures"].values()))
            self.check("EPA_MISSING_ORIGINAL_AND_UNKNOWN_COUNTS_PRESERVED", descriptor["body_ref"] is None
                and descriptor["body_available"] is False and descriptor["metadata"]["archive_path"] is None
                and descriptor["metadata"]["raw_response_bytes"] is None
                and descriptor["metadata"]["raw_response_sha256"] == "df9d373119eb5ecbdc01b8034aa42f6d382bbdb197f99bf20d937606936f1cd3"
                and review["source_review"]["reason"] == "MISSING_OR_WHOLE_WITHHELD_ORIGINAL"
                and review["publisher_query_rows"] is None and review["observed_program_rows"] is None
                and review["facility_field_evidence"] == review["program_inventory"] == review["selected_program_reviews"] == []
                and all(not review[key] for key in ("facility_identity_supported", "program_inventory_supported", "legal_owner_verified",
                    "permitted_operator_verified", "issuer_identity_verified", "continuous_issuer_exposure_verified",
                    "negative_facility_or_program_absence_verified", "environmental_liability_verified", "live_acceptance_verified",
                    "registry_adoption_allowed", "historical_availability_reconstructed", "scoring_applied")))
        elif lane == "contracts":
            self.check("PRIME_MISSING_QUERY_AND_RAW_PINS_STAY_UNKNOWN", plan["request"]["query"] is None
                and all(page["body_sha256"] is None and page["manifest_sha256"] is None for page in plan["request"]["pages"])
                and review["status"] == "WAIT" and review["query_scope"] is None and review["source_inputs_known_at"] is None
                and review["reported_unique_awards"] is None and review["pages"] == review["award_observations"] == []
                and all(row["expected_sha256"] is None and row["hash_pin_known"] is False for row in review["missing_evidence"])
                and all(not review[key] for key in ("query_complete", "observed_page_chain_complete", "publisher_revision_semantics_verified",
                    "source_revision_history_complete", "genuine_negative_verified", "selected_award_absence_verified",
                    "recipient_legal_identity_verified", "issuer_coverage_complete", "earlier_uei_relationship_inferred",
                    "historical_availability_reconstructed", "revenue_or_cumulative_difference_inferred", "registry_adoptions", "trading_allowed")))
            legacy = review["legacy_observations"][0]
            ref = plan["captures"]["legacy_lmt_report"]["body_ref"]
            body = json.loads(self.read(ref["path"]))
            selected = body["cases"]["usaspending_positive_contract_uei"]["payload"]
            self.check("PRIME_LEGACY_100_ROW_REPORT_IS_NOT_ORIGINAL", len(selected["results"]) == legacy["reported_rows"] == 100
                and legacy["reported_has_next"] is True and legacy["original_http_body"] is False
                and legacy["whole_original_request_present"] is False and legacy["may_supply_missing_prime_pages"] is False)
        else:
            row = review["transaction"]
            path = plan["captures"]["account_packet"]["body_ref"]["path"]
            xml = self.read(path)[row["original_packet_xml_byte_start"]:row["original_packet_xml_byte_end"]]
            self.range_hash(path, row["original_packet_xml_byte_start"], row["original_packet_xml_byte_end"], row["original_xml_sha256"], "SEC_ORIGINAL_XML")
            self.range_hash(path, row["original_packet_row_byte_start"], row["original_packet_row_byte_end"], row["original_row_sha256"], "SEC_ORIGINAL_ROW")
            self.check("SEC_ORIGINAL_DATE_ELEMENT_HASH", sha(row["original_date_element_quote"].encode()) == row["original_date_element_sha256"]
                and row["original_date_element_quote"].encode() in xml and len(xml) == row["original_xml_bytes"])
            self.derived[row["original_date_element_sha256"]].append({"kind":"ORIGINAL_DATE_ELEMENT_IN_PINNED_XML", "path":path})
            tree = ET.fromstring(xml)
            transactions = [item for item in tree.iter() if item.tag.split("}")[-1] == row["kind"]]
            chosen = transactions[row["ordinal"]-1]
            actual_day = next(item.text for node in chosen for item in node if node.tag.split("}")[-1] == "transactionDate" and item.tag.split("}")[-1] == "value")
            self.check("SEC_ROLE_PRESENT_NOT_EXTENDED_TO_2026_TRANSACTION", actual_day == row["date_literal"] == "2026-08-31"
                and review["selected_role_temporal_assertion"]["source_extent_upper_day"] == "2025-10-21"
                and review["selected_role_temporal_assertion"]["source_temporal_fit"] == "OUTSIDE_SOURCE_ASSERTION_SCOPE"
                and review["selected_role_temporal_assertion"]["source_assertion_temporally_compatible"] is False
                and review["source_temporal_assessment_supported"] is True
                and all(not review[key] for key in ("natural_person_account_binding_verified", "exact_role_interval_verified", "continuous_role_verified",
                    "approved_name_alias", "issuer_attribution_allowed", "historical_machine_availability_inferred", "execution_verified",
                    "instrument_identity_verified", "registry_modified", "full_specialist_acceptance", "scoring_applied", "trading_allowed")))
        start_key = "review_started_at" if "review_started_at" in review else "review_requested_at"
        end_key = "known_at" if "known_at" in review else "knowledge_at"
        if lane == "sec":
            start_key = "review_ingested_at"
        start, end = timestamp(review[start_key]), timestamp(review[end_key])
        self.check("ACTUAL_REPORT_PHYSICAL_CLOCK_ORDER", start <= end, lane=lane, start=review[start_key], end=review[end_key])
        supported_clocks = []
        for _, item, _, _ in walk(review):
            for key in ("known_at", "knowledge_at", "review_completed_at", "review_available_at", "source_known_at"):
                value = item.get(key)
                if isinstance(value, str):
                    supported_clocks.append(timestamp(value))
        self.check("ACTUAL_NESTED_SUPPORT_CLOCKS_NOT_AFTER_OUTER_END", all(value <= end for value in supported_clocks), lane=lane,
                   supporting_clocks_inspected=len(supported_clocks))

    def range_hash(self, path, start, end, digest, kind):
        raw = self.read(path)
        passed = type(start) is int and type(end) is int and 0 <= start < end <= len(raw) and sha(raw[start:end]) == digest
        self.check(kind, passed, path=path, byte_start=start, byte_end=end, sha256=digest)
        if passed:
            self.derived[digest].append({"kind":"ORIGINAL_BYTE_RANGE", "path":path,"byte_start":start,"byte_end":end})

    def resolve(self, name):
        if name.startswith(("evidence/", "market_checker_app/", "tests/")):
            return name
        matches = self.by_basename.get(name, [])
        return matches[0] if len(matches) == 1 else None

    def hash_claims(self):
        # Capture files are indexed before hash-only references are resolved.
        for path, value in self.documents.items():
            for _, row, _, _ in walk(value):
                for field in ("body_path", "archive_path", "metadata_path"):
                    if isinstance(row.get(field), str):
                        resolved = self.resolve(row[field])
                        if resolved and resolved in self.tracked and resolved not in WITHHELD:
                            self.read(resolved)
        matches = defaultdict(list)
        for path, raw in self.cache.items():
            matches[sha(raw)].append(path)
        for doc, value in self.documents.items():
            for pointer, row, parent, name in walk(value):
                for key, digest in row.items():
                    if not isinstance(digest, str) or not HASH.fullmatch(digest):
                        continue
                    nominal, expected_size = None, None
                    if key.startswith(("evidence/", "market_checker_app/", "tests/")):
                        nominal = key
                    if key == "sha256":
                        if isinstance(row.get("path"), str): nominal = self.resolve(row["path"])
                        elif isinstance(name, str) and name.startswith(("evidence/", "market_checker_app/", "tests/")): nominal = name
                        elif isinstance(parent, dict) and isinstance(name, str) and name.endswith("_pin"):
                            if isinstance(parent.get(name[:-4]+"_path"), str): nominal = self.resolve(parent[name[:-4]+"_path"])
                        expected_size = row.get("bytes", row.get("size_bytes"))
                    if key.endswith("_sha256"):
                        stem = key[:-7]
                        if isinstance(row.get(stem+"_path"), str): nominal = self.resolve(row[stem+"_path"])
                        expected_size = row.get(stem+"_bytes", expected_size)
                    declared = {"document":doc,"pointer":pointer+"/"+str(key),"declared_sha256":digest,"nominal_path":nominal,
                                "declared_bytes":expected_size}
                    if digest in self.derived:
                        declared.update(status="VERIFIED_DERIVED_VALUE", evidence=self.derived[digest])
                    elif nominal is not None:
                        try:
                            raw = self.read(nominal)
                            if sha(raw) == digest:
                                declared.update(status="VERIFIED_LOCAL_FILE", resolved_path=nominal, actual_bytes=len(raw))
                            elif nominal.startswith(("market_checker_app/", "tests/")) and self.historical.get(digest):
                                declared.update(status="VERIFIED_PRESERVED_HISTORICAL_COPY_CURRENT_PATH_DIFFERS",
                                    resolved_path=self.historical[digest][0], current_path_sha256=sha(raw), actual_bytes=len(self.read(self.historical[digest][0])))
                            else:
                                declared.update(status="MISMATCHED_DECLARED_LOCAL_PIN", actual_sha256=sha(raw),actual_bytes=len(raw))
                        except (OSError, ValueError) as error:
                            declared.update(status="MISSING_OR_FORBIDDEN_DECLARED_LOCAL_PIN", reason=type(error).__name__)
                    elif matches.get(digest):
                        selected = matches[digest][0]
                        declared.update(status="VERIFIED_HASH_RESOLVED_IN_DECLARED_SCOPE",resolved_path=selected,actual_bytes=len(self.read(selected)))
                    else:
                        declared.update(status="NO_IDENTIFIABLE_LOCAL_WHOLE_FILE_OR_RETAINED_DERIVATION",
                            interpretation="not credited as a verified file pin; may be an unavailable raw hash, transient child result or publisher plan hash")
                    if expected_size is not None and "actual_bytes" in declared and (type(expected_size) is not int or expected_size != declared["actual_bytes"]):
                        declared["status"] = "MISMATCHED_DECLARED_LOCAL_SIZE"
                    if declared["status"].startswith(("MISMATCHED", "MISSING_OR_FORBIDDEN")):
                        self.findings.append(declared)
                    self.declarations.append(declared)

    def old_evidence(self):
        def tree(commit):
            result = {}
            for raw in self.git("ls-tree", "-r", "-l", "-z", commit, "evidence").split(b"\0"):
                if raw:
                    header, path = raw.split(b"\t",1); mode, kind, blob, size = header.decode().split()
                    result[path.decode()]={"mode":mode,"kind":kind,"git_blob":blob,"bytes":int(size)}
            return result
        before, after = tree(BASE), tree(self.snapshot)
        changed = [path for path, old in before.items() if after.get(path) != old]
        self.check("ALL_6BA_EVIDENCE_GIT_BLOBS_UNCHANGED", not changed, base=BASE,snapshot=self.snapshot,
                   prior_files=len(before),prior_bytes=sum(x["bytes"] for x in before.values()),changed_paths=changed)
        for path,digest in WITHHELD.items():
            self.check("KNOWN_WHOLE_WITHHELD_PATH_ABSENT_WITHOUT_READING", path not in after and not (self.root/path).exists(),
                       path=path,known_sha256=digest)
        self.check("NO_OLDER_TRACKED_PATH_CHANGED_BY_PEER", self.git("diff","--name-only",self.snapshot) == b"")

    def run(self):
        self.load()
        self.hash_claims()
        self.old_evidence()
        finish = datetime.now(timezone.utc)
        return {"schema_version":"batch11_five_source_handoff_peer_v1","snapshot_commit":self.snapshot,
            "snapshot_tree":self.git("rev-parse",self.snapshot+"^{tree}").decode().strip(),"old_evidence_base":BASE,
            "audit_started_at":self.started.isoformat(),"audit_completed_at":finish.isoformat(),"known_at":finish.isoformat(),
            "status":"FINDINGS" if self.findings else "SCOPED_CONTENT_AND_IDENTIFIABLE_FILE_PINS_CHECKED",
            "five_current_handoffs":{lane:self.pin("evidence/"+cfg[0]+"/"+cfg[1]) for lane,cfg in LANES.items()},
            "documents_inspected":len(self.documents),"read_scope_pins":[self.pin(path) for path in sorted(self.cache)],
            "input_bodies":self.inputs,"checks":self.checks,"findings":self.findings,
            "hash_declaration_status_counts":dict(Counter(x["status"] for x in self.declarations)),"hash_declarations":self.declarations,
            "historical_vs_current":"Historical copies are byte-verified separately and never credited as the active source qualification; contracts34/regulatory31 and healthcare earlier failures remain their own versions.",
            "limits":["No source collection, new primary evidence, production interpretation replay or test suite was performed.",
                "Hashes without an identifiable retained whole-file path or retained derivation are explicitly not credited.",
                "Original EPA bytes/length and prime request/page pins remain unavailable; this audit does not recover them.",
                "Literal source observations do not establish adoption, legal ownership, continuity, complete negatives, historical machine availability, Windows/OOS or specialist acceptance.",
                "The original quota database was neither opened, read, hashed, copied nor mutated."],
            "source_HTTP_search_collector_SQL_originalDB_actions":0,"suites_or_tests_rerun":0,
            "source_registry_model_score_budget_trading_or_shared_file_mutations":0,"acceptance_or_adoption_claimed":False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-root",type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument("--snapshot",default=SNAPSHOT)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    auditor=Audit(args.source_root,args.snapshot)
    report=auditor.run()
    report["verifier"]={"path":"evidence/batch11_source_handoff_peer_20261019/audit_source_handoffs.py",
        "bytes":len(Path(__file__).read_bytes()),"sha256":sha(Path(__file__).read_bytes())}
    if args.output is not None:
        target=args.output.resolve(); allowed=(args.source_root/"evidence/batch11_source_handoff_peer_20261019").resolve()
        if not target.is_relative_to(allowed):raise ValueError("Audit output must stay in its owned new folder")
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open("xb") as stream:stream.write((json.dumps(report,indent=2,sort_keys=True)+"\n").encode())
    print(json.dumps({"status":report["status"],"findings":report["findings"],"documents":report["documents_inspected"],
        "read_scope_files":len(report["read_scope_pins"]),"hash_statuses":report["hash_declaration_status_counts"],
        "checks":len(report["checks"]),"source_HTTP_SQL_test_actions":0},sort_keys=True))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
