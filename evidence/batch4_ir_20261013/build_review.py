"""Reproduce source-only positive and partial controls from actual IR captures."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import feedparser

from market_checker_app.models import NewsItem
from market_checker_app.services.ir_primary_verification_service import CapturedIRDocument, verify_primary_ir_lead


ROOT = Path(__file__).resolve().parent
PUBLISHER_HOSTS = ("news.microsoft.com", "www.microsoft.com")


def load_captures():
    manifest = json.loads((ROOT / "captures.json").read_bytes())
    if manifest["request_count"] > 6 or len(manifest["http_requests"]) != manifest["request_count"]:
        raise ValueError("IR capture manifest exceeds shared request bound")
    captures = {}
    for item in manifest["captures"]:
        if item.get("http_status") != 200:
            continue
        body = (ROOT / item["path"]).read_bytes()
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
    clock = max(row.observed_at for row in captures.values())
    positives = []
    for filename in ("announcement.html", "ir_linked_announcement.html"):
        page = captures[filename]
        report = verify_primary_ir_lead(
            lead_for_page(captures, page), feed_capture=feed, primary_capture=page, authority_capture=authority,
            publisher_hosts=PUBLISHER_HOSTS, knowledge_at=clock,
        )
        positives.append({"capture_path": "primary/" + filename, "review": report})
    dividend_lead = lead_for_page(captures, captures["ir_linked_announcement.html"])
    partial = verify_primary_ir_lead(
        dividend_lead, feed_capture=feed, primary_capture=authority, authority_capture=authority,
        publisher_hosts=PUBLISHER_HOSTS, knowledge_at=clock,
    )
    return {
        "schema_version": 1, "result": "PRIMARY_PUBLISHER_SOURCE_ONLY_REVIEW",
        "actual_http_requests": manifest["request_count"], "raw_http_200_captures": len(captures),
        "positive_source_only_cases": positives,
        "actual_partial_control": {
            "control_semantics": "Actual HTTP200 IR homepage is not the RSS lead's original announcement. This is a page-binding/non-article control, not a completed negative issuer search.",
            "capture_path": "primary/ir_authority.html", "review": partial,
        },
        "rss_feed_capture_scope": "first 30 entries, actual feed contains 10; no all-publisher coverage claim",
        "issuer_identity_verified": False, "issuer_attribution_allowed": False,
        "scoring_applied": False, "completion_verified": False, "windows_end_to_end_verified": False,
        "network_or_quota_in_runtime_verifier": False,
        "next_task": "Quota-managed leased LIVE primary traversal and independent dated legal-issuer/instrument evidence before issuer-scoped acceptance.",
        "limits": ["SOURCE_VERIFIED concerns captured publisher content, not claim truth or issuer attribution",
                   "database RSS findings remain UNVERIFIED; original leads persist unchanged",
                   "reported publication clocks do not prove exact first dissemination; capture clocks remain October",
                   "earnings release date is a prospective announcement, not already released financial results",
                   "publisher ticker/brand mentions do not establish an independent dated legal identity",
                   "one-off bounded captures do not consume/reset app provider quotas or prove runtime LIVE integration",
                   "today's archived replay is not historical/OOS availability or Windows end-to-end evidence"],
    }


if __name__ == "__main__":
    target = ROOT / "source_review.json"
    target.write_text(json.dumps(build_review_artifact(), indent=2) + "\n", encoding="utf-8")
    print(target)
