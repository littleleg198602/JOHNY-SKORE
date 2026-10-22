"""Read-only changes/presence audit for two exact award transaction traversals.

Raw capture hashes and pagination are verified again; a receipt's COMPLETE
label is not an input. An absent row is only an observation about a terminal
offset traversal, never a publisher deletion, revision date or company revenue.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from market_checker_app.services.award_transaction_history_service import (
    MAX_MANIFEST_BYTES, SOURCE_URL, CapturedTransactionPage,
    _ingestion_clock, _json, _modified_clock_audit, _read_file, _reject_constant,
    _request, _transaction, _utc, _validate_capture, _verified_target,
)
from market_checker_app.services.usaspending_request_budget import MAX_RESPONSE_BYTES, page_has_next, page_rows
from market_checker_app.services.usaspending_scout_service import _validate_identities, load_verified_identities
from market_checker_app.storage.scout_store import ScoutStore


AUDIT_VERSION = "award_transaction_capture_revision_audit_v1"
MAX_AUDIT_EVENT_BYTES = 1_000_000


def _capture_identity(metadata: dict) -> str:
    # Same publisher capture identity as the existing observation ledger. Replay
    # labels and local manifest filenames cannot manufacture another observation.
    original = {key: value for key, value in metadata.items()
                if key not in {"capture_mode", "original_capture_mode", "original_capture_manifest"}}
    original["capture_mode"] = "FIXTURE" if metadata["capture_mode"] == "FIXTURE" else "LIVE"
    return hashlib.sha256(_json(original)).hexdigest()


def _manifest(path: Path) -> tuple[CapturedTransactionPage, dict]:
    encoded = _read_file(path, MAX_MANIFEST_BYTES)
    manifest = json.loads(encoded, parse_constant=_reject_constant)
    if not isinstance(manifest, dict):
        raise ValueError("Transaction comparison manifest must be an object")
    # Withheld access-value bodies must remain unread. Neither a mirror nor a
    # rewritten/sanitized body is accepted as a substitute source observation.
    if (manifest.get("body_withheld") is True or manifest.get("withheld") is True
            or any(isinstance(manifest.get(key), str) and manifest[key].startswith("WITHHELD")
                   for key in ("publication_status", "archive_status", "status"))):
        raise ValueError("Transaction comparison source body is withheld")
    name = manifest.get("body_file")
    if not isinstance(name, str) or not re.fullmatch(r"body-[a-f0-9]{64}\.response", name):
        raise ValueError("Transaction comparison needs an exact archived body")
    metadata = {key: value for key, value in manifest.items() if key != "body_file"}
    mode = metadata.get("capture_mode")
    if mode not in {"LIVE", "ARCHIVE_REPLAY", "FIXTURE"}:
        raise ValueError("Transaction comparison acquisition mode is unsupported")
    if mode == "ARCHIVE_REPLAY" and metadata.get("original_capture_mode") != "LIVE":
        raise ValueError("Transaction comparison replay needs original LIVE provenance")
    if name != f"body-{metadata.get('response_sha256')}.response":
        raise ValueError("Transaction comparison body name must bind its byte hash")
    request = metadata.get("request")
    if not isinstance(request, dict):
        raise ValueError("Transaction comparison needs an exact source request")
    expected = _request(request.get("award_id"), request.get("page"), request.get("limit"))
    captured = CapturedTransactionPage(_read_file(path.parent / name, MAX_RESPONSE_BYTES), metadata)
    _validate_capture(captured, request=expected, mode=mode)
    return captured, {"manifest_name": path.name, "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
                      "body_file": name, "body_sha256": metadata["response_sha256"],
                      "capture_identity": _capture_identity(metadata),
                      "capture_mode": mode, "original_capture_mode": metadata.get("original_capture_mode"),
                      "request": request, "request_started_at": metadata["request_started_at"],
                      "response_completed_at": metadata["response_completed_at"]}


def _traversal(paths: list[Path], *, award_id: str, cutoff: datetime) -> tuple[dict, dict[str, dict]]:
    if not isinstance(paths, list) or not 1 <= len(paths) <= 5:
        raise ValueError("Transaction comparison needs one to five manifests per traversal")
    summary = {"source_url": SOURCE_URL, "pages": [], "defects": [], "reported_rows": 0,
               "valid_rows": 0, "traversal_complete": False, "pagination_snapshot_guaranteed": False,
               "ambiguous_transaction_ids": [], "source_origin": None, "query_scope": None}
    pages = []
    for path in paths:
        try:
            capture, reference = _manifest(Path(path))
            pages.append((capture, reference))
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
            summary["defects"].append({"reason": "INVALID_CAPTURE", "error_type": type(error).__name__})
    pages.sort(key=lambda item: item[0].metadata["request"]["page"])
    rows, ambiguous = {}, set()
    last_action, last_completion, terminal = None, None, False
    scope = None
    for index, (capture, reference) in enumerate(pages):
        summary["pages"].append(reference)
        metadata, request = capture.metadata, capture.metadata["request"]
        origin = "FIXTURE" if metadata["capture_mode"] == "FIXTURE" else "PUBLISHER_CAPTURE"
        query = {key: value for key, value in request.items() if key != "page"}
        if scope is None:
            scope = query
            summary.update(query_scope=query, source_origin=origin)
        if request["award_id"] != award_id or query != scope or origin != summary["source_origin"]:
            summary["defects"].append({"reason": "QUERY_OR_SOURCE_SCOPE_MISMATCH"})
            continue
        if request["page"] != index + 1 or terminal:
            summary["defects"].append({"reason": "PAGE_SEQUENCE_OR_TERMINAL_MISMATCH"})
        start, completed = _utc(metadata["request_started_at"]), _utc(metadata["response_completed_at"])
        if completed > cutoff:
            summary["defects"].append({"reason": "CAPTURE_AFTER_REQUESTED_CUTOFF"})
            continue
        if last_completion is not None and start < last_completion:
            summary["defects"].append({"reason": "OVERLAPPING_PAGE_CAPTURE_CLOCKS"})
        last_completion = completed
        if metadata.get("http_status") != 200 or not metadata["complete_response"] or metadata.get("error_type"):
            summary["defects"].append({"reason": "UNSUCCESSFUL_OR_INCOMPLETE_RESPONSE"})
            continue
        try:
            payload = json.loads(capture.body, parse_constant=_reject_constant)
            raw_rows = page_rows(payload, limit=request["limit"])
            has_next = page_has_next(payload, page=request["page"], rows=raw_rows)
            page_metadata = payload["page_metadata"]
            if (page_metadata.get("next") != (request["page"] + 1 if has_next else None)
                    or page_metadata.get("previous") != (request["page"] - 1 if request["page"] > 1 else None)
                    or page_metadata.get("hasPrevious") is not (request["page"] > 1)):
                raise ValueError("Malformed transaction comparison pagination")
            terminal = not has_next
        except (ValueError, KeyError, TypeError) as error:
            summary["defects"].append({"reason": "INVALID_PAGINATION_OR_JSON", "error_type": type(error).__name__})
            continue
        summary["reported_rows"] += len(raw_rows)
        for position, raw in enumerate(raw_rows):
            try:
                row = _transaction(raw, captured_at=completed)
            except (ValueError, KeyError, TypeError, OverflowError):
                summary["defects"].append({"reason": "INVALID_TRANSACTION_ROW", "page": request["page"], "row_index": position})
                continue
            identifier = row["id"]
            if last_action is not None and row["action_date"] < last_action:
                summary["defects"].append({"reason": "ACTION_DATE_ORDER_MISMATCH"})
            last_action = row["action_date"]
            clock_audit = _modified_clock_audit(row, captured_at=completed)
            if clock_audit["conflicts"]:
                summary["defects"].append({"reason": "MODIFIED_CLOCK_CONFLICT", "transaction_id": identifier})
            if identifier in rows or identifier in ambiguous:
                summary["defects"].append({"reason": "DUPLICATE_TRANSACTION_ID", "transaction_id": identifier})
                rows.pop(identifier, None)
                ambiguous.add(identifier)
                continue
            rows[identifier] = {"row": row, "source_row_sha256": hashlib.sha256(_json(row)).hexdigest(),
                                "source_modified_clock_audit": clock_audit,
                                "capture_response_completed_at": metadata["response_completed_at"],
                                "source_capture_identity": reference["capture_identity"],
                                "source_position": {"page": request["page"], "row_index": position}}
    summary["ambiguous_transaction_ids"] = sorted(ambiguous)
    summary["valid_rows"] = len(rows)
    if pages:
        summary["request_started_at"] = pages[0][0].metadata["request_started_at"]
        summary["response_completed_at"] = pages[-1][0].metadata["response_completed_at"]
    summary["traversal_complete"] = terminal and not summary["defects"]
    return summary, rows


def audit_award_transaction_capture_versions(
    store: ScoutStore, *, subject_id: str, award_finding_id: str,
    baseline_manifests: list[Path], current_manifests: list[Path],
    identities: list[dict] | None = None, as_of: datetime | None = None,
) -> dict:
    """Read-only exact-scope audit; no network, ledger writes or score inputs."""
    cutoff = _utc(as_of or datetime.now(timezone.utc))
    result = {"schema_version": AUDIT_VERSION, "status": "INVALID_COMPARISON",
              "audit_recorded_at": _ingestion_clock().isoformat(), "as_of": cutoff.isoformat(),
              "subject_id": subject_id, "award_finding_id": award_finding_id,
              "acquisition_mode": "ARCHIVE_COMPARISON", "http_attempts": 0,
              "read_only": True, "events": [], "events_truncated": False,
              "source_origin": None, "fixtures_count_as_live": False, "source_manifest_is_publisher_signed": False,
              "counts": {"unchanged": 0, "payload_changed": 0, "newly_reported": 0,
                         "not_reported_in_later_traversal": 0, "presence_unresolved": 0},
              "new_source_capture_comparison": False, "publisher_revision_time_verified": False,
              "publisher_deletion_verified": False, "source_revision_order_verified": False,
              "modification_history_complete": False, "issuer_coverage_complete": False,
              "company_revenue_inferred": False, "scoring_applied": False,
              "coverage_scope": "Two exact-query bounded traversals for one generated award and verified UEI"}
    try:
        known = _validate_identities(identities if identities is not None else load_verified_identities())
        target, award, identity = _verified_target(store, known, subject_id=subject_id, finding_id=award_finding_id, clock=cutoff)
        result.update(generated_internal_id=award["generated_internal_id"], uei=identity["uei"],
                      relationship_known_at=identity["known_at"], award_available_at=target["available_at"],
                      verified_identity_sha256=hashlib.sha256(_json(identity)).hexdigest())
        before, previous_rows = _traversal(baseline_manifests, award_id=award["generated_internal_id"], cutoff=cutoff)
        after, current_rows = _traversal(current_manifests, award_id=award["generated_internal_id"], cutoff=cutoff)
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as error:
        return {**result, "error_type": type(error).__name__}
    result.update(baseline=before, current=after)
    if (before["query_scope"] is None or before["query_scope"] != after["query_scope"]
            or before["source_origin"] != after["source_origin"]
            or before["query_scope"].get("award_id") != award["generated_internal_id"]
            or any(defect["reason"] == "QUERY_OR_SOURCE_SCOPE_MISMATCH"
                   for side in (before, after) for defect in side["defects"])):
        return {**result, "status": "INCOMPARABLE_CAPTURE_SCOPES"}
    result["source_origin"] = before["source_origin"]
    source_before = {page["capture_identity"] for page in before["pages"]}
    source_after = {page["capture_identity"] for page in after["pages"]}
    if source_before == source_after:
        result["counts"]["unchanged"] = len(previous_rows.keys() & current_rows.keys())
        return {**result, "status": "SAME_SOURCE_CAPTURE"}
    if (not before.get("response_completed_at") or not after.get("request_started_at")
            or _utc(after["request_started_at"]) < _utc(before["response_completed_at"])
            or _utc(after["response_completed_at"]) <= _utc(before["response_completed_at"])):
        return {**result, "status": "UNORDERED_CAPTURE_WINDOWS"}
    result["new_source_capture_comparison"] = True
    full = before["traversal_complete"] and after["traversal_complete"]
    used = 0

    def event(value: dict) -> None:
        nonlocal used
        value.update(publisher_revision_at=None, publisher_deleted_at=None,
                     publisher_revision_time_verified=False, publisher_deletion_verified=False)
        size = len(_json(value))
        if used + size <= MAX_AUDIT_EVENT_BYTES:
            result["events"].append(value)
            used += size
        else:
            result["events_truncated"] = True

    for identifier in sorted(previous_rows.keys() & current_rows.keys()):
        old, new = previous_rows[identifier], current_rows[identifier]
        if old["source_row_sha256"] == new["source_row_sha256"]:
            result["counts"]["unchanged"] += 1
            continue
        result["counts"]["payload_changed"] += 1
        changed = [key for key in sorted(old["row"].keys() | new["row"].keys())
                   if key not in old["row"] or key not in new["row"] or _json(old["row"][key]) != _json(new["row"][key])]
        event({"transaction_id": identifier, "kind": "PAYLOAD_CHANGED", "changed_fields": changed,
               "before_source_row_sha256": old["source_row_sha256"], "after_source_row_sha256": new["source_row_sha256"],
               "before_action_date": old["row"]["action_date"], "after_action_date": new["row"]["action_date"],
               "before_modified_clock_audit": old["source_modified_clock_audit"],
               "after_modified_clock_audit": new["source_modified_clock_audit"],
               "last_captured_prior_payload_at": old["capture_response_completed_at"],
               "different_payload_captured_at": new["capture_response_completed_at"],
               "obligation_delta_inferred": False})
    unresolved = (previous_rows.keys() ^ current_rows.keys()) | set(before["ambiguous_transaction_ids"]) | set(after["ambiguous_transaction_ids"])
    for identifier in sorted(unresolved):
        old, new = previous_rows.get(identifier), current_rows.get(identifier)
        ambiguous = identifier in before["ambiguous_transaction_ids"] or identifier in after["ambiguous_transaction_ids"]
        if not full or ambiguous:
            result["counts"]["presence_unresolved"] += 1
            event({"transaction_id": identifier, "kind": "PRESENCE_UNRESOLVED",
                   "reason": "INCOMPLETE_OR_AMBIGUOUS_CAPTURE_TRAVERSAL"})
        elif old:
            result["counts"]["not_reported_in_later_traversal"] += 1
            event({"transaction_id": identifier, "kind": "NOT_REPORTED_IN_LATER_TRAVERSAL",
                   "last_captured_present_at": old["capture_response_completed_at"],
                   "absence_observation_window": {"started_at": after["request_started_at"],
                                                  "completed_at": after["response_completed_at"]},
                   "pagination_snapshot_guaranteed": False})
        elif new:
            result["counts"]["newly_reported"] += 1
            event({"transaction_id": identifier, "kind": "NEWLY_REPORTED_IN_CAPTURE",
                   "reported_in_current_capture_at": new["capture_response_completed_at"],
                   "publisher_creation_at": None})
    result["status"] = "COMPLETE_CAPTURE_COMPARISON" if full and not result["events_truncated"] else "PARTIAL_CAPTURE_COMPARISON"
    return result
