from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.models import RunMetadata
from market_checker_app.prediction_contract import build_point_in_time_snapshot
from market_checker_app.prediction_label_runner import resolve_prediction_labels
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.services.specialist_label_availability_service import load_label_observations
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.storage.sqlite_store import SQLiteStore
from tests.test_prediction_label_runner import _history
from tests.test_sec_form4_observation import NOW, _Index, _analysis


def _pending_store(directory):
    store = SQLiteStore(Path(directory) / "history.db")
    stamp = datetime(2026, 1, 2, 22, tzinfo=timezone.utc)
    store.ensure_schema()
    run_id = store.insert_run(RunMetadata(stamp, stamp, 1, 1, 0, 0))
    store.save_prediction_snapshots([build_point_in_time_snapshot(
        run_id=run_id, ticker="AAPL", observed_at=stamp,
        feature_payload={"current_price": 100.0}, baseline_output={"action": "BUY"},
        provenance={"price_source": "synthetic_test"}, benchmark_ticker="SPY",
        benchmark_selection="default_fallback")])
    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    frames = {"AAPL": _history([100, 101, 102, 103, 104, 110], dates),
              "SPY": _history([100, 100, 101, 102, 103, 105], dates)}
    return store, frames


class Batch4CoordinatorIntegrationTests(unittest.TestCase):
    def test_ir_source_review_ui_retains_lead_and_physical_import_cutoff(self):
        from market_checker_app.services.news_scout_service import NewsScoutService
        from tests.test_batch4_ir_primary_verification import _fixtures, NOW as cutoff, POLICY

        lead, feed, page, authority = _fixtures()
        imported_at = cutoff + timedelta(days=1)
        with tempfile.TemporaryDirectory() as directory, patch(
            "market_checker_app.services.news_scout_service._local_ingestion_clock", return_value=imported_at,
        ):
            store = ScoutStore(Path(directory) / "scout.db")
            service = NewsScoutService(store)
            service.ingest([lead], allowed_tickers=["MSFT"])
            candidates = store.latest_findings(["MSFT"], as_of=cutoff, source="rss", stage="search_candidate")
            original = dict(store.finding_by_id(candidates[0]["finding_id"], subject_id="MSFT"))
            service.verify_primary_candidate(candidates[0]["finding_id"], ticker="MSFT", feed_capture=feed,
                primary_capture=page, authority_capture=authority, publisher_hosts=POLICY, as_of=cutoff)
            self.assertEqual([], store.latest_findings(["MSFT"], as_of=cutoff,
                                                      source="rss", stage="ir_primary_source_review"))
            reviews = store.latest_findings(["MSFT"], as_of=imported_at,
                                           source="rss", stage="ir_primary_source_review")
            self.assertEqual(1, len(reviews))
            self.assertEqual("SOURCE_VERIFIED", reviews[0]["source_verification_status"])
            self.assertEqual(imported_at, datetime.fromisoformat(reviews[0]["local_verification_available_at"]))
            self.assertEqual("UNVERIFIED", reviews[0]["verification_status"])
            self.assertFalse(reviews[0]["issuer_attribution_allowed"])
            self.assertFalse(reviews[0]["scoring_applied"])
            self.assertEqual(original, dict(store.finding_by_id(candidates[0]["finding_id"], subject_id="MSFT")))
            self.assertEqual(1, len(store.latest_findings(["MSFT"], as_of=imported_at,
                                                        source="rss", stage="search_candidate")))

    def test_persisted_form4_review_reaches_document_and_ui_without_identity_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            scout = SecScoutService(store, client=_Index())
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            indexed, _, _ = _analysis(store)
            documents = [doc for doc in indexed.documents if doc.metadata.get("form4_observation")]
            self.assertEqual(1, len(documents))
            review = documents[0].metadata["form4_identity_review"]
            self.assertEqual("WAIT_INDEPENDENT_IDENTITY_REVIEW", review["status"])
            self.assertFalse(review["reporting_owner_identity_independently_verified"])
            self.assertFalse(review["instrument_identity_independently_verified"])
            self.assertFalse(review["scoring_applied"])
            rows = store.latest_findings(["AAPL"], as_of=NOW, source="sec", stage="filing_document")
            self.assertEqual("WAIT_INDEPENDENT_IDENTITY_REVIEW", rows[0]["form4_identity_review_status"])
            self.assertEqual(1, rows[0]["form4_reviewed_owners"])
            self.assertEqual(1, rows[0]["form4_reviewed_instruments"])

    def test_transaction_ui_retains_selected_award_and_unresolved_modified_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            clock = datetime(2026, 10, 12, tzinfo=timezone.utc)
            for stage in ("prime_contract", "award_transaction"):
                store.record_finding(source="usaspending", subject_id="LMT", source_object_id=stage,
                    content_hash=stage, title=stage, source_url="https://api.usaspending.gov/api/v2/transactions/",
                    locator=stage, published_at=clock, available_at=clock, observed_at=clock,
                    details={"stage": stage, "generated_internal_id": "CONT_AWD_TEST",
                             "source_modified_clock_audit": {"status": "UNREPORTED"},
                             "publisher_publication_time_verified": False, "revenue_inferred": False})
            award = store.latest_findings(["LMT"], as_of=clock, source="usaspending", stage="prime_contract")[0]
            transaction = store.latest_findings(["LMT"], as_of=clock, source="usaspending", stage="award_transaction")[0]
            self.assertEqual("CONT_AWD_TEST", award["generated_internal_id"])
            self.assertEqual("UNREPORTED", transaction["modified_clock_status"])
            self.assertFalse(transaction["publisher_publication_time_verified"])
            self.assertFalse(transaction["revenue_inferred"])

    def test_bounded_label_runner_exposes_capture_success_and_actual_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            store, frames = _pending_store(directory)
            cutoff = datetime(2026, 1, 9, 22, tzinfo=timezone.utc)
            report = resolve_prediction_labels(store=store, limit=1, as_of=cutoff,
                                               price_loader=lambda ticker: frames[ticker])
            self.assertEqual("SUCCESS", report["status"])
            self.assertEqual((1, 0), (report["archive_written"], report["archive_failures"]))
            observations, metadata = load_label_observations(Path(directory) / "prediction_label_observations")
            self.assertEqual(1, metadata["loaded_files"])
            self.assertGreater(datetime.fromisoformat(observations[0]["captured_at"]), cutoff)
            final = store.read_prediction_snapshots().iloc[0]
            self.assertGreater(datetime.fromisoformat(final["target_observed_at"]), cutoff)

    def test_capture_failure_is_partial_and_recoverable_through_bounded_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            store, frames = _pending_store(directory)
            with patch("market_checker_app.services.prediction_label_service.persist_label_observation",
                       side_effect=OSError("synthetic archive failure")):
                report = resolve_prediction_labels(store=store, limit=1,
                    as_of=datetime(2026, 1, 9, 22, tzinfo=timezone.utc), price_loader=lambda ticker: frames[ticker])
            self.assertEqual("PARTIAL", report["status"])
            self.assertEqual((0, 1, 0), (report["archive_written"], report["archive_failures"], report["resolved"]))
            self.assertEqual("PENDING", store.read_prediction_snapshots().iloc[0]["label_status"])


if __name__ == "__main__":
    unittest.main()
