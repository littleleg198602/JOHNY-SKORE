from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime, timezone
import hashlib
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.source_validation import public_https_reference


INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
DEFAULT_SECURITIES = Path(__file__).resolve().parents[1] / "data" / "verified_13f_securities.json"
MAX_ZIP_BYTES = 150_000_000


def _valid_cusip(value: str) -> bool:
    """Validate the standard ninth-character CUSIP check digit."""
    if not re.fullmatch(r"[A-Z0-9]{8}[0-9]", value):
        return False
    total = 0
    for index, character in enumerate(value[:8], start=1):
        number = int(character) if character.isdigit() else ord(character) - ord("A") + 10
        if index % 2 == 0:
            number *= 2
        total += number // 10 + number % 10
    return (10 - total % 10) % 10 == int(value[-1])


def _security_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _official_zip_url(url: str) -> str:
    parsed = urlsplit(public_https_reference(url))
    if (parsed.hostname != "www.sec.gov" or parsed.port not in {None, 443}
            or not re.fullmatch(r"/files/datastandardsinnovation/data/form-13f-data-sets/"
                                r"[0-9a-z-]+_form13f\.zip", parsed.path)
            or parsed.query or parsed.fragment):
        raise ValueError("13F dataset URL must be an official quarterly SEC ZIP")
    return url


class Sec13fDatasetClient(Protocol):
    def latest_url(self) -> str: ...
    def dataset(self, url: str) -> bytes: ...


class Sec13fHttpClient:
    def __init__(self, user_agent: str) -> None:
        if not user_agent.strip():
            raise ValueError("SEC User-Agent required")
        self.user_agent = user_agent

    def latest_url(self) -> str:
        with urlopen(Request(INDEX_URL, headers={"User-Agent": self.user_agent}), timeout=25) as response:
            if response.geturl() != INDEX_URL:
                raise ValueError("SEC index redirected")
            html = response.read(1_000_001)
        if len(html) > 1_000_000:
            raise ValueError("Unbounded SEC dataset index")
        matches = re.findall(rb'href=["\']([^"\']+_form13f\.zip)["\']', html, re.I)
        if not matches:
            raise ValueError("No 13F ZIP on SEC index")
        return _official_zip_url(urljoin(INDEX_URL, matches[0].decode("ascii")))

    def dataset(self, url: str) -> bytes:
        _official_zip_url(url)
        with urlopen(Request(url, headers={"User-Agent": self.user_agent}), timeout=90) as response:
            if response.geturl() != url:
                raise ValueError("13F ZIP redirected")
            chunks, total = [], 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise ValueError("13F ZIP exceeds budget")
                chunks.append(chunk)
        return b"".join(chunks)


def load_verified_securities(path: Path = DEFAULT_SECURITIES) -> list[dict]:
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("13F securities manifest must be a list")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("13F security must be an object")
        ticker, cusip = entry.get("ticker"), entry.get("cusip")
        if (not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", ticker)
                or not isinstance(cusip, str) or not _valid_cusip(cusip)):
            raise ValueError("13F security requires canonical ticker and CUSIP")
        if not isinstance(entry.get("issuer_cik"), str) or not re.fullmatch(r"[0-9]{10}", entry["issuer_cik"]):
            raise ValueError("13F security requires a ten-digit issuer CIK")
        start, end = date.fromisoformat(entry["effective_from"]), date.fromisoformat(entry["effective_to"])
        known = datetime.fromisoformat(entry["known_at"])
        if end < start or known.tzinfo is None or known.utcoffset() is None:
            raise ValueError("13F security requires bounded dated evidence")
        if (ticker, cusip, start, end) in seen:
            raise ValueError("Duplicate 13F instrument mapping")
        seen.add((ticker, cusip, start, end))
        for key in ("cusip_evidence_url", "instrument_evidence_url", "ticker_evidence_url"):
            parsed = urlsplit(public_https_reference(entry[key]))
            if parsed.hostname != "www.sec.gov" or parsed.port not in {None, 443}:
                raise ValueError("13F instrument needs official SEC evidence")
        if not entry.get("issuer_name") or not entry.get("class_description"):
            raise ValueError("13F instrument needs issuer and class")
    return entries


def _member(archive: ZipFile, prefix: str):
    matches = [i for i in archive.infolist()
               if i.filename.rsplit("/", 1)[-1].upper().startswith(prefix)
               and i.filename.lower().endswith((".txt", ".tsv"))]
    if len(matches) != 1 or matches[0].file_size > 1_200_000_000:
        raise ValueError(f"Missing or oversized 13F {prefix} table")
    return matches[0]


