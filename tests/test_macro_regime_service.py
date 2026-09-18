from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from market_checker_app.services.macro_regime_service import (
    build_macro_regime_report,
    parse_macro_observations,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


def _at(day: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)


def _line(indicator: str, value: float, *, available: int = 2, vintage: int = 2, scope: str = "GLOBAL") -> str:
    unit = "index" if indicator in {"VIX", "DXY"} else "USD/bbl" if indicator == "WTI" else "pct"
    return f"{indicator} | {scope} | 2025-12 | {value} | {unit} | {_at(0).isoformat()} | {_at(available).isoformat()} | {_at(vintage).isoformat()} | https://example.com/{indicator.lower()}"


class MacroRegimeServiceTests(unittest.TestCase):
    def test_selects_only_vintage_available_at_cutoff_and_reports_sector_strength(self) -> None:
        text = "\n".join([
            _line("VIX", 28), _line("US10Y", 4.2), _line("T10Y2Y", -0.2),
            _line("DXY", 105), _line("WTI", 75), _line("CPI_YOY", 2.8),
            _line("INDPRO_YOY", -0.5), _line("SECTOR_RELATIVE_20D", 3.0, scope="TECHNOLOGY"),
            _line("CPI_YOY", 9.9, available=10, vintage=10),
        ])
        observations, errors = parse_macro_observations(text)
        self.assertEqual([], errors)
        report = build_macro_regime_report(observations, as_of=_at(5))
        self.assertEqual("READY", report["status"])
        self.assertEqual("RISK_OFF", report["regime"])
        self.assertEqual(1, report["future_or_revised_observation_excluded_count"])
        cpi = next(item for item in report["selected_observations"] if item["indicator_id"] == "CPI_YOY")
        self.assertEqual(2.8, cpi["value"])
        self.assertEqual("TECHNOLOGY", report["sector_relative_strength"][0]["scope"])

    def test_missing_required_indicator_is_explicit_and_persistable(self) -> None:
        observations, errors = parse_macro_observations(_line("VIX", 15))
        self.assertEqual([], errors)
        report = build_macro_regime_report(observations, as_of=_at(5))
        self.assertEqual("INSUFFICIENT_DATA", report["status"])
        self.assertIn("US10Y", report["reason"])
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "macro.db")
            self.assertEqual(1, store.save_macro_observations(observations))
            self.assertTrue(store.save_macro_regime_report(report))
            self.assertEqual(1, len(store.read_macro_observations()))
            self.assertEqual(1, len(store.read_macro_regime_reports()))


if __name__ == "__main__":
    unittest.main()
