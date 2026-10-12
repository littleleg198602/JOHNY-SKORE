from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_checker_app.agents.contracts import (
    AgentContext, EntityRecord, GovernanceEventStatus, GovernanceEventType,
)
from market_checker_app.agents.governance_event_agent import GovernanceEventAgent
from market_checker_app.agents.scout_index_agent import ScoutIndexAgent
from market_checker_app.collectors.sec_edgar_client import SecCompany, SecFiling
from market_checker_app.services.sec_form4_service import (
    Form4ValidationError, PARSER_VERSION, extract_form4_observation, form4_transactions_as_of,
)
from market_checker_app.services.sec_scout_service import SecScoutService
from market_checker_app.storage.scout_store import ScoutStore


NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
CIK = "0000320193"
ACCESSION = "0000320193-26-000123"
URL = f"https://www.sec.gov/Archives/edgar/data/320193/{ACCESSION.replace('-', '')}/form4.xml"
FILING = SecFiling(ACCESSION, "4", NOW - timedelta(days=1), NOW - timedelta(days=2),
                   "xslF345X06/form4.xml", URL, URL.replace("form4.xml", f"{ACCESSION}-index.htm"))


def _owner(cik="12345", name="Jane Example"):
    return f"""<reportingOwner><reportingOwnerId><rptOwnerCik>{cik}</rptOwnerCik>
      <rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
      <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
      </reportingOwnerRelationship></reportingOwner>"""


def _transaction(*, code="P", direction="A", title="Class A Common Stock", price="100.5",
                 transaction_date="2026-10-09", footnote=False, tag="nonDerivativeTransaction"):
    price_cell = f"<value>{price}</value>" if price is not None else ""
    price_cell += '<footnoteId id="F1"/>' if footnote else ""
    return f"""<{tag}><securityTitle><value>{title}</value></securityTitle>
      <transactionDate><value>{transaction_date}</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
      <transactionAmounts><transactionShares><value>1250</value></transactionShares>
      <transactionPricePerShare>{price_cell}</transactionPricePerShare>
      <transactionAcquiredDisposedCode><value>{direction}</value></transactionAcquiredDisposedCode>
      </transactionAmounts><postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value>
      </sharesOwnedFollowingTransaction></postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></{tag}>"""


def _xml(*, issuer=CIK, owners=None, transactions=None, plan="0", form="4", footnotes="", extra=""):
    return f"""<ownershipDocument><documentType>{form}</documentType><periodOfReport>2026-10-09</periodOfReport>
      <issuer><issuerCik>{issuer}</issuerCik><issuerName>Apple Inc.</issuerName><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
      {_owner() if owners is None else owners}{'' if plan is None else f'<aff10b5One>{plan}</aff10b5One>'}
      <nonDerivativeTable>{_transaction() if transactions is None else transactions}</nonDerivativeTable>
      <footnotes>{footnotes}</footnotes>{extra}</ownershipDocument>""".encode()


def _parse(payload=None, *, filing=FILING, observed_at=NOW):
    return extract_form4_observation(_xml() if payload is None else payload,
                                    filing=filing, issuer_cik=CIK, observed_at=observed_at)


class _Index:
    def __init__(self, payload=None, filings=(FILING,)):
        self.payload = _xml() if payload is None else payload
        self.filings = filings
        self.document_calls = 0

    def fetch_filing_index(self, ticker, **kwargs):
        return SecCompany(ticker, CIK, "Apple Inc."), self.filings

    def fetch_filing_document(self, filing, *, cik):
        assert cik == CIK
        self.document_calls += 1
        return self.payload


def _analysis(store, *, clock=NOW, cik=CIK, initial=None):
    state = {"entities_by_ticker": {"AAPL": EntityRecord(
        "listing:aapl", "AAPL", cik=cik, legal_entity_id=f"cik:{cik}",
    )}, **(initial or {})}
    orchestration_id = "test-form4" + ("" if clock == NOW else f":{clock.isoformat()}")
    context = AgentContext(orchestration_id, ("AAPL",), clock, state=state)
    indexed = ScoutIndexAgent(store.db_path).run(context)
    context.state.update(indexed.state_updates)
    context.state["agent_results"] = {"scout_index": indexed}
    governed = GovernanceEventAgent().run(context)
    return indexed, governed, context


