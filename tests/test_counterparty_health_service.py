from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from market_checker_app.services.counterparty_health_service import (
    build_counterparty_health_report,
    parse_counterparty_health_sources,
)
from market_checker_app.storage.sqlite_store import SQLiteStore


UTC = timezone.utc


def _source(name: str, entity_type: str, ticker: str, status: str) -> str:
    return " | ".join(
        [
            name,
            entity_type,
            ticker,
            status,
            "SEC EDGAR",
            "2025-02-20T00:00:00Z",
            "https://www.sec.gov/Archives/example",
            "FY2024 annual filing",
        ]
    )


class CounterpartyHealthServiceTests(unittest.TestCase):
    def test_public_counterparty_uses_only_snapshot_available_at_cutoff(self) -> None:
        sources, errors = parse_counterparty_health_sources(
            "\n".join(
                [
                    _source("Example Components", "PUBLIC", "EXMP", "PUBLIC_FILING_AVAILABLE"),
                    _source("Private Supplier", "PRIVATE", "-", "PUBLIC_DOCUMENT_LIMITED"),
                ]
            )
        )
        self.assertEqual([], errors)
        relationships = [
            {
                "relationship_id": "rel-public",
                "ticker": "AAPL",
                "counterparty": "Example Components",
                "metadata_json": json.dumps({"counterparty_identity_status": "IDENTIFIED"}),
            },
            {
                "relationship_id": "rel-private",
                "ticker": "AAPL",
                "counterparty": "Private Supplier",
                "metadata_json": json.dumps({"counterparty_identity_status": "IDENTIFIED"}),
            },
            {
                "relationship_id": "rel-anon",
                "ticker": "AAPL",
                "counterparty": "Unnamed major customer",
                "metadata_json": json.dumps({"counterparty_identity_status": "ANONYMOUS"}),
            },
        ]
        snapshots = [
            {
                "snapshot_id": "old",
                "ticker": "EXMP",
                "as_of": "2025-02-21T00:00:00+00:00",
                "availability_at": "2025-02-21T00:00:00+00:00",
                "period_basis": "ANNUAL",
                "period_start": "2024-01-01T00:00:00+00:00",
                "period_end": "2024-12-31T00:00:00+00:00",
                "values_json": json.dumps({"cash_and_equivalents": 50, "total_debt": 100, "operating_cash_flow": 20, "free_cash_flow": 10, "debt_to_cash_ratio": 2}),
                "missing_reasons_json": "{}",
                "source_accessions_json": json.dumps({"total_debt": ["0001"]}),
                "source_urls_json": json.dumps({"total_debt": ["https://www.sec.gov/Archives/old"]}),
            },
            {
                "snapshot_id": "future-revision",
                "ticker": "EXMP",
                "as_of": "2025-04-01T00:00:00+00:00",
                "availability_at": "2025-04-01T00:00:00+00:00",
                "values_json": "{}",
            },
        ]
        report = build_counterparty_health_report(
            relationships,
            snapshots,
            sources,
            as_of=datetime(2025, 3, 1, tzinfo=UTC),
        )
        by_id = {entry["relationship_id"]: entry for entry in report["entries"]}
        self.assertEqual("PUBLIC_FILING_EVIDENCE_AVAILABLE", by_id["rel-public"]["status"])
        self.assertEqual("old", by_id["rel-public"]["snapshot_id"])
        self.assertEqual(100, by_id["rel-public"]["metrics"]["total_debt"])
        self.assertEqual("LIMITED_PUBLIC_DOCUMENT", by_id["rel-private"]["status"])
        self.assertEqual("COUNTERPARTY_NOT_IDENTIFIED", by_id["rel-anon"]["status"])
        self.assertTrue(report["analysis_only"])
        self.assertFalse(report["ranking_modified"])

    def test_missing_manifest_and_snapshot_do_not_create_rating_and_persist(self) -> None:
        sources, errors = parse_counterparty_health_sources(
            _source("Mapped Public", "PUBLIC", "MAP", "PUBLIC_FILING_AVAILABLE")
        )
        self.assertEqual([], errors)
        report = build_counterparty_health_report(
            [
                {"relationship_id": "mapped", "ticker": "MSFT", "counterparty": "Mapped Public", "metadata_json": '{"counterparty_identity_status":"IDENTIFIED"}'},
                {"relationship_id": "unknown", "ticker": "MSFT", "counterparty": "No Map", "metadata_json": '{"counterparty_identity_status":"IDENTIFIED"}'},
            ],
            [],
            sources,
            as_of=datetime(2025, 3, 1, tzinfo=UTC),
        )
        statuses = {entry["relationship_id"]: entry["status"] for entry in report["entries"]}
        self.assertEqual("PUBLIC_FILING_NOT_INGESTED", statuses["mapped"])
        self.assertEqual("IDENTITY_NOT_MAPPED", statuses["unknown"])
        self.assertTrue(all(entry["health_assessment"] == "NOT_RATED" for entry in report["entries"]))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "history.db")
            self.assertTrue(store.save_counterparty_health_report(report))
            self.assertFalse(store.save_counterparty_health_report(report))
            self.assertEqual(1, len(store.read_counterparty_health_reports()))

    def test_parser_rejects_guessed_ticker_for_private_entity(self) -> None:
        _, errors = parse_counterparty_health_sources(
            _source("Private Supplier", "PRIVATE", "GUESS", "PUBLIC_DOCUMENT_LIMITED")
        )
        self.assertEqual(1, len(errors))
        self.assertIn("nesmí mít veřejný ticker", errors[0])


if __name__ == "__main__":
    unittest.main()
