from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import unittest
from urllib.error import HTTPError

from market_checker_app.collectors.short_report_client import (
    ShortReportClient,
    ShortReportFetchError,
)
from market_checker_app.collectors.source_diagnostics import source_failure_detail
from market_checker_app.config import ShortReportSourceConfig


def _source() -> ShortReportSourceConfig:
    return ShortReportSourceConfig(
        ticker="TEST",
        publisher="Test publisher",
        published_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        url="https://example.com/report",
        discovery_method="fixture",
    )


class SourceDiagnosticsTests(unittest.TestCase):
    def test_transient_timeout_is_retried_and_attempt_count_is_recorded(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def transport(*_: object) -> tuple[bytes, str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary timeout")
            return b"<html><title>Report</title><p>Readable text</p></html>", "text/html", "https://example.com/report"

        report = ShortReportClient(
            user_agent="JohnySkoreTests tests@example.com",
            transport=transport,
            sleep=sleeps.append,
        ).fetch(_source())

        self.assertEqual(2, calls)
        self.assertEqual([1.0], sleeps)
        self.assertEqual(2, report.fetch_attempts)

    def test_access_denied_is_not_retried_and_is_actionable(self) -> None:
        calls = 0

        def transport(*_: object) -> tuple[bytes, str, str]:
            nonlocal calls
            calls += 1
            raise HTTPError(
                "https://www.sec.gov/Archives/example.htm",
                403,
                "Forbidden",
                hdrs=None,
                fp=BytesIO(b"forbidden"),
            )

        with self.assertRaises(ShortReportFetchError) as raised:
            ShortReportClient(
                user_agent="JohnySkoreTests tests@example.com",
                transport=transport,
            ).fetch(_source())

        detail = source_failure_detail(
            source="SEC EDGAR filing text",
            ticker="TEST",
            url="https://www.sec.gov/Archives/example.htm",
            error=raised.exception,
            parser="ShortReportClient",
        )
        self.assertEqual(1, calls)
        self.assertEqual(1, detail["attempts"])
        self.assertEqual(403, detail["http_status"])
        self.assertEqual("ACCESS_DENIED", detail["category"])
        self.assertIn("User-Agent", detail["remediation"])


if __name__ == "__main__":
    unittest.main()
