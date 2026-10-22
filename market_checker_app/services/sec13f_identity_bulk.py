"""Prepare many SEC instrument candidates without promoting name matches.

The quarterly 13(f) list has CUSIPs/classes, but no ticker or issuer CIK.
The live SEC ticker catalog has CIKs, but no class or historical validity.
Consequently their join is a review queue, never a verified registry. Export
requires archived structured instrument and ticker/class evidence plus an
explicit review of the bounded identity interval.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

from market_checker_app.services.sec13f_scout_service import _valid_cusip
from market_checker_app.utils.symbols import normalize_yahoo_symbol


MAX_SOURCE_BYTES = 4_000_000
MAX_SOURCE_ROWS = 100_000
MAX_SNAPSHOTS = 2_000
MAX_BUNDLE_BYTES = 100_000_000
MAX_UNIVERSE = 1_000
MAX_CHECKPOINT_BYTES = 32_000_000
MAX_REVIEW_BYTES = 8_000_000
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
PRODUCTION_DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
IDENTITY_FIELDS = (
    "ticker", "issuer_cik", "cusip", "issuer_name", "class_description",
    "effective_from", "effective_to", "known_at", "cusip_evidence_url",
    "instrument_evidence_url", "ticker_evidence_url",
)


def _clock(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Evidence time requires a timezone")
    return result.astimezone(timezone.utc)


def quarter_period(quarter: str) -> tuple[date, date]:
    if not re.fullmatch(r"20[0-9]{2}q[1-4]", quarter):
        raise ValueError("Quarter must have the form 2026q2")
    year, number = int(quarter[:4]), int(quarter[-1])
    start = date(year, number * 3 - 2, 1)
    next_start = date(year + 1, 1, 1) if number == 4 else date(year, number * 3 + 1, 1)
    return start, date.fromordinal(next_start.toordinal() - 1)


def list_url(quarter: str) -> str:
    quarter_period(quarter)
    # The SEC current-list page advertises this quarter without the legacy
    # "-txt" suffix. Preserve older evidence URLs rather than rewriting them.
    if quarter == "2026q3":
        return "https://www.sec.gov/files/investment/13flist2026q3.txt"
    return f"https://www.sec.gov/files/investment/13flist{quarter}-txt.txt"


def list_quarter(url: str) -> str:
    official_url("13f_list", url)
    return re.fullmatch(r"/files/investment/13flist(20[0-9]{2}q[1-4])(?:-txt)?\.txt",
                        urlsplit(url).path).group(1)


def _ticker(value: str) -> str:
    # Preserve canonical output spelling, normalize SEC punctuation for lookup.
    return normalize_yahoo_symbol(value).replace(".", "-").replace("/", "-")


def _name(value: str) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", value.upper()))


def share_class(value: str) -> str | None:
    """Small fail-closed vocabulary; no inferred equivalence of share classes."""
    value = " ".join(value.upper().split())
    if re.search(r"\b(?:PREFERRED|PFD|ADR|ADS|DEPOSITARY|DEP|NOTE|CALL|PUT|UNIT|RIGHT|WARRANT)\b", value):
        return None
    value = re.sub(r"[,;]?\s*(?:\$[0-9.]+\s*)?PAR VALUE\b.*$", "", value).strip(" ,;")
    for source, replacement in (
        ("SHARES OF COMMON STOCK", "COM"), ("COMMON STOCK", "COM"),
        ("COMMON SHARES", "COM"), ("CAPITAL STOCK", "COM"), ("CAP STK", "COM"),
        ("ORDINARY SHARES", "ORD SHS"), ("CLASS", "CL"),
    ):
        value = value.replace(source, replacement)
    value = re.sub(r"\bNEW\b", "", value)
    value = " ".join(value.split())
    match = re.fullmatch(r"(?:(?:COM|ORD|SHS)(?:\s+(?:COM|ORD|SHS))?\s+)?CL\s+([A-Z])(?:\s+(?:COM|ORD|SHS)){0,2}", value)
    if match:
        return f"EQUITY:{match[1]}"
    if re.fullmatch(r"(?:COM|ORD|SHS)(?:\s+(?:COM|ORD|SHS))?", value):
        return "EQUITY:UNCLASSIFIED"
    return None


def official_url(kind: str, url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov"
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Bulk identities require an unmodified official SEC URL")
    if kind == "ticker_catalog":
        valid = url in {TICKER_URL, EXCHANGE_URL}
    elif kind == "13f_list":
        valid = re.fullmatch(r"/files/investment/13flist20[0-9]{2}q[1-4](?:-txt)?\.txt", parsed.path) is not None
    elif kind in {"schedule13", "ticker_filing", "corporate_action_filing"}:
        valid = re.fullmatch(r"/Archives/edgar/data/[0-9]{1,10}/[0-9]{18}/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", parsed.path) is not None
        valid = valid and all(part not in {".", ".."} for part in parsed.path.split("/"))
        if kind == "schedule13":
            # An XSL view can render HTML and discard the structured issuer CIK.
            valid = valid and parsed.path.lower().endswith(".xml") and "/xsl" not in parsed.path.lower()
        elif kind == "corporate_action_filing":
            valid = valid and parsed.path.lower().endswith((".htm", ".html"))
    else:
        valid = False
    if not valid:
        raise ValueError(f"Unexpected SEC endpoint for {kind}")
    return url


@dataclass(frozen=True)
class SourceSnapshot:
    kind: str
    url: str
    observed_at: datetime
    payload: bytes
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        official_url(self.kind, self.url)
        object.__setattr__(self, "observed_at", _clock(self.observed_at))
        if not isinstance(self.payload, bytes) or not 0 < len(self.payload) <= MAX_SOURCE_BYTES:
            raise ValueError("Empty or oversized SEC snapshot")
        if self.kind in {"schedule13", "ticker_filing", "corporate_action_filing"} and self.published_at is None:
            raise ValueError("Filing snapshots require their SEC publication time")
        if self.published_at is not None:
            object.__setattr__(self, "published_at", _clock(self.published_at))
            if self.published_at > self.observed_at:
                raise ValueError("A snapshot cannot precede publication")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    def metadata(self) -> dict:
        return {
            "kind": self.kind, "url": self.url, "sha256": self.sha256,
            "observed_at": _clock(self.observed_at).isoformat(),
            "published_at": _clock(self.published_at).isoformat() if self.published_at else None,
        }

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(json.dumps(self.metadata(), sort_keys=True).encode()).hexdigest()


def guard_production_write(path: Path) -> None:
    resolved, production = Path(path).resolve(), PRODUCTION_DATA_DIRECTORY.resolve()
    if resolved == production or production in resolved.parents:
        raise ValueError("Production data directory cannot be overwritten or used for bulk outputs")


def atomic_json(path: Path, payload: object) -> None:
    guard_production_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers can briefly deny replacement of the destination.
        # Keep the complete temporary file and the previous checkpoint intact
        # until the same atomic rename succeeds, or the bounded retry expires.
        for attempt in range(20):
            try:
                os.replace(name, path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_snapshot(workspace: Path, snapshot: SourceSnapshot) -> str:
    directory = Path(workspace) / "snapshots"
    guard_production_write(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload_path = directory / f"{snapshot.sha256}.bin"
    if payload_path.exists():
        if payload_path.read_bytes() != snapshot.payload:
            raise ValueError("Cached snapshot failed its digest check")
    else:
        # Publish complete bytes exclusively. An interrupted write must not
        # leave a partial digest-named file that permanently blocks resume.
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".snapshot.")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(snapshot.payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, payload_path)
            except FileExistsError:
                if payload_path.read_bytes() != snapshot.payload:
                    raise ValueError("Concurrent snapshot content differs")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    atomic_json(directory / f"{snapshot.snapshot_id}.json", snapshot.metadata())
    return snapshot.snapshot_id


def load_snapshots(workspace: Path, *, snapshot_ids: set[str] | None = None,
                   kinds: set[str] | None = None) -> list[SourceSnapshot]:
    if snapshot_ids is not None and (len(snapshot_ids) > MAX_SNAPSHOTS or any(
            not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) for key in snapshot_ids)):
        raise ValueError("Malformed or unbounded snapshot selection")
    directory = Path(workspace) / "snapshots"
    paths = sorted(directory.glob("*.json"))
    if len(paths) > MAX_SNAPSHOTS:
        raise ValueError("Snapshot count exceeds the bulk budget")
    snapshots, total_bytes, loaded_ids = [], 0, set()
    for path in paths:
        if snapshot_ids is not None and path.stem not in snapshot_ids:
            continue
        if path.stat().st_size > 8_192:
            raise ValueError("Unbounded snapshot metadata")
        item = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(item, dict) or set(item) != {"kind", "url", "sha256", "observed_at", "published_at"}:
            raise ValueError("Malformed snapshot metadata")
        if kinds is not None and item.get("kind") not in kinds:
            continue
        digest = item["sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Malformed snapshot digest")
        payload_path = directory / f"{digest}.bin"
        size = payload_path.stat().st_size
        total_bytes += size
        if size > MAX_SOURCE_BYTES or total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("Unbounded cached SEC source")
        snapshot = SourceSnapshot(
            item["kind"], item["url"], _clock(item["observed_at"]), payload_path.read_bytes(),
            _clock(item["published_at"]) if item["published_at"] else None,
        )
        if snapshot.sha256 != digest or path.stem != snapshot.snapshot_id or item != snapshot.metadata():
            raise ValueError("Cached SEC snapshot digest or metadata changed")
        snapshots.append(snapshot)
        loaded_ids.add(snapshot.snapshot_id)
    if snapshot_ids is not None and loaded_ids != snapshot_ids:
        raise ValueError("Missing requested SEC snapshot")
    return snapshots


def read_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    if path.stat().st_size > MAX_CHECKPOINT_BYTES:
        raise ValueError("Unbounded bulk checkpoint")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Malformed bulk checkpoint")
    ids = state.get("snapshot_ids")
    if (not isinstance(ids, list) or not 0 < len(ids) <= MAX_SNAPSHOTS
            or any(not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Checkpoint has malformed source references")
    return state


def parse_13f_list(snapshot: SourceSnapshot) -> list[dict]:
    if snapshot.kind != "13f_list":
        raise ValueError("Expected the quarterly SEC securities list")
    rows, seen = [], {}
    for line_number, line in enumerate(snapshot.payload.decode("utf-8-sig").splitlines(), 1):
        if line_number > MAX_SOURCE_ROWS:
            raise ValueError("SEC securities-list row budget exceeded")
        if not line.strip():
            continue
        # SEC's TXT list: 9-char CUSIP, marker, 30-char issuer, 27-char class,
        # then addition/deletion/status flags. Do not split issuer words.
        if (len(line) < 67 or len(line) > 80 or not re.fullmatch(r"[A-Z0-9]{8}[0-9]", line[:9])
                or line[9] not in {" ", "*"} or line[67:70].strip() not in {"", "*A*", "*D*"}
                or line[70:79].strip()):
            raise ValueError(f"Malformed SEC securities-list row {line_number}")
        row = {"cusip": line[:9], "issuer_name": line[10:40].strip(),
               "class_description": line[40:67].strip(), "flags": line[67:].strip(),
               "line_number": line_number}
        option_class = row["class_description"] in {"CALL", "PUT"}
        if (not _name(row["issuer_name"]) or not row["class_description"]
                or row["cusip"] in seen and (not option_class or seen[row["cusip"]] != row["class_description"])):
            raise ValueError("Missing labels or duplicate CUSIP in SEC list")
        # SEC's synthetic option identifiers do not always have a standard
        # CUSIP check digit. They remain non-equity rows and cannot become a
        # candidate or an exported identity. All other rows require a checksum.
        if not _valid_cusip(row["cusip"]) and not option_class:
            raise ValueError(f"Invalid CUSIP in SEC securities-list row {line_number}")
        # The official list repeats synthetic ETF option identifiers, sometimes
        # with different addition/deletion flags. Preserve all original lines;
        # neither occurrence can be an equity candidate or reviewed identity.
        # An option/equity or CALL/PUT collision still fails closed.
        seen[row["cusip"]] = row["class_description"]
        rows.append(row)
    if not rows:
        raise ValueError("No securities in SEC list")
    return rows


def parse_ticker_catalog(snapshot: SourceSnapshot) -> dict[str, list[dict]]:
    if snapshot.kind != "ticker_catalog":
        raise ValueError("Expected the SEC ticker catalog")
    payload = json.loads(snapshot.payload)
    if not isinstance(payload, dict):
        raise ValueError("Malformed SEC ticker catalog")
    if isinstance(payload.get("fields"), list) and isinstance(payload.get("data"), list):
        fields = payload["fields"]
        if not {"cik", "ticker", "name"} <= set(fields) or len(fields) != len(set(fields)):
            raise ValueError("Malformed SEC catalog columns")
        rows = []
        for values in payload["data"]:
            if not isinstance(values, list) or len(values) != len(fields):
                raise ValueError("Malformed SEC catalog row")
            rows.append(dict(zip(fields, values)))
    else:
        rows = list(payload.values())
    if not rows or len(rows) > MAX_SOURCE_ROWS:
        raise ValueError("Empty or unbounded SEC catalog")
    result = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed SEC ticker row")
        ticker = row.get("ticker")
        cik = str(row.get("cik_str", row.get("cik", "")))
        name = row.get("title", row.get("name"))
        if (not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9./-]{1,15}", ticker)
                or not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0
                or not isinstance(name, str) or not _name(name)):
            raise ValueError("Malformed SEC ticker identity")
        item = {"ticker": ticker, "issuer_cik": cik.zfill(10), "issuer_name": name.strip()}
        # Keep distinct issuers for the same symbol; a dict assignment would
        # silently replace the first issuer when a symbol was reused.
        if item not in result[_ticker(ticker)]:
            result[_ticker(ticker)].append(item)
    return dict(result)


def parse_schedule13(snapshot: SourceSnapshot) -> dict:
    if snapshot.kind != "schedule13":
        raise ValueError("Expected raw SEC Schedule 13D/G XML")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", snapshot.payload, re.I):
        raise ValueError("DTD/entity declarations are not allowed")
    try:
        root = ElementTree.fromstring(snapshot.payload)
    except ElementTree.ParseError as exc:
        raise ValueError("Malformed SEC Schedule 13 XML") from exc
    if root.tag not in {"{http://www.sec.gov/edgar/schedule13g}edgarSubmission",
                        "{http://www.sec.gov/edgar/schedule13D}edgarSubmission"}:
        raise ValueError("Unrecognized SEC Schedule 13 XML namespace")
    if sum(1 for _ in root.iter()) > MAX_SOURCE_ROWS:
        raise ValueError("Schedule 13 XML element budget exceeded")

    def one(parent, name):
        nodes = [child for child in parent if child.tag.rsplit("}", 1)[-1].lower() == name.lower()]
        if len(nodes) != 1 or nodes[0].tag.split("}", 1)[0] != root.tag.split("}", 1)[0]:
            raise ValueError(f"Missing/ambiguous Schedule 13 {name}")
        return nodes[0]

    def value(parent, name):
        node = one(parent, name)
        if len(node) or not (node.text or "").strip():
            raise ValueError(f"Malformed Schedule 13 {name}")
        return node.text.strip()

    form = value(one(root, "headerData"), "submissionType").upper()
    if form not in {"SCHEDULE 13G", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}:
        raise ValueError("Unexpected Schedule 13 submission form")
    if root.tag.split("}", 1)[0].lower().endswith("13g") != ("13G" in form):
        raise ValueError("Schedule 13 form conflicts with its XML namespace")
    cover = one(one(root, "formData"), "coverPageHeader")
    issuer = one(cover, "issuerInfo")
    cik = value(issuer, "issuerCik")
    containers = [child for child in issuer
                  if child.tag.rsplit("}", 1)[-1].lower() in {"issuercusip", "issuercusips"}]
    if len(containers) != 1:
        raise ValueError("Missing/ambiguous Schedule 13 issuer CUSIP container")
    if containers[0].tag.rsplit("}", 1)[-1].lower() == "issuercusip":
        cusip = value(issuer, "issuerCusip").upper()
    else:
        # Current official XML can use the plural container even for one
        # instrument. Keep export single-instrument and fail closed if the
        # filing lists multiple identifiers or conflicting namespace claims.
        grouped_cusips = one(issuer, "issuerCusips")
        if len(grouped_cusips) != 1:
            raise ValueError("Schedule 13 requires a single unambiguous issuer CUSIP")
        cusip = value(grouped_cusips, "issuerCusipNumber").upper()
    if not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0 or not _valid_cusip(cusip):
        raise ValueError("Invalid Schedule 13 CIK/CUSIP")
    event = value(cover, "eventDateRequiresFilingThisStatement" if "13G" in form else "dateOfEvent")
    event_date = datetime.strptime(event, "%m/%d/%Y").date()
    if event_date > _clock(snapshot.published_at).date():
        raise ValueError("Schedule event is after publication")
    return {"issuer_cik": cik.zfill(10), "cusip": cusip,
            "issuer_name": value(issuer, "issuerName"),
            "class_title": value(cover, "securitiesClassTitle"),
            "event_date": event_date.isoformat(), "snapshot_id": snapshot.snapshot_id}


class _TickerCoverParser(HTMLParser):
    """Read issuer and ticker/class pairs from the same inline-XBRL context."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.context = None
        self.contexts = defaultdict(lambda: defaultdict(list))
        self.facts = defaultdict(lambda: defaultdict(list))
        self.captures = []
        self.namespaces = set()
        self.context_stack = []
        self.seen_context_ids = set()
        self.element_count = 0

    def handle_starttag(self, tag, attrs):
        self.element_count += 1
        if self.element_count > MAX_SOURCE_ROWS:
            raise ValueError("Filing HTML element budget exceeded")
        attrs = dict(attrs)
        for prefix, pattern in (
            ("dei", r"http://xbrl\.sec\.gov/dei/20[0-9]{2}"),
            ("xbrli", r"http://www\.xbrl\.org/2003/instance"),
            ("ix", r"http://www\.xbrl\.org/(?:2008|2013)/inlineXBRL"),
        ):
            if f"xmlns:{prefix}" in attrs:
                if not re.fullmatch(pattern, attrs[f"xmlns:{prefix}"] or ""):
                    raise ValueError("Incorrect filing inline-XBRL namespace")
                self.namespaces.add(prefix)
        if tag == "xbrli:context":
            if ("xbrli" not in self.namespaces or self.context is not None
                    or not attrs.get("id") or attrs["id"] in self.seen_context_ids):
                raise ValueError("Ambiguous filing XBRL context")
            self.context = attrs["id"]
            self.context_stack = [tag]
            self.seen_context_ids.add(self.context)
        elif self.context:
            self.context_stack.append(tag)
        if self.context and (
            self.context_stack == ["xbrli:context", "xbrli:entity", "xbrli:identifier"]
            and attrs.get("scheme") == "http://www.sec.gov/CIK"
            or self.context_stack in [["xbrli:context", "xbrli:period", anchor]
                                      for anchor in ("xbrli:instant", "xbrli:startdate", "xbrli:enddate")]
        ):
            self.captures.append([tag, self.context, tag, []])
        if tag == "ix:nonnumeric" and (attrs.get("name") or "").lower() in {"dei:tradingsymbol", "dei:security12btitle"}:
            if (self.namespaces != {"dei", "xbrli", "ix"} or self.context
                    or not attrs.get("contextref") or attrs.get("continuedat")
                    or (attrs.get("xsi:nil") or "").lower() in {"true", "1"}
                    or any(capture[0] == tag for capture in self.captures)):
                raise ValueError("Incomplete or unsupported inline ticker/class fact")
            self.captures.append([tag, attrs["contextref"], attrs["name"].lower(), []])
        if tag == "ix:exclude" and any(capture[2].startswith("dei:") for capture in self.captures):
            raise ValueError("Excluded text requires a separate inline-XBRL review")

    def handle_data(self, data):
        for capture in self.captures:
            capture[3].append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.captures) - 1, -1, -1):
            current = self.captures[index]
            if current[0] == tag:
                _, context, key, parts = self.captures.pop(index)
                target = self.facts if key.startswith("dei:") else self.contexts
                target[context][key].append(" ".join("".join(parts).split()))
                break
        if self.context:
            if not self.context_stack or self.context_stack.pop() != tag:
                raise ValueError("Malformed filing XBRL context structure")
            if tag == "xbrli:context":
                self.context = None


