from __future__ import annotations

import unittest
from unittest.mock import patch

from market_checker_app.services.progress_service import ProgressService


class ProgressSnapshotTests(unittest.TestCase):
    def test_throttle_precedes_deep_copy_and_final_retains_every_row(self):
        samples = []
        progress = ProgressService(687, on_update=samples.append, emit_interval_seconds=0.15)
        with patch("market_checker_app.services.progress_service.time.monotonic", return_value=10.0):
            with patch.object(progress, "snapshot", wraps=progress.snapshot) as snapshots:
                progress.set_global_step("start", "Start", 0.01)
                for position in range(1, 688):
                    ticker = f"T{position:04d}"
                    progress.set_current(ticker, position, "analyze", "Analyze")
                    for step in range(6):
                        progress.set_step(ticker, f"step{step}", "Working", step / 6)
                    progress.add_completed_row({"ticker": ticker, "nested": {"source": [position]}})
                progress.log("WARNING", "Retained warning")
                progress.finalize("Finished")
                self.assertEqual(2, snapshots.call_count)
        self.assertEqual(2, len(samples))
        self.assertEqual([], samples[0].completed_rows)
        self.assertEqual(687, samples[-1].processed_symbols)
        self.assertEqual(687, len(samples[-1].completed_rows))
        self.assertEqual([f"T{p:04d}" for p in range(1, 688)],
                         [row["ticker"] for row in samples[-1].completed_rows])
        self.assertEqual(["Retained warning"], samples[-1].warnings)
        self.assertEqual((1.0, "done"), (samples[-1].overall_progress, samples[-1].current_step))

    def test_callback_and_explicit_snapshots_have_independent_nested_data(self):
        samples = []
        progress = ProgressService(2, on_update=samples.append)
        progress.add_completed_row({"ticker": "AAPL", "nested": {"items": [1]}})
        first = samples[-1]
        first.completed_rows[0]["nested"]["items"].append(99)
        first.errors.append("callback mutation")
        progress.log("INFO", "Next state")
        second = samples[-1]
        self.assertEqual([1], second.completed_rows[0]["nested"]["items"])
        self.assertEqual([], second.errors)
        second.completed_rows[0]["nested"]["items"].append(33)
        explicit = progress.snapshot()
        explicit.completed_rows.clear()
        self.assertEqual([1], progress.snapshot().completed_rows[0]["nested"]["items"])
        self.assertEqual([1, 99], first.completed_rows[0]["nested"]["items"])

    def test_delayed_emission_keeps_skipped_logs_and_current_counts(self):
        samples = []
        progress = ProgressService(2, on_update=samples.append, emit_interval_seconds=0.15)
        clock = [0.0]
        with patch("market_checker_app.services.progress_service.time.monotonic", side_effect=lambda: clock[0]):
            progress.set_global_step("start", "Start", 0.01)
            progress.log("ERROR", "Source failed", "AAPL")
            progress.add_completed_row({"ticker": "AAPL"})
            self.assertEqual(1, len(samples))
            clock[0] = 0.16
            progress.set_current("MSFT", 2, "analyze", "Next ticker")
        self.assertEqual(2, len(samples))
        self.assertEqual(1, samples[-1].processed_symbols)
        self.assertEqual([{"ticker": "AAPL"}], samples[-1].completed_rows)
        self.assertEqual(["[AAPL] Source failed"], samples[-1].errors)
        self.assertEqual("Source failed", samples[-1].recent_logs[0]["message"])

    def test_global_progress_does_not_regress_on_stale_ticker_updates(self):
        samples = []
        progress = ProgressService(2, on_update=samples.append)
        progress.set_global_step("audit", "Audit", 0.97)
        progress.set_current("AAPL", 1, "late", "Late ticker callback")
        progress.add_completed_row({"ticker": "AAPL"})
        progress.finalize("Done")
        values = [state.overall_progress for state in samples]
        self.assertEqual(sorted(values), values)

    def test_no_callback_does_not_copy_and_default_delivers_each_update(self):
        progress = ProgressService(1)
        with patch.object(progress, "snapshot", wraps=progress.snapshot) as snapshots:
            progress.log("INFO", "No callback")
            progress.add_completed_row({"ticker": "AAPL"})
            progress.finalize("Done")
            self.assertEqual(0, snapshots.call_count)
        self.assertEqual(1, len(progress.snapshot().completed_rows))
        samples = []
        unthrottled = ProgressService(1, on_update=samples.append)
        unthrottled.log("INFO", "Every update")
        unthrottled.add_completed_row({"ticker": "MSFT"})
        unthrottled.finalize("Done")
        self.assertEqual(3, len(samples))

    def test_completed_rows_are_owned_and_flat_snapshots_are_independent(self):
        samples = []
        progress = ProgressService(2, on_update=samples.append)
        flat = {"ticker": "AAPL", "value": 1.5, "known": True, "missing": None}
        nested = {"ticker": "MSFT", "items": [2]}
        progress.add_completed_row(flat)
        progress.add_completed_row(nested)
        flat["items"] = ["late mutation"]
        nested["items"].append(9)
        samples[-1].completed_rows[0]["value"] = 99
        samples[-1].completed_rows[1]["items"].append(8)
        state = progress.snapshot()
        self.assertNotIn("items", state.completed_rows[0])
        self.assertEqual(1.5, state.completed_rows[0]["value"])
        self.assertEqual([2], state.completed_rows[1]["items"])
        self.assertEqual(1.5, samples[0].completed_rows[0]["value"])


if __name__ == "__main__":
    unittest.main()
