"""Independently audit explicit source plans and retained public inputs, offline."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import parse_qs, unquote, urljoin, urlsplit


BASE = "9d60cc650ff0d64b720f84fabfe1348e44c6bcf4"
BASE_TREE = "138a1169556f1fdd102c68cd95fda429b3e6c17b"
PLAN_PINS = {
    "identity": "31decb1072f57ba81b8b38b30403066ffb691dd892d079f551c3a6ec76d3ab62",
    "healthcare": "d89f491a62e5e045f9cf30845bd8944526b3062defa141e1f2ceead49bdb64ba",
    "regulatory": "463b5b059fa52f80632ff0f0dd7e9362bdda77bf8b72ee8ffc3a47ab056be385",
    "sec": "26463062446bd6c2173fccf11388d5aacfe4bc41ca5946bcf6d27884c39714cc",
}
SELECTED = {"P_NAME_AMENDMENT", "P_REGISTERED_SECURITIES_DESCRIPTION", "MSFT_2026_EX31_1",
            "FDA_ORIGINAL_APPROVAL_LETTER", "FDA_SUPPLEMENT_17_LABEL"}
WITHHELD = (
    "evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
    "evidence/batch4_ir_20261013/primary/ir_authority.html",
    "evidence/batch5_ir_20261014/primary/ir_authority.html",
    "evidence/batch8_regulatory_20261015/raw/njd_case_page.html",
)
ACCESS = re.compile(r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[A-Z0-9]{16}\b|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}|(?:api[_-]?key|subscription[_-]?key|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|password)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}|"
    r"[?&](?:sig|signature|x-amz-signature|x-goog-signature|access_token)=[^\s\"'<>]{8,})", re.I)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("DUPLICATE_JSON_KEY")
        out[key] = value
    return out


def decode(raw):
    return json.loads(raw, object_pairs_hook=no_duplicate_keys)


def safe_url(url):
    assert isinstance(url, str) and not ACCESS.search(unescape(url)), "ACCESS_BEARING_URL_REFUSED"
    p = urlsplit(url)
    assert p.scheme == "https" and p.hostname and p.username is None and p.password is None
    assert p.port in (None, 443) and not p.fragment and not any(c.isspace() for c in url)
    assert not any(part in {".", ".."} for part in unquote(p.path).split("/"))
    return p


def resource_key(url):
    """Conservative comparison, including SEC ix and DOJ inline aliases."""
    p = urlsplit(unescape(url))
    host = (p.hostname or "").lower().removeprefix("www.")
    path = unquote(p.path)
    if host == "sec.gov" and path.lower() == "/ix":
        path = parse_qs(p.query).get("doc", [path])[0]
    m = re.fullmatch(r"/Archives/edgar/data/(\d+)/([\d-]+)/(.+)", path, re.I)
    if host == "sec.gov" and m:
        return ("sec.gov", str(int(m[1])), m[2].replace("-", ""), m[3].casefold())
    m = re.fullmatch(r"/atr/media/(\d+)/dl/?", path, re.I)
    if host == "justice.gov" and m:
        return (host, "media", m[1])
    return (host, path.rstrip("/").casefold(), p.query)


def walk(value, pointer="", parent=None, field=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, pointer + "/" + k.replace("~", "~0").replace("/", "~1"), value, k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk(v, pointer + "/" + str(i), parent, field)
    elif isinstance(value, str):
        yield pointer, value, parent or {}, field


class PublicInputs:
    def __init__(self, repo):
        self.repo = repo.resolve()
        listing = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "-z", BASE])
        self.entries, self.cache, self.pins = {}, {}, {}
        for row in listing.split(b"\0"):
            if not row:
                continue
            header, path = row.split(b"\t", 1)
            mode, kind, blob = header.decode().split()
            self.entries[path.decode()] = (mode, kind, blob)
        assert not any(p in self.entries for p in WITHHELD)

    def read(self, relative, expected=None):
        assert isinstance(relative, str) and not PurePosixPath(relative).is_absolute()
        assert all(s not in {"", ".", ".."} for s in relative.split("/"))
        assert relative in self.entries and relative not in WITHHELD
        assert Path(relative).suffix.lower() not in {".db", ".sqlite", ".sqlite3", ".wal", ".journal"}
        if relative not in self.cache:
            mode, kind, blob = self.entries[relative]
            assert kind == "blob" and mode != "120000"
            path = self.repo / relative
            assert not path.is_symlink() and all(not p.is_symlink() for p in path.parents if p.is_relative_to(self.repo))
            raw = path.read_bytes()
            assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == blob
            self.cache[relative] = raw
        raw = self.cache[relative]
        pin = {"path": relative, "bytes": len(raw), "sha256": digest(raw)}
        if expected is not None:
            assert pin == {k: expected[k] for k in pin}, "WHOLE_PUBLIC_PIN_MISMATCH"
        self.pins[relative] = pin
        return raw


def span(raw, start, end, expected_hash, literal=None):
    assert type(start) is int and type(end) is int and 0 <= start < end <= len(raw)
    part = raw[start:end]
    assert digest(part) == expected_hash, "ORIGINAL_BYTE_SPAN_HASH_MISMATCH"
    if literal is not None:
        assert part == literal.encode("utf-8"), "ORIGINAL_LITERAL_SPAN_MISMATCH"
    return {"byte_start": start, "byte_end_exclusive": end, "bytes": len(part), "sha256": expected_hash}


def native_span(raw, item):
    return span(raw, item["byte_start"], item.get("byte_end_exclusive", item.get("byte_end")), item["sha256"],
                item.get("literal_bytes_utf8", item.get("original_quote")))


def history_kind(path, context, field):
    leaf = Path(path).name.lower()
    if field not in {"url", "requested_url", "request_url", "final_url", "response_url", "resolved_url", "source_url"}:
        return "REFERENCE_ONLY"
    if any(k in context for k in ("reserved_at", "reservation_created_at", "claim_recorded_at")) or "reserved" in leaf:
        return "CONSUMED_RESERVATION_OR_ATTEMPT"
    clocks = {"started_at", "retrieval_started_at", "observed_at", "attempted_at", "requested_at", "completed_at"}
    outcomes = {"http_status", "status", "capture_kind", "retention", "raw_sha256", "body_sha256", "sha256", "failure_type"}
    if clocks.intersection(context) and outcomes.intersection(context):
        return "RECORDED_ATTEMPT_OR_RESPONSE"
    if any(word in leaf for word in ("receipt", "metadata", "attempt")) and outcomes.intersection(context):
        return "RECORDED_ATTEMPT_OR_RESPONSE"
    return "REFERENCE_ONLY"


def audit(repo, plan_paths, selection_path):
    public = PublicInputs(repo)
    plans, plan_pins = {}, {}
    for role, path in plan_paths.items():
        assert not path.is_symlink() and path.stat().st_size < 1_000_000
        raw = path.read_bytes()
        assert digest(raw) == PLAN_PINS[role], "FROZEN_SOURCE_PLAN_CHANGED"
        assert not ACCESS.search(unescape(raw.decode())), "WHOLE_PLAN_ACCESS_GATE_FAILED"
        d = decode(raw)
        assert d.get("base_commit", d.get("baseline_commit")) == BASE
        plans[role] = d
        plan_pins[role] = {"path": str(path), "bytes": len(raw), "sha256": digest(raw)}

    # Exhaustive tracked JSON inspection, without opening any runtime DB or ledger.
    inventory, json_nodes = [], []
    for path in sorted(p for p in public.entries if p.endswith(".json")):
        raw = public.read(path)
        d = decode(raw)
        inventory.append({"path": path, "bytes": len(raw), "sha256": digest(raw)})
        json_nodes.append((path, d))
    manifest = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    assert len(inventory) == 1485 and sum(r["bytes"] for r in inventory) == 28_223_043

    def history(url):
        key, leaf = resource_key(url), urlsplit(url).path.rsplit("/", 1)[-1]
        matches = []
        for path, d in json_nodes:
            for pointer, value, context, field in walk(d):
                decoded = unescape(value)
                exact = decoded == url
                is_URL = decoded.lower().startswith(("http://", "https://")) and not any(c.isspace() for c in decoded)
                same = is_URL and resource_key(decoded) == key
                if not same and url not in decoded and leaf.casefold() not in decoded.casefold():
                    continue
                kind = history_kind(path, context, field) if same else "REFERENCE_ONLY"
                matches.append({"path": path, "json_pointer": pointer, "exact_URL_value": exact,
                    "same_canonical_resource": same, "kind": kind})
        command = ["git", "-C", str(repo), "grep", "-l", "-I", "-i", "-F", "-e", url, "-e", leaf, BASE, "--", ".",
                   ":!*.db", ":!*.sqlite", ":!*.sqlite3", ":!*.zip", ":!*.png", ":!*.jpg", ":!*.jpeg", ":!*.pdf"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode in (0, 1)
        text_paths = [s.removeprefix(BASE + ":") for s in result.stdout.splitlines()]
        return {"tracked_JSON_matches": matches, "prior_attempt_or_reservation_matches": [m for m in matches if m["same_canonical_resource"] and m["kind"] != "REFERENCE_ONLY"],
            "tracked_text_leaf_or_URL_match_paths": text_paths, "unrecorded_external_or_collector_history_recovered": False}

    def check_reference(parent_pin, metadata_pin, source_url, href, href_span, anchor_span, enclosing=None):
        raw = public.read(parent_pin["path"], parent_pin)
        assert not ACCESS.search(unescape(raw.decode("utf-8", errors="replace"))), "WHOLE_PARENT_ACCESS_GATE_FAILED"
        receipt = decode(public.read(metadata_pin["path"], metadata_pin))
        href_part = native_span(raw, href_span)
        anchor_part = native_span(raw, anchor_span)
        anchor = raw[anchor_part["byte_start"]:anchor_part["byte_end_exclusive"]]
        hrefs = re.findall(rb"\bhref\s*=\s*[\"']([^\"']+)[\"']", anchor, re.I)
        assert len(hrefs) == 1 and unescape(hrefs[0].decode()) == unescape(href)
        resolved = urljoin(source_url, unescape(href))
        safe_url(resolved)
        verified = {"parent": parent_pin, "metadata": metadata_pin, "parent_source_url": source_url,
            "href": href, "href_span": href_part, "anchor_span": anchor_part, "resolved_literal_URL": resolved,
            "whole_parent_original_and_spans_verified": True}
        if enclosing is not None:
            verified["enclosing_row_span"] = native_span(raw, enclosing)
        return verified, receipt

    candidates = []
    for role in ("identity", "healthcare", "sec"):
        plan = plans[role]
        for c in plan["candidates"]:
            ident, url = c.get("candidate_id", c.get("id")), c["url"]
            p = safe_url(url)
            refs, reasons = [], []
            if role == "identity":
                b = c["request_bounds"]
                bounds = {"maximum_attempts": b["maximum_transport_attempts"], "maximum_response_bytes": b["maximum_response_bytes"], "timeout_seconds": b["timeout_seconds"]}
                assert b["retry_allowed"] is b["redirects_allowed"] is b["fallback_or_alternate_url_allowed"] is False
                for r in c["literal_references"]:
                    parent = plan["retained_parents"][r["parent_key"]]
                    assert parent["body"] == {"path": r["parent_path"], "bytes": r["parent_bytes"], "sha256": r["parent_sha256"]}
                    hs = {"byte_start": r["href_byte_start"], "byte_end": r["href_byte_end_exclusive"], "sha256": r["href_bytes_sha256"], "original_quote": r["literal_href"]}
                    an = {"byte_start": r["original_anchor_start"], "byte_end": r["original_anchor_end_exclusive"], "sha256": r["original_anchor_sha256"], "original_quote": r["original_anchor_utf8"]}
                    proof, receipt = check_reference(parent["body"], parent["metadata"], parent["source_url"], r["literal_href"], hs, an)
                    assert proof["resolved_literal_URL"] == r["resolved_exact_url"] == url
                    assert receipt.get("final_url", receipt.get("requested_url")) == parent["source_url"]
                    refs.append(proof)
                scope = {k: c[k] for k in ("expected_archive_issuer_cik", "expected_accession_directory", "expected_filename", "expected_source_route", "exact_missing_fact_to_inspect", "limitations_before_acquisition")}
                m = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d{18})/([^/]+)", p.path)
                assert p.hostname == "www.sec.gov" and m and int(m[1]) == int(c["expected_archive_issuer_cik"]) == 1474432
                assert m[2] == c["expected_accession_directory"] and m[3] == c["expected_filename"]
            elif role == "healthcare":
                r, b = c["literal_retained_parent"], c["root_capture_preconditions"]
                bounds = {"maximum_attempts": b["maximum_attempts_for_this_url"], "maximum_response_bytes": b["maximum_body_bytes"], "timeout_seconds": 25}
                bounds["timeout_bound_source"] = "FROZEN_COORDINATOR_SELECTION_PROPOSAL"
                assert b["retries"] == b["redirects"] == b["collector_API_requests"] == 0
                assert b["fallback_or_alternate_url"] is b["network_authorization_granted_by_this_plan"] is b["new_or_reconstructed_quota_database"] is False
                proof, receipt = check_reference(r["whole_original"], r["capture_metadata"], r["response_url"], r["literal_href"]["literal_bytes_utf8"], r["literal_href"], r["whole_anchor"], r["complete_enclosing_action_row"])
                assert proof["resolved_literal_URL"] == url and receipt["body_sha256"] == r["whole_original"]["sha256"]
                assert receipt["request_url"] == r["request_url"] and receipt["observed_at"] == r["observed_at"]
                assert p.hostname == "www.accessdata.fda.gov" and p.path.startswith("/drugsatfda_docs/") and p.path.endswith(".pdf") and "761081" in p.path
                scope = c["requested_scope"]
                assert scope["application_number"] == "761081" and scope["issuer_ticker"] == "PFE"
                row = public.cache[r["whole_original"]["path"]][r["complete_enclosing_action_row"]["byte_start"]:r["complete_enclosing_action_row"]["byte_end_exclusive"]]
                expected_day = datetime.fromisoformat(scope["parent_action_day"]).strftime("%m/%d/%Y").encode()
                assert expected_day in row and scope["parent_submission"].encode() in row
                refs.append(proof)
            else:
                r = c["literal_parent_reference"]
                proof, receipt = check_reference(r["parent_whole_original"], r["parent_capture_receipt"], r["parent_source_url"], r["href_literal"], r["href_original_span"], r["link_anchor"], r["document_row"])
                assert proof["resolved_literal_URL"] == r["resolved_literal_href"]
                source_receipt = receipt["captures"][0]
                assert source_receipt["url"] == r["parent_source_url"] and source_receipt["sha256"] == r["parent_whole_original"]["sha256"]
                refs.append(proof)
                b = c.get("bounds", c.get("bounds_if_separately_selected_before_any_request", c.get("bounds_if_new_explicit_cap_plan_approved")))
                bounds = {"maximum_attempts": b["maximum_attempts"], "maximum_response_bytes": b["maximum_response_bytes"], "timeout_seconds": b["timeout_seconds"]}
                assert b["retries"] is b["redirects"] is False
                scope = {k: c[k] for k in ("source_route", "exact_fact_sought", "advance_limit") if k in c}
                assert p.hostname == "www.sec.gov" and "/edgar/data/789019/000119312526323660/" in p.path
                if proof["resolved_literal_URL"] != url:
                    reasons.append("RAW_URL_DERIVED_FROM_IX_ARGUMENT_NOT_A_DIRECT_LITERAL_LINK")
                if bounds["maximum_response_bytes"] > 4_000_000:
                    reasons.append("LARGE_BODY_REQUIRES_SEPARATE_STRICT_CAP_PLAN")
            assert bounds["maximum_attempts"] == 1 and bounds["timeout_seconds"] == 25
            h = history(url)
            if h["prior_attempt_or_reservation_matches"]:
                reasons.append("PRIOR_RECORDED_REQUEST_OR_CONSUMED_RESERVATION")
            selected = ident in SELECTED
            if not selected:
                reasons.append("UNSELECTED_NO_FALLBACK_AUTHORIZATION")
            if selected:
                assert not reasons and 0 < bounds["maximum_response_bytes"] <= 4_000_000
            candidates.append({"role": role, "candidate_id": ident, "exact_URL": url, "root_selected_for_separate_research_review": selected,
                "status": "ELIGIBLE_SELECTED_BOUNDED_RESEARCH_REVIEW_ONLY" if not reasons else "NOT_SELECTED_OR_BLOCKED",
                "blocking_or_exclusion_reasons": reasons, "verified_literal_references": refs, "scope": scope, "bounds": bounds,
                "tracked_history": h, "source_body_observed_or_content_presumed": False, "collection_authorized_by_audit": False})

    regulatory = plans["regulatory"]
    assert regulatory["status"] == "BLOCKED" and regulatory["candidates"] == [] and regulatory["eligible_candidate_count"] == 0
    assert regulatory["source_requests_allowed_by_this_plan"] == regulatory["source_requests_performed"] == regulatory["source_requests_reserved"] == 0
    assert regulatory["protected_journal_database_opened_hashed_copied_replayed_or_reset"] is False
    selected = [r for r in candidates if r["root_selected_for_separate_research_review"]]
    assert len(selected) == 5 and sum(r["bounds"]["maximum_response_bytes"] for r in selected) == 10_256_000
    selection_raw = selection_path.read_bytes()
    assert digest(selection_raw) == "60c86f690de7a7bc8be143455317d21b37053548a1eae771b3565c2bbaea87d4"
    proposal = decode(selection_raw)
    assert proposal["base_public_head"] == BASE and proposal["max_http_transports"] == 5
    assert proposal["aggregate_max_response_bytes"] == 10_256_000
    assert proposal["retry"] is proposal["redirect"] is proposal["fallback"] is False
    assert {r["candidate_id"] for r in proposal["urls"]} == SELECTED and len(proposal["urls"]) == 5
    for row in proposal["urls"]:
        item = next(r for r in selected if r["candidate_id"] == row["candidate_id"])
        assert row["url"] == item["exact_URL"] and row["max_response_bytes"] == item["bounds"]["maximum_response_bytes"]
        assert row["maximum_attempts"] == 1 and row["timeout_seconds"] == 25 and row["http_attempted"] is False
        assert row["source_plan"]["sha256"] == PLAN_PINS[item["role"]]
        assert row["source_plan"]["bytes"] == plan_pins[item["role"]]["bytes"]
        assert any(ref["parent"] == row["original_literal_parent"] and ref["href"] == row["literal_href"]
            and ref["href_span"]["byte_start"] == row["href_byte_start"]
            and ref["href_span"]["byte_end_exclusive"] == row["href_byte_end_exclusive"]
            and ref["href_span"]["sha256"] == row["href_sha256"] for ref in item["verified_literal_references"])
    controls = []
    for url in ("https://www.sec.gov/Archives/edgar/data/789019/000078901926000157/0000789019-26-000157.txt",
                "https://www.microsoft.com/en-us/investor/default",
                "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=761081",
                "https://ecf.njd.uscourts.gov/cgi-bin/show_public_doc?2024cv04055-284"):
        h = history(url)
        assert h["prior_attempt_or_reservation_matches"], "ACTUAL_CONSUMED_HISTORY_NOT_RECOGNIZED"
        controls.append({"URL": url, "status": "CONSUMED_DO_NOT_RETRY", "history": h})
    # Recheck only the new plan bytes at completion; no source collection or app run.
    assert all(digest(path.read_bytes()) == PLAN_PINS[role] for role, path in plan_paths.items())
    return {"schema_version": "sourcefront21_independent_candidate_plan_audit_v1", "audited_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": BASE, "base_tree": BASE_TREE, "frozen_plan_pins": plan_pins, "status": "FIVE_SELECTED_LITERAL_RESEARCH_CANDIDATES_VERIFIED_NOT_AUTHORIZED",
        "coordinator_selection_proposal": {"path": str(selection_path), "bytes": len(selection_raw), "sha256": digest(selection_raw),
            "status": proposal["status"], "is_consumed_attempt_history": False, "is_collection_authorization": False},
        "selected_exact_URLs": [r["exact_URL"] for r in selected], "candidates": candidates,
        "selection_bounds": {"maximum_GET_attempts": 5, "maximum_total_response_bytes": 10_256_000, "maximum_seconds_per_candidate": 25, "retries": 0, "redirects": 0, "fallbacks": 0, "root_separate_pre_request_reservations_required": True},
        "regulatory": {"status": "BLOCKED", "eligible_candidates": 0, "scope_sought": regulatory["scope_sought"], "route_assessments": regulatory["route_assessments"],
            "Doc51_or_prior_four_original_audit_repeated": False, "new_document_number_or_Order_URL_inferred": False},
        "tracked_history_inventory": {"JSON_files": len(inventory), "JSON_bytes": sum(r["bytes"] for r in inventory), "JSON_parse_or_duplicate_key_errors": 0,
            "manifest_sha256": digest(manifest), "manifest_definition": "SHA256 compact sorted-key UTF8 JSON array sorted by path, rows{path,bytes,sha256}",
            "scope": "Every tracked JSON at exact public base plus git-grep all tracked nonbinary text for exact URL/leaf; original body references are distinguished from request/reservation receipts.",
            "record_matches_are_distinct_HTTP_transports": False, "unrecorded_external_or_app_collector_history_recovered": False},
        "actual_consumed_route_boundary_checks": controls,
        "permanent_blocks": ["NHTSA approval block: no retry, alternate route/transport or state/quota reconstruction.",
            "FDA/CMS/Clinical/USAspending app collectors remain blocked while original durable histories are missing. Separately reserved static FDA primary research does not recover or bypass those histories.",
            "Consumed market13-symbol plan remains consumed: no replay/new plan/transport/quotaDB or synthetic forecasts.",
            "Four whole access-bearing bodies remain absent and unopened; no reconstructed, sanitized, encoded, compressed or wrapped substitute."],
        "interpretation_limits": ["Research eligibility is not network authorization, app collector/live acceptance or a safe-complete body guarantee.",
            "Natural-person/account/alias/role interval, control/ownership, CUSIP/action/symbol validity, nativeWindows, historical/OOS and full21 acceptance remain unproved.",
            "A same-accession exhibit is a distinct document, not an independent disclosure.2015 charter,2019 label, EX32.1 and large ix10K are not fallback requests.",
            "True request/observation/completion clocks and whole-body safety/byte limits must be enforced by any separately authorized coordinator capture; no historical availability is backdated."],
        "actions": {"HTTP_search_connector_browser_or_collector_calls": 0, "SQL_DB_or_quota_operations": 0, "protected_journal_DB_touched": False,
            "prior_suites_CI_readiness_or_four_original_audit_reruns": 0, "source_plan_or_parent_mutations": 0, "source_body_copies_or_substitutes": 0,
            "shared_code_tests_registries_status_or_config_changes": 0, "forecast_label_or_trading_actions": 0},
        "candidate_parent_and_metadata_pins": [v for p, v in public.pins.items() if not p.endswith(".json") or any(p == r["metadata"]["path"] for c in candidates for r in c["verified_literal_references"])],
        "withheld_paths_absent_from_public_Git_catalog_only": list(WITHHELD)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    for role in PLAN_PINS:
        parser.add_argument("--" + role + "-plan", type=Path, required=True)
    parser.add_argument("--selection-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.repo_root, {role: getattr(args, role + "_plan") for role in PLAN_PINS}, args.selection_plan)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "selected_count": len(result["selected_exact_URLs"]),
                      "maximum_GETs": 5, "maximum_total_bytes": 10_256_000, "HTTP_SQL_actions": 0}))