def parse_ticker_filing(snapshot: SourceSnapshot) -> list[dict]:
    if snapshot.kind != "ticker_filing":
        raise ValueError("Expected an issuer SEC filing")
    parser = _TickerCoverParser()
    parser.feed(snapshot.payload.decode("utf-8-sig"))
    parser.close()
    if parser.captures or parser.context is not None or parser.namespaces != {"dei", "xbrli", "ix"}:
        raise ValueError("Incomplete filing or missing SEC DEI namespace")
    result = []
    archive_cik = urlsplit(snapshot.url).path.split("/")[4].zfill(10)
    for context, values in parser.facts.items():
        tickers = set(values.get("dei:tradingsymbol", []))
        classes = set(values.get("dei:security12btitle", []))
        entity = parser.contexts.get(context, {})
        ciks = entity.get("xbrli:identifier", [])
        dates = entity.get("xbrli:instant", []) + entity.get("xbrli:enddate", [])
        if len(tickers) > 1 or len(classes) > 1 or len(ciks) > 1 or len(dates) > 1:
            raise ValueError("Ambiguous filing ticker/class context")
        if len(tickers) != 1 or len(classes) != 1 or len(ciks) != 1 or len(dates) != 1:
            continue
        cik, ticker, anchor = next(iter(ciks)), next(iter(tickers)), next(iter(dates))
        starts = entity.get("xbrli:startdate", [])
        if (not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", anchor)
                or (entity.get("xbrli:enddate") and (len(starts) != 1 or date.fromisoformat(starts[0]) > date.fromisoformat(anchor)))
                or (entity.get("xbrli:instant") and starts)):
            raise ValueError("Malformed filing ticker/class period")
        if (not re.fullmatch(r"[0-9]{1,10}", cik) or int(cik) == 0
                or cik.zfill(10) != archive_cik or not re.fullmatch(r"[A-Z0-9./-]{1,15}", ticker)
                or date.fromisoformat(anchor) > _clock(snapshot.published_at).date()):
            continue
        result.append({"ticker": ticker, "issuer_cik": cik.zfill(10),
                       "class_title": next(iter(classes)), "anchor_date": anchor,
                       "context_ref": context, "snapshot_id": snapshot.snapshot_id})
    return result


