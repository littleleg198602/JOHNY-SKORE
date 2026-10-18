"""Original exhibit/image linkage and separately labelled visual observations.

This offline consumer does not collect, OCR, authenticate visual transcriptions,
adopt an identity or infer an effective day from a certificate specimen.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import re
from urllib.parse import urljoin

from PIL import Image, UnidentifiedImageError

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.sec13f_identity_bulk import _clock, _name, _valid_cusip
from market_checker_app.services.sec_corporate_action_bridge import class_terms
from market_checker_app.services.sec_symbol_action_scope_review import _captures, _encoded, _filing, _url

VERSION = "sec-certificate-instrument-review-v1"
MAX_BYTES, MAX_PIXELS = 4_000_000, 12_000_000
_FIELD_NAMES = {"issuer_name", "class_and_par", "cusip", "specimen_mark", "dated_field", "corporate_seal"}
_REQUIRED_FIELDS = _FIELD_NAMES - {"corporate_seal"}
_VOID = {"img", "br", "hr", "meta", "link", "input", "area", "base", "col", "embed", "param", "source", "track", "wbr"}


def _physical_clock():
    return datetime.now(timezone.utc)


class _ImageReferences(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.stack, self.references, self.nodes = [], [], 0
        self.feed(body.decode("utf-8", errors="strict"))
        self.close()

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100_000 or len(self.stack) > 128:
            raise ValueError("Certificate wrapper structure exceeds its bound")
        attrs = dict(attrs)
        if tag == "base":
            raise ValueError("Certificate media cannot use an alternate base URL")
        style = str(attrs.get("style", "")).replace(" ", "").casefold()
        hidden = bool(self.stack and self.stack[-1][1]) or tag in {"script", "style", "noscript", "ix:hidden", "ix:header"}
        hidden |= "hidden" in attrs or "display:none" in style or "visibility:hidden" in style
        if tag == "img" and not hidden:
            literal = attrs.get("src")
            if not isinstance(literal, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)", literal, re.I):
                raise ValueError("Select a literal same-directory certificate image reference")
            self.references.append(literal)
            if len(self.references) > 32:
                raise ValueError("Certificate image reference budget exceeded")
        if tag not in _VOID:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def certificate_image_references(original_wrapper: bytes, wrapper_url: str) -> list[dict]:
    """List explicit visible original references, without a request or file search."""
    if not isinstance(original_wrapper, bytes) or not 0 < len(original_wrapper) <= MAX_BYTES or _has_sensitive_body(original_wrapper):
        raise ValueError("Safe bounded whole original wrapper bytes are required")
    cik, accession, _ = _url(wrapper_url)
    if accession is None:
        raise ValueError("Certificate wrapper must be an exact SEC accession document")
    rows = []
    for reference in _ImageReferences(original_wrapper).references:
        resolved = urljoin(wrapper_url, reference)
        linked_cik, linked_accession, _ = _url(resolved)
        if (linked_cik, linked_accession) != (cik, accession):
            raise ValueError("Certificate image escaped its original accession")
        rows.append({"literal_reference": reference, "url": resolved})
    return rows


def _image_dimensions(body):
    try:
        with Image.open(BytesIO(body)) as image:
            width, height = image.size
            if (image.format not in {"JPEG", "PNG"} or not 0 < width * height <= MAX_PIXELS
                    or width > 12_000 or height > 12_000 or getattr(image, "n_frames", 1) != 1):
                raise ValueError("Certificate image format or decoded pixel budget is unsupported")
            image.load()
            return [width, height], image.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Complete original certificate image cannot be decoded") from error


def _sources(captures, scope, cutoff, physical):
    result = {}
    for key, pair in captures.items():
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], dict):
            raise ValueError("Select exact capture metadata and whole bytes")
        metadata, body = pair
        if metadata.get("status") != "CAPTURE_FAILED":
            result.update(_captures({key: pair}, scope, cutoff, physical))
            continue
        # A transport failure is not a successful HTTP response, an empty image
        # or an absence fact. It can legitimately have no body hash or size.
        if body is not None or _has_sensitive_body(_encoded(metadata)) or len(_encoded(metadata)) > 16_000:
            raise ValueError("A failed capture cannot have a replacement body")
        url = metadata.get("requested_url")
        cik, accession, filename = _url(url)
        if cik != scope["issuer_cik"] or metadata.get("final_url", url) != url:
            raise ValueError("Failed certificate capture URL conflicts with its scope")
        started, observed = _clock(metadata.get("started_at")), _clock(metadata.get("observed_at"))
        known = _clock(metadata.get("known_at", metadata.get("observed_at")))
        if not started <= observed <= known <= cutoff <= physical:
            raise ValueError("Failed capture has reversed or future clocks")
        status = metadata.get("http_status")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("Malformed failed-capture HTTP status")
        result[key] = {"body": None, "url": url, "cik": cik, "accession": accession, "filename": filename,
                       "observed": observed, "known": known, "sha256": metadata.get("raw_sha256"), "bytes": metadata.get("bytes"),
                       "metadata_sha256": hashlib.sha256(_encoded(metadata)).hexdigest()}
    return result


def _visual_measurement(measurement, source, dimensions, physical):
    expected = {"schema_version", "source_image_sha256", "image_dimensions", "measured_at", "method", "fields"}
    if (not isinstance(measurement, dict) or set(measurement) != expected
            or measurement["schema_version"] != "sec_certificate_visual_measurement_v1"
            or measurement["method"] != "VISUAL_TRANSCRIPTION_FROM_ORIGINAL"
            or measurement["source_image_sha256"] != source["sha256"]
            or measurement["image_dimensions"] != dimensions):
        raise ValueError("Visual observation must pin the exact original image and dimensions")
    measured = _clock(measurement["measured_at"])
    if not source["known"] <= measured <= physical:
        raise ValueError("Visual measurement cannot precede image knowledge or follow physical review")
    fields = measurement["fields"]
    if not isinstance(fields, list) or not 1 <= len(fields) <= len(_FIELD_NAMES):
        raise ValueError("Select a bounded explicit visual field scope")
    indexed = {}
    for row in fields:
        if not isinstance(row, dict) or set(row) != {"field", "literal", "bounds"}:
            raise ValueError("Visual field requires its literal text and original pixel region")
        name, literal, box = row["field"], row["literal"], row["bounds"]
        if name not in _FIELD_NAMES or name in indexed or not isinstance(literal, str) or not 1 <= len(literal) <= 700:
            raise ValueError("Visual fields cannot be duplicate, unnamed or unbounded")
        if (not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box)
                or not 0 <= box[0] < box[2] <= dimensions[0] or not 0 <= box[1] < box[3] <= dimensions[1]):
            raise ValueError("Visual field is outside the original image pixel extent")
        if literal != " ".join(literal.split()):
            raise ValueError("Keep one explicit normalized transcription without hidden whitespace")
        indexed[name] = {"field": name, "literal": literal, "bounds": list(box),
                         "source_image_sha256": source["sha256"], "measured_at": measured.isoformat(),
                         "automated_pixel_text_authentication": False}
    return indexed, measured


def review_sec_certificate_instrument(request: dict, captures: dict, *, as_of: datetime | None = None) -> dict:
    """Review exact exhibit linkage and explicitly derived specimen observations.

    `captures` is key -> (metadata, original bytes | None). Transcription is a
    caller-supplied visual annotation, not a primary source or OCR attestation.
    Source capture, publisher declarations, visual measurement and this new
    interpretation retain separate clocks. No network or filesystem writes occur.
    """
    expected = {"scope", "wrapper_key", "index_key", "image_key", "image_reference", "measurement"}
    if not isinstance(request, dict) or set(request) != expected or len(_encoded(request)) > 24_000 or _has_sensitive_body(_encoded(request)):
        raise ValueError("Select an exact bounded safe certificate review request")
    scope = request["scope"]
    if (not isinstance(scope, dict) or set(scope) != {"issuer_cik", "issuer_name", "class_title", "cusip", "old_symbol", "new_symbol"}
            or not isinstance(scope["issuer_cik"], str) or not re.fullmatch(r"[0-9]{10}", scope["issuer_cik"]) or int(scope["issuer_cik"]) == 0
            or not isinstance(scope["issuer_name"], str) or not 1 <= len(scope["issuer_name"]) <= 200
            or not isinstance(scope["class_title"], str) or not 1 <= len(scope["class_title"]) <= 250
            or class_terms(scope["class_title"]) is None
            or class_terms(scope["class_title"])["par_term"] == "UNSPECIFIED"
            or class_terms(scope["class_title"])["share_class"] == "EQUITY:UNCLASSIFIED"
            or not isinstance(scope["cusip"], str) or not _valid_cusip(scope["cusip"])
            or any(not isinstance(scope[key], str) or not re.fullmatch(r"[A-Z0-9./-]{1,15}", scope[key]) for key in ("old_symbol", "new_symbol"))):
        raise ValueError("An exact expected issuer/class/CUSIP and separate symbol scope is required")
    physical = _clock(_physical_clock())
    cutoff = physical if as_of is None else _clock(as_of)
    if cutoff != physical:
        raise ValueError("A new certificate interpretation cannot be backdated or future-dated")
    keys = [request[key] for key in ("wrapper_key", "index_key", "image_key")]
    if (any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) for key in keys)
            or len(set(keys)) != 3 or not isinstance(captures, dict) or set(captures) != set(keys)):
        raise ValueError("Select exactly the separate wrapper, index and image captures")
    sources = _sources(captures, scope, cutoff, physical)
    wrapper, index, source = [sources[key] for key in keys]
    if sum(row["bytes"] or 0 for row in sources.values()) > 12_000_000:
        raise ValueError("Certificate review whole-source byte budget exceeded")
    if wrapper["accession"] is None or source["accession"] != wrapper["accession"] or not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)", str(request["image_reference"]), re.I):
        raise ValueError("Select one exact same-accession certificate image reference")
    filing = _filing(wrapper, index, {"proof_kind": "INDEX", "form": "EX-4.1"})
    gaps, links, observations, measured, dimensions, image_format = list(filing["blockers"]), [], {}, None, None, None
    accepted = filing["acceptance_unqualified_raw"]
    if accepted and filing["filing_on"] and accepted[:10] != filing["filing_on"]:
        gaps.append("INDEX_ACCEPTANCE_CALENDAR_DAY_DIFFERS_FROM_FILING_DAY")
    if wrapper["body"] is None:
        gaps.append("ORIGINAL_CERTIFICATE_WRAPPER_UNAVAILABLE")
    else:
        links = certificate_image_references(wrapper["body"], wrapper["url"])
        selected = [row for row in links if row["literal_reference"] == request["image_reference"]]
        if len(selected) != 1 or selected[0]["url"] != source["url"]:
            raise ValueError("Captured image is not its exact unambiguous literal wrapper link")
        if any(row["url"] != source["url"] for row in links):
            gaps.append("OTHER_REFERENCED_CERTIFICATE_IMAGE_NOT_CAPTURED")
    if source["body"] is None:
        if request["measurement"] is not None:
            raise ValueError("Unavailable original image cannot have a replacement visual observation")
        gaps.append("ORIGINAL_CERTIFICATE_IMAGE_UNAVAILABLE")
    else:
        dimensions, image_format = _image_dimensions(source["body"])
        if request["measurement"] is None:
            gaps.append("EXPLICIT_ORIGINAL_IMAGE_VISUAL_MEASUREMENT_MISSING")
        else:
            observations, measured = _visual_measurement(request["measurement"], source, dimensions, physical)
    missing = sorted(_REQUIRED_FIELDS - set(observations))
    if missing:
        gaps.append("REQUIRED_LITERAL_SPECIMEN_FIELDS_UNOBSERVED")
    specimen = observations.get("specimen_mark", {}).get("literal")
    if specimen is not None and specimen != "SPECIMEN":
        gaps.append("EXACT_SPECIMEN_MARK_UNRESOLVED")
    dated = observations.get("dated_field", {}).get("literal")
    if dated == "Dated:":
        gaps.append("CERTIFICATE_DATED_FIELD_BLANK")
    elif dated is not None:
        gaps.append("CERTIFICATE_DATE_TEXT_IS_NOT_AN_ACTION_EFFECTIVE_DAY")
    literal_cusip = observations.get("cusip", {}).get("literal")
    cusip = None
    if literal_cusip is not None:
        matched = re.fullmatch(r"CUSIP ([0-9A-Z*#@ ]{9,16})", literal_cusip)
        if matched is None or not _valid_cusip(matched[1].replace(" ", "")):
            raise ValueError("Literal labelled CUSIP does not have an exact valid checksum")
        cusip = matched[1].replace(" ", "")
        if cusip != scope["cusip"]:
            gaps.append("OBSERVED_EXPECTED_CUSIP_CONFLICT")
    literal_name = observations.get("issuer_name", {}).get("literal")
    if literal_name is not None and _name(literal_name) != _name(scope["issuer_name"]):
        gaps.append("OBSERVED_EXPECTED_ISSUER_NAME_CONFLICT")
    literal_class = observations.get("class_and_par", {}).get("literal")
    terms = class_terms(literal_class) if literal_class is not None else None
    if literal_class is not None and terms != class_terms(scope["class_title"]):
        gaps.append("OBSERVED_EXPECTED_CLASS_OR_PAR_CONFLICT")
    literal_scope_observed = (not missing and specimen == "SPECIMEN" and cusip == scope["cusip"]
                              and _name(literal_name) == _name(scope["issuer_name"])
                              and terms == class_terms(scope["class_title"]))
    gaps.extend(["SPECIMEN_IS_NOT_AN_ISSUED_INSTRUMENT_VALIDITY_INTERVAL", "SYMBOL_NOT_STATED_IN_CERTIFICATE_FIELD_SCOPE",
                 "PSTG_TO_P_EXACT_EFFECTIVE_DAY_UNPROVEN", "PSTG_TO_P_CLASS_CUSIP_CONTINUITY_UNPROVEN", "AS_FILED_IMAGE_VERSION_UNVERIFIED"])
    report = {"schema_version": VERSION, "status": "CERTIFICATE_SPECIMEN_SCOPE_PARTIAL", "scope": dict(scope),
              "review_recorded_at": physical.isoformat(), "knowledge_at": physical.isoformat(), "as_of": cutoff.isoformat(),
              "source_knowledge_at": max(row["known"] for row in sources.values()).isoformat(),
              "measurement_recorded_at": measured.isoformat() if measured is not None else None,
              "measurement_sha256": hashlib.sha256(_encoded(request["measurement"])).hexdigest() if request["measurement"] is not None else None,
              "measurement_is_derived_annotation": True, "automated_pixel_text_authentication": False,
              "literal_specimen_class_cusip_scope_observed": literal_scope_observed,
              "literal_observations": observations, "missing_literal_fields": missing,
              "observed_cusip": cusip, "observed_class_terms": terms, "image_dimensions": dimensions, "image_format": image_format,
              "wrapper_image_references": links, "publication_scope": {**filing, "acceptance_timezone_inferred": False,
                  "image_publication_timestamp_verified": False, "corporate_seal_is_action_effective_day": False},
              "effective_on": None, "issued_instrument_validity_verified": False, "symbol_cusip_continuity_verified": False,
              "certificate_number": None, "issued_share_count": None, "registered_holder": None,
              "facsimile_signature_authenticates_person": False, "countersignature_or_registration_validity_verified": False,
              "blockers": sorted(set(gaps)), "new_verified_identities": 0, "current_q3_verified_instrument_coverage": 0,
              "registry_adoptions": 0, "production_registry_changed": False, "automatic_alias_allowed": False,
              "interval_extension_allowed": False, "historical_machine_availability_inferred": False,
              "score_changed": False, "trading_allowed": False, "network_requests_performed": 0,
              "capture_pins": {key: {"url": row["url"], "sha256": row["sha256"], "bytes": row["bytes"],
                  "metadata_sha256": row["metadata_sha256"], "observed_at": row["observed"].isoformat(),
                  "known_at": row["known"].isoformat(), "body_available": row["body"] is not None,
                  "capture_status": captures[key][0].get("status"), "http_status": captures[key][0].get("http_status")}
                  for key, row in sources.items()}}
    report["review_sha256"] = hashlib.sha256(_encoded(report)).hexdigest()
    return report
