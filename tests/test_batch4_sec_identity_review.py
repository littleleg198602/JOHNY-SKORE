from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
import unittest

from evidence.batch4_sec_20261013.build_review import build_review_artifact, repository_reference
from market_checker_app.services.sec_form4_identity_review import (
    IDENTITY_REVIEW_VERSION, review_form4_identities,
)
from market_checker_app.services.sec_form4_service import Form4ValidationError
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.storage.scout_store import ScoutStore
from tests.test_sec_form4_observation import NOW, URL, _Index, _parse, _owner, _xml


ROOT = Path(__file__).resolve().parents[1]


def _candidate(kind="OWNER_PERSON_IDENTITY", *, title="Class A Common Stock", derivative=False):
    # Explicitly synthetic primary-document fixtures, never production evidence.
    if kind == "OWNER_CIK_PROFILE":
        body = b'{"cik":"0000012345","name":"Jane Example","entityType":"other"}'
        url = "https://data.sec.gov/submissions/CIK0000012345.json"
        quote = '"name":"Jane Example"'
    else:
        quote = (f"Apple Inc. 0000320193 AAPL {title} CUSIP 037833100"
                 if kind == "INSTRUMENT_IDENTITY" else "Jane Example CIK 0000012345")
        body = f"<html><p>{quote}</p></html>".encode()
        url = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000999/identity.htm"
    digest = hashlib.sha256(body).hexdigest()
    record = {
        "record_id": kind.casefold(), "kind": kind, "source_url": url, "quote": quote,
        "sha256": digest, "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
        "known_at": NOW.isoformat(), "published_at": (NOW - timedelta(days=1)).isoformat(),
        "effective_from": "2026-10-09", "effective_to": "2026-10-10",
    }
    if kind == "INSTRUMENT_IDENTITY":
        record.update(issuer_cik="0000320193", ticker="AAPL", security_title=title,
                      derivative=derivative, instrument_identifier={"scheme": "CUSIP", "value": "037833100"})
    else:
        record.update(owner_cik="0000012345", owner_name="Jane Example")
    return record, {digest: body}


def _review(record, documents, *, observation=None, clock=NOW, **kwargs):
    return review_form4_identities(_parse() if observation is None else observation,
                                  knowledge_at=clock, evidence=[record], captured_documents=documents, **kwargs)