def identity_version_sha256(entry: dict) -> str:
    """Bind a transition to an exact immutable registry version, including its clock."""
    try:
        version = {key: entry[key] for key in IDENTITY_FIELDS}
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed identity version for transition") from exc
    if any(not isinstance(value, str) for value in version.values()):
        raise ValueError("Malformed identity version for transition")
    return hashlib.sha256(json.dumps(version, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(entry: dict) -> tuple[str, str, str]:
    return entry["issuer_cik"], entry["cusip"], share_class(entry["class_description"]) or _name(entry["class_description"])


def _check_identity_conflicts(entries: list[dict]) -> None:
    """The runtime loader validates rows individually, so preflight their joint timeline."""
    for field, label in (("ticker", "ticker"), ("cusip", "CUSIP")):
        grouped = defaultdict(list)
        for entry in entries:
            key = _ticker(entry[field]) if field == "ticker" else entry[field]
            grouped[key].append(entry)
        for versions in grouped.values():
            versions.sort(key=lambda row: (date.fromisoformat(row["effective_from"]), date.fromisoformat(row["effective_to"])))
            for previous, current in zip(versions, versions[1:]):
                if date.fromisoformat(current["effective_from"]) <= date.fromisoformat(previous["effective_to"]):
                    if field == "ticker" and _identity(previous) != _identity(current):
                        raise ValueError("Overlapping incompatible ticker CIK/CUSIP/security-class identities")
                    if field == "cusip" and _ticker(previous["ticker"]) != _ticker(current["ticker"]):
                        raise ValueError("Ambiguous CUSIP assigned to multiple canonical tickers")
                    if field == "ticker":
                        raise ValueError(f"Overlapping/duplicate {label} identity")


class _ActionPassageParser(HTMLParser):
    """Visible primary filing text only; hidden facts and scripts cannot support review."""
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.element_count = [], [], 0
        self.has_html = False

    def handle_starttag(self, tag, attrs):
        self.element_count += 1
        if self.element_count > MAX_SOURCE_ROWS:
            raise ValueError("Corporate-action HTML element budget exceeded")
        attributes = dict(attrs)
        style = re.sub(r"\s+", "", attributes.get("style") or "").lower()
        hidden = (tag in {"head", "script", "style", "ix:hidden", "template", "noscript"}
                  or "hidden" in attributes or "display:none" in style or "visibility:hidden" in style
                  or bool(self.stack and self.stack[-1][1]))
        self.has_html |= tag == "html"
        if tag not in self._VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        # Fail closed on malformed HTML rather than exposing text hidden by
        # a mismatched closing tag. Explicitly closed SEC filing HTML is supported.
        if not self.stack or self.stack[-1][0] != tag:
            raise ValueError("Malformed corporate-action filing HTML")
        self.stack.pop()

    def handle_data(self, value):
        if self.stack and not self.stack[-1][1]:
            self.parts.append(value)


def _action_text(snapshot: SourceSnapshot) -> str:
    if snapshot.kind != "corporate_action_filing":
        raise ValueError("Transition requires an archived primary corporate-action filing")
    payload = snapshot.payload.decode("utf-8-sig")
    if payload.lstrip().upper().startswith("<DOCUMENT>"):
        # Some official .htm responses retain the single EDGAR SGML document
        # envelope. Its unclosed metadata tags are not HTML; do not weaken the
        # visible-text parser to accommodate them. Accept only this exact
        # envelope for the requested filename, leaving the archived bytes intact.
        envelope = re.fullmatch(
            r"\s*<DOCUMENT>\s*<TYPE>[A-Za-z0-9/-]+\s*<SEQUENCE>[0-9]+\s*"
            r"<FILENAME>([A-Za-z0-9_.-]+)\s*(?:<DESCRIPTION>[^\r\n<]*\r?\n)?"
            r"<TEXT>\s*(<HTML(?:\s[^>]*)?>.*</HTML>)\s*</TEXT>\s*</DOCUMENT>\s*",
            payload, re.I | re.S,
        )
        if envelope is None or envelope[1] != urlsplit(snapshot.url).path.rsplit("/", 1)[-1]:
            raise ValueError("Malformed or mismatched single SEC DOCUMENT envelope")
        payload = envelope[2]
    parser = _ActionPassageParser()
    parser.feed(payload)
    parser.close()
    if not parser.has_html or parser.stack:
        raise ValueError("Incomplete corporate-action filing HTML")
    return " ".join(" ".join(parser.parts).split())


def _dated_action_text(value: str, effective_on: date) -> bool:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%d %B %Y"):
        try:
            if datetime.strptime(value, pattern).date() == effective_on:
                return True
        except ValueError:
            pass
    return False


def _literal_in(value: str, passage: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", passage) is not None


class Sec13fIdentityBulk:
    def __init__(self, *, snapshots: list[SourceSnapshot], universe: list[str],
                 quarter: str, as_of: datetime, existing: list[dict] | None = None,
                 max_candidates: int = 8):
        if (not 0 < len(universe) <= MAX_UNIVERSE or len(set(universe)) != len(universe)
                or not 1 <= max_candidates <= 20 or len(snapshots) > MAX_SNAPSHOTS):
            raise ValueError("Invalid universe or candidate/source budget")
        if any(not isinstance(t, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", t) for t in universe):
            raise ValueError("Expected canonical ticker spelling")
        if len({_ticker(ticker) for ticker in universe}) != len(universe):
            raise ValueError("Canonical universe contains conflicting ticker aliases")
        self.universe, self.quarter, self.as_of = list(universe), quarter, _clock(as_of)
        self.universe_set = set(universe)
        self.period = quarter_period(quarter)
        self.existing, self.max_candidates = existing or [], max_candidates
        _check_identity_conflicts(self.existing)
        self.snapshots = {snapshot.snapshot_id: snapshot for snapshot in snapshots}
        if sum(len(snapshot.payload) for snapshot in snapshots) > MAX_BUNDLE_BYTES:
            raise ValueError("Source bundle exceeds the memory budget; select a smaller evidence batch")
        if len(self.snapshots) != len(snapshots):
            raise ValueError("Duplicate SEC snapshot")
        if any(_clock(snapshot.observed_at) > self.as_of for snapshot in snapshots):
            raise ValueError("SEC snapshots contain future knowledge")
        catalogs = [snapshot for snapshot in snapshots if snapshot.kind == "ticker_catalog"]
        lists = [snapshot for snapshot in snapshots if snapshot.kind == "13f_list" and list_quarter(snapshot.url) == quarter]
        if len(catalogs) != 1 or len(lists) != 1:
            raise ValueError("Select exactly one ticker catalog and one quarterly securities list")
        self.catalog_snapshot, self.list_snapshot = catalogs[0], lists[0]
        if _clock(self.list_snapshot.observed_at).date() < self.period[1]:
            raise ValueError("Quarterly list snapshot precedes the quarter close")
        self.catalog = parse_ticker_catalog(self.catalog_snapshot)
        self.rows = parse_13f_list(self.list_snapshot)
        self.by_cusip = {row["cusip"]: row for row in self.rows}
        self.by_name = defaultdict(list)
        self.by_token = defaultdict(list)
        for row in self.rows:
            if share_class(row["class_description"]) and "*D*" not in row["flags"]:
                self.by_name[_name(row["issuer_name"])].append(row)
                first_token = _name(row["issuer_name"]).split()[0]
                self.by_token[first_token].append(row)
        self.instruments, self.covers = [], []
        for snapshot in snapshots:
            if snapshot.kind == "schedule13":
                self.instruments.append(parse_schedule13(snapshot))
            elif snapshot.kind == "ticker_filing":
                self.covers.extend(parse_ticker_filing(snapshot))
        self.fingerprint = hashlib.sha256(json.dumps({
            "schema": 1, "snapshots": sorted(self.snapshots), "universe": self.universe,
            "quarter": quarter, "as_of": self.as_of.isoformat(), "existing": self.existing,
            "max_candidates": max_candidates,
        }, sort_keys=True).encode()).hexdigest()

    def candidate(self, ticker: str) -> dict:
        if ticker not in self.universe_set:
            raise ValueError("Candidate ticker is outside the canonical universe")
        companies = self.catalog.get(_ticker(ticker), [])
        result = {"ticker": ticker, "identity_status": "CANDIDATE_ONLY",
                  "scoring_allowed": False, "catalog_matches": companies,
                  "candidate_instruments": [], "missing_evidence": [
                      "same-context SEC ticker/class filing",
                      "structured SEC issuer-CIK/CUSIP/class filing",
                      "explicit dated binding review"],
                  "catalog_snapshot_id": self.catalog_snapshot.snapshot_id,
                  "list_snapshot_id": self.list_snapshot.snapshot_id}
        if len({row["issuer_cik"] for row in companies}) != 1:
            result["status"] = "AMBIGUOUS_CATALOG" if companies else "WAIT_TICKER_CIK"
            return result
        cik = companies[0]["issuer_cik"]
        selected = {}
        for company in companies:
            key = _name(company["issuer_name"])
            exact = self.by_name.get(key, [])
            pool = exact or self.by_token.get(key.split()[0], [])[:200]
            for row in pool:
                # Bounded similarity helps the human find SEC abbreviations;
                # it never proves a CIK, share class, or historical ticker.
                similarity = SequenceMatcher(None, key, _name(row["issuer_name"])).ratio()
                if exact or similarity >= 0.55:
                    selected[row["cusip"]] = {**row, "match_basis": "EXACT_NAME_CANDIDATE" if exact else "NAME_SUGGESTION",
                                              "name_similarity": round(similarity, 6), "issuer_cik_candidate": cik}
        for instrument in self.instruments:
            row = self.by_cusip.get(instrument["cusip"])
            if (instrument["issuer_cik"] == cik and row and "*D*" not in row["flags"]
                    and share_class(instrument["class_title"]) is not None
                    and share_class(instrument["class_title"]) == share_class(row["class_description"])):
                selected[row["cusip"]] = {**row, "match_basis": "EXACT_CIK_CUSIP_CLASS_CANDIDATE",
                                          "issuer_cik_candidate": cik,
                                          "instrument_snapshot_id": instrument["snapshot_id"]}
        ordered = sorted(selected.values(), key=lambda row: (
            "instrument_snapshot_id" not in row, -row.get("name_similarity", 0), row["cusip"]))
        result["candidates_total"] = len(ordered)
        result["candidates_truncated"] = len(ordered) > self.max_candidates
        result["candidate_instruments"] = ordered[:self.max_candidates]
        result["ticker_filing_matches"] = [cover for cover in self.covers
                                           if cover["issuer_cik"] == cik and _ticker(cover["ticker"]) == _ticker(ticker)]
        result["status"] = "NEEDS_REVIEW" if ordered else "WAIT_INSTRUMENT_EVIDENCE"
        if any(entry["ticker"] == ticker and entry["issuer_cik"] == cik
               and _clock(entry["known_at"]) <= self.as_of
               and date.fromisoformat(entry["effective_from"]) <= self.period[0]
               and date.fromisoformat(entry["effective_to"]) >= self.period[1]
               and entry["cusip"] in self.by_cusip
               and "*D*" not in self.by_cusip[entry["cusip"]]["flags"]
               and share_class(entry["class_description"]) is not None
               and share_class(entry["class_description"]) == share_class(
                   self.by_cusip[entry["cusip"]]["class_description"])
               for entry in self.existing):
            result["status"] = "ALREADY_REVIEWED"
        return result

    def run_batch(self, checkpoint: Path, *, batch_size: int = 100) -> dict:
        if not 1 <= batch_size <= MAX_UNIVERSE:
            raise ValueError("Batch size must be bounded")
        state = read_checkpoint(checkpoint)
        if state is not None:
            rows = state.get("results")
            if (state.get("fingerprint") != self.fingerprint or state.get("schema_version") != 1
                    or state.get("quarter") != self.quarter or state.get("as_of") != self.as_of.isoformat()
                    or state.get("snapshot_ids") != sorted(self.snapshots)
                    or type(state.get("total_tickers")) is not int or state["total_tickers"] != len(self.universe)
                    or state.get("identity_status") != "CANDIDATE_ONLY" or state.get("verified_registry_changed") is not False
                    or not isinstance(rows, list) or len(rows) > len(self.universe)
                    or type(state.get("next_index")) is not int or state["next_index"] != len(rows)
                    or any(not isinstance(row, dict) or row.get("identity_status") != "CANDIDATE_ONLY"
                           or row.get("scoring_allowed") is not False
                           or row.get("catalog_snapshot_id") != self.catalog_snapshot.snapshot_id
                           or row.get("list_snapshot_id") != self.list_snapshot.snapshot_id for row in rows)
                    or [row.get("ticker") for row in rows] != self.universe[:len(rows)]
                    or state.get("status") != ("COMPLETE_REVIEW_QUEUE" if len(rows) == len(self.universe) else "PARTIAL_REVIEW_QUEUE")):
                raise ValueError("Checkpoint does not match its sources, universe, clock, or cursor")
        else:
            state = {"schema_version": 1, "fingerprint": self.fingerprint,
                     "quarter": self.quarter, "as_of": self.as_of.isoformat(),
                     "snapshot_ids": sorted(self.snapshots),
                     "total_tickers": len(self.universe), "results": [], "next_index": 0,
                     "identity_status": "CANDIDATE_ONLY", "verified_registry_changed": False,
                     "status": "PARTIAL_REVIEW_QUEUE"}
            atomic_json(checkpoint, state)
        stop = min(len(self.universe), state["next_index"] + batch_size)
        for index in range(state["next_index"], stop):
            state["results"].append(self.candidate(self.universe[index]))
            state["next_index"] = index + 1
            state["status"] = "COMPLETE_REVIEW_QUEUE" if index + 1 == len(self.universe) else "PARTIAL_REVIEW_QUEUE"
            # Persist after every ticker, not just at the end of a slow batch.
            atomic_json(checkpoint, state)
        return state

    def validate_reviewed(self, reviewed: list[dict]) -> list[dict]:
        if not isinstance(reviewed, list) or len(reviewed) > MAX_UNIVERSE:
            raise ValueError("Unbounded reviewed identity batch")
        accepted = []
        for entry in reviewed:
            if not isinstance(entry, dict) or not isinstance(entry.get("review"), dict):
                raise ValueError("Each proposed identity needs a dated explicit review")
            review = entry["review"]
            if (review.get("approved") is not True
                    or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
                    or not isinstance(review.get("dated_binding_note"), str) or not review["dated_binding_note"].strip()):
                raise ValueError("An unreviewed candidate cannot enter the registry")
            required = {"ticker", "issuer_cik", "cusip", "issuer_name", "class_description",
                        "effective_from", "effective_to", "known_at", "cusip_evidence_url",
                        "instrument_evidence_url", "ticker_evidence_url"}
            if not required <= set(entry) or any(not isinstance(entry[key], str) for key in required):
                raise ValueError("Reviewed identity has malformed registry fields")
            ticker, cik, cusip = entry["ticker"], entry["issuer_cik"], entry["cusip"]
            if ticker not in self.universe_set or not re.fullmatch(r"[0-9]{10}", cik) or int(cik) == 0 or not _valid_cusip(cusip):
                raise ValueError("Reviewed identity is outside the canonical universe")
            catalog_ciks = {company["issuer_cik"] for company in self.catalog.get(_ticker(ticker), [])}
            if catalog_ciks != {cik}:
                raise ValueError("Reviewed ticker has an ambiguous or conflicting SEC catalog CIK")
            start, end = date.fromisoformat(entry["effective_from"]), date.fromisoformat(entry["effective_to"])
            if (start.isoformat() != entry["effective_from"] or end.isoformat() != entry["effective_to"]
                    or not self.period[0] <= start <= end <= self.period[1]):
                raise ValueError("Identity interval is outside its official quarterly list")
            source_list = self.snapshots.get(review.get("list_snapshot_id"))
            source_instrument = self.snapshots.get(review.get("instrument_snapshot_id"))
            source_ticker = self.snapshots.get(review.get("ticker_snapshot_id"))
            if (source_list != self.list_snapshot or source_instrument is None or source_ticker is None
                    or source_instrument.kind != "schedule13" or source_ticker.kind != "ticker_filing"):
                raise ValueError("Reviewed identity lacks archived independent SEC proofs")
            known, reviewed_at = _clock(entry["known_at"]), _clock(review["reviewed_at"])
            latest_source = max(source.observed_at for source in (self.catalog_snapshot, source_list, source_instrument, source_ticker))
            if not latest_source <= reviewed_at <= known <= self.as_of:
                raise ValueError("Reviewed identity contains future or backdated knowledge")
            row = self.by_cusip.get(cusip)
            if (row is None or row["issuer_name"] != entry.get("issuer_name")
                    or row["class_description"] != entry.get("class_description") or "*D*" in row["flags"]):
                raise ValueError("CUSIP/issuer/class does not match the active exact SEC list row")
            key = share_class(row["class_description"])
            if key is None:
                raise ValueError("Instrument class requires a separate specialized review")
            instrument = parse_schedule13(source_instrument)
            if (instrument["issuer_cik"] != cik or instrument["cusip"] != cusip
                    or share_class(instrument["class_title"]) != key
                    or not start <= date.fromisoformat(instrument["event_date"]) <= end):
                raise ValueError("Structured instrument proof has wrong CIK, CUSIP, class, or date")
            covers = [cover for cover in parse_ticker_filing(source_ticker)
                      if _ticker(cover["ticker"]) == _ticker(ticker) and cover["issuer_cik"] == cik
                      and start <= date.fromisoformat(cover["anchor_date"]) <= end]
            if len(covers) != 1 or share_class(covers[0]["class_title"]) != key:
                raise ValueError("Ticker/CIK/class has no unique same-context dated SEC proof")
            urls = {"cusip_evidence_url": source_list.url,
                    "instrument_evidence_url": source_instrument.url,
                    "ticker_evidence_url": source_ticker.url}
            if any(entry.get(key) != value for key, value in urls.items()):
                raise ValueError("Manifest citations do not match the archived proofs")
            production = {key: entry[key] for key in IDENTITY_FIELDS}
            _check_identity_conflicts([*self.existing, *accepted, production])
            accepted.append(production)
        self._validate_transitions(reviewed, accepted)
        return accepted

    def _validate_transitions(self, reviewed: list[dict], accepted: list[dict]) -> None:
        """Check every changed boundary involving this batch, independent of input order.

        Exact quotes prove source presence, not the legal interpretation of an
        action. A named reviewer still approves the old/new roles and date.
        Neither past validity intervals nor past knowledge times are rewritten.
        """
        grouped, required, claims = defaultdict(list), {}, {}
        new_ids = {identity_version_sha256(entry) for entry in accepted}
        for entry in [*self.existing, *accepted]:
            grouped[("ticker", _ticker(entry["ticker"]))].append(entry)
            # A renamed symbol can disappear from the current catalog while
            # the same CUSIP survives under another canonical symbol. Grouping
            # only by ticker misses that boundary and silently permits an
            # unproved OLD -> NEW continuity. Different CUSIPs/classes of the
            # same issuer still remain independent instruments.
            grouped[("cusip", entry["cusip"])].append(entry)
        for (field, _), versions in grouped.items():
            versions.sort(key=lambda row: date.fromisoformat(row["effective_from"]))
            for previous, current in zip(versions, versions[1:]):
                pair = identity_version_sha256(previous), identity_version_sha256(current)
                changed = (_identity(previous) != _identity(current) if field == "ticker"
                           else _ticker(previous["ticker"]) != _ticker(current["ticker"]))
                if changed and new_ids.intersection(pair):
                    required[pair] = (previous, current)
        for entry in reviewed:
            transitions = entry["review"].get("identity_transitions", [])
            if not isinstance(transitions, list) or len(transitions) > 2:
                raise ValueError("Identity transitions must be a bounded list of temporal neighbours")
            owner = identity_version_sha256(entry)
            for transition in transitions:
                if not isinstance(transition, dict):
                    raise ValueError("Malformed primary identity-transition review")
                pair = transition.get("from_identity_sha256"), transition.get("to_identity_sha256")
                if any(not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) for key in pair):
                    raise ValueError("Transition must bind exact prior and next identity versions")
                if pair not in required or owner not in pair or pair in claims:
                    raise ValueError("Transition is duplicate, unrelated, or not the exact chronological neighbour")
                claims[pair] = (entry, transition)
        if claims.keys() != required.keys():
            raise ValueError("Ticker/CIK/CUSIP/security-class change requires a dated primary identity-transition review")
        for pair, (previous, current) in required.items():
            owner, transition = claims[pair]
            source_id = transition.get("source_snapshot_id")
            if not isinstance(source_id, str) or not re.fullmatch(r"[a-f0-9]{64}", source_id):
                raise ValueError("Transition lacks its exact archived primary snapshot reference")
            snapshot = self.snapshots.get(source_id)
            if snapshot is None:
                raise ValueError("Transition lacks its archived primary corporate-action filing")
            text = _action_text(snapshot)
            archive_cik = urlsplit(snapshot.url).path.split("/")[4].zfill(10)
            if archive_cik not in {previous["issuer_cik"], current["issuer_cik"]}:
                raise ValueError("Corporate-action filing must be filed by the prior or next exact issuer CIK")
            review_at, known = _clock(owner["review"]["reviewed_at"]), _clock(owner["known_at"])
            if (snapshot.observed_at > review_at or max(_clock(previous["known_at"]), _clock(current["known_at"])) > known):
                raise ValueError("Transition evidence or identity versions contain backdated knowledge")
            effective = transition.get("effective_on")
            if not isinstance(effective, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", effective):
                raise ValueError("Transition requires an exact effective date")
            effective_on = date.fromisoformat(effective)
            if not date.fromisoformat(previous["effective_to"]) < effective_on <= date.fromisoformat(current["effective_from"]):
                raise ValueError("Transition effective date conflicts with preserved validity intervals")
            passage, date_text = transition.get("action_excerpt"), transition.get("effective_date_text")
            if (not isinstance(passage, str) or not 30 <= len(passage) <= 4_000
                    or not isinstance(date_text, str) or len(date_text) > 40 or not _dated_action_text(date_text, effective_on)):
                raise ValueError("Transition requires a bounded primary action passage with its exact date")
            passage = " ".join(passage.split())
            if passage not in text or not _literal_in(date_text, passage):
                raise ValueError("Transition action passage or effective date is absent from visible primary source text")
            for role, version in (("from_identity", previous), ("to_identity", current)):
                proof = transition.get(role)
                if not isinstance(proof, dict) or any(not isinstance(proof.get(key), str) or not proof[key]
                                                      for key in ("excerpt", "ticker_text", "issuer_cik_text", "class_title")):
                    raise ValueError("Transition must quote both exact ticker/CIK/CUSIP/class identities")
                excerpt = " ".join(proof["excerpt"].split())
                cik_text, ticker_text, title = proof["issuer_cik_text"], proof["ticker_text"], proof["class_title"]
                if (not excerpt or excerpt not in passage or len(title) > 300
                        or not re.fullmatch(r"[0-9]{1,10}", cik_text) or cik_text.zfill(10) != version["issuer_cik"]
                        or not re.fullmatch(r"[A-Z0-9./-]{1,15}", ticker_text) or _ticker(ticker_text) != _ticker(version["ticker"])
                        or share_class(title) is None or share_class(title) != share_class(version["class_description"])
                        or not all(_literal_in(value, excerpt) for value in (cik_text, ticker_text, version["cusip"], title))):
                    raise ValueError("Transition passage does not bind the exact old/new ticker/CIK/CUSIP/security class")
