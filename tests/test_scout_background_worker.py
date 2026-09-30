from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.collectors.sec_edgar_client import SecCompany
from market_checker_app.scout_background_worker import (
    drain_sec_queue, start_sec_background_scan,
)
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.storage.scout_store import ScoutStore


class EmptyIndex:
    def __init__(self):
        self.visited = []

    def fetch_filing_index(self, ticker, **kwargs):
        self.visited.append(ticker)
        return SecCompany(ticker=ticker, cik="0000000001", name=ticker), ()


class ScoutBackgroundWorkerTests(unittest.TestCase):
    def test_one_click_drains_more_than_25_and_does_not_reschedule_completed(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            run_id = store.begin_background_worker("sec", as_of=datetime.now(timezone.utc))
            client = EmptyIndex()
            tickers = [f"T{i:03d}" for i in range(60)]
            result = drain_sec_queue(
                store, run_id=run_id,
                scout=SecScoutService(store, client=client),
                tickers=tickers, batch_size=25,
            )
            self.assertEqual("COMPLETED", result["status"])
            self.assertEqual(60, result["processed"])
            self.assertEqual(60, len(set(client.visited)))
            self.assertEqual(60, store.completed_issuer_jobs())
            self.assertEqual({"DONE": 60}, store.metrics())
            self.assertEqual("COMPLETED", store.background_worker_status("sec")["status"])

    def test_a_failed_job_is_deferred_while_remaining_tickers_continue(self):
        class OneFailure(EmptyIndex):
            def fetch_filing_index(self, ticker, **kwargs):
                if ticker == "AAA":
                    return None
                return super().fetch_filing_index(ticker, **kwargs)

        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            run_id = store.begin_background_worker("sec", as_of=datetime.now(timezone.utc))
            result = drain_sec_queue(
                store, run_id=run_id,
                scout=SecScoutService(store, client=OneFailure()),
                tickers=["AAA", "BBB", "CCC"], batch_size=1,
            )
            self.assertEqual("PAUSED", result["status"])
            self.assertEqual(3, result["processed"])
            self.assertEqual(1, result["failed"])
            self.assertEqual(2, store.completed_issuer_jobs())
            self.assertIsNotNone(store.next_job_due("sec"))

    def test_launch_is_single_instance_and_failure_is_visible(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            store = ScoutStore(path)
            self.assertEqual("WAIT_ACCESS", start_sec_background_scan(
                store, db_path=path, user_agent=""))
            with patch("market_checker_app.scout_background_worker.subprocess.Popen") as popen:
                self.assertEqual("STARTED", start_sec_background_scan(
                    store, db_path=path, user_agent="JohnySkore/2.1 contact@example.com"))
                self.assertEqual("BUSY", start_sec_background_scan(
                    store, db_path=path, user_agent="JohnySkore/2.1 contact@example.com"))
                self.assertEqual(1, popen.call_count)
                self.assertEqual("JohnySkore/2.1 contact@example.com",
                                 popen.call_args.kwargs["env"]["JOHNY_SKORE_SEC_USER_AGENT"])
            previous = store.background_worker_status("sec")
            store.update_background_worker(
                "sec", previous["run_id"], as_of=datetime.now(timezone.utc),
                status="COMPLETED", processed=0, new_findings=0, failed=0,
            )
            with patch("market_checker_app.scout_background_worker.subprocess.Popen",
                       side_effect=OSError("cannot launch")):
                self.assertEqual("ERROR", start_sec_background_scan(
                    store, db_path=path, user_agent="JohnySkore/2.1 contact@example.com"))
            self.assertEqual("ERROR", store.background_worker_status("sec")["status"])

    def test_stale_worker_can_be_replaced_without_old_run_overwriting_progress(self):
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            start = datetime(2026, 9, 30, tzinfo=timezone.utc)
            old = store.begin_background_worker("sec", as_of=start)
            self.assertIsNone(store.begin_background_worker(
                "sec", as_of=start + timedelta(minutes=59)))
            new = store.begin_background_worker(
                "sec", as_of=start + timedelta(hours=2))
            self.assertNotEqual(old, new)
            self.assertFalse(store.update_background_worker(
                "sec", old, as_of=start + timedelta(hours=2),
                status="COMPLETED", processed=100, new_findings=0, failed=0,
            ))
            self.assertEqual(new, store.background_worker_status("sec")["run_id"])


if __name__ == "__main__":
    unittest.main()
