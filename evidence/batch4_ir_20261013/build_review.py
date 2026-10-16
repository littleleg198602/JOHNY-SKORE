"""Replay safe originals; withheld required bodies produce a current PARTIAL.

Historical manifests/reports stay immutable. New diagnostics are written only
to the additive batch5 withholding evidence, never the old source_review.json.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import feedparser

from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_verification_service import (
    CapturedIRDocument, UnavailableIRDocument, capture_available_at, verify_primary_ir_lead,
)


ROOT = Path(__file__).resolve().parent
PUBLISHER_HOSTS = ("news.microsoft.com", "www.microsoft.com")
WITHHOLDING_RECORD = ROOT.parent / "batch5_ir_prior_withholding_20261014/withholding.json"
CURRENT_REPORT_PATH = WITHHOLDING_RECORD.parent / "current_review.json"


def load_captures(root=None, withholding_path=None):
    root = Path(root or ROOT)
    record_path = Path(withholding_path or WITHHOLDING_RECORD)
    manifest = json.loads((root / "captures.json").read_bytes())
    if manifest["request_count"] > 6 or len(manifest["http_requests"]) != manifest["request_count"]:
        raise ValueError("IR capture manifest exceeds shared request bound")
    withholding = json.loads(record_path.read_bytes()) if record_path.is_file() else None
    captures = {}
    for item in manifest["captures"]:
        if item.get("http_status") != 200:
            continue
        if item["path"] == "primary/ir_authority.html":
            # Known rejected authority bytes are never opened again, even if
            # accidentally restored at this pathname. A missing disclosure
            # record likewise cannot recover source verification from them.
            if withholding is not None:
                rows = [row for row in withholding["withdrawals"] if row["repository_path"] ==
                        "evidence/batch4_ir_20261013/primary/ir_authority.html"]
                if (len(rows) != 1 or rows[0]["original_capture_metadata"] != item
                        or rows[0]["retention"] != "WITHHELD_SENSITIVE_BODY"
                        or not withholding["whole_body_gate_positive"]):
                    raise ValueError("IR authority withholding metadata conflicts with the original manifest")
                unavailable_at = datetime.fromisoformat(withholding["audit_completed_at"])
                reason = "WITHHELD_SENSITIVE_BODY"
            else:
                unavailable_at, reason = datetime.now(timezone.utc), "MISSING_CAPTURE_BODY"
            captures[Path(item["path"]).name] = UnavailableIRDocument(
                requested_url=item["url"], resolved_url=item["resolved_url"],
                observed_at=datetime.fromisoformat(item["observed_at"]), unavailable_at=unavailable_at,
                sha256=item["sha256"], size_bytes=item["size_bytes"], reason=reason,
                content_type=item["content_type"], http_status=item["http_status"],
                http_date=item["http_date"], last_modified=item["last_modified"],
            )
            continue
        body = (root / item["path"]).read_bytes()
        if len(body) != item["size_bytes"] or hashlib.sha256(body).hexdigest() != item["sha256"]:
            raise ValueError("IR raw capture bytes/hash conflict")
        captures[Path(item["path"]).name] = CapturedIRDocument(
            requested_url=item["url"], resolved_url=item["resolved_url"], body=body,
            observed_at=datetime.fromisoformat(item["observed_at"]), content_type=item["content_type"],
            http_status=item["http_status"], http_date=item["http_date"], last_modified=item["last_modified"],
        )
    return manifest, captures


def lead_for_page(captures, page):
    feed = captures["press_releases_feed.xml"]
    entries = feedparser.parse(feed.body).entries[:30]
    entry = next(row for row in entries if row.link == page.resolved_url)
    from email.utils import parsedate_to_datetime
    return NewsItem(
        ticker="MSFT", source=feed.resolved_url, title=entry.title, summary=entry.get("summary", ""),
        published_at=parsedate_to_datetime(entry.published), observed_at=feed.observed_at, sentiment_weight=0.0,
        url=entry.link, original_url=entry.link, feed_url=feed.resolved_url,
        publisher="Microsoft Source", publisher_domain="news.microsoft.com",
    )


def build_review_artifact():
    manifest, captures = load_captures()
    authority, feed = captures["ir_authority.html"], captures["press_releases_feed.xml"]
    clock = max(capture_available_at(row) for row in captures.values())
    partial_cases = []
    for filename in ("announcement.html", "ir_linked_announcement.html"):
        page = captures[filename]
        report = verify_primary_ir_lead(
            lead_for_page(captures, page), feed_capture=feed, primary_capture=page, authority_capture=authority,
            publisher_hosts=PUBLISHER_HOSTS, knowledge_at=clock,
        )
        partial_cases.append({"capture_path": "primary/" + filename, "review": report})
    dividend_lead = lead_for_page(captures, captures["ir_linked_announcement.html"])
    partial = verify_primary_ir_lead(
        dividend_lead, feed_capture=feed, primary_capture=authority, authority_capture=authority,
        publisher_hosts=PUBLISHER_HOSTS, knowledge_at=clock,
    )
    return {
        "schema_version": 2, "result": "PRIMARY_IR_REQUIRED_BODY_WITHHELD",
        "actual_http_requests": manifest["request_count"], "raw_http_200_captures": len(captures),
        "http_requests_in_current_replay": 0,
        "readable_original_captures": sum(isinstance(row, CapturedIRDocument) for row in captures.values()),
        "withheld_required_captures": sum(isinstance(row, UnavailableIRDocument) for row in captures.values()),
        "positive_source_only_cases": [], "current_partial_source_only_cases": partial_cases,
        "historical_positive_reports_are_current_acceptance": False,
        "current_report_available_at": clock.isoformat(),
        "actual_partial_control": {
            "control_semantics": "The historical HTTP200 IR portal control body is now entirely withheld. Current replay rejects missing required bodies before parsing; it cannot reproduce the original page-content control or prove a negative issuer search.",
            "capture_path": "primary/ir_authority.html", "review": partial,
        },
        "rss_feed_capture_scope": "first 30 entries, actual feed contains 10; no all-publisher coverage claim",
        "issuer_identity_verified": False, "issuer_attribution_allowed": False,
        "scoring_applied": False, "completion_verified": False, "windows_end_to_end_verified": False,
        "network_or_quota_in_runtime_verifier": False,
        "next_task": "An IR portal whose entire original body can pass the disclosure gate, plus independently dated issuer/instrument evidence.",
        "limits": ["The old source_review.json remains immutable historical source-version evidence; its two positives are not current acceptance",
                   "A required portal body is entirely withheld; original hash/clocks are metadata and cannot replace content verification",
                   "SOURCE_VERIFIED concerns captured publisher content, not claim truth or issuer attribution",
                   "database RSS findings remain UNVERIFIED; original leads persist unchanged",
                   "reported publication clocks do not prove exact first dissemination; capture clocks remain October",
                   "earnings release date is a prospective announcement, not already released financial results",
                   "publisher ticker/brand mentions do not establish an independent dated legal identity",
                   "one-off bounded captures do not consume/reset app provider quotas or prove runtime LIVE integration",
                   "today's archived replay is not historical/OOS availability or Windows end-to-end evidence"],
    }


if __name__ == "__main__":
    # Historical source_review.json is never an output of the current guard.
    with CURRENT_REPORT_PATH.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(build_review_artifact(), indent=2, sort_keys=True) + "\n")
    print(CURRENT_REPORT_PATH.name)
