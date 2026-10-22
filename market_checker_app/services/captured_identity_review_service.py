"""Explicit original-byte reviews, separate from source collection and adoption."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

PLAN_VERSION = "captured_identity_review_plan_v1"
REPORT_VERSION = "captured_identity_review_v1"
MAX_PLAN_BYTES = 65_536
MAX_BODY_BYTES = 8_000_000
MAX_TOTAL_BYTES = 24_000_000
MAX_CAPTURES = 16
_HASH = re.compile(r"[a-f0-9]{64}")
_KINDS = {"CMS_PROVIDER_OWNER", "EPA_FRS_OWNER", "FORM4_PERSON", "STOCK_MERGER_EXCHANGE", "SEC_OPERATIONAL_METRIC",
          "SEC_CONTROL_INSTRUMENT", "HEALTHCARE_PRODUCT_SCOPE", "DOJ_ENFORCEMENT_SCOPE",
          "SEC_SUPPLY_CHAIN_SCOPE", "SEC_PERSON_KIND", "SEC_IR_DISCLOSURE_AUTHORITY",
          "SEC_SYMBOL_ACTION_SCOPE", "FDA_STRUCTURED_PRODUCT", "DOJ_CASE_POSTURE",
          "SEC_PURCHASE_OBLIGATION_SCOPE", "SEC_OWNER_INSTRUMENT_SCOPE",
          "SEC_INSTRUMENT_CLASS_SCOPE", "FDA_APPLICATION_PRODUCT", "NHTSA_NEGATIVE_SCOPE",
          "SEC_SUPPLIER_CONCENTRATION", "SEC_COMPLETE_PACKET",
          "SEC_CERTIFICATE_INSTRUMENT", "FDA_LEGAL_APPLICANT_BRIDGE", "DOJ_CASE_CAPTION",
          "SEC_SUPPLIER_REVENUE_DENOMINATOR", "SEC_PERSON_ROLE_CLAIM",
          "SEC_DATED_INSTRUMENT_POINT", "FDA_APPLICANT_CHRONOLOGY", "EPA_FRS_PROGRAM_SCOPE",
          "USASPENDING_PRIME_SCOPE", "SEC_PERSON_ROLE_TEMPORAL",
          "SEC_CHARTER_CLASS", "SEC_FORM144_ACCOUNT_NOTICE", "WAFER_CAPACITY_DEPOSITS"}
_BODY_BUDGETS = {kind: 4_000_000 for kind in (
    "SEC_CONTROL_INSTRUMENT", "HEALTHCARE_PRODUCT_SCOPE", "DOJ_ENFORCEMENT_SCOPE",
    "SEC_SUPPLY_CHAIN_SCOPE", "SEC_PERSON_KIND", "SEC_IR_DISCLOSURE_AUTHORITY",
    "SEC_SYMBOL_ACTION_SCOPE", "FDA_STRUCTURED_PRODUCT", "DOJ_CASE_POSTURE",
    "SEC_PURCHASE_OBLIGATION_SCOPE", "SEC_OWNER_INSTRUMENT_SCOPE",
    "SEC_INSTRUMENT_CLASS_SCOPE", "FDA_APPLICATION_PRODUCT", "NHTSA_NEGATIVE_SCOPE",
    "SEC_SUPPLIER_CONCENTRATION", "SEC_COMPLETE_PACKET",
    "SEC_CERTIFICATE_INSTRUMENT", "FDA_LEGAL_APPLICANT_BRIDGE", "DOJ_CASE_CAPTION",
    "SEC_SUPPLIER_REVENUE_DENOMINATOR", "SEC_PERSON_ROLE_CLAIM",
    "SEC_DATED_INSTRUMENT_POINT", "FDA_APPLICANT_CHRONOLOGY", "EPA_FRS_PROGRAM_SCOPE",
    "USASPENDING_PRIME_SCOPE", "SEC_PERSON_ROLE_TEMPORAL",
    "SEC_CHARTER_CLASS", "SEC_FORM144_ACCOUNT_NOTICE", "WAFER_CAPACITY_DEPOSITS")}


def _time(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("An aware evidence clock is required")
    return parsed.astimezone(timezone.utc)


def retained_file(root: Path, name: str) -> Path:
    """Resolve an explicitly selected retained file, never a symlink or escape."""
    if (not isinstance(name, str) or not name or len(name) > 1_000
            or "\\" in name or ":" in name or "\x00" in name
            or PurePosixPath(name).is_absolute() or any(p in {"", ".", ".."} for p in name.split("/"))):
        raise ValueError("Select a retained relative file")
    root = Path(root)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("Retained root cannot be a symlink")
    path = root.joinpath(*name.split("/"))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Retained file cannot be a symlink")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Retained original file is unavailable")
    return path


def _json_bytes(body: bytes) -> dict:
    if _has_sensitive_body(body):
        raise ValueError("Whole access-bearing input is withheld")
    def constant(_: str):
        raise ValueError("Unsupported numeric constant")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate evidence field")
            result[key] = value
        return result
    try:
        result = json.loads(body, parse_constant=constant, object_pairs_hook=pairs)
    except RecursionError:
        raise ValueError("Evidence nesting budget exceeded") from None
    if not isinstance(result, dict):
        raise ValueError("Evidence must be a JSON object")
    if _has_sensitive_body(json.dumps(result, ensure_ascii=False).encode("utf-8")):
        raise ValueError("Whole access-bearing input is withheld")
    return result


def read_review_plan(source_root: Path, plan_name: str) -> tuple[dict, str]:
    path = retained_file(source_root, plan_name)
    with path.open("rb") as stream:
        body = stream.read(MAX_PLAN_BYTES + 1)
    if len(body) > MAX_PLAN_BYTES:
        raise ValueError("Evidence plan byte budget exceeded")
    plan = _json_bytes(body)
    if (set(plan) - {"schema_version", "kind", "request", "captures", "as_of"}
            or plan.get("schema_version") != PLAN_VERSION or plan.get("kind") not in _KINDS
            or not isinstance(plan.get("request"), dict) or not isinstance(plan.get("captures"), dict)
            or not 1 <= len(plan["captures"]) <= MAX_CAPTURES):
        raise ValueError("Invalid bounded captured-review plan")
    return plan, hashlib.sha256(body).hexdigest()


def _load_captures(plan: dict, source_root: Path, cutoff: datetime) -> dict[str, tuple[dict, bytes | None]]:
    result, total = {}, 0
    body_budget = min(MAX_BODY_BYTES, _BODY_BUDGETS.get(plan.get("kind"), MAX_BODY_BYTES))
    for key, item in plan["captures"].items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key)
                or not isinstance(item, dict) or set(item) != {"metadata", "body_available", "body_ref"}
                or not isinstance(item["metadata"], dict) or type(item["body_available"]) is not bool):
            raise ValueError("Malformed captured source descriptor")
        metadata, ref = item["metadata"], item["body_ref"]
        for field in ("observed_at", "known_at", "requested_at", "started_at"):
            if metadata.get(field) is not None and _time(metadata[field]) > cutoff:
                raise ValueError("Captured source was unavailable at the requested cutoff")
        if not item["body_available"]:
            if ref is not None:
                raise ValueError("Unavailable whole body cannot have a substitute file")
            result[key] = (metadata, None)
            continue
        if (metadata.get("retention") in {"WITHHELD_SENSITIVE_BODY", "MISSING_BODY"}
                or metadata.get("raw_publication_permitted") is False
                or metadata.get("public_raw_available") is False
                or any(str(metadata.get(field, "")).startswith("WITHHELD")
                       for field in ("status", "publication_status", "archive_status"))):
            raise ValueError("Known unavailable body cannot be restored")
        if (not isinstance(ref, dict) or set(ref) != {"path", "sha256", "bytes"}
                or not isinstance(ref["sha256"], str) or not _HASH.fullmatch(ref["sha256"])
                or type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= body_budget):
            raise ValueError("Missing exact original-byte pin")
        total += ref["bytes"]
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Captured evidence byte budget exceeded")
        path = retained_file(source_root, ref["path"])
        with path.open("rb") as stream:
            body = stream.read(ref["bytes"] + 1)
        if len(body) != ref["bytes"] or hashlib.sha256(body).hexdigest() != ref["sha256"]:
            raise ValueError("Original captured bytes changed")
        if _has_sensitive_body(body):
            raise ValueError("Whole access-bearing body is withheld")
        result[key] = (metadata, body)
    return result


def _dispatch(plan: dict, captures: dict, source_root: Path, clock: datetime, cutoff: datetime) -> dict:
    request, kind = plan["request"], plan["kind"]
    if kind == "WAFER_CAPACITY_DEPOSITS":
        from market_checker_app.services.wafer_capacity_deposit_review import review_wafer_capacity_deposits
        return review_wafer_capacity_deposits(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "SEC_CHARTER_CLASS":
        from market_checker_app.services.sec_corporate_action_bridge import review_charter_class_disclosure
        service = _bulk_service(plan, captures, cutoff)
        return review_charter_class_disclosure(service, request["claim"],
            as_of=cutoff if "as_of" in plan else None)
    if kind == "SEC_FORM144_ACCOUNT_NOTICE":
        from market_checker_app.services.sec_form4_person_bridge import (
            SECPersonCapture, review_form144_account_notice,
        )
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = SECPersonCapture(body=body, **fields)
        return review_form144_account_notice(notice_capture=selected["notice"],
            account_packet_capture=selected["account_packet"], selection=request["selection"],
            knowledge_at=cutoff if "as_of" in plan else None)
    if kind == "SEC_DATED_INSTRUMENT_POINT":
        from market_checker_app.services.sec_dated_instrument_point_review import review_sec_dated_instrument_point
        return review_sec_dated_instrument_point(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "FDA_APPLICANT_CHRONOLOGY":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new applicant chronology cannot be backdated")
        from market_checker_app.services.fda_applicant_chronology_review import review_fda_applicant_chronology
        return review_fda_applicant_chronology(request, captures,
            reviewed_at=cutoff if "as_of" in plan else None)
    if kind == "EPA_FRS_PROGRAM_SCOPE":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new program scope interpretation cannot be backdated")
        from market_checker_app.services.epa_frs_program_scope_review import review_epa_frs_program_scope
        return review_epa_frs_program_scope(request, captures,
            reviewed_at=cutoff if "as_of" in plan else None)
    if kind == "USASPENDING_PRIME_SCOPE":
        from market_checker_app.services.usaspending_prime_scope_review import review_usaspending_prime_scope
        return review_usaspending_prime_scope(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "SEC_PERSON_ROLE_TEMPORAL":
        from market_checker_app.services.sec_person_role_temporal_review import (
            PersonRoleTemporalCapture, review_sec_person_role_temporal,
        )
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = PersonRoleTemporalCapture(body=body, **fields)
        return review_sec_person_role_temporal(primary_capture=selected["primary"],
            index_capture=selected["index"], account_packet_capture=selected["account_packet"],
            selection=request["selection"], temporal_request=request["temporal_request"],
            knowledge_at=cutoff if "as_of" in plan else None)
    if kind == "SEC_CERTIFICATE_INSTRUMENT":
        from market_checker_app.collectors.sec_certificate_instrument_review import review_sec_certificate_instrument
        return review_sec_certificate_instrument(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "FDA_LEGAL_APPLICANT_BRIDGE":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new applicant bridge interpretation cannot be backdated")
        from market_checker_app.collectors.fda_legal_applicant_bridge_review import review_fda_legal_applicant_bridge
        return review_fda_legal_applicant_bridge(request, captures, reviewed_at=clock)
    if kind == "DOJ_CASE_CAPTION":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new case caption interpretation cannot be backdated")
        from market_checker_app.collectors.doj_case_caption_review import review_doj_case_caption
        return review_doj_case_caption(request, captures, reviewed_at=clock)
    if kind == "SEC_SUPPLIER_REVENUE_DENOMINATOR":
        from market_checker_app.collectors.sec_supplier_revenue_denominator_review import review_sec_supplier_revenue_denominator
        return review_sec_supplier_revenue_denominator(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "SEC_PERSON_ROLE_CLAIM":
        from market_checker_app.collectors.sec_person_role_claim_review import (
            PersonRoleClaimCapture, review_sec_person_role_claims,
        )
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = PersonRoleClaimCapture(body=body, **fields)
        return review_sec_person_role_claims(primary_capture=selected["primary"],
            index_capture=selected["index"], account_packet_capture=selected["account_packet"],
            selection=request["selection"], knowledge_at=cutoff if "as_of" in plan else None)
    if kind == "SEC_INSTRUMENT_CLASS_SCOPE":
        from market_checker_app.services.sec_instrument_class_scope_review import review_sec_instrument_class_scope
        return review_sec_instrument_class_scope(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "FDA_APPLICATION_PRODUCT":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new FDA application interpretation cannot be backdated")
        from market_checker_app.services.fda_application_product_review import review_fda_application_product
        return review_fda_application_product(request, captures, reviewed_at=clock)
    if kind == "NHTSA_NEGATIVE_SCOPE":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new NHTSA interpretation cannot be backdated")
        from market_checker_app.services.nhtsa_negative_scope_review import review_nhtsa_negative_scope
        return review_nhtsa_negative_scope(request, captures, reviewed_at=clock)
    if kind == "SEC_SUPPLIER_CONCENTRATION":
        from market_checker_app.services.sec_supplier_concentration_review import review_sec_supplier_concentration
        return review_sec_supplier_concentration(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "SEC_COMPLETE_PACKET":
        from market_checker_app.services.sec_complete_packet_review import (
            CompletePacketCapture, review_sec_complete_packet,
        )
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = CompletePacketCapture(body=body, **fields)
        return review_sec_complete_packet(packet_capture=selected["packet"],
            xml_capture=selected["xml"], index_capture=selected["index"],
            selection=request["selection"], knowledge_at=cutoff if "as_of" in plan else None)
    if kind == "SEC_SYMBOL_ACTION_SCOPE":
        from market_checker_app.services.sec_symbol_action_scope_review import review_symbol_action_scope
        return review_symbol_action_scope(request, captures,
            as_of=cutoff if "as_of" in plan else None)
    if kind == "SEC_OWNER_INSTRUMENT_SCOPE":
        from market_checker_app.services.sec_owner_instrument_scope_review import (
            OwnerInstrumentCapture, review_sec_owner_instrument_scope,
        )
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = OwnerInstrumentCapture(body=body, **fields)
        return review_sec_owner_instrument_scope(prior_account_capture=selected["prior_account"],
            packet_capture=selected["packet"], xml_capture=selected["xml"], index_capture=selected["index"],
            selection=request["selection"], knowledge_at=cutoff if "as_of" in plan else None)
    if kind == "FDA_STRUCTURED_PRODUCT":
        if "as_of" in plan and cutoff < clock:
            raise ValueError("A new FDA product interpretation cannot be backdated")
        from market_checker_app.services.fda_structured_product_archive import (
            FdaStructuredProductSelection, FdaProductArchiveCapture,
            review_fda_structured_product_archive,
        )
        selection = dict(request["selection"])
        selection["active_ingredients"] = tuple(tuple(pair) for pair in selection.get("active_ingredients", ()))
        def product_source(key: str):
            return FdaProductArchiveCapture(*captures[key]) if key in captures else None
        return review_fda_structured_product_archive(
            selection=FdaStructuredProductSelection(**selection),
            structured_capture=product_source("structured"),
            issuer_capture=product_source("issuer"), reviewed_at=clock)
    if kind == "DOJ_CASE_POSTURE":
        from market_checker_app.services.doj_case_posture_review import review_doj_case_posture
        from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry
        return review_doj_case_posture(request,
            {key: {"metadata": metadata, "body": body} for key, (metadata, body) in captures.items()},
            as_of=cutoff if "as_of" in plan else None, ingested_at=clock,
            legal_records=DatedCounterpartyIdentityRegistry.load()._records)
    if kind == "SEC_PURCHASE_OBLIGATION_SCOPE":
        from market_checker_app.services.sec_purchase_obligation_scope_review import review_sec_purchase_obligation_scope
        issuer_metadata, issuer_body = captures["issuer"]
        index_metadata, index_body = captures["index"]
        return review_sec_purchase_obligation_scope(issuer_body, issuer_capture=issuer_metadata,
            index_body=index_body, index_capture=index_metadata, selection=request["selection"],
            as_of=cutoff if "as_of" in plan else None)
    if kind == "HEALTHCARE_PRODUCT_SCOPE":
        from market_checker_app.services.healthcare_product_scope_review import (
            FdaProductApplicationScope, CapturedHealthcareSource, SecOwnershipSelection,
            review_healthcare_product_scope,
        )
        scope = dict(request["scope"])
        if "product_markers" in scope:
            scope["product_markers"] = tuple(scope["product_markers"])
        def healthcare_source(key: str):
            return CapturedHealthcareSource(*captures[key]) if key in captures else None
        return review_healthcare_product_scope(scope=FdaProductApplicationScope(**scope),
            fda_capture=healthcare_source("fda"), ownership_capture=healthcare_source("ownership"),
            issuer_capture=healthcare_source("issuer"),
            ownership_selection=SecOwnershipSelection(**request["ownership_selection"])
                if request.get("ownership_selection") is not None else None,
            reviewed_at=clock)
    if kind == "SEC_SUPPLY_CHAIN_SCOPE":
        from market_checker_app.services.sec_supply_chain_scope_review import review_sec_supply_chain_scope
        issuer_metadata, issuer_body = captures["issuer"]
        counterparty_metadata, counterparty_body = captures.get("counterparty", (None, None))
        return review_sec_supply_chain_scope(issuer_body, issuer_capture=issuer_metadata,
            selection=request["selection"], counterparty_body=counterparty_body,
            counterparty_capture=counterparty_metadata,
            counterparty_selection=request.get("counterparty_selection"),
            as_of=cutoff if "as_of" in plan else None)
    if kind == "DOJ_ENFORCEMENT_SCOPE":
        from market_checker_app.services.doj_enforcement_scope_review import review_doj_enforcement_scope
        from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry
        selected = {key: {"metadata": metadata, "body": body} for key, (metadata, body) in captures.items()}
        return review_doj_enforcement_scope(request, selected, as_of=cutoff, ingested_at=clock,
            legal_records=DatedCounterpartyIdentityRegistry.load()._records)
    if kind == "SEC_IR_DISCLOSURE_AUTHORITY":
        from market_checker_app.services.sec_ir_disclosure_authority_review import (
            SECIRDisclosureCapture, review_sec_ir_disclosure_authority,
        )
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = SECIRDisclosureCapture(body=body, **fields)
        return review_sec_ir_disclosure_authority(source_capture=selected["source"],
            index_capture=selected["index"], selection=request["selection"], knowledge_at=cutoff)
    if kind == "SEC_PERSON_KIND":
        from market_checker_app.services.sec_person_kind_review import PersonKindCapture, review_sec_person_kind
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = PersonKindCapture(body=body, **fields)
        return review_sec_person_kind(account_descriptor=request["account_descriptor"],
            person_fact=request["person_fact"], account_capture=selected["account"],
            primary_capture=selected["primary"], index_capture=selected["index"],
            knowledge_at=cutoff, requested_event_days=tuple(request.get("requested_event_days", ())))
    if kind == "SEC_OPERATIONAL_METRIC":
        from market_checker_app.services.sec_operational_metrics_review import review_sec_operational_metric
        metadata, body = captures["source"]
        if body is None:
            raise ValueError("Operational metrics require their original primary body")
        return review_sec_operational_metric(body, capture_metadata=metadata,
            expected_body_sha256=plan["captures"]["source"]["body_ref"]["sha256"],
            selection=request["selection"], as_of=cutoff if "as_of" in plan else None)
    if kind == "CMS_PROVIDER_OWNER":
        from market_checker_app.services.cms_provider_ownership_review import (
            CapturedSource, CmsProviderOwnerScope, review_cms_provider_ownership,
        )
        return review_cms_provider_ownership(
            scope=CmsProviderOwnerScope(**request["scope"]),
            owner_capture=CapturedSource(*captures["owner"]),
            provider_capture=CapturedSource(*captures["provider"]),
            issuer_capture=CapturedSource(*captures["issuer"]), reviewed_at=clock,
        )
    if kind == "EPA_FRS_OWNER":
        from market_checker_app.services.epa_frs_ownership_review import review_frs_ownership
        selected = []
        for key, (metadata, body) in captures.items():
            ref = plan["captures"][key]["body_ref"]
            if body is not None and (metadata.get("archive_path") != ref["path"]
                    or metadata.get("raw_response_sha256") != ref["sha256"]
                    or metadata.get("raw_response_bytes") != ref["bytes"]):
                raise ValueError("EPA semantic and original-byte pins disagree")
            if body is None and metadata.get("public_raw_available") is True:
                raise ValueError("EPA body availability disagrees with metadata")
            selected.append(metadata)
        return review_frs_ownership(request["proposal"], selected,
            repository_root=source_root, ingested_at=clock, as_of=cutoff)
    if kind == "FORM4_PERSON":
        from market_checker_app.services.sec_form4_person_bridge import SECPersonCapture, review_form4_person_bridge
        selected = {}
        for key, (metadata, body) in captures.items():
            fields = dict(metadata)
            for field in ("observed_at", "known_at"):
                if fields.get(field) is not None:
                    fields[field] = _time(fields[field])
            selected[key] = SECPersonCapture(body=body, **fields)
        return review_form4_person_bridge(request["observation"],
            source_capture=selected["source"], evidence=[v for k,v in selected.items() if k != "source"],
            knowledge_at=cutoff, scope_mode=request.get("scope_mode", "EVENT_DATE"))
    service = _bulk_service(plan, captures, cutoff)
    if kind == "SEC_CONTROL_INSTRUMENT":
        from market_checker_app.services.sec_control_instrument_review import review_control_instrument_bridge
        return review_control_instrument_bridge(service, request["review_request"])
    from market_checker_app.services.sec13f_transition_review import instrument_transition_review
    return instrument_transition_review(service, tickers=request["tickers"],
        corporate_action_bridges=[request["proposal"]])


def _bulk_service(plan: dict, captures: dict, cutoff: datetime):
    from market_checker_app.services.sec13f_identity_bulk import Sec13fIdentityBulk, SourceSnapshot
    from market_checker_app.services.sec_corporate_action_bridge import Form25Snapshot
    from market_checker_app.services.sec13f_scout_service import load_verified_securities
    from market_checker_app.utils.ticker_universe import load_canonical_ticker_records
    snapshots = []
    for metadata, body in captures.values():
        if body is None:
            raise ValueError("Merger review requires its original primary bodies")
        fields = {"url": metadata["url"], "observed_at": _time(metadata["observed_at"]), "payload": body,
                  "published_at": _time(metadata["published_at"]) if metadata.get("published_at") else None}
        snapshot = Form25Snapshot(**fields) if metadata["kind"] == "form25_nse" else SourceSnapshot(kind=metadata["kind"], **fields)
        if metadata.get("snapshot_id") != snapshot.snapshot_id or metadata.get("sha256") != snapshot.sha256:
            raise ValueError("Merger snapshot semantic and original-byte pins disagree")
        snapshots.append(snapshot)
    return Sec13fIdentityBulk(snapshots=snapshots,
        universe=[row["ticker"] for row in load_canonical_ticker_records()], quarter=plan["request"]["quarter"],
        as_of=cutoff, existing=load_verified_securities())


def _publish(result: dict, directory: Path) -> tuple[str, str]:
    body = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    if len(body) > MAX_TOTAL_BYTES or _has_sensitive_body(body):
        raise ValueError("Whole review output is unavailable for archival")
    directory = Path(directory)
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("Review archive cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body).hexdigest()
    name = "review-" + digest + ".json"
    destination = directory / name
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink():
                raise ValueError("Immutable review archive collision")
            with destination.open("rb") as existing:
                matches = existing.read(len(body) + 1) == body
            if not matches:
                raise ValueError("Immutable review archive collision")
    finally:
        temporary.unlink(missing_ok=True)
    return name, digest


def run_captured_identity_review(*, source_root: Path, plan_name: str, archive_root: Path) -> dict:
    clock = datetime.now(timezone.utc)
    plan, digest = read_review_plan(source_root, plan_name)
    cutoff = _time(plan.get("as_of", clock))
    if cutoff > clock:
        raise ValueError("A review cannot request future knowledge")
    captures = _load_captures(plan, Path(source_root), cutoff)
    review = _dispatch(plan, captures, Path(source_root), clock, cutoff)
    if not isinstance(review, dict):
        raise ValueError("Captured review returned unsupported data")
    result = {"schema_version": REPORT_VERSION, "kind": plan["kind"],
              "recorded_at": datetime.now(timezone.utc).isoformat(), "requested_as_of": cutoff.isoformat(),
              "plan_sha256": digest, "review": review, "source_http_requests": 0,
              "registry_adoptions": 0, "issuer_attribution_allowed": False,
              "scoring_applied": False, "full_specialist_acceptance": False}
    name, sha = _publish(result, archive_root)
    result.update(archive_filename=name, archive_sha256=sha, archive_status="SAVED")
    return result
