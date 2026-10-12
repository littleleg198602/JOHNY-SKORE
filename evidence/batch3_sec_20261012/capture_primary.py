"""Capture bounded primary source bytes; no registry or application mutations.

Run from the repository root. This capture script records actual local UTC
observation clocks and unmodified response bytes. An access/rate-limit response
stops that publisher's batch; it is never retried or replaced with a fixture.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
PRIMARY = ROOT / "primary"
MAX_BYTES = 2_000_000
USER_AGENT = (
    "JOHNY-SKORE primary-evidence-research/1.0 "
    "(public project contact: github.com/littleleg198602/JOHNY-SKORE)"
)
SOURCES = [
    ("msft_2026_index.html", "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/0001193125-26-323660-index.htm"),
    ("msft_2026_R1.htm", "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/R1.htm"),
    ("lmt_2025_index.html", "https://www.sec.gov/Archives/edgar/data/936468/000162828026004195/0001628280-26-004195-index.html"),
    ("lmt_2025_R1.htm", "https://www.sec.gov/Archives/edgar/data/936468/000162828026004195/R1.htm"),
    ("lmt_2025_ex21.htm", "https://www.sec.gov/Archives/edgar/data/936468/000162828026004195/ex21q42025.htm"),
    ("aapl_2025_index.html", "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"),
    ("aapl_2025_R1.htm", "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/R1.htm"),
    ("nvda_2026_index.html", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/0001045810-26-000021-index.htm"),
    ("nvda_2026_R1.htm", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/R1.htm"),
    ("nvda_2026_10k.htm", "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"),
    ("sikorsky_gleif.json", "https://api.gleif.org/api/v1/lei-records/54930054HRQM5ZB1WZ32"),
    ("msft_submissions.json", "https://data.sec.gov/submissions/CIK0000789019.json"),
    ("lmt_submissions.json", "https://data.sec.gov/submissions/CIK0000936468.json"),
    ("aapl_submissions.json", "https://data.sec.gov/submissions/CIK0000320193.json"),
    ("nvda_submissions.json", "https://data.sec.gov/submissions/CIK0001045810.json"),
]


class SamePublisherRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if (new.scheme != "https" or new.hostname != old.hostname
                or new.username or new.password or new.port not in {None, 443}):
            raise ValueError("Cross-publisher redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main() -> int:
    manifest = ROOT / "captures.json"
    if manifest.exists() and "--append-missing" not in sys.argv:
        raise RuntimeError("Captures are immutable: use a new dated directory")
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    PRIMARY.mkdir(parents=True, exist_ok=True)
    opener = build_opener(SamePublisherRedirect())
    captures = previous.get("captures", [])
    blocked = set(previous.get("blocked_publishers", []))
    captured_urls = {item["url"] for item in captures}
    for filename, url in SOURCES:
        host = urlsplit(url).hostname
        if host in blocked or url in captured_urls:
            continue
        started = datetime.now(timezone.utc).isoformat()
        item = {"url": url, "retrieval_started_at": started}
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            with opener.open(request, timeout=30) as response:
                content = response.read(MAX_BYTES + 1)
                observed = datetime.now(timezone.utc).isoformat()
                if len(content) > MAX_BYTES:
                    raise ValueError("Primary record exceeds capture byte limit")
                item.update(http_status=response.status, resolved_url=response.geturl(),
                            observed_at=observed, content_type=response.headers.get("Content-Type"),
                            http_date=response.headers.get("Date"),
                            last_modified=response.headers.get("Last-Modified"))
                path = PRIMARY / filename
                path.write_bytes(content)
                item.update(path=str(path.relative_to(ROOT)), size_bytes=len(content),
                            sha256=hashlib.sha256(content).hexdigest(), capture_kind="RAW_HTTP_RESPONSE_BODY")
        except HTTPError as error:
            item.update(http_status=error.code, observed_at=datetime.now(timezone.utc).isoformat(),
                        failure_reason=str(error))
            if error.code in {401, 403, 429}:
                blocked.add(host)
        except Exception as error:
            item.update(observed_at=datetime.now(timezone.utc).isoformat(),
                        failure_reason=f"{type(error).__name__}: {error}")
        captures.append(item)
        print(filename, item.get("http_status"), item.get("size_bytes"), item.get("failure_reason", ""), flush=True)
        (ROOT / "captures.json").write_text(json.dumps({
            "schema_version": 1, "capture_finished_at": datetime.now(timezone.utc).isoformat(),
            "capture_method": "urllib HTTPS; identity encoding; bounded bytes; same-publisher redirects only",
            "request_user_agent": USER_AGENT,
            "captures": captures, "blocked_publishers": sorted(blocked),
        }, indent=2) + "\n", encoding="utf-8")
    return 1 if blocked or any("failure_reason" in item for item in captures) else 0


if __name__ == "__main__":
    sys.exit(main())
