"""Original-byte specimen linkage and derived visual fields, never acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from market_checker_app.collectors import sec_certificate_instrument_review as module

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
OBSERVED = NOW - timedelta(hours=1)
BASE = "https://www.sec.gov/Archives/edgar/data/1474432/000147443226000027/"
CLASS = "Class A Common Stock, $0.0001 par value per share"
SENTENCE = "FULLY PAID AND NONASSESSABLE SHARES OF CLASS A COMMON STOCK, $0.0001 PAR VALUE PER SHARE, OF"


def capture(url, body):
    return ({"requested_url": url, "final_url": url, "status": "PUBLIC_ORIGINAL_BYTES_CAPTURED", "http_status": 200,
             "started_at": (OBSERVED - timedelta(seconds=1)).isoformat(), "observed_at": OBSERVED.isoformat(),
             "raw_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}, body)


def replace_body(captures, key, body):
    captures[key] = capture(captures[key][0]["requested_url"], body)


def fixture():
    wrapper = b'<html><body><img src="image_0.jpg"><img src="image_1.jpg"></body></html>'
    index = f'''<html><span class="companyName">Everpure, Inc. CIK: <a href="/cgi-bin/browse-edgar?CIK=0001474432&amp;action=getcompany">0001474432</a></span>
    <div class="infoHead">Filing Date</div><div class="info">2026-03-25</div>
    <div class="infoHead">Accepted</div><div class="info">2026-03-24 18:36:40</div>
    <table><tr><td>2</td><td>EX-4.1</td><td><a href="{BASE}certificate.htm">certificate.htm</a></td><td>EX-4.1</td><td>{len(wrapper)}</td></tr></table></html>'''.encode()
    fields = [
        {"field": "issuer_name", "literal": "EVERPURE, INC.", "bounds": [10, 35, 300, 60]},
        {"field": "class_and_par", "literal": SENTENCE, "bounds": [10, 75, 780, 100]},
        {"field": "cusip", "literal": "CUSIP 74624M 10 2", "bounds": [10, 115, 300, 140]},
        {"field": "specimen_mark", "literal": "SPECIMEN", "bounds": [10, 155, 300, 180]},
        {"field": "dated_field", "literal": "Dated:", "bounds": [10, 195, 600, 220]},
        {"field": "corporate_seal", "literal": "CORPORATE SEAL OCTOBER 2, 2009", "bounds": [10, 235, 500, 260]},
    ]
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    for row in fields:
        draw.text((row["bounds"][0], row["bounds"][1]), row["literal"], fill="black")
    stream = BytesIO(); image.save(stream, format="PNG"); raw = stream.getvalue()
    captures = {"wrapper": capture(BASE + "certificate.htm", wrapper),
                "index": capture(BASE + "0001474432-26-000027-index.html", index),
                "image": capture(BASE + "image_0.jpg", raw)}
    measurement = {"schema_version": "sec_certificate_visual_measurement_v1", "source_image_sha256": hashlib.sha256(raw).hexdigest(),
                   "image_dimensions": [800, 500], "measured_at": (OBSERVED + timedelta(minutes=1)).isoformat(),
                   "method": "VISUAL_TRANSCRIPTION_FROM_ORIGINAL", "fields": fields}
    request = {"scope": {"issuer_cik": "0001474432", "issuer_name": "Everpure, Inc.", "class_title": CLASS,
                          "cusip": "74624M102", "old_symbol": "PSTG", "new_symbol": "P"},
               "wrapper_key": "wrapper", "index_key": "index", "image_key": "image", "image_reference": "image_0.jpg", "measurement": measurement}
    return request, captures


class CertificateInstrumentReviewTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(module, "_physical_clock", return_value=NOW)
        patcher.start(); self.addCleanup(patcher.stop)
        self.request, self.captures = fixture()

    def review(self, **kwargs):
        return module.review_sec_certificate_instrument(self.request, self.captures, **kwargs)

    def field(self, name):
        return next(row for row in self.request["measurement"]["fields"] if row["field"] == name)

    def test_exact_source_link_and_literal_fields_remain_a_specimen_not_an_adoption(self):
        before = deepcopy((self.request, self.captures))
        with patch("socket.socket.connect", side_effect=AssertionError("No HTTP")), patch("sqlite3.connect", side_effect=AssertionError("No DB")), patch.object(Path, "glob", side_effect=AssertionError("No discovery")):
            report = self.review()
        self.assertEqual(before, (self.request, self.captures))
        self.assertTrue(report["literal_specimen_class_cusip_scope_observed"])
        self.assertTrue(report["measurement_is_derived_annotation"])
        self.assertFalse(report["automated_pixel_text_authentication"])
        self.assertEqual("74624M102", report["observed_cusip"])
        self.assertEqual({"share_class": "EQUITY:A", "par_term": "USD:0.0001"}, report["observed_class_terms"])
        self.assertEqual("CERTIFICATE_SPECIMEN_SCOPE_PARTIAL", report["status"])
        self.assertEqual(0, report["registry_adoptions"])
        self.assertEqual(0, report["current_q3_verified_instrument_coverage"])
        for key in ("effective_on", "certificate_number", "issued_share_count", "registered_holder"):
            self.assertIsNone(report[key])
        for key in ("issued_instrument_validity_verified", "symbol_cusip_continuity_verified", "automatic_alias_allowed", "production_registry_changed", "historical_machine_availability_inferred", "score_changed", "trading_allowed", "interval_extension_allowed", "facsimile_signature_authenticates_person", "countersignature_or_registration_validity_verified"):
            self.assertFalse(report[key])
        digest = report.pop("review_sha256")
        self.assertEqual(digest, hashlib.sha256(module._encoded(report)).hexdigest())

    def test_original_link_must_match_the_image_and_cannot_be_duplicated(self):
        self.request["image_reference"] = "image_1.jpg"
        with self.assertRaisesRegex(ValueError, "literal wrapper link"): self.review()
        self.request, self.captures = fixture()
        replace_body(self.captures, "wrapper", self.captures["wrapper"][1].replace(b"image_1.jpg", b"image_0.jpg"))
        with self.assertRaisesRegex(ValueError, "unambiguous"): self.review()

    def test_hidden_link_or_alternate_base_cannot_authorize_an_image(self):
        for wrapper in (b'<html><body><img hidden src="image_0.jpg"></body></html>',
                        b'<html><body><div style="display:none"><img src="image_0.jpg"></div></body></html>',
                        b'<html><head><base href="https://example.test/"></head><img src="image_0.jpg"></html>'):
            with self.subTest(wrapper=wrapper):
                replace_body(self.captures, "wrapper", wrapper)
                with self.assertRaises(ValueError): self.review()

    def test_foreign_escape_query_and_non_image_references_are_refused(self):
        for literal in ("../image_0.jpg", "https://example.test/image_0.jpg", "image_0.jpg?key=value", "image_0.svg"):
            with self.subTest(literal=literal):
                replace_body(self.captures, "wrapper", ('<html><img src="' + literal + '"></html>').encode())
                with self.assertRaisesRegex(ValueError, "same-directory"): self.review()

    def test_publication_index_filer_accession_and_form_are_not_borrowed(self):
        for old, new in ((b"0001474432", b"0000029915"), (b"EX-4.1", b"EX-99.1"), (b"certificate.htm", b"other.htm")):
            with self.subTest(new=new):
                self.request, self.captures = fixture()
                replace_body(self.captures, "index", self.captures["index"][1].replace(old, new))
                with self.assertRaises(ValueError): self.review()
        self.request, self.captures = fixture()
        metadata, body = self.captures["index"]
        self.captures["index"] = capture(metadata["requested_url"].replace("000147443226000027", "000147443226000028"), body)
        with self.assertRaisesRegex(ValueError, "accession"): self.review()

    def test_index_size_and_unqualified_acceptance_conflicts_are_preserved(self):
        old = str(len(self.captures["wrapper"][1])).encode()
        replace_body(self.captures, "index", self.captures["index"][1].replace(b"<td>" + old + b"</td>", b"<td>1252</td>"))
        report = self.review()
        self.assertIn("INDEX_DECLARED_SERVED_BYTE_CONFLICT", report["blockers"])
        self.assertIn("INDEX_ACCEPTANCE_CALENDAR_DAY_DIFFERS_FROM_FILING_DAY", report["blockers"])
        self.assertIsNone(report["publication_scope"]["publication_at"])
        self.assertEqual("2026-03-24 18:36:40", report["publication_scope"]["acceptance_unqualified_raw"])
        self.assertFalse(report["publication_scope"]["acceptance_timezone_inferred"])
        self.assertFalse(report["publication_scope"]["image_publication_timestamp_verified"])

    def test_measurement_must_pin_the_original_hash_dimensions_and_pixel_extent(self):
        for field, value in (("source_image_sha256", "0" * 64), ("image_dimensions", [801, 500])):
            with self.subTest(field=field):
                self.request, self.captures = fixture(); self.request["measurement"][field] = value
                with self.assertRaisesRegex(ValueError, "exact original image"): self.review()
        for box in ([0, 0, 801, 500], [-1, 0, 20, 20], [10, 10, 10, 20], [False, 0, 20, 20]):
            with self.subTest(box=box):
                self.request, self.captures = fixture(); self.field("issuer_name")["bounds"] = box
                with self.assertRaisesRegex(ValueError, "pixel extent"): self.review()

    def test_measurement_clock_cannot_precede_capture_or_follow_new_physical_review(self):
        for value in (OBSERVED - timedelta(seconds=1), NOW + timedelta(seconds=1)):
            with self.subTest(value=value):
                self.request["measurement"]["measured_at"] = value.isoformat()
                with self.assertRaisesRegex(ValueError, "measurement cannot precede"): self.review()
        self.request["measurement"]["measured_at"] = (OBSERVED + timedelta(seconds=1)).isoformat()
        report = self.review()
        self.assertNotEqual(report["measurement_recorded_at"], report["source_knowledge_at"])
        self.assertEqual(NOW.isoformat(), report["knowledge_at"])

    def test_new_interpretation_cannot_use_explicit_past_or_future_asof(self):
        for delta in (-1, 1):
            with self.subTest(delta=delta):
                with self.assertRaisesRegex(ValueError, "backdated or future-dated"): self.review(as_of=NOW + timedelta(seconds=delta))

    def test_completion_clock_records_new_knowledge_after_the_start_source_cutoff(self):
        completed = NOW + timedelta(seconds=4)
        with patch.object(module, "_physical_clock", side_effect=[NOW, completed]) as clock:
            report = self.review()
        self.assertEqual(2, clock.call_count)
        self.assertEqual(NOW.isoformat(), report["review_started_at"])
        self.assertEqual(NOW.isoformat(), report["as_of"])
        self.assertEqual(completed.isoformat(), report["review_recorded_at"])
        self.assertEqual(completed.isoformat(), report["knowledge_at"])
        self.assertEqual(OBSERVED.isoformat(), report["source_knowledge_at"])
        self.assertLess(report["measurement_recorded_at"], report["knowledge_at"])

    def test_completion_clock_is_sampled_after_slow_image_and_derived_class_parsing(self):
        current, events = [NOW], []
        decode, class_field = module._image_dimensions, module._certificate_class_terms

        def physical():
            events.append(("clock", current[0]))
            return current[0]

        def slow_decode(body):
            events.append(("decode", current[0]))
            result = decode(body)
            current[0] += timedelta(seconds=3)
            return result

        def slow_class(literal):
            events.append(("class", current[0]))
            result = class_field(literal)
            current[0] += timedelta(seconds=2)
            return result

        with patch.object(module, "_physical_clock", side_effect=physical), \
                patch.object(module, "_image_dimensions", side_effect=slow_decode), \
                patch.object(module, "_certificate_class_terms", side_effect=slow_class):
            report = self.review()
        self.assertEqual(["clock", "decode", "class", "clock"], [event for event, _ in events])
        self.assertEqual((NOW + timedelta(seconds=5)).isoformat(), report["knowledge_at"])
        self.assertTrue(report["literal_specimen_class_cusip_scope_observed"])
        self.assertFalse(report["automated_pixel_text_authentication"])
        self.assertIsNone(report["effective_on"])

    def test_physical_completion_rollback_cannot_backdate_the_new_interpretation(self):
        with patch.object(module, "_physical_clock", side_effect=[NOW, NOW - timedelta(microseconds=1)]):
            with self.assertRaisesRegex(ValueError, "clock moved backwards"): self.review()

    def test_explicit_source_cutoff_cannot_include_an_interpretation_completed_later(self):
        with patch.object(module, "_physical_clock", side_effect=[NOW, NOW + timedelta(microseconds=1)]):
            with self.assertRaisesRegex(ValueError, "completed after the explicit cutoff"): self.review(as_of=NOW)

    def test_equal_start_and_completion_are_valid_with_an_explicit_aware_cutoff(self):
        with patch.object(module, "_physical_clock", side_effect=[NOW, NOW]) as clock:
            report = self.review(as_of=NOW.astimezone(timezone(timedelta(hours=2))))
        self.assertEqual(2, clock.call_count)
        self.assertEqual(NOW.isoformat(), report["review_started_at"])
        self.assertEqual(NOW.isoformat(), report["knowledge_at"])
        self.assertEqual(NOW.isoformat(), report["as_of"])

    def test_unavailable_image_partial_review_also_records_its_completion(self):
        metadata, _ = self.captures["image"]
        self.captures["image"] = ({**metadata, "status": "WITHHELD_SENSITIVE_BODY"}, None)
        self.request["measurement"] = None
        completed = NOW + timedelta(seconds=2)
        with patch.object(module, "_physical_clock", side_effect=[NOW, completed]):
            report = self.review()
        self.assertIn("review_started_at", report)
        self.assertEqual(NOW.isoformat(), report["review_started_at"])
        self.assertEqual(completed.isoformat(), report["knowledge_at"])
        self.assertIn("ORIGINAL_CERTIFICATE_IMAGE_UNAVAILABLE", report["blockers"])
        self.assertFalse(report["literal_specimen_class_cusip_scope_observed"])

    def test_physical_completion_clock_requires_an_explicit_timezone(self):
        with patch.object(module, "_physical_clock", side_effect=[NOW, NOW.replace(tzinfo=None)]):
            with self.assertRaisesRegex(ValueError, "requires a timezone"): self.review()

    def test_ocr_uncertainty_is_not_silently_corrected_into_a_cusip(self):
        for literal in ("CUSIP ?74be4M 10 2", "CUSIP 74624M103", "74624M102", "CUSIP 74624M 10 2 037833100"):
            with self.subTest(literal=literal):
                self.field("cusip")["literal"] = literal
                with self.assertRaisesRegex(ValueError, "valid checksum"): self.review()

    def test_different_valid_cusip_name_and_class_are_explicit_conflicts(self):
        for name, literal, blocker in (("cusip", "CUSIP 037833100", "OBSERVED_EXPECTED_CUSIP_CONFLICT"),
                                      ("issuer_name", "Different Company", "OBSERVED_EXPECTED_ISSUER_NAME_CONFLICT"),
                                      ("class_and_par", "Class B Common Stock, $0.0001 par value per share", "OBSERVED_EXPECTED_CLASS_OR_PAR_CONFLICT")):
            with self.subTest(name=name):
                self.request, self.captures = fixture(); self.field(name)["literal"] = literal
                report = self.review(); self.assertIn(blocker, report["blockers"])
                self.assertFalse(report["literal_specimen_class_cusip_scope_observed"])
                self.assertEqual(0, report["registry_adoptions"])

    def test_only_the_exact_certificate_class_sentence_envelope_is_supported(self):
        self.assertTrue(self.review()["literal_specimen_class_cusip_scope_observed"])
        for literal in ("Another company has " + CLASS, SENTENCE + " another issuer", "FULLY PAID SHARES OF " + CLASS, "Common Stock"):
            with self.subTest(literal=literal):
                self.field("class_and_par")["literal"] = literal
                self.assertIn("OBSERVED_EXPECTED_CLASS_OR_PAR_CONFLICT", self.review()["blockers"])

    def test_missing_visual_measurement_does_not_trigger_ocr_or_invent_fields(self):
        self.request["measurement"] = None
        with patch("subprocess.run", side_effect=AssertionError("No OCR process")):
            report = self.review()
        self.assertIn("EXPLICIT_ORIGINAL_IMAGE_VISUAL_MEASUREMENT_MISSING", report["blockers"])
        self.assertIsNone(report["observed_cusip"])
        self.assertIsNone(report["measurement_recorded_at"])
        self.assertFalse(report["literal_specimen_class_cusip_scope_observed"])

    def test_seal_date_and_nonblank_dated_field_cannot_become_action_or_issue_validity(self):
        report = self.review()
        self.assertEqual("CORPORATE SEAL OCTOBER 2, 2009", report["literal_observations"]["corporate_seal"]["literal"])
        self.assertIn("CERTIFICATE_DATED_FIELD_BLANK", report["blockers"])
        self.field("dated_field")["literal"] = "Dated: October 2, 2009"
        report = self.review()
        self.assertIn("CERTIFICATE_DATE_TEXT_IS_NOT_AN_ACTION_EFFECTIVE_DAY", report["blockers"])
        self.assertIsNone(report["effective_on"])
        self.assertFalse(report["publication_scope"]["corporate_seal_is_action_effective_day"])

    def test_missing_fields_or_wrong_specimen_mark_remain_partial(self):
        self.request["measurement"]["fields"] = [row for row in self.request["measurement"]["fields"] if row["field"] != "dated_field"]
        report = self.review()
        self.assertEqual(["dated_field"], report["missing_literal_fields"])
        self.assertFalse(report["literal_specimen_class_cusip_scope_observed"])
        self.request, self.captures = fixture(); self.field("specimen_mark")["literal"] = "ISSUED"
        self.assertIn("EXACT_SPECIMEN_MARK_UNRESOLVED", self.review()["blockers"])

    def test_transport_failure_has_unknown_body_and_is_not_an_http_empty_or_negative(self):
        metadata = deepcopy(self.captures["image"][0])
        for key in ("raw_sha256", "bytes"):
            metadata.pop(key)
        metadata.update(status="CAPTURE_FAILED", http_status=None, error_type="URLError")
        self.captures["image"] = (metadata, None); self.request["measurement"] = None
        report = self.review(); image = report["capture_pins"]["image"]
        self.assertIsNone(image["http_status"]); self.assertIsNone(image["sha256"]); self.assertIsNone(image["bytes"])
        self.assertFalse(image["body_available"])
        self.assertIn("ORIGINAL_CERTIFICATE_IMAGE_UNAVAILABLE", report["blockers"])
        self.assertIsNone(report["observed_cusip"])

    def test_withheld_image_cannot_be_restored_or_replaced_by_a_transcription(self):
        metadata, body = self.captures["image"]
        self.captures["image"] = ({**metadata, "status": "WITHHELD_SENSITIVE_BODY"}, body)
        with self.assertRaisesRegex(ValueError, "restored or substituted"): self.review()
        self.captures["image"] = ({**metadata, "status": "WITHHELD_SENSITIVE_BODY"}, None)
        with self.assertRaisesRegex(ValueError, "replacement visual"): self.review()
        self.request["measurement"] = None
        self.assertIn("ORIGINAL_CERTIFICATE_IMAGE_UNAVAILABLE", self.review()["blockers"])

    def test_any_access_bearing_whole_body_or_request_is_refused(self):
        replace_body(self.captures, "wrapper", self.captures["wrapper"][1] + b' api_key="EXAMPLEONLY000000"')
        with self.assertRaisesRegex(ValueError, "access-bearing"): self.review()
        self.request, self.captures = fixture(); self.field("issuer_name")["literal"] = "api_key=EXAMPLEONLY000000"
        with self.assertRaisesRegex(ValueError, "safe certificate"): self.review()

    def test_incomplete_nonimage_animated_or_overbudget_pixels_are_refused(self):
        for raw in (b"not a certificate image", self.captures["image"][1][:100]):
            with self.subTest(kind=len(raw)):
                replace_body(self.captures, "image", raw); self.request["measurement"] = None
                with self.assertRaisesRegex(ValueError, "cannot be decoded"): self.review()
        for size, fmt in (((4000, 4000), "PNG"), ((20, 20), "GIF")):
            with self.subTest(size=size, fmt=fmt):
                stream = BytesIO(); Image.new("L", size, 255).save(stream, format=fmt)
                replace_body(self.captures, "image", stream.getvalue())
                with self.assertRaisesRegex(ValueError, "pixel budget is unsupported"): self.review()

    def test_source_bytes_metadata_clocks_and_strict_request_scope_are_enforced(self):
        for mutation in ("bytes", "clock", "route", "extra", "generic"):
            with self.subTest(mutation=mutation):
                self.request, self.captures = fixture()
                if mutation == "bytes": self.captures["image"][0]["raw_sha256"] = "0" * 64
                elif mutation == "clock": self.captures["image"][0]["known_at"] = (OBSERVED - timedelta(seconds=1)).isoformat()
                elif mutation == "route": self.captures["image"][0]["requested_url"] = BASE.replace("/1474432/", "/29915/") + "image_0.jpg"
                elif mutation == "extra": self.request["effective_on"] = "2009-10-02"
                else: self.request["scope"]["class_title"] = "Common Stock"
                with self.assertRaises(ValueError): self.review()

    def test_visual_annotations_are_independent_bounded_and_never_machine_authenticated(self):
        report = self.review()
        report["literal_observations"]["cusip"]["bounds"][0] = 0
        self.assertEqual(10, self.field("cusip")["bounds"][0])
        for mutation in ("duplicate", "method", "hidden_whitespace", "oversize"):
            with self.subTest(mutation=mutation):
                self.request, self.captures = fixture()
                if mutation == "duplicate": self.request["measurement"]["fields"][1] = deepcopy(self.field("issuer_name"))
                elif mutation == "method": self.request["measurement"]["method"] = "AUTOMATED_AUTHENTICATION"
                elif mutation == "hidden_whitespace": self.field("issuer_name")["literal"] = "EVERPURE,\nINC."
                else: self.field("issuer_name")["literal"] = "A" * 701
                with self.assertRaises(ValueError): self.review()

    def test_actual_original_certificate_adds_literal_class_cusip_but_no_action_day(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "evidence/batch10_identity_20261018/replay_certificate.py"
        spec = importlib.util.spec_from_file_location("actual_certificate_replay", path)
        replay = importlib.util.module_from_spec(spec); spec.loader.exec_module(replay)
        request, captures, plan = replay.build_inputs()
        report = module.review_sec_certificate_instrument(request, captures)
        self.assertEqual("SEC_CERTIFICATE_INSTRUMENT", plan["kind"])
        self.assertEqual(3, len(captures))
        self.assertTrue(report["literal_specimen_class_cusip_scope_observed"])
        self.assertEqual("74624M102", report["observed_cusip"])
        self.assertEqual([1978, 1318], report["image_dimensions"])
        self.assertEqual("JPEG", report["image_format"])
        self.assertEqual(917915, report["capture_pins"]["image"]["bytes"])
        self.assertEqual("c05da755dd60ea349f6368eb97e74cd07d93a3a6a176bd1d427467a9f7481f45", report["capture_pins"]["image"]["sha256"])
        self.assertEqual("2026-10-18T04:25:43.865560+00:00", report["source_knowledge_at"])
        self.assertEqual(1252, report["publication_scope"]["index_declared_bytes"])
        self.assertEqual(1361, report["capture_pins"]["wrapper"]["bytes"])
        self.assertEqual(9, len(report["blockers"]))
        self.assertIn("CERTIFICATE_DATED_FIELD_BLANK", report["blockers"])
        self.assertIn("PSTG_TO_P_CLASS_CUSIP_CONTINUITY_UNPROVEN", report["blockers"])
        self.assertIsNone(report["effective_on"])
        self.assertEqual(0, report["new_verified_identities"])
        self.assertEqual(0, report["registry_adoptions"])
        self.assertEqual(0, report["current_q3_verified_instrument_coverage"])


if __name__ == "__main__":
    unittest.main()
