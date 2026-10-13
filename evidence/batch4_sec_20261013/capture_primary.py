"""Bounded immutable SEC capture for one Form 4 exact identity review.

No credentials, registry writes, backdating or automatic identity promotion.
Run once from the repository root; HTTP 401/403/429 ends the publisher batch.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
MAX_BYTES = 2_000_000
USER_AGENT = ("JOHNY-SKORE primary-evidence-research/1.0 "
              "(public project contact: github.com/littleleg198602/JOHNY-SKORE)")
SOURCES = (
    ("msft_form4_index.html", "https://www.sec.gov/Archives/edgar/data/789019/000078901926000161/0000789019-26-000161-index.html"),
    ("msft_form4.xml", "https://www.sec.gov/Archives/edgar/data/789019/000078901926000161/form4.xml"),
    ("nadella_submissions.json", "https://data.sec.gov/submissions/CIK0001513142.json"),
    ("msft_submissions.json", "https://data.sec.gov/submissions/CIK0000789019.json"),
    ("msft_2026_R1.htm", "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/R1.htm"),
)


class SecRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if (new.scheme != "https" or new.hostname != old.hostname
                or new.username or new.password or new.port not in {None, 443}
                or new.query or new.fragment):
            raise ValueError("Unexpected SEC redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    path = ROOT / "captures.json"
    if path.exists():
        raise RuntimeError("Capture manifest already exists; use a new dated directory")
    (ROOT / "primary").mkdir(parents=True, exist_ok=True)
    opener = build_opener(SecRedirect())
    captures = []
    blocked = False
    for filename, url in SOURCES:
        item = {"url": url, "retrieval_started_at": datetime.now(timezone.utc).isoformat()}
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            with opener.open(request, timeout=30) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("SEC response exceeds identity review capture byte limit")
                item.update(http_status=response.status, resolved_url=response.geturl(),
                            content_type=response.headers.get("Content-Type"),
                            http_date=response.headers.get("Date"),
                            last_modified=response.headers.get("Last-Modified"))
            saved = ROOT / "primary" / filename
            saved.write_bytes(body)
            item.update(path=str(saved.relative_to(ROOT)), size_bytes=len(body),
                        sha256=hashlib.sha256(body).hexdigest(),
                        capture_kind="RAW_HTTP_RESPONSE_BODY")
        except HTTPError as error:
            item.update(http_status=error.code, failure_reason=str(error))
            blocked = error.code in {401, 403, 429}
        except Exception as error:
            item["failure_reason"] = f"{type(error).__name__}: {error}"
        item["observed_at"] = datetime.now(timezone.utc).isoformat()
        captures.append(item)
        path.write_text(json.dumps({
            "schema_version": 1, "capture_method": "bounded urllib HTTPS; identity encoding; SEC redirects only",
            "request_user_agent": USER_AGENT, "captures": captures, "publisher_batch_blocked": blocked,
            "limits": ["capture time is knowledge time, not historical availability",
                       "HTTP date/Last-Modified are retained, not substituted for filing publication",
                       "a filing index is the same disclosure, not independent owner or instrument evidence"],
        }, indent=2) + "\n", encoding="utf-8")
        print(filename, item.get("http_status"), item.get("size_bytes"), item.get("failure_reason", ""), flush=True)
        if blocked:
            break
    return int(blocked or any("failure_reason" in item for item in captures))


if __name__ == "__main__":
    raise SystemExit(main())
