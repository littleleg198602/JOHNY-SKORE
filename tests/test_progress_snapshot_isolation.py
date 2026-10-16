import unittest

from market_checker_app.services.progress_service import ProgressService


class ProgressSnapshotIsolationTests(unittest.TestCase):
    def test_snapshots_detach_flat_rows_and_all_mutable_fields(self):
        progress = ProgressService(total_symbols=2)
        row = {"Ticker": "AAPL", "Score": 1.0}
        progress.add_completed_row(row)
        progress.log("WARNING", "original", "AAPL")
        first = progress.snapshot()
        row["Ticker"] = "INPUT_MUTATION"
        first.completed_rows[0]["Ticker"] = "CALLBACK_MUTATION"
        first.warnings.append("changed")
        first.errors.append("changed")
        first.fallbacks.append("changed")
        first.recent_logs[0]["message"] = "changed"
        second = progress.snapshot()
        self.assertEqual("AAPL", second.completed_rows[0]["Ticker"])
        self.assertEqual(["[AAPL] original"], second.warnings)
        self.assertEqual([], second.errors)
        self.assertEqual([], second.fallbacks)
        self.assertEqual("original", second.recent_logs[0]["message"])
        progress.add_completed_row({"Ticker": "MSFT"})
        self.assertEqual(1, len(first.completed_rows))
        self.assertEqual(1, first.processed_symbols)

    def test_callback_cannot_mutate_nested_rows_or_future_snapshots(self):
        received = []
        def callback(state):
            received.append(state)
            if state.completed_rows:
                state.completed_rows[0]["details"]["labels"].append("callback")
        progress = ProgressService(total_symbols=2, on_update=callback)
        row = {"Ticker": "AAPL", "details": {"labels": ["original"]}}
        progress.add_completed_row(row)
        row["details"]["labels"].append("input")
        progress.set_step("AAPL", "next", "next")
        self.assertEqual(["original"], progress.snapshot().completed_rows[0]["details"]["labels"])
        self.assertEqual(["original", "callback"], received[0].completed_rows[0]["details"]["labels"])
        self.assertEqual(["original", "callback"], received[1].completed_rows[0]["details"]["labels"])
