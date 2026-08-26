from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

import pandas as pd

from market_checker_app.collectors.gleif_client import GleifIdentity
from market_checker_app.collectors.sec_edgar_client import (
    SecCompany,
    SecCompanyBundle,
    SecCompanyFact,
    SecFiling,
)
from market_checker_app.collectors.short_report_client import FetchedShortReport
from market_checker_app.live_source_smoke import (
    run_live_source_smoke,
    verify_company_identity_pilot,
)
from market_checker_app.models import NewsItem, YahooSnapshot
from market_checker_app.services.agent_runtime_service import AgentRuntimeService
from market_checker_app.services.company_intelligence_manifest_service import (
    parse_identity_records,
)
from market_checker_app.services.short_report_manifest_service import (
    parse_short_report_sources,
)
from market_checker_app.services.watchlist_service import load_watchlist


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


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


class _IdentitySEC(_SEC):
    def __init__(self, records):
        self.records = records

    def resolve_company(self, ticker):
        record = self.records.get(ticker)
        if not record or not record.get("cik"):
            return None
        return SecCompany(
            ticker=ticker,
            cik=str(record["cik"]),
            name=str(record["name"]),
            exchange=str(record.get("exchange") or "") or None,
        )


class _GLEIF:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def resolve(self, *, lei=None, isin=None):
        self.calls.append((lei, isin))
        for record in self.records.values():
            if lei and record.get("lei") != lei:
                continue
            if isin and record.get("isin") != isin:
                continue
            if not (lei or isin) or not record.get("lei"):
                continue
            return GleifIdentity(
                lei=str(record["lei"]),
                legal_name=str(record["name"]),
                country_code=str(record.get("country_code") or "") or None,
                jurisdiction=None,
                registered_as=None,
                registration_status="ISSUED",
                entity_status="ACTIVE",
                parent_lei=None,
                isins=(str(record["isin"]),) if record.get("isin") else (),
                source_url=str(record["source_url"]),
            )
        return None


class _LeakySEC:
    def fetch_company_bundle(self, ticker, **kwargs):
        raise RuntimeError("request failed for JohnySkore test@example.com")


class _Report:
    def fetch(self, source):
        return FetchedShortReport(
            source=source,
            final_url=source.url,
            mime_type="text/html",
            title=f"Research report on {source.ticker}",
            text=f"{source.ticker} " + "verified report content " * 20,
            content_hash="a" * 64,
            size_bytes=1_024,
            extractor="html.parser",
        )


def _production_identity_records():
    settings, warning = AgentRuntimeService(
        ROOT / "market_checker_app" / "autonomous_runtime.json"
    ).load()
    if warning:
        raise AssertionError(warning)
    records, errors = parse_identity_records(settings.identity_records_text)
    if errors:
        raise AssertionError("; ".join(errors))
    return records


def _production_watchlist() -> list[str]:
    return load_watchlist(
        ROOT / "market_checker_app" / "production_watchlist.txt"
    )


def _production_short_report_source():
    settings, warning = AgentRuntimeService(
        ROOT / "market_checker_app" / "autonomous_runtime.json"
    ).load()
    if warning:
        raise AssertionError(warning)
    sources, errors = parse_short_report_sources(
        settings.short_report_sources_text
    )
    if errors:
        raise AssertionError("; ".join(errors))
    if not sources:
        raise AssertionError("Expected at least one production short-report source")
    return sources[0]


class LiveSourceSmokeTests(unittest.TestCase):
    def test_committed_pilot_has_36_exact_primary_identities(self) -> None:
        records = _production_identity_records()

        self.assertEqual(
            {
                "NVDA",
                "AAPL",
                "GOOGL",
                "GOOG",
                "MSFT",
                "AMZN",
                "AVGO",
                "META",
                "TSLA",
                "LLY",
                "JPM",
                "WMT",
                "AMD",
                "V",
                "XOM",
                "JNJ",
                "INTC",
                "MA",
                "ABBV",
                "BAC",
                "CSCO",
                "COST",
                "ORCL",
                "LRCX",
                "AMAT",
                "CVX",
                "GE",
                "CAT",
                "KO",
                "UNH",
                "MS",
                "MRK",
                "PG",
                "NFLX",
                "PANW",
                "RTX",
            },
            set(records),
        )
        self.assertEqual(
            36,
            sum(bool(item.get("cik")) for item in records.values()),
        )
        self.assertEqual(
            0,
            sum(bool(item.get("lei")) for item in records.values()),
        )
        self.assertTrue(set(records).issubset(_production_watchlist()))
        self.assertTrue(all(item.get("source_url") for item in records.values()))

    def test_identity_pilot_resolves_all_records_without_name_discovery(self) -> None:
        records = _production_identity_records()
        gleif = _GLEIF(records)

        details = verify_company_identity_pilot(
            identity_records=records,
            sec_user_agent="JohnySkore test@example.com",
            universe_tickers=_production_watchlist(),
            sec_client=_IdentitySEC(records),
            gleif_client=gleif,
        )

        self.assertEqual(36, details["configured_identity_count"])
        self.assertEqual(36, details["resolved_identity_count"])
        self.assertEqual(0, details["unresolved_identity_count"])
        self.assertEqual(0, details["quarantined_conflict_count"])
        self.assertEqual(687, details["production_universe_count"])
        self.assertFalse(details["name_matching_used"])
        self.assertEqual([], gleif.calls)

    def test_identity_pilot_fails_closed_on_registry_conflict(self) -> None:
        records = _production_identity_records()

        class ConflictingSEC(_IdentitySEC):
            def resolve_company(self, ticker):
                company = super().resolve_company(ticker)
                if ticker == "AAPL" and company is not None:
                    return SecCompany(
                        ticker=company.ticker,
                        cik="0000000001",
                        name=company.name,
                        exchange=company.exchange,
                    )
                return company

        with self.assertRaisesRegex(RuntimeError, "SEC CIK konflikt pro AAPL"):
            verify_company_identity_pilot(
                identity_records=records,
                sec_user_agent="JohnySkore test@example.com",
                universe_tickers=_production_watchlist(),
                sec_client=ConflictingSEC(records),
                gleif_client=_GLEIF(records),
            )

    def test_identity_pilot_rejects_ticker_outside_production_universe(self) -> None:
        records = _production_identity_records()

        with self.assertRaisesRegex(RuntimeError, "mimo produkční watchlist"):
            verify_company_identity_pilot(
                identity_records=records,
                sec_user_agent="JohnySkore test@example.com",
                universe_tickers=["OUTSIDE"],
                sec_client=_IdentitySEC(records),
            )

    def test_all_live_contracts_are_audited_without_persisting_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "smoke.json"
            payload = run_live_source_smoke(
                tickers=["AAPL", "MSFT", "SOFI"],
                output_path=output,
                sec_user_agent="JohnySkore test@example.com",
                external_report_source=_production_short_report_source(),
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
                external_report_source=_production_short_report_source(),
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
                external_report_source=_production_short_report_source(),
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
