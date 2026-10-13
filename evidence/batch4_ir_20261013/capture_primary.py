"""At most six HTTP requests for one Microsoft primary feed/page audit.

Raw responses are immutable. No fixture substitutes for unavailable sources.
Redirect requests share the same hard budget and exact Microsoft host policy.
"""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import feedparser


ROOT = Path(__file__).resolve().parent
MAX_BYTES = 2_000_000
MAX_REQUESTS = 6
ALLOWED_HOSTS = {"www.microsoft.com", "news.microsoft.com", "aka.ms"}
USER_AGENT = "JOHNY-SKORE primary-IR-audit/1.0 (github.com/littleleg198602/JOHNY-SKORE)"


def allowed_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment):
        raise ValueError("Unexpected IR source host or URL")
    if parsed.hostname == "aka.ms" and url != "https://aka.ms/AA13ep0o":
        raise ValueError("Only the exact captured IR-page short link is permitted")
    return url


class RequestBudget:
    def __init__(self):
        self.requests = []

    def reserve(self, url):
        allowed_url(url)
        if len(self.requests) >= MAX_REQUESTS:
            raise ValueError("IR capture six-request budget exhausted")
        self.requests.append({"url": url, "requested_at": datetime.now(timezone.utc).isoformat()})


class Redirect(HTTPRedirectHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.budget.reserve(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    manifest = ROOT / "captures.json"
    append = "--append-ir-linked" in sys.argv
    if manifest.exists() and not append:
        raise RuntimeError("IR captures already exist; use a new dated directory")
    if append and not manifest.exists():
        raise RuntimeError("IR linked append requires the original capture manifest")
    (ROOT / "primary").mkdir(parents=True, exist_ok=True)
    budget = RequestBudget()
    opener = build_opener(Redirect(budget))
    previous = json.loads(manifest.read_bytes()) if append else {}
    captures = previous.get("captures", [])
    budget.requests = previous.get("http_requests", [])
    blocked_hosts = set(previous.get("blocked_hosts", []))

    def capture(filename, url):
        if (ROOT / "primary" / filename).exists() or any(row["url"] == url for row in captures):
            raise RuntimeError("Existing raw IR capture cannot be overwritten")
        item = {"url": url, "retrieval_started_at": datetime.now(timezone.utc).isoformat()}
        body = None
        try:
            if urlsplit(url).hostname in blocked_hosts:
                raise ValueError("Publisher batch blocked by earlier response")
            budget.reserve(url)
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            with opener.open(request, timeout=15) as response:
                content = response.read(MAX_BYTES + 1)
                if len(content) > MAX_BYTES:
                    raise ValueError("IR capture exceeds two-megabyte bound")
                allowed_url(response.geturl())
                item.update(http_status=response.status, resolved_url=response.geturl(),
                            content_type=response.headers.get("Content-Type"),
                            http_date=response.headers.get("Date"),
                            last_modified=response.headers.get("Last-Modified"))
            path = ROOT / "primary" / filename
            path.write_bytes(content)
            body = content
            item.update(path=str(path.relative_to(ROOT)), size_bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest(), capture_kind="RAW_HTTP_RESPONSE_BODY")
        except HTTPError as error:
            item.update(http_status=error.code, failure_reason=str(error))
            if error.code in {401, 403, 429}:
                blocked_hosts.add(urlsplit(url).hostname)
        except Exception as error:
            item["failure_reason"] = f"{type(error).__name__}: {error}"
        item["observed_at"] = datetime.now(timezone.utc).isoformat()
        captures.append(item)
        manifest.write_text(json.dumps({
            "schema_version": 1, "capture_method": "bounded urllib HTTPS; exact Microsoft hosts; shared redirect budget",
            "max_requests": MAX_REQUESTS, "max_bytes_per_capture": MAX_BYTES, "timeout_seconds": 15,
            "http_requests": budget.requests, "request_count": len(budget.requests),
            "captures": captures, "blocked_hosts": sorted(blocked_hosts),
            "request_user_agent": USER_AGENT,
            "limits": ["one bounded feed/page traversal, not complete publisher coverage",
                       "capture clocks are actual availability, not RSS publication dates",
                       "Microsoft host/brand provenance does not verify dated issuer/instrument identity"],
        }, indent=2) + "\n", encoding="utf-8")
        print(filename, item.get("http_status"), item.get("size_bytes"), item.get("failure_reason", ""), flush=True)
        return body, item

    if append:
        authority = (ROOT / "primary/ir_authority.html").read_text(encoding="utf-8")
        parsed = feedparser.parse((ROOT / "primary/press_releases_feed.xml").read_bytes())
        linked = next((entry for entry in parsed.entries[:30]
                       if str(entry.get("link", "")) in authority
                       and str(entry.get("link", "")) not in {row["url"] for row in captures}), None)
        linked_url = str(linked["link"]) if linked is not None else None
        if linked_url is None:
            class Links(HTMLParser):
                def __init__(self):
                    super().__init__(); self.links = []
                def handle_starttag(self, tag, attrs):
                    if tag == "a": self.links.append(dict(attrs))
            parser = Links(); parser.feed(authority)
            titles = {str(entry.get("title", "")).casefold() for entry in parsed.entries[:30]}
            linked_url = next((link.get("href") for link in parser.links
                               if str(link.get("aria-label", "")).casefold() in titles
                               and link.get("href") == "https://aka.ms/AA13ep0o"), None)
        if linked_url is None:
            raise RuntimeError("No unfetched actual feed entry has a captured IR-page direct/approved short link")
        capture("ir_linked_announcement.html", linked_url)
        return int(any("failure_reason" in row for row in captures))
    capture("ir_authority.html", "https://www.microsoft.com/en-us/investor/default")
    feed, feed_capture = capture("press_releases_feed.xml", "https://news.microsoft.com/source/tag/press-releases/feed/")
    if feed is None:
        return 1
    parsed = feedparser.parse(feed)
    entries = list(parsed.entries)[:30]
    selected = next((entry for entry in entries
                     if any(word in str(entry.get("title", "")).casefold()
                            for word in ("earnings", "dividend", "financial", "quarter"))), entries[0] if entries else None)
    if selected is None:
        (ROOT / "feed_selection.json").write_text(json.dumps({
            "status": "PARTIAL", "reason": "No entry in bounded actual publisher feed", "parser_bozo": bool(parsed.bozo),
        }, indent=2) + "\n", encoding="utf-8")
        return 1
    url = str(selected.get("link", ""))
    (ROOT / "feed_selection.json").write_text(json.dumps({
        "status": "ACTUAL_FEED_LEAD", "title": str(selected.get("title", "")), "original_url": url,
        "published_raw": str(selected.get("published", "")), "feed_url": feed_capture["resolved_url"],
        "feed_observed_at": feed_capture["observed_at"], "entries_in_bounded_capture": len(entries),
        "feed_parser_bozo": bool(parsed.bozo), "all_publisher_history_complete": False,
    }, indent=2) + "\n", encoding="utf-8")
    capture("announcement.html", url)
    return int(any("failure_reason" in row for row in captures))


if __name__ == "__main__":
    raise SystemExit(main())
