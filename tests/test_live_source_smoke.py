from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

import pandas as pd

from market_checker_app.collectors.sec_edgar_client import (
    SecCompany,
    SecCompanyBundle,
    SecCompanyFact,
    SecFiling,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.live_source_smoke import run_live_source_smoke
from market_checker_app.models import NewsItem, YahooSnapshot


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class _Yahoo:
    def fetch_ohlc_only(self, ticker, period="1mo", interval="1d"):
        return pd.DataFrame({"Close": [100.0, 101.0, 102.0]}), None

    def fetch_metadata(self, ticker):
        return YahooSnapshot(ticker=ticker, data={"currentPrice": 102.0}, status="partial"), None


class _RSS:
    def collect(self, sources, tickers):
        return [
            NewsItem(
                ticker=ticker,
                source=sources[index],
                title=f"{ticker} quarterly update",
                summary="",
                published_at=NOW,
                sentiment_weight=0.0,
                url=f"https://example.com/{ticker.lower()}",
            )
            for index, ticker in enumerate(tickers)
        ], []


def _bundle() -> SecCompanyBundle:
    filing = SecFiling(
        accession_number="0000320193-26-000001",
        form="10-K",
        filed_at=NOW,
        report_date=NOW,
        primary_document="aapl.htm",
        filing_url="https://www.sec.gov/Archives/aapl.htm",
        index_url="https://www.sec.gov/Archives/aapl-index.htm",
    )
    fact = SecCompanyFact(
        taxonomy="us-gaap",
        concept="Assets",
        label="Assets",
        description="Assets",
        unit="USD",
        value=1.0,
        filed_at=NOW,
        form="10-K",
        accession_number=filing.accession_number,
        source_url=filing.filing_url,
    )
    return SecCompanyBundle(
        company=SecCompany("AAPL", "0000320193", "Apple Inc.", "Nasdaq"),
        filings=(filing,),
        facts=(fact,),
    )


class _SEC:
    def fetch_company_bundle(self, ticker, **kwargs):
        return _bundle()


class _LeakySEC:
    def fetch_company_bundle(self, ticker, **kwargs):
        raise RuntimeError("request failed for JohnySkore test@example.com")


class _Report:
    def fetch(self, source):
        return FetchedShortReport(
            source=source,
            final_url=source.url,
            mime_type="text/html",
            title="MW is Short TAL",
            text="TAL " + "verified report content " * 20,
            content_hash="a" * 64,
            size_bytes=1_024,
            extractor="html.parser",
        )


class LiveSourceSmokeTests(unittest.TestCase):
    def test_all_live_contracts_are_audited_without_persisting_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "smoke.json"
            payload = run_live_source_smoke(
                tickers=["AAPL", "MSFT", "SOFI"],
                output_path=output,
                sec_user_agent="JohnySkore test@example.com",
                yahoo_client=_Yahoo(),
                rss_client=_RSS(),
                sec_client=_SEC(),
                report_client=_Report(),
            )
            persisted = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("PASS", payload["status"])
        self.assertEqual(
            {"yahoo", "rss", "sec_edgar", "external_short_report"},
            {check["name"] for check in payload["checks"]},
        )
        self.assertFalse(payload["raw_source_content_persisted"])
        self.assertNotIn("test@example.com", json.dumps(persisted))

    def test_missing_declared_sec_contact_fails_smoke_but_writes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "smoke.json"
            payload = run_live_source_smoke(
                tickers=["AAPL"],
                output_path=output,
                sec_user_agent="",
                yahoo_client=_Yahoo(),
                rss_client=_RSS(),
                sec_client=_SEC(),
                report_client=_Report(),
            )

            persisted = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("FAIL", payload["status"])
        self.assertIn("sec_edgar", payload["failed_checks"])
        self.assertEqual("FAIL", persisted["status"])

    def test_sec_contact_is_redacted_even_if_a_client_exception_echoes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "smoke.json"
            payload = run_live_source_smoke(
                tickers=["AAPL"],
                output_path=output,
                sec_user_agent="JohnySkore test@example.com",
                yahoo_client=_Yahoo(),
                rss_client=_RSS(),
                sec_client=_LeakySEC(),
                report_client=_Report(),
            )

            persisted = output.read_text(encoding="utf-8")

        self.assertEqual("FAIL", payload["status"])
        self.assertNotIn("test@example.com", persisted)
        self.assertIn("<redacted-sec-user-agent>", persisted)


if __name__ == "__main__":
    unittest.main()