class Form4ObservationTests(unittest.TestCase):
    def test_captured_sec_documents_preserve_sale_plan_and_non_trade_codes(self):
        evidence = Path(__file__).resolve().parents[1] / "evidence"
        for label, accession, filed, owner, code, count, plan in (
            ("withholding", "0000789019-26-000214", "2026-09-15", "0001819285", "F", 1, False),
            ("award", "0000789019-26-000185", "2026-09-08", "0000904858", "A", 1, False),
            ("sale", "0000789019-26-000161", "2026-09-02", "0001513142", "S", 8, True),
        ):
            with self.subTest(label=label):
                filing = replace(FILING, accession_number=accession,
                    filed_at=datetime.fromisoformat(filed).replace(tzinfo=timezone.utc))
                parsed = extract_form4_observation(
                    (evidence / f"parallel_sec_20261011_form4_{label}.xml").read_bytes(),
                    filing=filing, issuer_cik="789019", observed_at=NOW,
                )
                self.assertEqual(owner, parsed["reporting_owners"][0]["cik"])
                self.assertEqual(count, len(parsed["transactions"]))
                self.assertEqual({code}, {row["transaction_code"] for row in parsed["transactions"]})
                self.assertIs(plan, parsed["rule_10b5_1_checkbox"])
                if label == "sale":
                    self.assertIn("March 8, 2026", parsed["remarks"])

    def test_single_owner_and_exact_share_class_are_preserved_without_instrument_resolution(self):
        observation = _parse()
        self.assertEqual(CIK, observation["issuer_cik"])
        self.assertEqual("0000012345", observation["reporting_owners"][0]["cik"])
        self.assertEqual("AS_FILED_CIK", observation["reporting_owners"][0]["identity_status"])
        self.assertFalse(observation["reporting_owner_identity_verified"])
        rows = form4_transactions_as_of(observation, issuer_cik=CIK, accession_number=ACCESSION,
                                       source_url=URL, knowledge_at=NOW)
        self.assertEqual(1, len(rows))
        self.assertEqual("Class A Common Stock", rows[0].security_title)
        self.assertEqual("nonDerivativeTable/nonDerivativeTransaction:1", rows[0].row_locator)
        self.assertEqual(1250.0, rows[0].shares)
        self.assertEqual(100.5, rows[0].price_per_share)
        self.assertFalse(observation["instrument_identity_verified"])
        self.assertFalse(observation["scoring_applied"])

    def test_rule10b5_1_checkbox_is_three_state_document_context(self):
        for value, expected in (("0", False), ("false", False), ("1", True), ("true", True), (None, None)):
            with self.subTest(value=value):
                parsed = _parse(_xml(plan=value))
                self.assertIs(expected, parsed["rule_10b5_1_checkbox"])
                self.assertEqual("DOCUMENT_CHECKBOX_NOT_PER_TRANSACTION", parsed["rule_10b5_1_scope"])
        with self.assertRaises(Form4ValidationError):
            _parse(_xml(plan="sometimes"))

    def test_joint_report_is_not_multiplied_or_assigned_to_first_owner(self):
        observation = _parse(_xml(owners=_owner() + _owner("23456", "Other Owner")))
        self.assertEqual(2, len(observation["reporting_owners"]))
        self.assertEqual("JOINT_REPORT_NOT_ALLOCATED", observation["transactions"][0]["owner_attribution"])
        self.assertEqual([], form4_transactions_as_of(observation, issuer_cik=CIK,
                        accession_number=ACCESSION, source_url=URL, knowledge_at=NOW))

    def test_missing_footnoted_price_is_unknown_and_note_is_retained(self):
        observation = _parse(_xml(transactions=_transaction(price=None, footnote=True),
                                 footnotes='<footnote id="F1">Price omitted; see contract.</footnote>',
                                 extra="<remarks>The reporting person adopted a trading plan on 2026-03-01.</remarks>"))
        self.assertIsNone(observation["transactions"][0]["price_per_share"])
        rows = form4_transactions_as_of(observation, issuer_cik=CIK,
               accession_number=ACCESSION, source_url=URL, knowledge_at=NOW)
        self.assertEqual((("F1", "Price omitted; see contract."),), rows[0].footnotes)
        self.assertIn("2026-03-01", rows[0].remarks)

    def test_wrong_issuer_invalid_owner_and_missing_note_are_rejected(self):
        for payload in (_xml(issuer="789019"), _xml(owners=_owner("0")),
                        _xml(owners=_owner() + _owner()), _xml(owners=""),
                        _xml(transactions=_transaction(footnote=True))):
            with self.subTest(payload=payload[:50]), self.assertRaises(Form4ValidationError):
                _parse(payload)

    def test_wrong_type_html_dtd_and_size_limits_fail_closed(self):
        for payload in (_xml(form="3"), b"<html>SEC access denied</html>",
                        b'<!DOCTYPE ownershipDocument [<!ENTITY x "text">]>' + _xml()):
            with self.subTest(payload=payload[:50]), self.assertRaises(Form4ValidationError):
                _parse(payload)
        with patch("market_checker_app.services.sec_form4_service.MAX_FORM4_BYTES", 10):
            with self.assertRaises(Form4ValidationError):
                _parse()
        with patch("market_checker_app.services.sec_form4_service.MAX_FORM4_TRANSACTIONS", 1):
            with self.assertRaises(Form4ValidationError):
                _parse(_xml(transactions=_transaction() + _transaction()))

    def test_invalid_numbers_and_dates_do_not_silently_drop_rows(self):
        for kwargs in ({"price": "NaN"}, {"price": "-1"}, {"price": "infinite"},
                       {"transaction_date": "2026-99-99"}, {"transaction_date": "2026-10-12"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Form4ValidationError):
                _parse(_xml(transactions=_transaction(**kwargs)))

    def test_first_observation_and_filing_dates_prevent_lookahead(self):
        parsed = _parse()
        self.assertEqual([], form4_transactions_as_of(parsed, issuer_cik=CIK, accession_number=ACCESSION,
                         source_url=URL, knowledge_at=NOW - timedelta(seconds=1)))
        with self.assertRaises(Form4ValidationError):
            _parse(observed_at=FILING.filed_at - timedelta(seconds=1))
        with self.assertRaises(Form4ValidationError):
            _parse(observed_at=NOW.replace(tzinfo=None))
        for kwargs in ({"issuer_cik": "789019"}, {"accession_number": "another"}, {"source_url": URL + "wrong"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Form4ValidationError):
                form4_transactions_as_of(parsed, **{"issuer_cik": CIK, "accession_number": ACCESSION,
                                         "source_url": URL, "knowledge_at": NOW, **kwargs})

    def test_holdings_are_not_transactions_and_namespace_is_supported(self):
        payload = _xml(extra="""<derivativeTable><derivativeTransaction>
          <securityTitle><value>Stock Option</value></securityTitle><transactionDate><value>2026-10-09</value></transactionDate>
          <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
          </derivativeTransaction><derivativeHolding><securityTitle><value>RSU</value></securityTitle></derivativeHolding>
          </derivativeTable>""").replace(b"<ownershipDocument>", b'<ownershipDocument xmlns="urn:sec:ownership">')
        parsed = _parse(payload)
        self.assertEqual(2, len(parsed["transactions"]))
        self.assertFalse(parsed["transactions"][0]["derivative"])
        self.assertTrue(parsed["transactions"][1]["derivative"])
        self.assertEqual(1, parsed["non_transaction_holdings"])


class Form4ProductionChainTests(unittest.TestCase):
    def test_pipeline_uses_durable_scout_when_synchronous_sec_is_disabled(self):
        import pandas as pd
        from market_checker_app.config import AppConfig, DecisionAgentConfig, EntityRegistryConfig
        from market_checker_app.services.pipeline_service import PipelineService

        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            scout = SecScoutService(store, client=_Index())
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            config = AppConfig(
                sqlite_path=store.db_path,
                entity_registry=EntityRegistryConfig(enable_gleif=False, identity_records={
                    "AAPL": EntityRecord("listing:aapl", "AAPL", cik=CIK,
                                         legal_entity_id=f"cik:{CIK}"),
                }),
                decision_agent=DecisionAgentConfig(enabled=False),
            )
            self.assertFalse(config.fundamental_ingestion.enabled)
            self.assertFalse(config.european_filings.enabled)
            with patch("market_checker_app.services.pipeline_service.datetime") as clock:
                clock.now.return_value = NOW
                with patch("market_checker_app.agents.orchestrator.utc_now", return_value=NOW):
                    report = PipelineService(config)._run_agents(
                        ["AAPL"], pd.DataFrame({"ticker": ["AAPL"]}),
                    )
            executions = {row.agent_name: row for row in report.executions}
            self.assertIn("scout_index", executions)
            self.assertIn("governance_event", executions)
            self.assertNotIn("f2_sec", executions)
            events = executions["governance_event"].result.governance_events
            self.assertEqual(1, len(events))
            self.assertFalse(events[0].metadata["scoring_applied"])
            self.assertEqual(CIK, events[0].metadata["issuer_cik"])

    def test_governance_prefers_durable_rows_after_synchronous_state_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            scout = SecScoutService(store, client=_Index())
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            indexed, _, context = _analysis(store)
            context.state["sec_insider_transactions_by_ticker"] = {}
            events = GovernanceEventAgent().run(context).governance_events
            self.assertEqual(1, len(events))
            self.assertEqual(CIK, events[0].metadata["issuer_cik"])

    def test_new_primary_version_supersedes_rows_but_old_evidence_remains_available(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            client = _Index()
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            index = next(row for row in store.findings_as_of("AAPL", as_of=NOW)
                         if json.loads(row["details_json"])["stage"] == "filing_index")
            later = NOW + timedelta(days=1)
            client.payload = _xml(transactions=_transaction(price="200"))
            store.enqueue(source="sec", subject_id="AAPL", reason=f"filing_document:{ACCESSION}",
                          due_at=later, priority=-1, cursor=index["finding_id"])
            scout.run_batch(as_of=later, limit=1)
            original = _analysis(store)[1].governance_events
            indexed, governed, _ = _analysis(store, clock=later)
            self.assertEqual(100.5, original[0].price_per_share)
            self.assertEqual(1, len(governed.governance_events))
            self.assertEqual(200.0, governed.governance_events[0].price_per_share)
            self.assertEqual(1, indexed.metadata["superseded_form4_versions"])
            self.assertEqual(3, len(store.findings_for_snapshot(f"test-form4:{later.isoformat()}")))

    def test_storage_replay_and_governance_keep_provenance_without_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            client = _Index(_xml(plan="1", transactions=_transaction(footnote=True),
                                footnotes='<footnote id="F1">Contract price is weighted average.</footnote>'))
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL"], as_of=NOW)
            self.assertEqual(2, scout.run_batch(as_of=NOW, limit=2)["new_findings"])
            indexed, governed, context = _analysis(store)
            self.assertEqual(1, len(indexed.documents))
            self.assertEqual(1, indexed.metadata["superseded_index_documents"])
            self.assertEqual(1, indexed.metadata["observed_form4_transactions"])
            self.assertEqual(1, len(governed.governance_events))
            event = governed.governance_events[0]
            self.assertEqual(GovernanceEventType.INSIDER_TRADE, event.event_type)
            self.assertEqual(125625.0, event.event_value)
            self.assertFalse(event.metadata["open_market_trade"])
            self.assertTrue(event.metadata["purchase_sale_coded"])
            self.assertFalse(event.metadata["execution_venue_verified"])
            self.assertTrue(event.metadata["rule_10b5_1_checkbox"])
            self.assertEqual("Class A Common Stock", event.metadata["security_title"])
            self.assertEqual({"F1": "Contract price is weighted average."}, event.metadata["as_filed_footnotes"])
            self.assertFalse(event.metadata["instrument_identity_verified"])
            self.assertTrue(event.metadata["human_review_required"])
            self.assertFalse(event.metadata["scoring_applied"])
            self.assertEqual(0.0, governed.evidence[0].direction)
            self.assertEqual(2, len(store.findings_for_snapshot("test-form4")))
            later = NOW + timedelta(days=1)
            restored = SecScoutService(ScoutStore(store.db_path), client=client)
            restored.schedule(["AAPL"], as_of=later)
            self.assertEqual(0, restored.run_batch(as_of=later, limit=2)["new_findings"])
            self.assertEqual(1, client.document_calls)
            self.assertEqual(0, _analysis(store, clock=NOW - timedelta(seconds=1))[0].metadata["observed_form4_transactions"])

    def test_different_classes_and_identical_rows_do_not_collapse_events(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            payload = _xml(transactions=_transaction() + _transaction(title="Class B Common Stock") + _transaction())
            scout = SecScoutService(store, client=_Index(payload))
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            indexed, governed, _ = _analysis(store)
            self.assertEqual(3, indexed.metadata["observed_form4_transactions"])
            self.assertEqual(3, len(governed.governance_events))
            self.assertEqual(3, len({event.event_id for event in governed.governance_events}))

    def test_issuer_conflict_keeps_index_and_failed_job_then_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scout.db"
            store = ScoutStore(path)
            broken = SecScoutService(store, client=_Index(_xml(issuer="789019")))
            broken.schedule(["AAPL"], as_of=NOW)
            result = broken.run_batch(as_of=NOW, limit=2)
            self.assertEqual("PARTIAL", result["status"])
            self.assertEqual(1, result["failed"])
            self.assertEqual(1, len(store.findings_as_of("AAPL", as_of=NOW)))
            self.assertEqual(0, _analysis(store)[0].metadata["observed_form4_transactions"])
            restored = SecScoutService(ScoutStore(path), client=_Index())
            self.assertEqual(1, restored.run_batch(as_of=NOW + timedelta(minutes=31), limit=2)["new_findings"])

    def test_joint_report_replaces_legacy_first_owner_state_without_events(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            single = form4_transactions_as_of(_parse(), issuer_cik=CIK, accession_number=ACCESSION,
                                             source_url=URL, knowledge_at=NOW)[0]
            scout = SecScoutService(store, client=_Index(_xml(owners=_owner() + _owner("23456", "Other Owner"))))
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            indexed, governed, context = _analysis(store, initial={"sec_insider_transactions_by_ticker": {"AAPL": [single]}})
            self.assertEqual(2, len(indexed.documents[0].metadata["form4_observation"]["reporting_owners"]))
            self.assertEqual([], context.state["sec_insider_transactions_by_ticker"]["AAPL"])
            self.assertEqual([], governed.governance_events)

    def test_all_bounded_form4s_and_amendments_are_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            second = replace(FILING, accession_number="0000320193-26-000124")
            client = _Index(filings=(FILING, second))
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL"], as_of=NOW)
            self.assertEqual(4, scout.run_batch(as_of=NOW, limit=3)["new_findings"])
            self.assertEqual(2, client.document_calls)
            self.assertEqual(2, _analysis(store)[0].metadata["observed_form4_transactions"])

    def test_amendment_rows_stay_unverified_without_claiming_incremental_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            amendment = replace(FILING, form="4/A")
            client = _Index(_xml(form="4/A", extra="<dateOfOriginalSubmission>2026-10-09</dateOfOriginalSubmission>"), (amendment,))
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL"], as_of=NOW)
            self.assertEqual("OK", scout.run_batch(as_of=NOW, limit=2)["status"])
            event = _analysis(store)[1].governance_events[0]
            self.assertEqual(GovernanceEventStatus.UNVERIFIED, event.status)
            self.assertTrue(event.metadata["is_amendment"])
            self.assertFalse(event.metadata["ownership_change_interpreted"])

    def test_governance_rejects_conflicting_entity_cik(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            scout = SecScoutService(store, client=_Index())
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=2)
            governed = _analysis(store, cik="789019")[1]
            self.assertEqual([], governed.governance_events)
            self.assertTrue(any("issuer/provenance conflict" in warning for warning in governed.warnings))

    def test_backfill_old_unparsed_primary_is_new_observation_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "scout.db")
            client = _Index()
            scout = SecScoutService(store, client=client)
            scout.schedule(["AAPL"], as_of=NOW)
            scout.run_batch(as_of=NOW, limit=1)
            index = store.findings_as_of("AAPL", as_of=NOW)[0]
            old_details = json.loads(index["details_json"])
            old_details.update(stage="filing_document", parser_version="sec-html-v1")
            old_id, _ = store.record_finding(source="sec", subject_id="AAPL", source_object_id=index["source_object_id"],
                content_hash=hashlib.sha256(client.payload).hexdigest(), title="Legacy XML without rows", source_url=URL,
                locator="legacy", published_at=FILING.filed_at, available_at=NOW, observed_at=NOW, details=old_details)
            old_job = store.lease(as_of=NOW, source="sec")
            store.finish(old_job, as_of=NOW)
            later = NOW + timedelta(days=1)
            scout.schedule(["AAPL"], as_of=later)
            self.assertEqual(1, scout.run_batch(as_of=later, limit=2)["new_findings"])
            self.assertNotIn("form4_observation", json.loads(store.finding_by_id(old_id, subject_id="AAPL")["details_json"]))
            self.assertEqual(0, _analysis(store, clock=NOW)[0].metadata["observed_form4_transactions"])
            indexed, governed, _ = _analysis(store, clock=later)
            self.assertEqual(1, len(indexed.documents))
            self.assertEqual(1, indexed.metadata["superseded_form4_versions"])
            self.assertEqual(PARSER_VERSION, indexed.documents[0].metadata["form4_observation"]["parser_version"])
            self.assertEqual(1, len(governed.governance_events))
            self.assertEqual(3, len(store.findings_for_snapshot(f"test-form4:{later.isoformat()}")))


if __name__ == "__main__":
    unittest.main()