class Sec13fScoutService:
    """Sample reconstructed ordinary-share rows for cited CUSIPs only.

    A quarterly SEC archive is self-contained only when the original filing
    and every amendment needed for a manager/report-period chain are present.
    We therefore reconstruct those chains and fail closed for orphaned or
    ambiguous amendments instead of mixing superseded as-filed rows.
    """

    def __init__(self, store: ScoutStore, *, client: Sec13fDatasetClient,
                 securities: list[dict], max_rows: int = 25) -> None:
        if not 1 <= max_rows <= 100:
            raise ValueError("13F result budget must be bounded")
        self.store, self.client, self.securities, self.max_rows = store, client, securities, max_rows

    def run(self, *, as_of: datetime | None = None,
            universe: set[str] | None = None) -> dict[str, int | str]:
        clock = as_of or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("13F observation time needs timezone")
        requested_tickers = set(universe) if universe is not None else {
            entry["ticker"] for entry in self.securities
        }
        eligible = [e for e in self.securities if (universe is None or e["ticker"] in universe)
                    and datetime.fromisoformat(e["known_at"]) <= clock]
        mapped_tickers = {entry["ticker"] for entry in eligible}
        coverage = {
            "identity_coverage_basis": (
                "requested_universe" if universe is not None else "reviewed_manifest"
            ),
            "identity_requested_tickers": len(requested_tickers),
            "identity_mapped_tickers": len(mapped_tickers),
            "identity_unmapped_tickers": len(requested_tickers - mapped_tickers),
            "identity_coverage_ratio": (
                round(len(mapped_tickers) / len(requested_tickers), 6)
                if requested_tickers else 0.0
            ),
        }
        if not eligible:
            return {"status": "WAIT_IDENTITY", "new_findings": 0, **coverage}
        url = _official_zip_url(self.client.latest_url())
        fingerprint = hashlib.sha256(json.dumps(eligible, sort_keys=True).encode()).hexdigest()
        identity_key = f"{url}#{fingerprint}"
        if self.store.dataset_ingested("sec13f", identity_key):
            return {"status": "CURRENT", "new_findings": 0,
                    "dataset_url": url, **coverage}
        data = self.client.dataset(url)
        if not isinstance(data, bytes) or len(data) > MAX_ZIP_BYTES:
            raise ValueError("Invalid 13F ZIP")
        by_cusip = defaultdict(list)
        for entry in eligible:
            by_cusip[entry["cusip"]].append(entry)
        selected = defaultdict(list)
        counts = defaultdict(int)
        unresolved_amendment_groups = 0
        reconstructed_amendment_groups = 0
        with ZipFile(BytesIO(data)) as archive:
            submission = _member(archive, "SUBMISSION")
            coverpage = _member(archive, "COVERPAGE")
            info = _member(archive, "INFOTABLE")
            with archive.open(submission) as raw:
                reader = csv.DictReader(TextIOWrapper(raw, encoding="utf-8-sig"), delimiter="\t")
                if not {"ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"} <= set(reader.fieldnames or []):
                    raise ValueError("Malformed 13F submission columns")
                filings = {}
                for index, row in enumerate(reader):
                    if index >= 200_000:
                        raise ValueError("13F submission budget exceeded")
                    submission_type = row["SUBMISSIONTYPE"].strip().upper()
                    if submission_type not in {"13F-HR", "13F-HR/A"}:
                        continue
                    try:
                        filed = datetime.strptime(row["FILING_DATE"], "%d-%b-%Y").date()
                        period = datetime.strptime(row["PERIODOFREPORT"], "%d-%b-%Y").date()
                    except ValueError:
                        continue
                    accession = row["ACCESSION_NUMBER"].strip()
                    cik = row["CIK"].strip()
                    if (period <= filed <= clock.date() and re.fullmatch(r"\d{1,10}", cik)
                            and re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)):
                        filings[accession] = (cik, filed, period, submission_type)
            with archive.open(coverpage) as raw:
                reader = csv.DictReader(TextIOWrapper(raw, encoding="utf-8-sig"), delimiter="\t")
                required = {"ACCESSION_NUMBER", "ISAMENDMENT", "AMENDMENTNO", "AMENDMENTTYPE"}
                if not required <= set(reader.fieldnames or []):
                    raise ValueError("Malformed 13F cover page columns")
                amendment_types = {}
                for index, row in enumerate(reader):
                    if index >= 200_000:
                        raise ValueError("13F cover page budget exceeded")
                    accession = row["ACCESSION_NUMBER"].strip()
                    if accession not in filings:
                        continue
                    is_amendment = row["ISAMENDMENT"].strip().upper()
                    amendment_type = re.sub(r"\s+", " ", row["AMENDMENTTYPE"].strip().upper())
                    amendment_number = row["AMENDMENTNO"].strip()
                    amendment_types[accession] = (is_amendment, amendment_type, amendment_number)

            groups = defaultdict(list)
            for accession, (cik, filed, period, submission_type) in filings.items():
                groups[(cik, period)].append((filed, accession, submission_type))
            effective_filings = {}
            for (cik, period), chain in groups.items():
                chain.sort()
                initials = [item for item in chain if item[2] == "13F-HR"]
                amendments = [item for item in chain if item[2] == "13F-HR/A"]
                valid = len(initials) == 1 and (not amendments or initials[0] < amendments[0])
                effective = [initials[0]] if valid else []
                for expected_number, item in enumerate(amendments, start=1):
                    flags = amendment_types.get(item[1])
                    if (not flags or flags[0] not in {"Y", "YES", "TRUE", "1"}
                            or flags[2] != str(expected_number)):
                        valid = False
                        break
                    if flags[1] == "RESTATEMENT":
                        effective = [item]
                    elif flags[1] == "NEW HOLDINGS":
                        effective.append(item)
                    else:
                        valid = False
                        break
                if not valid:
                    unresolved_amendment_groups += 1
                    continue
                accessions = [item[1] for item in effective]
                chain_accessions = [item[1] for item in chain]
                if amendments:
                    reconstructed_amendment_groups += 1
                for filed, accession, submission_type in effective:
                    effective_filings[accession] = {
                        "cik": cik,
                        "filed": filed,
                        "period": period,
                        "submission_type": submission_type,
                        "effective_accessions": accessions,
                        "filing_chain": chain_accessions,
                        "amendments_included": bool(amendments),
                    }
            with archive.open(info) as raw:
                reader = csv.DictReader(TextIOWrapper(raw, encoding="utf-8-sig"), delimiter="\t")
                required = {"ACCESSION_NUMBER", "INFOTABLE_SK", "CUSIP", "NAMEOFISSUER", "TITLEOFCLASS",
                            "VALUE", "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"}
                if not required <= set(reader.fieldnames or []):
                    raise ValueError("Malformed 13F info table columns")
                for index, row in enumerate(reader):
                    if index >= 15_000_000:
                        raise ValueError("13F info table budget exceeded")
                    filing = effective_filings.get(row["ACCESSION_NUMBER"])
                    if (not filing or row["CUSIP"] not in by_cusip
                            or row["SSHPRNAMTTYPE"] != "SH" or row["PUTCALL"].strip()):
                        continue
                    for entry in by_cusip[row["CUSIP"]]:
                        if not (entry["effective_from"] <= filing["period"].isoformat() <= entry["effective_to"]):
                            continue
                        if _security_label(row["NAMEOFISSUER"]) != _security_label(entry["issuer_name"]):
                            continue
                        if _security_label(row["TITLEOFCLASS"]) != _security_label(entry["class_description"]):
                            continue
                        try:
                            value, shares = int(row["VALUE"]), int(row["SSHPRNAMT"])
                        except (ValueError, TypeError):
                            continue
                        if value < 0 or shares < 0:
                            continue
                        counts[entry["ticker"]] += 1
                        bucket = selected[entry["ticker"]]
                        bucket.append((value, row, filing, entry))
                        if len(bucket) > self.max_rows:
                            bucket.sort(key=lambda item: item[0], reverse=True)
                            del bucket[self.max_rows:]
        created = 0
        for ticker, bucket in selected.items():
            for value, row, filing, entry in bucket:
                cik, filed, period = filing["cik"], filing["filed"], filing["period"]
                accession = row["ACCESSION_NUMBER"]
                filing_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                              f"{accession.replace('-', '')}/{accession}-index.htm")
                details = {"stage": "13f_ordinary_share_holding", "manager_cik": cik,
                           "issuer_cik": entry["issuer_cik"], "cusip": entry["cusip"],
                           "security_class": entry["class_description"],
                           "filing_date": filed.isoformat(), "period_of_report": period.isoformat(),
                           "as_filed_value_usd": value, "reported_shares": int(row["SSHPRNAMT"]),
                           "information_table_key": row["INFOTABLE_SK"],
                           "cusip_evidence_url": entry["cusip_evidence_url"],
                           "instrument_evidence_url": entry["instrument_evidence_url"],
                           "ticker_evidence_url": entry["ticker_evidence_url"],
                           "instrument_known_at": entry["known_at"],
                           "submission_type": filing["submission_type"],
                           "amendments_included": filing["amendments_included"],
                           "effective_filing_accessions": filing["effective_accessions"],
                           "filing_chain_accessions": filing["filing_chain"],
                           "amendment_reconstruction_scope": "same_quarterly_sec_archive",
                           "top_rows_sample_only": True,
                           "period_is_publication_time": False, "scoring_applied": False}
                digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
                _, fresh = self.store.record_finding(
                    source="sec13f", subject_id=ticker,
                    source_object_id=f"{accession}:{row['INFOTABLE_SK']}", content_hash=digest,
                    title=f"13F {ticker} / manager CIK {cik} / {period.isoformat()}",
                    source_url=filing_url, locator=f"INFOTABLE_SK:{row['INFOTABLE_SK']}",
                    published_at=clock, available_at=clock, observed_at=clock,
                    details=details)
                created += int(fresh)
        self.store.record_specialist_check("sec13f", subject_id="DATASET", identity_key=identity_key,
                                           as_of=clock, candidate_count=sum(counts.values()),
                                           truncated=any(n > self.max_rows for n in counts.values()))
        sampled = any(n > self.max_rows for n in counts.values())
        status = "PARTIAL" if unresolved_amendment_groups else ("SAMPLED" if sampled else "OK")
        return {"status": status,
                "new_findings": created, "matched_rows": sum(counts.values()),
                "saved_rows": sum(len(b) for b in selected.values()),
                "reconstructed_amendment_groups": reconstructed_amendment_groups,
                "unresolved_amendment_groups": unresolved_amendment_groups,
                "dataset_url": url, **coverage}