class Batch4SecIdentityReviewTests(unittest.TestCase):
    def test_repository_reference_serializes_windows_and_posix_to_identical_git_paths(self):
        expected = "evidence/batch3_sec_20261012/primary/msft_2026_R1.htm"
        references = []
        for root in (PurePosixPath("/checkout/JOHNY-SKORE"), PureWindowsPath(r"C:\checkout\JOHNY-SKORE")):
            with self.subTest(flavor=type(root).__name__):
                reference = repository_reference(root / expected, root=root)
                self.assertEqual(expected, reference)
                self.assertNotIn("\\", reference)
                references.append(json.dumps({"prior_path": reference}, sort_keys=True))
        self.assertEqual(references[0], references[1])

    def test_repository_reference_rejects_other_roots_drives_and_parent_traversal(self):
        for root, foreign in (
            (PurePosixPath("/checkout/repo"), PurePosixPath("/checkout/repo-neighbour/evidence/a.htm")),
            (PureWindowsPath(r"C:\checkout\repo"), PureWindowsPath(r"C:\checkout\repo-neighbour\evidence\a.htm")),
            (PureWindowsPath(r"C:\checkout\repo"), PureWindowsPath(r"D:\checkout\repo\evidence\a.htm")),
            (PurePosixPath("/checkout/repo"), PurePosixPath("/checkout/repo/evidence/../../a.htm")),
            (PureWindowsPath(r"C:\checkout\repo"), PureWindowsPath(r"C:\checkout\repo\evidence\..\..\a.htm")),
        ):
            with self.subTest(root=str(root), foreign=str(foreign)), self.assertRaises(ValueError):
                repository_reference(foreign, root=root)

    def test_source_owners_roles_rows_and_footnotes_do_not_verify_identity_or_mutate_source(self):
        original = _parse()
        before = deepcopy(original)
        report = review_form4_identities(original, knowledge_at=NOW)
        self.assertEqual(before, original)
        self.assertEqual(IDENTITY_REVIEW_VERSION, report["review_version"])
        self.assertEqual(original["reporting_owners"][0], report["reporting_owners"][0]["as_filed"])
        self.assertEqual(original["transactions"][0], report["instruments"][0]["as_filed"])
        self.assertFalse(report["reporting_owner_identity_independently_verified"])
        self.assertFalse(report["instrument_identity_independently_verified"])
        self.assertFalse(report["execution_verified"])
        self.assertFalse(report["scoring_applied"])
        report["instruments"][0]["as_filed"]["price_per_share"] = 0
        self.assertEqual(before, original)

    def test_actual_captured_evidence_is_hash_checked_and_reproducible(self):
        artifact = build_review_artifact()
        expected = json.loads((ROOT / "evidence/batch4_sec_20261013/identity_review.json").read_bytes())
        self.assertEqual(expected, artifact)
        self.assertEqual("0000789019", artifact["identity_review"]["issuer_cik"])
        self.assertEqual(8, len(artifact["identity_review"]["instruments"]))
        self.assertEqual("0001513142", artifact["identity_review"]["reporting_owners"][0]["as_filed"]["cik"])
        self.assertFalse(artifact["completion_verified"])
        self.assertTrue(artifact["raw_cover_revision"]["raw_bytes_changed"])
        self.assertTrue(artifact["raw_cover_revision"]["visible_text_unchanged"])

    def test_actual_profile_cik_match_remains_person_and_historical_gap(self):
        artifact = build_review_artifact()
        candidate = artifact["identity_review"]["reporting_owners"][0]["candidates"][0]
        self.assertEqual("other", candidate["capture"]["publisher_entity_type"])
        self.assertEqual({"OUTSIDE_DECLARED_EFFECTIVE_INTERVAL", "PERSON_KIND_NOT_ESTABLISHED_BY_CIK_PROFILE"},
                         set(candidate["reasons"]))
        self.assertTrue(artifact["identity_review"]["reporting_owners"][0]["as_filed"]["is_director"])
        self.assertFalse(candidate["independently_verified"])

    def test_actual_cover_does_not_alias_security_title_or_create_cusip(self):
        artifact = build_review_artifact()
        for instrument in artifact["identity_review"]["instruments"]:
            self.assertEqual("Common Stock", instrument["as_filed"]["security_title"])
            self.assertFalse(instrument["as_filed"]["derivative"])
            self.assertTrue(instrument["footnotes"])
            self.assertEqual({"EXACT_SECURITY_TITLE_MISMATCH", "OUTSIDE_DECLARED_EFFECTIVE_INTERVAL",
                              "EXACT_INSTRUMENT_IDENTIFIER_MISSING"}, set(instrument["candidates"][0]["reasons"]))
            self.assertFalse(instrument["candidates"][0]["independently_verified"])

    def test_actual_publication_and_capture_clocks_remain_distinct(self):
        clocks = build_review_artifact()["source_clocks"]
        self.assertEqual("2026-09-02", clocks["filing_date_raw"])
        self.assertEqual("2026-09-02 18:04:41", clocks["index_acceptance_datetime_unqualified_raw"])
        self.assertEqual("2026-09-02T22:04:41.000Z", clocks["submissions_acceptance_datetime_raw"])
        self.assertTrue(clocks["first_form4_bytes_observed_at"].startswith("2026-10-13"))
        self.assertTrue(clocks["acceptance_clock_timezone_not_inferred_by_review"])

    def test_exact_dated_person_candidate_is_review_candidate_without_promotion(self):
        record, documents = _candidate()
        candidate = _review(record, documents)["reporting_owners"][0]["candidates"][0]
        self.assertEqual("EXACT_DATED_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW", candidate["status"])
        self.assertEqual([], candidate["reasons"])
        self.assertFalse(candidate["independently_verified"])
        self.assertFalse(candidate["declared_effective_interval_verified"])

    def test_exact_dated_instrument_candidate_requires_review_despite_title_ticker_identifier(self):
        record, documents = _candidate("INSTRUMENT_IDENTITY")
        report = _review(record, documents)
        candidate = report["instruments"][0]["candidates"][0]
        self.assertEqual("EXACT_DATED_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW", candidate["status"])
        self.assertFalse(report["instrument_identity_independently_verified"])
        self.assertFalse(report["execution_verified"])

    def test_cik_profile_never_establishes_person_kind(self):
        record, documents = _candidate("OWNER_CIK_PROFILE")
        candidate = _review(record, documents)["reporting_owners"][0]["candidates"][0]
        self.assertEqual(["PERSON_KIND_NOT_ESTABLISHED_BY_CIK_PROFILE"], candidate["reasons"])

    def test_future_evidence_does_not_expose_quoted_facts_or_capture_provenance(self):
        record, documents = _candidate()
        record["observed_at"] = record["known_at"] = (NOW + timedelta(hours=1)).isoformat()
        candidate = _review(record, documents)["reporting_owners"][0]["candidates"][0]
        self.assertEqual(["NOT_YET_KNOWN"], candidate["reasons"])
        self.assertIsNone(candidate["capture"])

    def test_cannot_read_future_source_observation(self):
        with self.assertRaisesRegex(Form4ValidationError, "future source observation"):
            review_form4_identities(_parse(), knowledge_at=NOW - timedelta(seconds=1))

    def test_wrong_exact_name_cannot_be_resolved_by_owner_cik(self):
        record, documents = _candidate()
        source = _parse(_xml(owners=_owner(name="Other Example")))
        candidate = _review(record, documents, observation=source)["reporting_owners"][0]["candidates"][0]
        self.assertEqual(["EXACT_OWNER_NAME_MISMATCH"], candidate["reasons"])

    def test_class_and_derivative_type_cannot_be_resolved_by_same_issuer(self):
        record, documents = _candidate("INSTRUMENT_IDENTITY", title="Class B Common Stock", derivative=True)
        candidate = _review(record, documents)["instruments"][0]["candidates"][0]
        self.assertEqual({"EXACT_SECURITY_TITLE_MISMATCH", "DERIVATIVE_TYPE_MISMATCH"}, set(candidate["reasons"]))

    def test_raw_xsl_index_and_submission_links_in_one_accession_are_not_independent(self):
        for suffix in ("form4.xml", "xslF345X06/form4.xml", "0000320193-26-000123-index.htm", "file.txt"):
            record, documents = _candidate()
            record["source_url"] = URL.rsplit("/", 1)[0] + "/" + suffix + "?view=other#note"
            with self.subTest(suffix=suffix):
                candidate = _review(record, documents)["reporting_owners"][0]["candidates"][0]
                self.assertIn("SAME_FILING_DISCLOSURE", candidate["reasons"])

    def test_same_raw_document_bytes_at_other_accession_do_not_become_independent(self):
        record, documents = _candidate()
        candidate = _review(record, documents, source_document_sha256=record["sha256"])["reporting_owners"][0]["candidates"][0]
        self.assertEqual(["SAME_SOURCE_DOCUMENT_BYTES"], candidate["reasons"])

    def test_effective_to_is_exclusive_not_unbounded(self):
        record, documents = _candidate()
        record.update(effective_from="2026-10-08", effective_to="2026-10-09")
        candidate = _review(record, documents)["reporting_owners"][0]["candidates"][0]
        self.assertEqual(["OUTSIDE_DECLARED_EFFECTIVE_INTERVAL"], candidate["reasons"])
        record["effective_to"] = None
        with self.assertRaises(Form4ValidationError):
            _review(record, documents)

    def test_owner_report_date_candidate_cannot_cover_another_transaction_date(self):
        record, documents = _candidate()
        source = _parse()
        source["transactions"][0]["transaction_date"] = "2026-10-08"
        owner = _review(record, documents, observation=source)["reporting_owners"][0]
        self.assertEqual([], owner["candidates"][0]["reasons"])
        self.assertEqual(["OUTSIDE_DECLARED_EFFECTIVE_INTERVAL"],
                         owner["transaction_date_checks"]["2026-10-08"][0]["reasons"])

    def test_publication_clock_conflict_is_retained_and_withheld(self):
        record, documents = _candidate()
        record["published_at"] = (NOW + timedelta(days=1)).isoformat()
        candidate = _review(record, documents)["reporting_owners"][0]["candidates"][0]
        self.assertEqual(["PUBLISHER_PUBLICATION_CLOCK_CONFLICT"], candidate["reasons"])
        self.assertEqual(record["published_at"], candidate["capture"]["published_at"])

    def test_capture_hash_and_quote_assertions_remain_strict(self):
        record, documents = _candidate()
        documents[record["sha256"]] += b"changed"
        with self.assertRaisesRegex(Form4ValidationError, "bytes/hash conflict"):
            _review(record, documents)
        record, documents = _candidate()
        record["quote"] += " not stated by publisher"
        with self.assertRaisesRegex(Form4ValidationError, "absent"):
            _review(record, documents)

    def test_profile_wrong_exact_cik_name_or_endpoint_is_rejected(self):
        for field, value in (("owner_cik", "0000023456"), ("owner_name", "Other Example"),
                             ("source_url", "https://data.sec.gov/submissions/CIK0000023456.json")):
            record, documents = _candidate("OWNER_CIK_PROFILE")
            record[field] = value
            with self.subTest(field=field), self.assertRaises(Form4ValidationError):
                _review(record, documents)

    def test_backdated_knowledge_unsafe_source_and_invented_identifier_are_rejected(self):
        for field, value in (("known_at", (NOW - timedelta(days=1)).isoformat()),
                             ("source_url", "https://www.sec.gov.evil.test/Archives/edgar/data/320193/file.htm")):
            record, documents = _candidate()
            record[field] = value
            with self.subTest(field=field), self.assertRaises(Form4ValidationError):
                _review(record, documents)
        record, documents = _candidate("INSTRUMENT_IDENTITY")
        record["instrument_identifier"] = {"scheme": "CUSIP", "value": "594918104"}
        with self.assertRaisesRegex(Form4ValidationError, "quoted identifier"):
            _review(record, documents)

    def test_quoted_identifier_with_bad_check_digit_is_still_rejected(self):
        for scheme, value in (("CUSIP", "037833101"), ("ISIN", "US0378331006")):
            record, _ = _candidate("INSTRUMENT_IDENTITY")
            record["quote"] = f"Apple Inc. 0000320193 AAPL Class A Common Stock {scheme} {value}"
            body = f"<p>{record['quote']}</p>".encode()
            record["sha256"] = hashlib.sha256(body).hexdigest()
            record["instrument_identifier"] = {"scheme": scheme, "value": value}
            with self.subTest(scheme=scheme), self.assertRaisesRegex(Form4ValidationError, "quoted identifier"):
                _review(record, {record["sha256"]: body})

    def test_derivative_without_retained_underlying_is_explicit_field_gap(self):
        source = _parse()
        source["transactions"][0]["derivative"] = True
        source["transactions"][0]["security_title"] = "Employee Stock Option"
        instrument = review_form4_identities(source, knowledge_at=NOW)["instruments"][0]
        self.assertEqual("NOT_RETAINED_IN_SOURCE_OBSERVATION", instrument["underlying_security_scope"])
        self.assertEqual("WAIT_INDEPENDENT_INSTRUMENT_IDENTITY", instrument["status"])

    def test_joint_reporting_rows_are_unallocated_and_unknown_title_remains_unknown(self):
        source = _parse(_xml(owners=_owner() + _owner("23456", "Other Owner")))
        source["transactions"][0]["security_title"] = None
        instrument = review_form4_identities(source, knowledge_at=NOW)["instruments"][0]
        self.assertEqual("JOINT_REPORT_NOT_ALLOCATED", instrument["row_owner_scope"])
        self.assertEqual("WAIT_AS_FILED_SECURITY_TITLE", instrument["status"])

    def test_evidence_limits_and_duplicate_ids_reject_ambiguous_review(self):
        record, documents = _candidate()
        with self.assertRaisesRegex(Form4ValidationError, "duplicate"):
            review_form4_identities(_parse(), knowledge_at=NOW, evidence=[record, record], captured_documents=documents)
        with self.assertRaisesRegex(Form4ValidationError, "exceed limits"):
            review_form4_identities(_parse(), knowledge_at=NOW, evidence=[record] * 65, captured_documents=documents)
        source = _parse()
        source["transactions"] *= 1000
        records = [dict(record, record_id=str(index)) for index in range(5)]
        with self.assertRaisesRegex(Form4ValidationError, "comparisons exceed limits"):
            review_form4_identities(source, knowledge_at=NOW, evidence=records, captured_documents=documents)

    def test_scout_persists_review_separately_from_source_observation_without_repeat_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            client = _Index()
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL"], as_of=NOW)
            self.assertEqual("OK", scout.run_batch(as_of=NOW, limit=2)["status"])
            details = [json.loads(row["details_json"]) for row in store.findings_as_of("AAPL", as_of=NOW)]
            primary = next(row for row in details if row["stage"] == "filing_document")
            self.assertEqual(_parse(), primary["form4_observation"])
            self.assertEqual(IDENTITY_REVIEW_VERSION, primary["form4_identity_review"]["review_version"])
            self.assertEqual(hashlib.sha256(client.payload).hexdigest(),
                             primary["form4_identity_review"]["source_document_sha256"])
            self.assertFalse(primary["form4_identity_review"]["scoring_applied"])
            scout.schedule(["AAPL"], as_of=NOW + timedelta(minutes=1))
            self.assertEqual(0, scout.run_batch(as_of=NOW + timedelta(minutes=1), limit=2)["new_findings"])
            self.assertEqual(1, client.document_calls)


if __name__ == "__main__":
    unittest.main()
