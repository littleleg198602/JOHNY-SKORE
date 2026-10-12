from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from market_checker_app.agents import (
    AgentStatus,
    EntityRegistryAgent,
    OrchestratorAgent,
)
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.utils.entity_identifiers import (
    normalize_cik,
    normalize_isin,
    normalize_lei,
)


APPLE_LEI = "HWUPKR0MPOU8FGXBT394"
APPLE_ISIN = "US0378331005"
SOURCE_URL = f"https://api.gleif.org/api/v1/lei-records/{APPLE_LEI}"


def _identity(
    *,
    ticker: str = "AAPL",
    name: str = "Apple Inc.",
    exchange: str = "Nasdaq",
    valid_from: str = "2020-01-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "entity_id": "listing:apple:primary",
        "ticker": ticker,
        "name": name,
        "exchange": exchange,
        "cik": "320193",
        "isin": APPLE_ISIN,
        "lei": APPLE_LEI,
        "mic": "XNAS",
        "country_code": "US",
        "security_type": "common_stock",
        "share_class": "Common",
        "parent_entity_id": "legal:apple-group",
        "aliases": ["Apple Computer, Inc."],
        "source": "gleif_primary_registry",
        "source_url": SOURCE_URL,
        "confidence": 1.0,
        "valid_from": valid_from,
    }


def _run_identity(ticker: str, identity: dict[str, object]):
    orchestrator = OrchestratorAgent()
    orchestrator.register(EntityRegistryAgent())
    return orchestrator.run(
        watchlist=[ticker],
        state={"entity_identity_by_ticker": {ticker: identity}},
    )


class EntityIdentifierValidationTests(unittest.TestCase):
    def test_primary_identifiers_are_normalized_and_checksum_validated(self) -> None:
        self.assertEqual("0000320193", normalize_cik("320193"))
        self.assertEqual(APPLE_ISIN, normalize_isin(APPLE_ISIN.lower()))
        self.assertEqual(APPLE_LEI, normalize_lei(APPLE_LEI.lower()))

        with self.assertRaisesRegex(ValueError, "ISIN checksum"):
            normalize_isin("US0378331004")
        with self.assertRaisesRegex(ValueError, "Invalid LEI"):
            normalize_lei("HWUPKR0MPOU8FGXBT395")

    def test_invalid_manifest_identifier_fails_closed(self) -> None:
        identity = _identity()
        identity["isin"] = "US0378331004"

        report = _run_identity("AAPL", identity)

        self.assertEqual(AgentStatus.FAILED, report.status)
        self.assertIn(
            "Invalid ISIN checksum",
            report.executions[0].result.error or "",
        )
        self.assertEqual([], report.entities)


