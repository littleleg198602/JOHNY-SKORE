"""Independent final-publication checks; all DBs/sources are temporary fixtures."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.services import user_windows_readiness_capture as readiness


class ReadinessPublicationPeerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        # Resolve native temp aliases before creating or comparing any paths.
        self.root=Path(self.temp.name).resolve()
        self.source_root=self.root/"synthetic_source"
        self.source_root.mkdir()
        self.source=self.source_root/"producer.py"
        self.source.write_bytes(b"SYNTHETIC_PRODUCER_VERSION_ONE\n")
        self.database=self.root/"synthetic.db"
        self.output=self.root/"receipt"/"readiness.json"
        self.clock={"wall":datetime.now(timezone.utc),"monotonic":1_000_000}
        self.callback_hits=[]

    def tearDown(self):
        self.temp.cleanup()

    def fixture_database(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE runs(run_id TEXT,started_at TEXT,finished_at TEXT,watchlist_size INTEGER,processed_symbols INTEGER,warnings_count INTEGER,errors_count INTEGER)")
            connection.execute("INSERT INTO runs VALUES('SYNTHETIC_RUN','2026-10-18','2026-10-18',1,1,0,0)")
            connection.commit()

    def runtime(self):
        flags={name:False for name in readiness._CI_NAMES};flags["GITHUB_ACTIONS"]=True
        return {"os_system":"Windows","os_release":"SYNTHETIC","os_version":"SYNTHETIC",
            "machine":"SYNTHETIC","os_name":"nt","sys_platform":"win32",
            "python_implementation":"CPython","python_version":"3.12.0",
            "hosted_ci_indicators":flags,"ci_detected":True}

    def recorder(self):
        return readiness.UserWindowsReadinessCapture(enabled=True,
            observation_clock=lambda:self.clock["wall"],monotonic_clock=lambda:self.clock["monotonic"],
            runtime_probe=self.runtime)

    def capture(self,callback=None):
        real_fsync=readiness.os.fsync
        def late(fileno):
            real_fsync(fileno)
            if not self.callback_hits:
                self.callback_hits.append("ACTUAL_TEMP_RECEIPT_FSYNC_REACHED")
                if callback is not None:callback()
        with patch.object(readiness,"SOURCE_ROOT",self.source_root),\
             patch.object(readiness,"SOURCE_FILES",("producer.py",)),\
             patch.object(readiness.os,"fsync",side_effect=late):
            return self.recorder().capture(output_path=self.output,tickers=["AAPL"],db_path=self.database)

    def assert_refused_after_late_mutation(self,callback):
        error=None
        try:self.capture(callback)
        except (ValueError,FileExistsError,OSError) as observed:error=observed
        self.assertEqual(["ACTUAL_TEMP_RECEIPT_FSYNC_REACHED"],self.callback_hits,
            "The real late publication guard must be reached; an earlier refusal is not this regression")
        self.assertIsNotNone(error,"Late fixture drift was published after the last source/clock validation")
        self.assertFalse(self.output.exists(),"A refused late mutation must not leave a public readiness receipt")
        self.assertEqual([],list(self.output.parent.glob(".user_windows_readiness_*.tmp")))

    def test_peer_late_whole_source_change_after_temp_fsync_refuses_publication(self):
        self.fixture_database()
        before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assert_refused_after_late_mutation(lambda:self.source.write_bytes(b"SYNTHETIC_PRODUCER_VERSION_TWO\n"))
        self.assertNotEqual(before,hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_peer_late_database_main_file_change_after_temp_fsync_refuses_publication(self):
        self.fixture_database()
        def mutate():
            with closing(sqlite3.connect(self.database)) as connection:
                connection.execute("UPDATE runs SET processed_symbols=2")
                connection.commit()
        self.assert_refused_after_late_mutation(mutate)

    def test_peer_late_database_journal_after_temp_fsync_refuses_quiescent_receipt(self):
        self.fixture_database()
        self.assert_refused_after_late_mutation(lambda:Path(str(self.database)+"-wal").write_bytes(b"SYNTHETIC_SIDECAR_NOT_REAL_SOURCE"))

    def test_peer_absent_database_created_after_temp_fsync_is_not_published_as_absent(self):
        self.assertFalse(self.database.exists())
        self.assert_refused_after_late_mutation(self.fixture_database)

    def test_peer_late_wall_clock_rollback_after_temp_fsync_refuses_publication(self):
        self.fixture_database()
        def rollback():self.clock["wall"]-=timedelta(seconds=1)
        self.assert_refused_after_late_mutation(rollback)

    def test_peer_late_monotonic_clock_rollback_after_temp_fsync_refuses_publication(self):
        self.fixture_database()
        def rollback():self.clock["monotonic"]-=1
        self.assert_refused_after_late_mutation(rollback)

    def test_peer_control_absent_runs_and_injected_hosted_windows_remain_nonacceptance(self):
        result=self.capture()
        self.assertTrue(self.callback_hits)
        self.assertEqual("FIXTURE_ONLY",result["status"])
        receipt=readiness.load_user_windows_readiness_receipt(self.output)
        self.assertTrue(receipt["fixture_only"])
        self.assertTrue(receipt["runtime"]["ci_detected"])
        self.assertEqual("ABSENT",receipt["database"]["status"])
        self.assertEqual("ABSENT",receipt["same_process_production_run_receipt"])
        for key in ("user_windows_end_to_end_verified","strict_pit_eligible","historical_OOS_verified","full_specialist_acceptance"):
            self.assertIs(receipt[key],False,key)


if __name__=="__main__":
    unittest.main()
