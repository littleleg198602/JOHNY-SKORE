"""Original-byte FRS review regressions; all transport remains disabled."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.epa_frs_ownership_review import (
    import_frs_ownership_review, review_frs_ownership,
)

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "evidence/batch6_regulatory_20261014"
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
ID = "110019986586"
QUOTE = f"Synthetic Owner Inc. is the owner of EXAMPLE FACILITY (FRS Registry ID {ID}) as of 2025-12-31."


def html(passage=QUOTE, name="Synthetic Owner Inc.", cik="0000000001"):
    return f'''<html xmlns:dei="http://xbrl.sec.gov/dei/2025"
        xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance">
        <xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
        <ix:nonnumeric name="dei:EntityRegistrantName" contextref="c1">{name}</ix:nonnumeric>
        <ix:nonnumeric name="dei:EntityCentralIndexKey" contextref="c1">{cik}</ix:nonnumeric>
        <p>{passage}</p></html>'''.encode()


def fixture(directory):
    row = {"RegistryId": ID, "FacilityName": "EXAMPLE FACILITY", "OperatorName": "Synthetic Owner Inc.",
        "ProgramFacilities": [{"ProgramSystemAcronym": "RCRAINFO", "ProgramSystemId": "SYNTHETIC-ID",
                               "ProgramFacilityName": "EXAMPLE FACILITY"}]}
    payload = {"Results": {"Message": "Success", "QueryRows": "1", "Facilities": [row]}}
    captures = []
    bodies = [("facility", json.dumps(payload).encode(), "https://ofmpub.epa.gov/frs_public2/frs_rest_services.get_facilities?registry_id="+ID,
               "application/json"),
              ("issuer", html(), "https://www.sec.gov/Archives/edgar/data/1/synthetic/filing.htm", "text/html")]
    for capture_id, body, url, mime in bodies:
        path = Path(directory) / (capture_id + ".raw")
        path.write_bytes(body)
        captures.append({"capture_id": capture_id, "requested_url": url, "final_url": url,
            "http_status": 200, "content_type": mime, "started_at": (NOW-timedelta(days=1)).isoformat(),
            "observed_at": (NOW-timedelta(days=1)).isoformat(), "archive_path": path.name,
            "raw_response_sha256": hashlib.sha256(body).hexdigest(), "raw_response_bytes": len(body),
            "public_raw_available": True, "origin": "SYNTHETIC_TEST"})
    proposal = {"ticker": "ONE", "issuer_cik": "0000000001", "issuer_name": "Synthetic Owner Inc.",
        "issuer_anchor_date": "2025-12-31", "frs_registry_id": ID, "facility_name": "EXAMPLE FACILITY",
        "owner_entity_name": "Synthetic Owner Inc.", "ownership_date": "2025-12-31",
        "facility_pin": {"capture_id": "facility", "json_pointer": "/Results/Facilities/0"},
        "issuer_pin": {"capture_id": "issuer"},
        "ownership_pin": {"capture_id": "issuer", "excerpt": QUOTE, "effective_date_text": "2025-12-31"}}
    return proposal, captures


def replace_body(directory, capture, body):
    (Path(directory)/capture["archive_path"]).write_bytes(body)
    capture.update(raw_response_bytes=len(body), raw_response_sha256=hashlib.sha256(body).hexdigest())


class Batch6FrsOwnershipTests(unittest.TestCase):
    def review(self, directory, proposal, captures, **kwargs):
        with patch("socket.create_connection", side_effect=AssertionError("No source HTTP permitted")):
            return review_frs_ownership(proposal, captures, repository_root=Path(directory),
                                        ingested_at=kwargs.pop("ingested_at", NOW), **kwargs)

    def test_exact_original_scopes_are_reviewable_but_never_adopted_or_liability(self):
        with TemporaryDirectory() as directory:
            proposal, captures = fixture(directory)
            report = self.review(directory, proposal, captures)
            self.assertEqual("REVIEWABLE_SOURCE_CANDIDATE", report["status"])
            self.assertTrue(report["facility_identity_supported"])
            self.assertTrue(report["issuer_identity_supported"])
            self.assertTrue(report["ownership_statement_supported"])
            self.assertEqual("RCRAINFO", report["program_bindings"][0]["ProgramSystemAcronym"])
            self.assertEqual(NOW.isoformat(), report["known_at"])
            self.assertEqual("UNVERIFIED", report["verification_status"])
            for flag in ("issuer_attribution_allowed", "issuer_facility_relationship_verified",
                         "environmental_liability_verified", "issuer_fine_verified", "issuer_conviction_verified",
                         "complete_facility_group_verified", "historical_availability_reconstructed",
                         "live_acceptance_verified", "registry_adoption_allowed", "scoring_applied", "prediction_input"):
                self.assertIs(False, report[flag])

    def test_actual_dow_summary_cannot_reconstruct_missing_epa_original_body(self):
        proposal = json.loads((FOLDER/"proposal.json").read_text())
        captures = json.loads((FOLDER/"capture_plan.json").read_text())
        old = ROOT/"evidence/epa_echo_live_20261002.json"
        original = old.read_bytes()
        clock = max(datetime.fromisoformat(item["observed_at"]) for item in captures) + timedelta(seconds=1)
        report = self.review(ROOT, proposal, captures, ingested_at=clock)
        self.assertEqual("PARTIAL", report["status"])
        self.assertFalse(report["facility_identity_supported"])
        self.assertFalse(report["ownership_statement_supported"])
        self.assertTrue(report["issuer_identity_supported"])
        self.assertEqual({"issuer_name": "Dow Inc.", "issuer_cik": "0001751788", "anchor_date": "2025-12-31", "context_ref": "c-2"},
                         report["issuer_identity_fact"])
        self.assertEqual("MISSING_OR_WITHHELD_ORIGINAL_BODY", report["captures"][0]["review_reason"])
        self.assertEqual(original, old.read_bytes())

    def test_actual_cofiler_name_cannot_inherit_parent_context_cik(self):
        proposal = json.loads((FOLDER/"proposal.json").read_text())
        captures = json.loads((FOLDER/"capture_plan.json").read_text())
        proposal["issuer_name"] = "The Dow Chemical Company"
        clock = max(datetime.fromisoformat(item["observed_at"]) for item in captures) + timedelta(seconds=1)
        report = self.review(ROOT, proposal, captures, ingested_at=clock)
        self.assertFalse(report["issuer_identity_supported"])
        self.assertIn("MISSING_SAME_CONTEXT_DATED_SEC_LEGAL_ENTITY", report["missing_evidence"])

    def test_operator_or_parent_passage_is_not_facility_ownership(self):
        for replacement in (QUOTE.replace("is the owner of", "is the operator of"),
                            QUOTE.replace("is the owner of", "is not the owner of"),
                            "Synthetic Owner Inc. is the direct parent company of a subsidiary as of 2025-12-31."):
            with self.subTest(passage=replacement), TemporaryDirectory() as directory:
                proposal, captures = fixture(directory)
                replace_body(directory, captures[1], html(replacement))
                proposal["ownership_pin"]["excerpt"] = replacement
                report = self.review(directory, proposal, captures)
                self.assertTrue(report["facility_identity_supported"])
                self.assertFalse(report["ownership_statement_supported"])
                self.assertEqual("PARTIAL", report["status"])

    def test_exact_frs_name_date_and_body_pins_cannot_be_weakened(self):
        for changed in ("hash", "name", "id", "date", "scope"):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                proposal, captures = fixture(directory)
                if changed == "hash":
                    (Path(directory)/captures[0]["archive_path"]).write_bytes(b"changed original bytes")
                elif changed == "name":
                    proposal["facility_name"] += "."
                elif changed == "id":
                    proposal["frs_registry_id"] = "110019986587"
                elif changed == "date":
                    proposal["ownership_date"] = "2025-12-30"
                else:
                    captures[0]["final_url"] += "&different_scope=1"
                report = self.review(directory, proposal, captures)
                self.assertEqual("PARTIAL", report["status"])
                self.assertFalse(report["ownership_statement_supported"] if changed == "date"
                                 else report["facility_identity_supported"])

    def test_failed_truncated_duplicate_and_invalid_program_payloads_stay_partial(self):
        for changed in ("message", "error", "duplicate_frs", "duplicate_program", "truncated"):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                proposal, captures = fixture(directory)
                payload = json.loads((Path(directory)/captures[0]["archive_path"]).read_bytes())
                results = payload["Results"]
                if changed == "message": results["Message"] = "Failure"
                elif changed == "error": results["Error"] = "source unavailable"
                elif changed == "duplicate_frs":
                    results["Facilities"] *= 2; results["QueryRows"] = "2"
                elif changed == "duplicate_program": results["Facilities"][0]["ProgramFacilities"] *= 2
                else: results["QueryRows"] = "3"
                replace_body(directory, captures[0], json.dumps(payload).encode())
                report = self.review(directory, proposal, captures)
                self.assertEqual("PARTIAL", report["status"])
                if changed != "truncated": self.assertFalse(report["facility_identity_supported"])
                else: self.assertIn("INCOMPLETE_FACILITY_QUERY", report["missing_evidence"])

    def test_hidden_or_copied_passage_cannot_be_independent_ownership(self):
        for changed in ("hidden", "same_capture", "same_hash"):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                proposal, captures = fixture(directory)
                if changed == "hidden":
                    replace_body(directory, captures[1], html('<span hidden>'+QUOTE+'</span>'))
                elif changed == "same_capture": proposal["ownership_pin"]["capture_id"] = "facility"
                else: captures[1]["raw_response_sha256"] = captures[0]["raw_response_sha256"]
                self.assertFalse(self.review(directory, proposal, captures)["ownership_statement_supported"])

    def test_physical_import_cutoff_cannot_backdate_or_read_later_evidence(self):
        with TemporaryDirectory() as directory:
            proposal, captures = fixture(directory)
            with patch.object(Path, "open", side_effect=AssertionError("Future import must not read body")):
                report = self.review(directory, proposal, captures, as_of=NOW-timedelta(microseconds=1))
            self.assertEqual(["NOT_YET_KNOWN_AT_PHYSICAL_IMPORT"], report["missing_evidence"])
            self.assertFalse(report["facility_identity_supported"])
            self.assertFalse(report["issuer_identity_supported"])
            captures[0]["observed_at"] = (NOW+timedelta(seconds=1)).isoformat()
            self.assertEqual("INVALID_SOURCE_CAPTURE_CLOCK", self.review(directory, proposal, captures)["captures"][0]["review_reason"])

    def test_missing_and_sensitive_bodies_never_create_replacement_raw_archives(self):
        with TemporaryDirectory() as directory:
            proposal, captures = fixture(directory)
            replace_body(directory, captures[0], b'{"azure_map_key":null}')
            report = self.review(directory, proposal, captures)
            self.assertEqual("WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE", report["captures"][0]["review_reason"])
            self.assertIsNone(report["captures"][0]["archive_path"])
            captures[0].update(public_raw_available=False, archive_path=None)
            captures[1].update(public_raw_available=False, archive_path=None)
            with patch.object(Path, "open", side_effect=AssertionError("Unavailable bodies must not be opened")):
                report = self.review(directory, proposal, captures)
            self.assertEqual("PARTIAL", report["status"])

    def test_bound_and_repository_paths_and_duplicate_capture_ids_are_strict(self):
        with TemporaryDirectory() as directory:
            proposal, captures = fixture(directory)
            for rows in ([], captures*3, captures+[deepcopy(captures[0])]):
                with self.assertRaises(ValueError): self.review(directory, proposal, rows)
            captures[0]["archive_path"] = "../outside.raw"
            self.assertEqual("NOT_AN_ORIGINAL_REPOSITORY_BODY", self.review(directory, proposal, captures)["captures"][0]["review_reason"])
            with self.assertRaises(ValueError): self.review(directory, proposal, captures, ingested_at=NOW.replace(tzinfo=None))

    def test_import_uses_actual_physical_clock_and_persists_review_only(self):
        with TemporaryDirectory() as directory:
            proposal, captures = fixture(directory)
            before = {p.name:p.read_bytes() for p in Path(directory).iterdir()}
            start = datetime.now(timezone.utc)
            result = import_frs_ownership_review(proposal, captures, repository_root=Path(directory),
                                                archive_root=Path(directory)/"reviews")
            end = datetime.now(timezone.utc)
            self.assertLessEqual(start, datetime.fromisoformat(result["known_at"]))
            self.assertLessEqual(datetime.fromisoformat(result["known_at"]), end)
            self.assertEqual(before, {p.name:p.read_bytes() for p in Path(directory).iterdir() if p.is_file()})
            self.assertEqual(["review.json"], [p.name for p in (Path(directory)/"reviews").rglob("*") if p.is_file()])
            stored = json.loads(Path(result["review_path"]).read_text())
            self.assertFalse(stored["registry_adoption_allowed"])


if __name__ == "__main__":
    unittest.main()
