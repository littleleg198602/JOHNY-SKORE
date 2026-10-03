from __future__ import annotations

import unittest

from market_checker_app.services.sec_document_extraction import extract_schedule_13_ownership


SCHEDULE_13D = b"""
<html><body>
<h1>SCHEDULE 13D</h1>
<p>Example Issuer Inc.</p><p>(Name of Issuer)</p>
<p>Class A Common Stock</p><p>(Title of Class of Securities)</p>
<p>644393100</p><p>(CUSIP Number)</p>
<p>Notice Recipient</p>
<p>(Name, Address and Telephone Number of Person Authorized to Receive Notices and Communications)</p>
<p>03/09/2026</p><p>(Date of Event Which Requires Filing of This Statement)</p>
<div>SCHEDULE 13D CUSIP No. | 644393100</div>
<div>1 | Name of reporting person Peter Levinson</div>
<div>2 | Check the appropriate box if a member of a Group</div>
<div>11 | Aggregate amount beneficially owned by each reporting person 732,000.00</div>
<div>13 | Percent of class represented by amount in Row (11) .3 %</div>
</body></html>
"""


class Schedule13ExtractionTests(unittest.TestCase):
    def test_extracts_as_filed_cover_fields_without_verifying_identity_or_change(self) -> None:
        result = extract_schedule_13_ownership(SCHEDULE_13D, form="SC 13D")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("2026-03-09", result["event_date"])
        self.assertEqual("Class A Common Stock", result["instrument"]["class_title"])
        self.assertEqual(["644393100"], result["instrument"]["cusips"])
        self.assertEqual("Peter Levinson", result["reporting_persons"][0]["name"])
        self.assertEqual(732000.0, result["reporting_persons"][0]["aggregate_beneficial_shares"])
        self.assertEqual(0.3, result["reporting_persons"][0]["percent_of_class"])
        self.assertFalse(result["reporting_person_identity_verified"])
        self.assertFalse(result["instrument_identity_verified"])
        self.assertFalse(result["ownership_change_interpreted"])
        self.assertFalse(result["scoring_applied"])

    def test_rejects_non_schedule_document_and_invalid_cusip(self) -> None:
        self.assertIsNone(extract_schedule_13_ownership(SCHEDULE_13D, form="10-K"))
        invalid = SCHEDULE_13D.replace(b"644393100", b"644393101")
        result = extract_schedule_13_ownership(invalid, form="SC 13D/A")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([], result["instrument"]["cusips"])
        self.assertFalse(result["instrument_identity_verified"])

    def test_truncated_cover_does_not_invent_people_or_change(self) -> None:
        result = extract_schedule_13_ownership(
            b"<html><h1>SCHEDULE 13G</h1><p>incomplete cover</p></html>",
            form="SC 13G",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
