from __future__ import annotations

import unittest

from market_checker_app.collectors.sec_edgar_client import (
    SEC_COMPANYFACTS_URL,
    SEC_SUBMISSIONS_FILE_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKER_MAP_URL,
    SecEdgarClient,
)
from market_checker_app.config import FundamentalIngestionConfig


CIK = "0000320193"


def _ticker_payload() -> dict[str, object]:
    return {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]],
    }


def _submission_table(
    forms: list[str],
    *,
    start_sequence: int = 1,
    items: list[str] | None = None,
) -> dict[str, object]:
    count = len(forms)
    return {
        "accessionNumber": [
            f"0000320193-26-{index:06d}"
            for index in range(start_sequence, start_sequence + count)
        ],
        "filingDate": [f"2026-0{min(index + 1, 9)}-01" for index in range(count)],
        "reportDate": [f"2025-1{index + 1:01d}-31" for index in range(count)],
        "form": forms,
        "primaryDocument": [
            f"document-{index}.xml" if form == "4" else f"document-{index}.htm"
            for index, form in enumerate(forms, start=start_sequence)
        ],
        "primaryDocDescription": [f"{form} filing" for form in forms],
        "items": items or [""] * count,
    }


class ExtendedSecFilingTests(unittest.TestCase):
    def test_default_configuration_includes_offerings_ownership_and_insiders(self) -> None:
        forms = set(FundamentalIngestionConfig().forms)

        self.assertTrue({"S-1", "424B1", "424B4", "424B5"}.issubset(forms))
        self.assertTrue({"4", "SC 13D", "SC 13G"}.issubset(forms))

    def test_historical_submission_file_is_loaded_when_recent_is_not_deep_enough(self) -> None:
        historical_name = "CIK0000320193-submissions-001.json"
        calls: list[str] = []

        def transport(url: str, _headers: dict[str, str], _timeout: float):
            calls.append(url)
            if url == SEC_TICKER_MAP_URL:
                return _ticker_payload()
            if url == SEC_SUBMISSIONS_URL.format(cik=CIK):
                return {
                    "filings": {
                        "recent": _submission_table(["10-Q"]),
                        "files": [{"name": historical_name}],
                    }
                }
            if url == SEC_SUBMISSIONS_FILE_URL.format(name=historical_name):
                return _submission_table(
                    ["10-K", "10-Q"],
                    start_sequence=10,
                )
            if url == SEC_COMPANYFACTS_URL.format(cik=CIK):
                return {"facts": {}}
            raise AssertionError(url)

        bundle = SecEdgarClient(
            user_agent="JohnySkoreTests tests@example.com",
            transport=transport,
            min_request_interval_seconds=0.0,
            sleep=lambda _seconds: None,
        ).fetch_company_bundle(
            "AAPL",
            allowed_forms=("10-K", "10-Q"),
            max_filings=4,
            concepts=("Assets",),
            max_facts_per_concept=2,
        )

        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(1, bundle.historical_submission_files_loaded)
        self.assertEqual({"10-K", "10-Q"}, {item.form for item in bundle.filings})
        self.assertIn(
            SEC_SUBMISSIONS_FILE_URL.format(name=historical_name),
            calls,
        )

    def test_form4_xml_is_normalized_to_an_insider_purchase(self) -> None:
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <ownershipDocument>
          <reportingOwner>
            <reportingOwnerId>
              <rptOwnerCik>12345</rptOwnerCik>
              <rptOwnerName>Jane Example</rptOwnerName>
            </reportingOwnerId>
          </reportingOwner>
          <nonDerivativeTable>
            <nonDerivativeTransaction>
              <transactionDate><value>2026-08-01</value></transactionDate>
              <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
              <transactionAmounts>
                <transactionShares><value>1250</value></transactionShares>
                <transactionPricePerShare><value>200.50</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
              </transactionAmounts>
              <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction>
              </postTransactionAmounts>
              <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
            </nonDerivativeTransaction>
          </nonDerivativeTable>
        </ownershipDocument>"""
        filing_url: list[str] = []

        def transport(url: str, _headers: dict[str, str], _timeout: float):
            if url == SEC_TICKER_MAP_URL:
                return _ticker_payload()
            if url == SEC_SUBMISSIONS_URL.format(cik=CIK):
                return {"filings": {"recent": _submission_table(["4"])}}
            if url == SEC_COMPANYFACTS_URL.format(cik=CIK):
                return {"facts": {}}
            raise AssertionError(url)

        def text_transport(
            url: str,
            _headers: dict[str, str],
            _timeout: float,
        ) -> bytes:
            filing_url.append(url)
            return xml

        bundle = SecEdgarClient(
            user_agent="JohnySkoreTests tests@example.com",
            transport=transport,
            text_transport=text_transport,
            min_request_interval_seconds=0.0,
            sleep=lambda _seconds: None,
        ).fetch_company_bundle(
            "AAPL",
            allowed_forms=("4",),
            max_filings=1,
            concepts=(),
            max_facts_per_concept=0,
        )

        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(1, len(bundle.insider_transactions))
        transaction = bundle.insider_transactions[0]
        self.assertEqual("0000012345", transaction.owner_cik)
        self.assertEqual("Jane Example", transaction.owner_name)
        self.assertEqual("P", transaction.transaction_code)
        self.assertEqual("A", transaction.acquired_disposed)
        self.assertEqual(1250.0, transaction.shares)
        self.assertEqual(200.5, transaction.price_per_share)
        self.assertEqual(5000.0, transaction.shares_owned_after)
        self.assertEqual([bundle.filings[0].filing_url], filing_url)

    def test_items_for_auditor_and_restatement_are_preserved(self) -> None:
        payload = {
            "filings": {
                "recent": _submission_table(
                    ["8-K"],
                    items=["4.01,4.02"],
                )
            }
        }

        filings = SecEdgarClient._parse_filings(
            payload,
            cik=CIK,
            allowed_forms=("8-K",),
            limit=1,
        )

        self.assertEqual(("4.01", "4.02"), filings[0].items)


if __name__ == "__main__":
    unittest.main()