class EntityRegistryStage51Tests(unittest.TestCase):
    def test_equal_windows_clock_samples_keep_report_cutoffs_strictly_ordered(self) -> None:
        frozen = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        with patch("market_checker_app.agents.contracts.datetime") as clock, \
                patch("market_checker_app.agents.contracts._last_utc_now", None):
            clock.now.return_value = frozen
            first = _run_identity("AAPL", _identity())
            second = _run_identity("APPL", _identity(ticker="APPL"))
        self.assertLess(first.executions[0].finished_at, first.finished_at)
        self.assertLess(first.finished_at, second.executions[0].finished_at)
        self.assertLess(second.executions[0].finished_at, second.finished_at)

    def test_sourced_dated_alias_resolves_only_inside_its_window(self) -> None:
        identity = _identity()
        identity["dated_ticker_aliases"] = [{
            "ticker": "OLD", "valid_from": "2020-01-01T00:00:00Z",
            "valid_to": "2025-01-01T00:00:00Z",
            "source_url": "https://www.sec.gov/Archives/old-ticker",
        }]
        report = _run_identity("AAPL", identity)
        self.assertEqual(AgentStatus.SUCCESS, report.status)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(report)
            before = store.resolve_ticker_identity_as_of(
                "OLD", known_at=report.finished_at,
                effective_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
            after = store.resolve_ticker_identity_as_of(
                "OLD", known_at=report.finished_at,
                effective_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(["AAPL"], list(before["ticker"]))
        self.assertTrue(after.empty)

    def test_ambiguous_overlapping_alias_fails_closed(self) -> None:
        first = _identity()
        first["dated_ticker_aliases"] = [{
            "ticker": "OLD", "valid_from": "2020-01-01T00:00:00Z",
            "valid_to": "2025-01-01T00:00:00Z",
            "source_url": SOURCE_URL,
        }]
        second = _identity(ticker="OTHER")
        second.update({
            "entity_id": "listing:other", "cik": "0000000001",
            "isin": None, "lei": None,
            "dated_ticker_aliases": [{
                "ticker": "OLD", "valid_from": "2024-01-01T00:00:00Z",
                "valid_to": "2026-01-01T00:00:00Z",
                "source_url": "https://www.sec.gov/Archives/other",
            }],
        })
        first_report = _run_identity("AAPL", first)
        second_report = _run_identity("OTHER", second)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first_report)
            store.save_orchestration_report(second_report)
            with self.assertRaisesRegex(ValueError, "Ambiguous dated ticker"):
                store.resolve_ticker_identity_as_of(
                    "OLD", known_at=second_report.finished_at,
                    effective_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
                )

    def test_unsourced_alias_is_rejected_before_persistence(self) -> None:
        identity = _identity()
        identity["dated_ticker_aliases"] = [{
            "ticker": "OLD", "valid_from": "2020-01-01T00:00:00Z",
        }]
        report = _run_identity("AAPL", identity)
        self.assertEqual(AgentStatus.FAILED, report.status)
        self.assertEqual([], report.entities)

    def test_later_alias_correction_preserves_the_prior_known_snapshot(self) -> None:
        first = _identity()
        first["dated_ticker_aliases"] = [{
            "ticker": "OLD", "valid_from": "2020-01-01T00:00:00Z",
            "valid_to": "2025-01-01T00:00:00Z", "source_url": SOURCE_URL,
        }]
        corrected = _identity()
        corrected["dated_ticker_aliases"] = [{
            "ticker": "OLD", "valid_from": "2020-01-01T00:00:00Z",
            "valid_to": "2024-01-01T00:00:00Z", "source_url": SOURCE_URL,
        }]
        first_report = _run_identity("AAPL", first)
        later_report = _run_identity("AAPL", corrected)
        effective = datetime(2024, 6, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first_report)
            store.save_orchestration_report(later_report)
            older_view = store.resolve_ticker_identity_as_of(
                "OLD", known_at=first_report.finished_at, effective_at=effective,
            )
            corrected_view = store.resolve_ticker_identity_as_of(
                "OLD", known_at=later_report.finished_at, effective_at=effective,
            )
            versions = store.read_entity_identity_versions("listing:apple:primary")
        self.assertEqual(["AAPL"], list(older_view["ticker"]))
        self.assertTrue(corrected_view.empty)
        self.assertEqual(2, len(versions))

    def test_registry_separates_legal_issuer_and_instrument_identity(self) -> None:
        report = _run_identity("aapl", _identity())

        self.assertEqual(AgentStatus.SUCCESS, report.status)
        entity = report.entities[0]
        self.assertEqual("listing:apple:primary", entity.entity_id)
        self.assertEqual(f"lei:{APPLE_LEI}", entity.legal_entity_id)
        self.assertEqual(entity.legal_entity_id, entity.issuer_id)
        self.assertEqual(f"isin:{APPLE_ISIN}", entity.instrument_id)
        self.assertEqual("0000320193", entity.cik)
        self.assertEqual("COMMON_STOCK", entity.security_type)
        self.assertEqual("legal:apple-group", entity.parent_entity_id)
        self.assertEqual("XNAS", entity.mic)
        self.assertEqual("US", entity.country_code)
        self.assertEqual("RESOLVED", entity.metadata["identity_resolution"])
        self.assertEqual(1, report.executions[0].result.metadata["resolved_identities"])

    def test_identity_changes_are_versioned_without_losing_old_ticker(self) -> None:
        first = _run_identity("AAPL", _identity())
        second_identity = _identity(
            ticker="APPL",
            name="Apple Corporation",
            exchange="NYSE",
            valid_from="2026-08-21T00:00:00Z",
        )
        second_identity["mic"] = "XNYS"
        second = _run_identity("APPL", second_identity)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first)
            store.save_orchestration_report(second)

            current = store.read_entities()
            versions = store.read_entity_identity_versions(
                "listing:apple:primary"
            )
            first_view = store.read_entity_identity_as_of(first.finished_at)
            second_view = store.read_entity_identity_as_of(second.finished_at)

        self.assertEqual(1, len(current))
        self.assertEqual("APPL", current.iloc[0]["ticker"])
        self.assertIn("AAPL", json.loads(current.iloc[0]["aliases_json"]))
        self.assertEqual(["AAPL", "APPL"], list(versions["ticker"]))
        self.assertIsNotNone(versions.iloc[0]["superseded_at"])
        self.assertIsNone(versions.iloc[1]["superseded_at"])
        self.assertEqual(["AAPL"], list(first_view["ticker"]))
        self.assertEqual(["APPL"], list(second_view["ticker"]))

    def test_versions_use_persisted_run_sequence_when_timestamps_are_equal(self) -> None:
        first = _run_identity("AAPL", _identity())
        second = _run_identity("APPL", _identity(
            ticker="APPL", name="Apple Corporation", exchange="NYSE",
            valid_from="2026-08-21T00:00:00Z",
        ))
        second.executions[0].finished_at = first.executions[0].finished_at

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first)
            store.save_orchestration_report(second)
            versions = store.read_entity_identity_versions("listing:apple:primary")

        self.assertEqual(["AAPL", "APPL"], list(versions["ticker"]))
        self.assertEqual(sorted(versions["agent_run_id"]), list(versions["agent_run_id"]))

    def test_unchanged_identity_reuses_version_but_keeps_observations(self) -> None:
        first = _run_identity("AAPL", _identity())
        second = _run_identity("AAPL", _identity())

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first)
            store.save_orchestration_report(second)
            versions = store.read_entity_identity_versions(
                "listing:apple:primary"
            )
            with store._connect() as conn:
                observations = conn.execute(
                    "SELECT COUNT(*) FROM entity_observations"
                ).fetchone()[0]

        self.assertEqual(1, len(versions))
        self.assertEqual(2, observations)

    def test_lower_confidence_identity_cannot_overwrite_primary_registry(self) -> None:
        primary = _run_identity("AAPL", _identity())
        weak_identity = _identity(name="Untrusted Apple Name")
        weak_identity.update(
            {
                "source": "secondary_source",
                "source_url": "https://example.com/apple",
                "confidence": 0.2,
            }
        )
        weak = _run_identity("AAPL", weak_identity)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(primary)
            store.save_orchestration_report(weak)
            current = store.read_entities()
            versions = store.read_entity_identity_versions(
                "listing:apple:primary"
            )

        self.assertEqual("Apple Inc.", current.iloc[0]["name"])
        self.assertEqual("gleif_primary_registry", current.iloc[0]["source"])
        self.assertEqual(1.0, current.iloc[0]["confidence"])
        self.assertEqual(1, len(versions))

    def test_conflicting_primary_identifier_rolls_back_atomically(self) -> None:
        first = _run_identity("AAPL", _identity())
        conflicting_identity = _identity()
        conflicting_identity["cik"] = "123456"
        conflicting = _run_identity("AAPL", conflicting_identity)

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "history.db")
            store.save_orchestration_report(first)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "Conflicting CIK"):
                store.save_orchestration_report(conflicting)

            self.assertEqual(1, len(store.read_orchestration_runs()))
            self.assertEqual(1, len(store.read_entity_identity_versions()))

    def test_existing_database_gets_additive_identity_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE entities (
                        entity_id TEXT PRIMARY KEY,
                        ticker TEXT NOT NULL UNIQUE,
                        yahoo_ticker TEXT,
                        name TEXT,
                        exchange TEXT,
                        cik TEXT,
                        isin TEXT,
                        lei TEXT,
                        sector TEXT,
                        industry TEXT,
                        aliases_json TEXT NOT NULL DEFAULT '[]',
                        source TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )

            store = SQLiteStore(db_path)
            store.ensure_schema()
            with store._connect() as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(entities)").fetchall()
                }
                version_table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_identity_versions'"
                ).fetchone()

        self.assertTrue(
            {
                "legal_entity_id",
                "issuer_id",
                "instrument_id",
                "parent_entity_id",
                "mic",
                "country_code",
                "source_url",
                "confidence",
            }.issubset(columns)
        )
        self.assertIsNotNone(version_table)


if __name__ == "__main__":
    unittest.main()
