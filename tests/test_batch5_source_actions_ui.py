"""Source actions use an explicitly selected lead, never automatic network."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from market_checker_app.services.agent_runtime_service import AgentRuntimeSettings
from market_checker_app.storage.scout_store import ScoutStore


class Batch5SourceActionsUITests(unittest.TestCase):
    def test_nhtsa_archive_button_reads_bounded_review_without_live_collection(self):
        from unittest.mock import ANY
        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load",
                   return_value=(AgentRuntimeSettings(), None)), \
             patch("market_checker_app.services.nhtsa_negative_review_service.read_nhtsa_scope_reviews", return_value={
                 "status": "NO_RECORDED_REVIEWS", "reviews": [], "live_complete_negative_model_years": 0,
             }) as read, \
             patch("socket.socket.connect", side_effect=AssertionError("Review UI attempted HTTP")):
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            database = Path(directory) / "scout.db"
            next(field for field in app.text_input if field.label == "DB soubor").set_value(str(database))
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            read.assert_not_called()
            next(button for button in app.button if button.label == "Zobrazit uložené kontroly modelů NHTSA").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            read.assert_called_once_with(ANY, as_of=ANY, limit=8)
            self.assertEqual(database, read.call_args.args[0].db_path)

    def test_award_comparison_uses_selected_retained_pages_without_collecting_again(self):
        from market_checker_app.services.award_transaction_history_service import AwardTransactionHistoryService
        from tests import test_award_transaction_history_service as fixtures

        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load",
                   return_value=(AgentRuntimeSettings(), None)), \
             patch("market_checker_app.scout_runner.run_award_capture_audit", return_value={
                 "status": "SAME_SOURCE_CAPTURE", "audit_archive_status": "SAVED", "counts": {"unchanged": 1},
             }) as compare, \
             patch("market_checker_app.scout_runner.run_award_transactions") as collect, \
             patch("socket.socket.connect", side_effect=AssertionError("Offline UI attempted network")):
            database = Path(directory) / "scout.db"
            store = ScoutStore(database)
            fixture = fixtures.AwardTransactionHistoryTests()
            fixture.store = store
            identity = {**fixtures.IDENTITY, "ticker": "LMT"}
            finding_id = fixture.seed(identity=identity)
            client = fixtures.FixtureClient([fixtures.capture(fixtures.page_payload([fixtures.transaction()]))])
            gathered = AwardTransactionHistoryService(store, archive_directory=database.parent / "award_transaction_captures",
                identities=[identity], client=client).run(subject_id="LMT", award_finding_id=finding_id, as_of=fixtures.CLOCK)
            manifest_name = Path(gathered["capture_manifests"][0]).name
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            next(field for field in app.text_input if field.label == "DB soubor").set_value(str(database))
            next(field for field in app.text_area if field.label == "Ruční watchlist (jeden ticker na řádek)").set_value("LMT")
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            compare.assert_not_called()
            collect.assert_not_called()
            next(field for field in app.multiselect if field.label == "Stránky dřívějšího průchodu").set_value([manifest_name])
            next(field for field in app.multiselect if field.label == "Stránky pozdějšího průchodu").set_value([manifest_name])
            app.run(timeout=30)
            next(button for button in app.button if button.label == "Porovnat zachycené průchody").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            compare.assert_called_once_with(db_path=database, subject_id="LMT", award_finding_id=finding_id,
                baseline_manifest_names=[manifest_name], current_manifest_names=[manifest_name])
            collect.assert_not_called()

    def test_ir_form_requires_explicit_primary_inputs_and_only_selected_existing_lead(self):
        app_path = Path(__file__).resolve().parents[1] / "market_checker_app" / "app.py"
        with tempfile.TemporaryDirectory() as directory, \
             patch("market_checker_app.services.agent_runtime_service.AgentRuntimeService.load",
                   return_value=(AgentRuntimeSettings(), None)), \
             patch("market_checker_app.scout_runner.run_primary_ir", return_value={
                 "status": "WAIT_QUOTA", "attempted_requests": 0, "source_archive_status": "SAVED",
             }) as collect, \
             patch("socket.socket.connect", side_effect=AssertionError("UI attempted network")):
            database = Path(directory) / "scout.db"
            store = ScoutStore(database)
            clock = datetime(2026, 10, 1, tzinfo=timezone.utc)
            finding_id = store.record_finding(
                source="rss", subject_id="MSFT", source_object_id="https://example.com/announcement",
                content_hash="test-lead", title="Selected primary lead", source_url="https://example.com/announcement",
                locator="rss:title", published_at=clock, available_at=clock, observed_at=clock,
                verification_status="UNVERIFIED", details={"stage": "search_candidate", "identity_status": "TICKER_HINT_ONLY"},
            )[0]
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            next(field for field in app.text_input if field.label == "DB soubor").set_value(str(database))
            next(field for field in app.text_area if field.label == "Ruční watchlist (jeden ticker na řádek)").set_value("MSFT")
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            collect.assert_not_called()
            next(button for button in app.button if button.label == "Načíst a zkontrolovat primární zdroj").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            collect.assert_not_called()
            next(field for field in app.text_input if field.label == "RSS kanál primárního vydavatele").set_value("https://example.com/rss")
            next(field for field in app.text_input if field.label == "Stránka vydavatele odkazující na tento RSS kanál").set_value("https://example.com/ir")
            next(field for field in app.text_input if field.label == "Domény primárního vydavatele (oddělené čárkou)").set_value("example.com, news.example.com")
            next(button for button in app.button if button.label == "Načíst a zkontrolovat primární zdroj").click()
            app.run(timeout=30)
            self.assertEqual([], list(app.exception))
            collect.assert_called_once_with(db_path=database, ticker="MSFT", candidate_id=finding_id,
                feed_url="https://example.com/rss", authority_url="https://example.com/ir",
                publisher_hosts=("example.com", "news.example.com"))
            self.assertEqual("UNVERIFIED", store.finding_by_id(finding_id, subject_id="MSFT")["verification_status"])


if __name__ == "__main__":
    unittest.main()
