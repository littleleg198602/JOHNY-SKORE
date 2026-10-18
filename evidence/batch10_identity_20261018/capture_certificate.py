"""One pre-reserved SEC certificate image transport; no retry or fallback."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body, _safe_header

HERE = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def main():
    plan_raw = (HERE / "request_plan.json").read_bytes()
    plan = json.loads(plan_raw)
    url = plan["requested_url"]
    assert url == "https://www.sec.gov/Archives/edgar/data/1474432/000147443226000027/image_0.jpg"
    assert plan["max_transports"] == 1 and plan["timeout_seconds"] == 25 and plan["max_bytes"] == 4_000_000
    captures = HERE / "captures"
    captures.mkdir(exist_ok=True)
    reserved = {"attempt": 1, "requested_url": url, "started_at": now(), "status": "REQUEST_RESERVED",
                "method": "GET", "timeout_seconds": 25, "max_bytes": 4_000_000,
                "redirects_allowed": False, "request_plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
                "research_only": True, "app_quota_managed_live_claimed": False,
                "historical_request_ledger_reconstructed": False, "access_values_exported": False}
    with (captures / "attempt-01.reserved.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reserved, sort_keys=True, indent=2) + "\n")
    result, began, raw = dict(reserved), time.monotonic(), None
    try:
        request = Request(url, headers={"User-Agent": "JOHNY-SKORE primary-evidence-research/1.0 (public repository: littleleg198602/JOHNY-SKORE)",
                                        "Accept-Encoding": "identity", "Accept": "image/jpeg"}, method="GET")
        try:
            response = build_opener(NoRedirect()).open(request, timeout=25)
        except HTTPError as error:
            response = error
        with response:
            result.update(http_status=int(response.code), final_url=response.geturl(), response_headers={
                key: _safe_header(response.headers.get(key)) for key in ("Date", "Last-Modified", "Content-Type", "Content-Length", "Content-Encoding")})
            parts, size = [], 0
            while True:
                remaining = 25 - (time.monotonic() - began)
                if remaining <= 0:
                    raise TimeoutError("Certificate complete-body deadline exceeded")
                wire = response.fp if isinstance(response, HTTPError) else response
                sock = getattr(getattr(getattr(wire, "fp", None), "raw", None), "_sock", None)
                if sock is not None:
                    sock.settimeout(remaining)
                chunk = getattr(response, "read1", response.read)(min(65_536, 4_000_001 - size))
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
                if size > 4_000_000:
                    raise ValueError("Certificate response byte cap exceeded")
            raw = b"".join(parts)
            result.update(observed_at=now(), bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) != len(raw):
                raise ValueError("Incomplete original certificate response")
            if time.monotonic() - began > 25:
                raise TimeoutError("Certificate complete-body deadline exceeded")
            if result["http_status"] != 200 or result["final_url"] != url or not raw:
                result["status"] = "WITHHELD_UNSUPPORTED_RESPONSE"
            elif response.headers.get("Content-Encoding", "identity").casefold() != "identity":
                result["status"] = "WITHHELD_ENCODED_BODY"
            elif _has_sensitive_body(raw):
                result["status"] = "WITHHELD_SENSITIVE_BODY"
            else:
                path = captures / (result["raw_sha256"] + (".jpg" if raw.startswith(b"\xff\xd8\xff") else ".bin"))
                with path.open("xb") as stream:
                    stream.write(raw)
                result.update(status="PUBLIC_ORIGINAL_BYTES_CAPTURED", body_path=path.name)
    except Exception as error:
        result.update(status="CAPTURE_FAILED", observed_at=now(), error_type=type(error).__name__)
    finally:
        raw = None
        result.setdefault("http_status", None)
        result.setdefault("observed_at", now())
        result["elapsed_seconds"] = time.monotonic() - began
        with (captures / "attempt-01.metadata.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(json.dumps({key: result.get(key) for key in ("status", "http_status", "bytes", "raw_sha256", "observed_at")}))


if __name__ == "__main__":
    main()
