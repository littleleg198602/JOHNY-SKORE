"""Explicit bounded Yahoo chart acquisition for normal captured market inputs.

Raw response bytes remain the parent evidence. Frame archives are a declared
direct mapping of original quote arrays, never adjusted-price reconstruction.
New observations cannot date publisher revisions or historical availability.
Nothing runs or writes until both ``enabled`` and ``execute`` are true.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import pandas as pd

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.captured_market_data_provider import (
    BENCHMARK_TICKERS, MANIFEST_SCHEMA_VERSION, CapturedMarketDataProvider,
    build_captured_market_data_entry,
)
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.services.prospective_specialist_history import _no_symlink, _read_json
from market_checker_app.services.us_equity_calendar_service import (
    _nyse_calendar, last_completed_session, sessions_between,
)
from market_checker_app.services.yahoo_price_provenance_service import (
    _access_value, capture_provider_frame, persist_observation,
    utc_clock, validate_observation,
)


VERSION = "captured_yahoo_chart_acquisition_v1"
PARSER_VERSION = "direct_yahoo_chart_daily_quote_arrays_v1"
FRAME_SERIALIZATION = "ROUND_TRIP_JSON_NUMBERS_NO_PRICE_ROUNDING"
ALLOWED_TICKERS = ("AAPL", *BENCHMARK_TICKERS)
MAX_REQUESTS = 13
MAX_BODY_BYTES = 2_000_000
SESSION_COUNT = 66
ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/"


def physical_clock():
    return datetime.now(timezone.utc)


def _encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _write_once(path: Path, body: bytes, *, exclusive=False):
    _no_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_symlink(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(body); handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if exclusive:
                raise ValueError("MARKET_ACQUISITION_PLAN_ALREADY_CLAIMED") from None
            if path.read_bytes() != body:
                raise ValueError("IMMUTABLE_MARKET_ACQUISITION_CONFLICT") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _url(ticker, sessions):
    query = {"period1": int(pd.Timestamp(sessions[0], tz="UTC").timestamp()),
        "period2": int((pd.Timestamp(sessions[-1], tz="UTC") + pd.Timedelta(days=1)).timestamp()),
        "interval": "1d", "events": "div,splits,capitalGains",
        "includeAdjustedClose": "true", "includePrePost": "false"}
    return ENDPOINT + ticker + "?" + urlencode(query)


def _exact_frame_json(frame):
    # Per-column lists preserve integer volume too. A heterogeneous ndarray or
    # pandas' fixed decimal JSON precision could silently alter original values.
    columns = list(frame.columns)
    body = {"columns": columns, "index": [value.isoformat() for value in frame.index],
        "data": [list(row) for row in zip(*(frame[column].tolist() for column in columns))]}
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


def _http_get(url, max_bytes):
    # A fresh opener has no cookies, auth handler, retries, environment proxy
    # credentials or redirect traversal. Only this exact URL is requested.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(url, headers={"Accept": "application/json",
        "Accept-Encoding": "identity", "User-Agent": "JOHNY-SKORE-public-market-input/1.0"})
    try:
        response = opener.open(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(max_bytes + 1)
        return {"status": response.code, "response_url": response.geturl(), "body": body,
            "content_encoding": response.headers.get("Content-Encoding", "identity"),
            "content_type": response.headers.get("Content-Type", ""),
            "body_complete_within_byte_budget": len(body) <= max_bytes}


def _json(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY_BYTES:
        raise ValueError("ORIGINAL_CHART_BODY_BYTE_LIMIT")
    if _has_sensitive_body(body):
        raise ValueError("WITHHELD_UNSAFE_WHOLE_CHART_BODY")
    # Inspect lexical whole bytes as well as decoded JSON fields. No unsafe
    # complete body is retained merely because its JSON is malformed.
    if _access_value(body.decode("utf-8", errors="replace")):
        raise ValueError("WITHHELD_UNSAFE_WHOLE_CHART_BODY")
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_ORIGINAL_CHART_FIELD")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("NONFINITE_ORIGINAL_CHART_VALUE")
    try:
        record = json.loads(body.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
        if _access_value(record):
            raise ValueError("WITHHELD_UNSAFE_WHOLE_CHART_BODY")
    except (UnicodeError, RecursionError):
        raise ValueError("INVALID_ORIGINAL_CHART_JSON") from None
    if not isinstance(record, dict):
        raise ValueError("INVALID_ORIGINAL_CHART_JSON")
    return record


def parse_original_chart(body: bytes, *, ticker: str, expected_sessions: list[str],
                         observed_at: datetime) -> tuple[pd.DataFrame, dict]:
    """Map exact complete raw OHLCV arrays. Never fill, adjust, shift or pad."""
    if ticker not in ALLOWED_TICKERS:
        raise ValueError("OUTSIDE_EXPLICIT_MARKET_ACQUISITION_SCOPE")
    observed = utc_clock(observed_at)
    record = _json(body)
    chart = record.get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise ValueError("YAHOO_CHART_ERROR_OR_MISSING_RESULT")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError("AMBIGUOUS_OR_MISSING_CHART_RESULT")
    result = results[0]; meta = result.get("meta")
    if (not isinstance(meta, dict) or meta.get("symbol") != ticker
            or meta.get("exchangeTimezoneName") != "America/New_York" or meta.get("currency") != "USD"
            or meta.get("instrumentType") != ("EQUITY" if ticker == "AAPL" else "ETF")
            or meta.get("exchangeName") != ("NMS" if ticker == "AAPL" else "PCX")
            or meta.get("dataGranularity") != "1d"):
        raise ValueError("FOREIGN_OR_AMBIGUOUS_CHART_INSTRUMENT")
    latest = last_completed_session(observed)
    expected = [value.date().isoformat() for value in sessions_between(
        latest - pd.Timedelta(days=400), latest)[-SESSION_COUNT:]] if latest is not None else []
    if expected_sessions != expected or len(expected) != SESSION_COUNT:
        raise ValueError("STALE_OR_SHIFTED_ACQUISITION_SESSION_WINDOW")
    stamps = result.get("timestamp")
    if (not isinstance(stamps, list) or len(stamps) != SESSION_COUNT
            or any(type(value) is not int for value in stamps)):
        raise ValueError("INCOMPLETE_OR_INVALID_CHART_SESSIONS")
    index = pd.to_datetime(stamps, unit="s", utc=True)
    schedule = _nyse_calendar().schedule(start_date=expected[0], end_date=expected[-1])
    if tuple(index) != tuple(schedule.market_open):
        raise ValueError("DUPLICATE_OR_SHIFTED_CHART_SESSIONS")
    if pd.Timestamp(observed) < schedule.iloc[-1].market_close:
        raise ValueError("CHART_SESSION_NOT_COMPLETED_WHEN_OBSERVED")
    indicators = result.get("indicators")
    if not isinstance(indicators, dict):
        raise ValueError("MISSING_ORIGINAL_CHART_OHLCV")
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], dict):
        raise ValueError("AMBIGUOUS_OR_MISSING_CHART_QUOTE")
    quote = quotes[0]
    if set(quote) != {"open", "high", "low", "close", "volume"}:
        raise ValueError("MISSING_OR_UNSUPPORTED_ORIGINAL_CHART_OHLCV")
    values = {name.title(): quote[name] for name in ("open", "high", "low", "close", "volume")}
    if "adjclose" in indicators:
        adjusted = indicators["adjclose"]
        if (not isinstance(adjusted, list) or len(adjusted) != 1
                or not isinstance(adjusted[0], dict) or set(adjusted[0]) != {"adjclose"}):
            raise ValueError("INVALID_ORIGINAL_ADJUSTED_CLOSE_ARRAY")
        values["Adj Close"] = adjusted[0]["adjclose"]
    for column, array in values.items():
        if (not isinstance(array, list) or len(array) != SESSION_COUNT
                or any(type(value) not in (int, float) or not math.isfinite(value)
                    or value < 0 or column != "Volume" and value == 0 for value in array)):
            raise ValueError("INCOMPLETE_OR_NONFINITE_ORIGINAL_CHART_ARRAY")
    frame = pd.DataFrame(values, index=index)
    if ((frame.High < frame[["Open", "Close", "Low"]].max(axis=1)).any()
            or (frame.Low > frame[["Open", "Close", "High"]].min(axis=1)).any()):
        raise ValueError("INCONSISTENT_ORIGINAL_CHART_OHLCV")
    events = result.get("events")
    if events is not None and (not isinstance(events, dict) or set(events) - {"dividends", "splits", "capitalGains"}):
        raise ValueError("UNSUPPORTED_ORIGINAL_CHART_ACTION_FIELDS")
    if events is not None and (len(_encoded(events)) > 64_000
            or any(not isinstance(value, dict) or len(value) > SESSION_COUNT for value in events.values())):
        raise ValueError("UNBOUNDED_OR_INVALID_ORIGINAL_CHART_ACTION_FIELDS")
    provenance = {"parser_version": PARSER_VERSION,
        "frame_derivation": "DIRECT_ORIGINAL_QUOTE_ARRAY_COLUMN_MAPPING_NO_PRICE_ADJUSTMENT",
        "frame_is_original_http_body": False, "session_timestamps_shifted": False,
        "synthesized_price_or_volume_or_action_values": False,
        "action_fields_status": "AS_RETURNED_EVENT_MAP" if events is not None else "MISSING_IN_RESPONSE",
        "original_action_fields": deepcopy(events), "action_columns_filled_with_zero": False,
        "publisher_revision_timing_status": "UNKNOWN", "publisher_revision_at": None,
        "strict_pit_eligible": False, "historical_acceptance_verified": False}
    return frame, provenance


def verify_original_chart_parent(record: dict, archive_root: Path) -> dict:
    """Re-read one exact raw parent and replay its parser; no HTTP or writes.

    The caller must still enforce the fixture marker and current freshness.
    Original observation clocks are checked, never replaced by this read time.
    A hash is not independent publisher authentication or revision knowledge.
    """
    record = validate_observation(record)
    if (record.get("evidence_origin") not in {"CAPTURED_YAHOO_CHART_REPLAY", "DETERMINISTIC_TEST_FIXTURE"}
            or record.get("kind") != "PROVIDER_RETURNED_FRAME" or record.get("provider") != "yfinance"
            or record.get("ticker") not in ALLOWED_TICKERS or record.get("provider_symbol") != record["ticker"]
            or record.get("interval") != "1d" or record.get("methodology") != "PROVIDER_VALUES_AS_RETURNED"
            or record.get("actions_requested") is not True or record.get("auto_adjust_requested") is not False
            or record.get("synthesized_columns") != []):
        raise ValueError("UNSUPPORTED_ORIGINAL_CHART_OBSERVATION")
    root = Path(archive_root); _no_symlink(root)
    if not root.is_dir():
        raise ValueError("ORIGINAL_CHART_ARCHIVE_ROOT_UNAVAILABLE")
    root = root.resolve(); parent = record.get("raw_http_parent")
    fields = {"request_url", "response_url", "raw_path", "body_sha256", "body_bytes", "attempted_at",
        "observed_at", "available_at", "archived_at", "http_status"}
    if not isinstance(parent, dict) or set(parent) != fields:
        raise ValueError("INVALID_ORIGINAL_CHART_PARENT_PIN")
    relative = parent["raw_path"]
    if (not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", relative)
            or PurePosixPath(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("ORIGINAL_CHART_PARENT_OUTSIDE_ARCHIVE_ROOT")
    path = root.joinpath(*PurePosixPath(relative).parts); _no_symlink(path)
    if (not path.resolve().is_relative_to(root) or not path.is_file()
            or type(parent["body_bytes"]) is not int or not 0 < parent["body_bytes"] <= MAX_BODY_BYTES
            or path.name != f"response_{record['ticker']}_{parent['body_sha256']}.raw"
            or path.stat().st_size != parent["body_bytes"]):
        raise ValueError("ORIGINAL_CHART_PARENT_HASH_OR_BYTE_MISMATCH")
    with path.open("rb") as handle:
        body = handle.read(MAX_BODY_BYTES + 1)
    if (len(body) != parent["body_bytes"] or hashlib.sha256(body).hexdigest() != parent["body_sha256"]
            or record.get("upstream_response_sha256") != parent["body_sha256"]):
        raise ValueError("ORIGINAL_CHART_PARENT_HASH_OR_BYTE_MISMATCH")
    def clock(key):
        try:
            return utc_clock(datetime.fromisoformat(parent[key]))
        except (ValueError, TypeError):
            raise ValueError("INVALID_ORIGINAL_CHART_PARENT_CLOCK") from None
    attempted, observed, available, archived = (clock(key) for key in
        ("attempted_at", "observed_at", "available_at", "archived_at"))
    captured = utc_clock(datetime.fromisoformat(record["captured_at"]))
    if (not attempted <= observed == available <= archived <= captured <= physical_clock()
            or record.get("started_at") != parent["attempted_at"]
            or record["observed_at"] != parent["observed_at"] or parent["http_status"] != 200):
        raise ValueError("ORIGINAL_CHART_PARENT_KNOWLEDGE_CLOCK_CONFLICT")
    frame_body = json.loads(record["frame_json"])
    sessions = [pd.Timestamp(value).date().isoformat() for value in frame_body["index"]]
    if parent["request_url"] != _url(record["ticker"], sessions) or parent["response_url"] != parent["request_url"]:
        raise ValueError("ORIGINAL_CHART_PARENT_REQUEST_BINDING_CONFLICT")
    frame, parser = parse_original_chart(body, ticker=record["ticker"], expected_sessions=sessions, observed_at=observed)
    if (_exact_frame_json(frame) != record["frame_json"] or parser != record.get("parser")
            or record.get("frame_serialization") != FRAME_SERIALIZATION or len(frame) != record.get("row_count")):
        raise ValueError("ORIGINAL_CHART_PARENT_FRAME_REPLAY_MISMATCH")
    return {"status": "VERIFIED_CONTENT_REPLAY", "raw_http_parent": deepcopy(parent),
        "frame_sha256": record["frame_sha256"], "parser_version": PARSER_VERSION,
        "publisher_revision_timing_status": "UNKNOWN", "strict_pit_eligible": False,
        "independent_publisher_authenticity_verified": False, "original_clocks_preserved": True}


class CapturedMarketInputAcquisition:
    """Default OFF; explicit scope, immutable archives and no hidden fallback.

    Injected clocks/transports are fixture-only and mark every resulting frame.
    The normal transport uses actual UTC completion clocks, not Yahoo metadata.
    """

    def __init__(self, *, archive_root: Path, enabled: bool = False,
                 observation_clock: Callable[[], datetime] | None = None,
                 transport: Callable | None = None):
        if type(enabled) is not bool:
            raise ValueError("EXPLICIT_ACQUISITION_ENABLE_BOOLEAN_REQUIRED")
        self.archive_root = Path(archive_root)
        self.enabled = enabled
        self.fixture_only = observation_clock is not None or transport is not None
        self.clock = observation_clock or physical_clock
        self.transport = transport or _http_get
        self.high_water = None

    def _now(self):
        now = utc_clock(self.clock())
        if self.high_water is not None and now < self.high_water:
            raise ValueError("MARKET_ACQUISITION_PHYSICAL_CLOCK_ROLLBACK")
        self.high_water = now
        return now

    def plan(self, tickers=ALLOWED_TICKERS):
        tickers = list(islice(iter(tickers), MAX_REQUESTS + 1))
        if (not 1 <= len(tickers) <= MAX_REQUESTS or len(set(tickers)) != len(tickers)
                or any(ticker not in ALLOWED_TICKERS for ticker in tickers)):
            raise ValueError("OUTSIDE_EXPLICIT_MARKET_ACQUISITION_SCOPE")
        now = self._now(); latest = last_completed_session(now)
        if latest is None:
            raise ValueError("LATEST_COMPLETED_NYSE_SESSION_UNAVAILABLE")
        sessions = [value.date().isoformat() for value in sessions_between(
            latest - pd.Timedelta(days=400), latest)[-SESSION_COUNT:]]
        plan = {"version": VERSION, "planned_at": now.isoformat(), "fixture_only": self.fixture_only,
            "tickers": tickers, "expected_sessions": sessions,
            "latest_completed_session": latest.date().isoformat(),
            "request_budget": len(tickers), "byte_budget_per_response": MAX_BODY_BYTES,
            "maximum_total_response_bytes": len(tickers) * MAX_BODY_BYTES,
            "requests": [{"ticker": ticker, "method": "GET", "url": _url(ticker, sessions)} for ticker in tickers],
            "retries": 0, "redirects": 0, "fallback_requests": 0,
            "publisher_revision_timing_status": "UNKNOWN", "strict_pit_eligible": False}
        plan["plan_id"] = canonical_hash(plan)
        return plan

    def _validate_plan(self, plan, now):
        if not isinstance(plan, dict) or plan.get("plan_id") != canonical_hash({k:v for k,v in plan.items() if k != "plan_id"}):
            raise ValueError("ACQUISITION_PLAN_CONTENT_MISMATCH")
        expected = self.plan(plan.get("tickers", ()))
        copied = deepcopy(plan); copied["planned_at"] = expected["planned_at"]
        copied["plan_id"] = canonical_hash({k:v for k,v in copied.items() if k != "plan_id"})
        if copied != expected or utc_clock(datetime.fromisoformat(plan["planned_at"])) > now:
            raise ValueError("STALE_OR_CHANGED_EXPLICIT_ACQUISITION_PLAN")

    def acquire(self, plan: dict, *, execute: bool = False):
        report = {"version": VERSION, "status": "DISABLED", "fixture_only": self.fixture_only,
            "source_http_requests": 0, "raw_captures": 0, "usable_count": 0,
            "manifest_path": None, "rows": [], "strict_pit_eligible": False,
            "forecasts_created": 0, "labels_created": 0, "db_writes": 0,
            "prospective_audit_implicitly_enabled": False}
        if type(execute) is not bool:
            raise ValueError("EXPLICIT_ACQUISITION_EXECUTE_BOOLEAN_REQUIRED")
        if not self.enabled or not execute:
            return report
        started = self._now(); self._validate_plan(plan, started)
        _no_symlink(self.archive_root)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self.archive_root = self.archive_root.resolve()
        acquisition_id = canonical_hash({"plan_id": plan["plan_id"], "started_at": started.isoformat()})
        directory = self.archive_root / ("acquisition_" + acquisition_id)
        claim = {"plan_id": plan["plan_id"], "started_at": started.isoformat(),
            "acquisition_id": acquisition_id, "fixture_only": self.fixture_only,
            "report_path": directory.relative_to(self.archive_root).as_posix() + "/acquisition_report.json"}
        claim_path = self.archive_root/"plan_claims"/("claim_" + plan["plan_id"] + ".json")
        try:
            _write_once(claim_path, _encoded(claim), exclusive=True)
        except ValueError as error:
            if str(error) != "MARKET_ACQUISITION_PLAN_ALREADY_CLAIMED":
                raise
            original, _ = _read_json(claim_path, 64_000)
            previous_path = self.archive_root/("acquisition_" + original["acquisition_id"])/"acquisition_report.json"
            if (not re.fullmatch(r"[a-f0-9]{64}", original.get("acquisition_id", ""))
                    or original.get("plan_id") != plan["plan_id"]
                    or original.get("report_path") != previous_path.relative_to(self.archive_root).as_posix()
                    or original.get("fixture_only") != self.fixture_only):
                raise ValueError("INVALID_EXISTING_ACQUISITION_CLAIM") from None
            if previous_path.is_file():
                previous, _ = _read_json(previous_path, 2_000_000)
                if previous.get("plan_id") != plan["plan_id"]:
                    raise ValueError("INVALID_EXISTING_ACQUISITION_REPORT")
                previous["original_source_http_requests"] = previous["source_http_requests"]
                previous.update(source_http_requests=0, replay=True, reason="PLAN_ALREADY_EXECUTED_NO_RETRY")
                return previous
            report.update(status="WAIT_DATA", replay=True, reason="PLAN_ALREADY_CLAIMED_NO_RETRY",
                plan_id=plan["plan_id"], acquisition_id=original["acquisition_id"])
            return report
        _write_once(directory/"plan.json", _encoded(plan))
        report.update(status="WAIT_DATA", plan_id=plan["plan_id"], acquisition_id=acquisition_id,
            started_at=started.isoformat(), archive_directory=directory.relative_to(self.archive_root).as_posix())
        entries = []; transport_stopped = False
        for request in plan["requests"]:
            ticker = request["ticker"]
            row = {"ticker": ticker, "status": "WAIT_DATA", "reason": "", "request_url": request["url"]}
            report["rows"].append(row)
            if transport_stopped:
                row["reason"] = "NOT_ATTEMPTED_AFTER_TRANSPORT_FAILURE"
                continue
            attempted = self._now(); row["attempted_at"] = attempted.isoformat()
            report["source_http_requests"] += 1
            try:
                response = self.transport(request["url"], MAX_BODY_BYTES)
                observed = self._now()
                body = response["body"]
                row.update(observed_at=observed.isoformat(), http_status=response["status"],
                    body_complete_within_byte_budget=response["body_complete_within_byte_budget"])
                if (not isinstance(body, bytes) or not response["body_complete_within_byte_budget"]
                        or not 0 < len(body) <= MAX_BODY_BYTES):
                    raise ValueError("INCOMPLETE_OR_OVERSIZED_ORIGINAL_RESPONSE")
                row.update(body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest())
                if response["content_encoding"].lower().strip() not in {"", "identity"}:
                    raise ValueError("WITHHELD_ENCODED_WHOLE_RESPONSE")
                if _has_sensitive_body(body):
                    raise ValueError("WITHHELD_UNSAFE_WHOLE_CHART_BODY")
                # Also inspect decoded keys/values before retaining ANY raw body.
                try:
                    _json(body)
                except ValueError as error:
                    if str(error).startswith("WITHHELD"):
                        raise
                    # A safe complete error body remains the original failed proof.
                raw_path = directory/("response_" + ticker + "_" + row["body_sha256"] + ".raw")
                _write_once(raw_path, body)
                row["raw_path"] = raw_path.relative_to(self.archive_root).as_posix()
                raw_archived_at = self._now()
                report["raw_captures"] += 1
                if response["response_url"] != request["url"]:
                    raise ValueError("REDIRECT_OR_ORIGINAL_RESPONSE_URL_CONFLICT")
                if response["status"] != 200:
                    raise ValueError("COMPLETE_HTTP200_REQUIRED")
                frame, parser = parse_original_chart(body, ticker=ticker,
                    expected_sessions=plan["expected_sessions"], observed_at=observed)
                capture = capture_provider_frame(ticker, frame, started_at=attempted, observed_at=observed,
                    upstream_response_sha256=row["body_sha256"], evidence_origin="CAPTURED_YAHOO_CHART_REPLAY")
                capture["frame_json"] = _exact_frame_json(frame)
                capture["frame_sha256"] = hashlib.sha256(capture["frame_json"].encode("utf-8")).hexdigest()
                capture["frame_serialization"] = FRAME_SERIALIZATION
                capture.update(parser=parser, raw_http_parent={key:row[key] for key in (
                    "request_url", "raw_path", "body_sha256", "body_bytes", "attempted_at", "observed_at", "http_status")})
                capture["raw_http_parent"]["response_url"] = response["response_url"]
                capture["raw_http_parent"]["available_at"] = observed.isoformat()
                capture["raw_http_parent"]["archived_at"] = raw_archived_at.isoformat()
                capture["captured_at"] = self._now().isoformat()
                capture["capture_id"] = canonical_hash({k:v for k,v in capture.items() if k != "capture_id"})
                if self.fixture_only:
                    capture.update(evidence_origin="DETERMINISTIC_TEST_FIXTURE", fixture_only=True)
                    capture["capture_id"] = canonical_hash({k:v for k,v in capture.items() if k != "capture_id"})
                observation_path = persist_observation(capture, directory/"observations")
                entry = build_captured_market_data_entry(archive_root=self.archive_root,
                    source_archive_path=observation_path.relative_to(self.archive_root).as_posix())
                row.update(status="CAPTURED", reason="", observation_path=entry["source"]["path"],
                    source=entry["source"], parser=parser)
                entries.append(entry)
            except (URLError, TimeoutError, ConnectionError, OSError) as error:
                row.update(reason="TRANSPORT_FAILURE", error_type=type(error).__name__)
                transport_stopped = True
            except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as error:
                row["reason"] = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z0-9_]{1,128}", str(error)) else "INVALID_ORIGINAL_MARKET_CAPTURE"
            row["completed_at"] = self._now().isoformat()
            _write_once(directory/("receipt_" + ticker + ".json"), _encoded(row))
        if entries:
            manifest_path = directory/"market_manifest.json"
            _write_once(manifest_path, _encoded({"schema_version": MANIFEST_SCHEMA_VERSION, "instruments": entries}))
            provider = CapturedMarketDataProvider(manifest_path=manifest_path, archive_root=self.archive_root,
                observation_clock=self._now if self.fixture_only else None)
            readiness = provider.readiness(plan["tickers"], include_benchmarks=False)
            report.update(manifest_path=manifest_path.relative_to(self.archive_root).as_posix(),
                manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                usable_count=readiness["usable_count"], readiness=readiness)
            report["status"] = "READY" if readiness["status"] == "READY" else ("PARTIAL" if readiness["usable_count"] else "WAIT_DATA")
        report["completed_at"] = self._now().isoformat()
        _write_once(directory/"acquisition_report.json", _encoded(report))
        return report
