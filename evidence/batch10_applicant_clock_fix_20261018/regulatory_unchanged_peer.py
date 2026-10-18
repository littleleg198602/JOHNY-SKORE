"""Independent row/table scope challenges; synthetic cases are not acceptance."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from market_checker_app.collectors import fda_legal_applicant_bridge_review as target
from tests.test_batch9_fda_application_product import fixture as static_fixture, PARTY

PARENT = "PEER SYNTHETIC PARENT INC."
CHILD = "Peer Synthetic Applicant Ireland Unlimited Company"
SHORT = "Peer Synthetic Applicant Ireland"
BASE = "https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/"


def capture(body, url, at="2026-10-16T08:00:00+00:00"):
    if isinstance(body, str): body = body.encode()
    return ({"capture_form": "ORIGINAL_HTTP_RESPONSE", "http_status": 200,
        "request_url": url, "response_url": url, "requested_at": at, "observed_at": at,
        "body_complete_within_byte_budget": True, "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(), "byte_budget": 4_000_000,
        "synthetic_contract_fixture_not_acceptance": True}, body)


def table(rows, *, header=True):
    all_rows = [*( [("Company", "Where Incorporated or Organized")] if header else []), *rows]
    return "<table>"+"".join("<tr>"+"".join("<td>"+cell+"</td>" for cell in row)+"</tr>" for row in all_rows)+"</table>"


def exhibit(roster_rows, foreign_rows=None, *, foreign_header=True):
    foreign = "" if foreign_rows is None else ('<section><h2>Independent license counterparties; not subsidiaries</h2>'
        +table(foreign_rows, header=foreign_header)+"</section>")
    return ('<DOCUMENT>\n<TYPE>EX-21\n<FILENAME>ex21.htm\n<TEXT>\n<html><body>'
        '<section><div>EXHIBIT 21</div><h2>SUBSIDIARIES OF THE COMPANY</h2>'
        '<p>The following is a list of subsidiaries of the Company as of December 31, 2017, omitting some subsidiaries.</p>'
        +table(roster_rows)+"</section>"+foreign+"</body></html>")


def fixture(roster_rows=None, foreign_rows=None, *, foreign_header=True):
    app, app_captures = static_fixture()
    company, company_captures = static_fixture("DRUGSFDA_APPLICATION_PAGE")
    app["source_key"], company["source_key"] = "application", "company"
    app["product_selection"]["source_party_name"] = CHILD
    company["product_selection"]["source_party_name"] = PARENT
    app_capture = capture(app_captures["page"][1].replace(PARTY.encode(), CHILD.encode()),
                          app_captures["page"][0]["request_url"], "2026-10-16T10:00:00+00:00")
    company_capture = capture(company_captures["page"][1].replace(PARTY.encode(), PARENT.encode()),
                              company_captures["page"][0]["request_url"], "2026-10-16T10:00:00+00:00")
    app["source_version_sha256"], company["source_version_sha256"] = app_capture[0]["body_sha256"], company_capture[0]["body_sha256"]
    filing = ('<DOCUMENT>\n<TYPE>10-K\n<FILENAME>parent.htm\n<TEXT>\n<html><body>'
        '<div>FORM 10-K</div><div>For the fiscal year ended December 31, 2017</div><div>'+PARENT+'</div>'
        '<div>(Exact name of registrant as specified in its charter)</div><a href="ex21.htm">21</a></body></html>')
    issuer = capture(json.dumps({"cik": "78003", "name": PARENT, "tickers": ["PFE"]}),
                     app_captures["issuer"][0]["request_url"], "2026-10-16T09:59:00+00:00")
    request = {"scope": {"issuer_cik": "0000078003", "issuer_ticker": "PFE", "issuer_name": PARENT,
        "application_type": "BLA", "application_number": "761081", "applicant_name": CHILD},
        "filing_key": "filing", "exhibit_key": "exhibit", "roster_as_of": "2017-12-31", "selected_rows": [],
        "application_request": app, "company_request": company}
    captures = {"filing": capture(filing, BASE+"parent.htm"),
        "exhibit": capture(exhibit(roster_rows or [(CHILD, "Ireland")], foreign_rows, foreign_header=foreign_header), BASE+"ex21.htm"),
        "application": app_capture, "company": company_capture, "issuer": issuer}
    return request, captures


class ApplicantScopePeerTests(unittest.TestCase):
    def review(self, request, captures):
        with patch("socket.socket.connect", side_effect=AssertionError("No source HTTP")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("No database")), \
             patch("builtins.open", side_effect=AssertionError("No file I/O inside API")):
            return target.review_fda_legal_applicant_bridge(request, captures)

    def no_ownership(self, report):
        self.assertEqual("PARTIAL", report["status"])
        self.assertEqual(0, report["registry_adoptions"])
        for field in ("current_applicant_issuer_control_verified", "direct_legal_parent_verified", "product_owner_verified",
                      "approval_date_is_ownership_start", "continuous_control_inferred", "issuer_attribution_allowed"):
            self.assertFalse(report[field])

    def test_foreign_counterparty_table_is_not_a_dated_subsidiary_point(self):
        request, captures = fixture([("Unrelated Genuine Subsidiary", "Ireland")], [(CHILD, "Ireland")])
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertFalse(report["exact_applicant_name_match_supported"])
        self.assertFalse(any(row["table_index"] == 1 for row in report["exact_applicant_roster_rows"]))

    def test_unrelated_table_cannot_borrow_previous_table_column_header(self):
        request, captures = fixture([("Unrelated Genuine Subsidiary", "Ireland")], [(CHILD, "Ireland")], foreign_header=False)
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertFalse(report["exact_applicant_name_match_supported"])
        self.assertEqual([], report["exact_applicant_roster_rows"])

    def test_caller_selected_foreign_row_cannot_authorize_roster_relationship(self):
        request, captures = fixture([("Unrelated Genuine Subsidiary", "Ireland")], [(CHILD, "Ireland")])
        request["selected_rows"] = [{"table_index": 1, "row_index": 1, "entity_name": CHILD, "jurisdiction": "Ireland"}]
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertFalse(report["exact_applicant_name_match_supported"])
        self.assertFalse(any(row["table_index"] == 1 for row in report["selected_roster_facts"]))

    def test_short_roster_name_and_same_country_do_not_borrow_unlimited_counterparty_name(self):
        request, captures = fixture([(SHORT, "Ireland")], [(CHILD, "Ireland")])
        request["selected_rows"] = [{"table_index": 0, "row_index": 1, "entity_name": SHORT, "jurisdiction": "Ireland"}]
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertFalse(report["exact_applicant_name_match_supported"])
        self.assertFalse(report["similar_name_is_approved_alias"])

    def test_valid_but_borrowed_issuer_CIK_cannot_reroute_original_legal_facts(self):
        request, captures = fixture()
        request["scope"]["issuer_cik"] = "0000789019"
        for name in ("application_request", "company_request"):
            request[name]["scope"]["issuer_cik"] = "0000789019"
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertFalse(report["exact_applicant_name_match_supported"])
        self.assertIsNone(report["roster_facts"])
        self.assertIn("EXACT_SEC_ISSUER_ROUTING_ACCESSION_REQUIRED", report["gaps"])

    def test_actual_unclosed_product_object_and_forged_selected_suffix_stay_withheld(self):
        from evidence.batch10_healthcare_20261018.replay_sources import inputs
        request, captures, _ = inputs()
        before = deepcopy((request, captures))
        request["selected_rows"][0]["entity_name"] = request["scope"]["applicant_name"]
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertFalse(report["exact_applicant_name_match_supported"])
        self.assertFalse(report["applicant_source"]["application_facts"]["detail_container_explicitly_closed"])
        self.assertEqual("2019-03-11", report["applicant_source"]["application_facts"]["original_approval_day"])
        self.assertIn("SELECTED_EXACT_ROSTER_ENTITY_JURISDICTION_ROW_MISSING", report["gaps"])
        self.assertEqual(before[1], captures)

    def test_legitimate_exact_point_with_2019_product_is_still_not_ownership_interval(self):
        request, captures = fixture()
        report = self.review(request, captures)
        self.no_ownership(report)
        self.assertTrue(report["exact_applicant_name_match_supported"])
        row = report["exact_applicant_roster_rows"][0]
        self.assertEqual("2017-12-31", row["roster_as_of"])
        self.assertFalse(row["roster_date_is_ownership_start"])
        self.assertFalse(row["product_application_or_IP_owner_verified"])
        self.assertEqual("2019-03-11", report["applicant_source"]["application_facts"]["original_approval_day"])


if __name__ == "__main__":
    unittest.main()
