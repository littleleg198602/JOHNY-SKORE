"""One reserved primary SEC research transport; no DB, redirects or retries."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

ROOT = Path(__file__).resolve().parent
CAP = 4_000_000
TIMEOUT = 25
UA = "JOHNY-SKORE primary-evidence-research/1.0 (public project contact: github.com/littleleg198602/JOHNY-SKORE)"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        stream.flush()
        import os
        os.fsync(stream.fileno())


def _url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in {None, 443} or "%" in parsed.path or ".." in parsed.path):
        raise ValueError("Only preplanned canonical primary SEC references are supported")
    if (parsed.hostname == "data.sec.gov" and parsed.path == "/submissions/CIK0001513142.json"
            or parsed.hostname == "www.sec.gov" and re.fullmatch(r"/Archives/edgar/data/789019/[0-9]{18}/[A-Za-z0-9_.-]{1,200}", parsed.path)):
        return url
    raise ValueError("Primary research is limited to the exact MSFT/Nadella source scope")


def capture(url, name, purpose):
    _url(url)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,100}\.(?:json|html|htm|txt|xml)", name):
        raise ValueError("Select one safe original capture filename")
    plan_raw = (ROOT / "research_plan.json").read_bytes()
    plan = json.loads(plan_raw)
    if (plan["maximum_transports"] != 6 or plan["per_transport_maximum_body_bytes"] != CAP
            or plan["per_transport_absolute_timeout_seconds"] != TIMEOUT
            or plan["redirects_or_retries_allowed"] or purpose not in plan["allowed_purposes"]
            or (ROOT / "research_stop.json").exists()):
        raise ValueError("Original research plan or stop condition blocks this transport")
    if not hasattr(signal, "setitimer"):
        raise RuntimeError("This one-off research requires an enforceable POSIX absolute deadline")
    reservations = sorted(ROOT.glob("attempt-*-reserved.json"))
    if len(reservations) >= 6:
        raise ValueError("The original bounded six-transport research packet is exhausted")
    sequence = len(reservations) + 1
    reserved = datetime.now(timezone.utc)
    reservation = {"sequence":sequence,"purpose":purpose,"url":url,"reserved_at":reserved.isoformat(),
        "research_plan_sha256":hashlib.sha256(plan_raw).hexdigest(),"maximum_original_body_bytes":CAP,
        "absolute_timeout_seconds":TIMEOUT,"absolute_deadline_enforcement":"POSIX_SETITIMER",
        "user_agent":UA,"redirects_or_retries_allowed":False,"application_collector_or_quota_db_used":False}
    _write(ROOT / f"attempt-{sequence:02d}-reserved.json", reservation)
    def deadline(signum, frame):
        raise TimeoutError("The reserved complete-body primary SEC deadline elapsed")
    handler = signal.signal(signal.SIGALRM, deadline)
    status = None; chunks = []; count = 0; result = dict(reservation)
    started = time.perf_counter()
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
    try:
        # One manually bounded transport; no application collector API, ledger,
        # automatic retry or redirect. Sequential invocations exceed the .2s
        # minimum cadence even when a transport fails immediately.
        time.sleep(.2)
        try:
            response = build_opener(NoRedirect()).open(Request(url, headers={"User-Agent":UA,"Accept-Encoding":"identity"}), timeout=TIMEOUT)
        except HTTPError as error:
            response = error
        with response:
            status = response.code
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > CAP:
                raise ValueError("Declared primary body exceeds its pre-reserved cap")
            while True:
                remaining = TIMEOUT - (time.perf_counter() - started)
                if remaining <= 0: raise TimeoutError("Primary complete-body deadline elapsed")
                wire = response.fp if isinstance(response, HTTPError) else response
                sock = getattr(getattr(getattr(wire, "fp", None), "raw", None), "_sock", None)
                if sock is not None: sock.settimeout(remaining)
                chunk = getattr(response, "read1", response.read)(min(65_536, CAP + 1 - count))
                if not chunk: break
                chunks.append(chunk); count += len(chunk)
                if count > CAP: raise ValueError("Primary body exceeds its pre-reserved cap")
            body = b"".join(chunks)
            if length.isdigit() and int(length) != len(body):
                raise ValueError("Primary body did not match its declared complete length")
            result.update(http_status=status, complete_original_body_received=True,
                observed_at=datetime.now(timezone.utc).isoformat(), size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(), content_type=response.headers.get("Content-Type"),
                http_date=response.headers.get("Date"), last_modified=response.headers.get("Last-Modified"))
            encoded = response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}
        sensitive = _has_sensitive_body(body)
        result["retention"] = "WITHHELD_SENSITIVE_BODY" if sensitive else "WITHHELD_ENCODED_BODY" if encoded else "ORIGINAL_BYTES"
        if not sensitive and not encoded:
            (ROOT / "primary").mkdir(exist_ok=True)
            with (ROOT / "primary" / name).open("xb") as stream:
                stream.write(body); stream.flush()
                import os
                os.fsync(stream.fileno())
            result["path"] = "primary/" + name
    except Exception as error:
        result.update(http_status=status, complete_original_body_received=False,
            observed_at=datetime.now(timezone.utc).isoformat(), retention="NOT_CAPTURED_COMPLETE_BODY",
            failure_type=type(error).__name__, received_prefix_bytes=count,
            received_prefix_sha256=hashlib.sha256(b"".join(chunks)).hexdigest())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, handler)
    result["elapsed_seconds"] = time.perf_counter() - started
    _write(ROOT / f"attempt-{sequence:02d}-result.json", result)
    if status in {401, 403, 429}:
        _write(ROOT / "research_stop.json", {"recorded_at":datetime.now(timezone.utc).isoformat(),
            "http_status":status,"reason":"PROVIDER_BLOCK_OR_COOLDOWN_NO_FURTHER_TRANSPORTS"})
    print(json.dumps({key:result.get(key) for key in ("sequence","http_status","retention","size_bytes","sha256","failure_type")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True); parser.add_argument("--name", required=True); parser.add_argument("--purpose", required=True)
    args = parser.parse_args(); capture(args.url, args.name, args.purpose)
