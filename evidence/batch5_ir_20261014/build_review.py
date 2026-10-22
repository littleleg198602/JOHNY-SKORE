"""Hash-check original LIVE captures and replay a scoped negative control.

The fresh authority body was wholly withheld. This script must never replace
it with a cleaned body or claim that an RSS feed authenticates an IR portal.
"""
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path

import feedparser

from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_verification_service import CapturedIRDocument, verify_primary_ir_lead


ROOT = Path(__file__).resolve().parent
HOSTS = ("news.microsoft.com", "www.microsoft.com")


def load_captures(root=ROOT):
    root = Path(root)
    runtime = json.loads((root / "runtime_report.json").read_bytes())
    ledger = json.loads((root / "ledger_proof.json").read_bytes())
    if (runtime["runtime_mode"] != "LIVE_HTTP" or runtime["attempted_requests"] != 3
            or len(runtime["capture_receipts"]) != 3 or runtime["status"] != "PARTIAL"
            or runtime["stop_reason"] != "WITHHELD_SENSITIVE_BODY"):
        raise ValueError("Actual source runtime proof changed")
    if (ledger["prior_rss_request_count"] != 0 or ledger["after_rss_request_count"] != 3
            or ledger["active_rss_leases_after_run"] != 0 or len(ledger["requests"]) != 3):
        raise ValueError("Shared persistent quota proof changed")
    captures = {}
    for receipt, request in zip(runtime["capture_receipts"], ledger["requests"]):
        if receipt != json.loads((root / "captures" / f"{receipt['sequence']:02d}_receipt.json").read_bytes()):
            raise ValueError("Capture receipt no longer matches immutable runtime proof")
        attempted = datetime.fromisoformat(receipt["attempted_at"])
        charged = datetime.fromisoformat(request["attempted_at"])
        observed = datetime.fromisoformat(receipt["completed_at"])
        if not attempted <= charged <= observed <= datetime.fromisoformat(runtime["completed_at"]):
            raise ValueError("Physical quota/capture clocks disagree")
        if receipt["status"] != 200 or request["source"] != "rss" or request["subject_id"] != "MSFT":
            raise ValueError("Actual HTTP/scoped subject proof changed")
        if receipt["role"] == "authority":
            if (receipt["retention"] != "WITHHELD_SENSITIVE_BODY" or "raw_path" in receipt
                    or request["outcome"] != "FAILED"):
                raise ValueError("Withheld authority must never become public body evidence")
            if any((root / "captures").glob("*authority*.raw")):
                raise ValueError("Entire rejected authority body must remain withheld")
            continue
        if receipt["retention"] != "ORIGINAL_BYTES_ARCHIVED" or request["outcome"] != "RETURNED":
            raise ValueError("Safe originals no longer have completed shared requests")
        body = (root / "captures" / receipt["raw_path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != receipt["sha256"] or len(body) != receipt["size_bytes"]:
            raise ValueError("Original source body/hash mismatch")
        captures[receipt["role"]] = CapturedIRDocument(
            requested_url=receipt["requested_url"], resolved_url=receipt["requested_url"], body=body,
            observed_at=observed, content_type=receipt["content_type"], http_status=receipt["status"],
            http_date=receipt["http_date"], last_modified=receipt["last_modified"],
        )
    return runtime, ledger, captures


def build_review_artifact(root=ROOT):
    runtime, ledger, captures = load_captures(root)
    feed, page = captures["feed"], captures["primary"]
    entries = list(feedparser.parse(feed.body).entries)[:30]
    selected = next(row for row in entries if row.link == page.resolved_url)
    clock = datetime.fromisoformat(runtime["completed_at"])
    lead = NewsItem("MSFT", feed.resolved_url, selected.title, "", parsedate_to_datetime(selected.published),
                    0.0, page.resolved_url, feed_url=feed.resolved_url, original_url=page.resolved_url,
                    observed_at=clock)
    # This deliberate wrong-document control retains real feed/page bytes.
    # The RSS feed is explicitly NOT the missing portal authority.
    control = verify_primary_ir_lead(lead, feed_capture=feed, primary_capture=page, authority_capture=feed,
                                    publisher_hosts=HOSTS, knowledge_at=clock)
    return {
        "schema_version": 1, "runtime_status": runtime["status"],
        "actual_http_requests": 3, "http_200_responses": 3, "safe_original_bodies": 2,
        "source_verified_live_cases": 0, "withheld_entire_bodies": 1,
        "withheld_authority_sha256": runtime["capture_receipts"][1]["sha256"],
        "quota_request_outcomes": [row["outcome"] for row in ledger["requests"]],
        "actual_capture_available_at": runtime["completed_at"],
        "captured_only_offline_control": {
            "semantics": "Fetched RSS feed cannot stand in for the withheld IR portal. This is a scoped required-document rejection, not a completed negative issuer/news search.",
            "additional_http_requests": 0, "runtime_source_verification_claim": False, "review": control,
        },
        "publication_clocks": control["publication_clocks"],
        "http_last_modified_is_not_publication": page.last_modified,
        "issuer_identity_verified": False, "issuer_attribution_allowed": False, "scoring_applied": False,
        "full_specialist_acceptance": False, "all_publisher_history_complete": False,
        "historical_knowledge_or_oos_proven": False, "windows_end_to_end_verified": False,
        "prior_capture_quota_reconstructed": False,
        "limits": ["Assigned access-value detection prevents IR portal content verification; its entire body is withheld",
                   "RSS/page exact title, original URL, quotation, and publisher publication clocks do not establish an independently dated issuer/instrument identity",
                   "Primary HTML publication time and HTTP Last-Modified are kept as distinct source clocks",
                   "SOURCE_VERIFIED is demonstrated only by explicitly synthetic transport tests in this batch",
                   "The prospective Oct14 shared RSS ledger does not reconstruct the earlier Oct13 one-off requests"],
    }


if __name__ == "__main__":
    target = ROOT / "source_review.json"
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(build_review_artifact(), indent=2, sort_keys=True) + "\n")
    print(target.name)
