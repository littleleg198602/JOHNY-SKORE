"""Prepare explicit prospective-source selections without discovering evidence.

The resulting manifest is adapter configuration, never a forecast, label or
historical acceptance. Existing original archives retain their own physical
clocks and bytes. Cooperative writers serialize publication and compare the
explicit expected current SHA before replacing the active configuration.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.prospective_specialist_history import (
    MAX_SOURCE_BYTES, MAX_SOURCE_REFERENCES, _check_input_clocks, _encoded,
    _hash, _no_symlink, _read_json, _source_references, _time, physical_clock,
)
from market_checker_app.services.specialist_incremental_archive_service import (
    MAX_RUN_ARCHIVE_BYTES, load_specialist_source_run_archive,
)
from market_checker_app.services.specialist_label_availability_service import (
    MAX_LABEL_OBSERVATION_BYTES, load_label_observation,
)
from market_checker_app.services.yahoo_price_provenance_service import MAX_CAPTURE_BYTES, load_observation
from market_checker_app.utils.ticker_universe import load_canonical_ticker_records


MANIFEST_SCHEMA_VERSION = "prospective_forecast_source_manifest_v1"
PREPARATION_SCHEMA_VERSION = "prospective_source_manifest_preparation_v1"
MANIFEST_FILENAME = "prospective_source_manifest.json"
VERSIONS_DIRECTORY = "prospective_source_manifest_versions"
MAX_FORECASTS = 687
MAX_MANIFEST_BYTES = 512_000
MAX_SELECTED_BYTES = MAX_SOURCE_BYTES
_SOURCE_FILE = re.compile(r"(?:yahoo_price_[a-f0-9]{64}|specialist_source_run_[a-f0-9]{64}_[a-f0-9]{64})\.json")
_LABEL_FILE = re.compile(r"prediction_label_([a-f0-9]{64})_[a-f0-9]{64}\.json")
_TICKER = re.compile(r"[A-Z0-9.-]{1,20}")


def _safe_path(path: Path) -> None:
    _no_symlink(path)
    if any(getattr(part, "is_junction", lambda: False)() for part in (path, *path.parents)):
        raise ValueError("HISTORY_SYMLINK_REFUSED")


def _root(path: Path) -> Path:
    path = Path(path)
    _safe_path(path)
    if not path.is_dir():
        raise ValueError("PROSPECTIVE_AUDIT_ROOT_UNAVAILABLE")
    return path.resolve()


def _relative(root: Path, value: object, *, label=False) -> Path:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", value):
        raise ValueError("INVALID_EXPLICIT_ARCHIVE_PATH")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("EXPLICIT_ARCHIVE_PATH_OUTSIDE_ROOT")
    if (_LABEL_FILE if label else _SOURCE_FILE).fullmatch(relative.name) is None:
        raise ValueError("UNSUPPORTED_EXPLICIT_ARCHIVE_FILENAME")
    path = root.joinpath(*relative.parts)
    _safe_path(path)
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("EXPLICIT_ARCHIVE_UNAVAILABLE")
    return path


def _manifest_body(forecasts, labels, *, selected=True) -> tuple[dict, bytes]:
    if (not isinstance(forecasts, list) or not isinstance(labels, list)
            or len(forecasts) > MAX_FORECASTS or len(labels) > MAX_FORECASTS or selected and not forecasts and not labels):
        raise ValueError("PROSPECTIVE_SOURCE_MANIFEST_LIMIT_OR_EMPTY")
    # Inspect the whole requested input before projecting its permitted fields.
    manifest = {"schema_version": MANIFEST_SCHEMA_VERSION,
                "forecast_sources": deepcopy(forecasts), "label_observations": deepcopy(labels)}
    encoded = _encoded(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("PROSPECTIVE_SOURCE_MANIFEST_BYTE_LIMIT")
    canonical = {row["ticker"] for row in load_canonical_ticker_records()}
    identities, grouped = set(), {}
    for row in manifest["forecast_sources"]:
        if (not isinstance(row, dict) or not {"ticker", "benchmark_ticker", "source_archive_paths"} <= set(row)
                or set(row) - {"ticker", "benchmark_ticker", "source_archive_paths", "snapshot_id"}
                or not isinstance(row.get("ticker"), str) or _TICKER.fullmatch(row["ticker"]) is None
                or selected and row["ticker"] not in canonical
                or not isinstance(row.get("benchmark_ticker"), str) or _TICKER.fullmatch(row["benchmark_ticker"]) is None):
            raise ValueError("INVALID_OR_FOREIGN_FORECAST_SOURCE_TICKER")
        if "snapshot_id" in row:
            _hash(row["snapshot_id"])
        identity = (row["ticker"], row["benchmark_ticker"], row.get("snapshot_id"))
        if identity in identities:
            raise ValueError("AMBIGUOUS_FORECAST_SOURCE_MANIFEST_ENTRY")
        identities.add(identity)
        grouped.setdefault(identity[:2], []).append(identity[2])
        paths = row["source_archive_paths"]
        if not isinstance(paths, list) or not (1 if selected else 0) <= len(paths) <= MAX_SOURCE_REFERENCES:
            raise ValueError("MISSING_OR_OVERBOUND_EXPLICIT_SOURCE_ARCHIVES")
        if any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths):
            raise ValueError("DUPLICATE_OR_INVALID_EXPLICIT_SOURCE_PATH")
    if selected and any(None in identifiers and len(identifiers) > 1 for identifiers in grouped.values()):
        raise ValueError("OVERLAPPING_SCOPED_AND_UNSCOPED_FORECAST_ENTRIES")
    label_ids = set()
    for row in manifest["label_observations"]:
        if not isinstance(row, dict) or set(row) != {"snapshot_id", "path"}:
            raise ValueError("INVALID_EXPLICIT_LABEL_MANIFEST_ENTRY")
        identifier = _hash(row["snapshot_id"])
        if identifier in label_ids:
            raise ValueError("AMBIGUOUS_EXPLICIT_LABEL_OBSERVATIONS")
        label_ids.add(identifier)
    return manifest, encoded


def _current(root: Path) -> tuple[dict | None, bytes | None, str | None]:
    path = root / MANIFEST_FILENAME
    _safe_path(path)
    if not path.exists():
        return None, None, None
    if not path.is_file():
        raise ValueError("ACTIVE_MANIFEST_NOT_A_REGULAR_FILE")
    record, raw = _read_json(path, MAX_MANIFEST_BYTES)
    if set(record) != {"schema_version", "forecast_sources", "label_observations"} or record.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("UNSUPPORTED_ACTIVE_SOURCE_MANIFEST")
    # Preserve a valid prior adapter configuration even when it is empty or
    # unready. It is configuration history, never newly accepted evidence.
    _manifest_body(record["forecast_sources"], record["label_observations"], selected=False)
    return record, raw, hashlib.sha256(raw).hexdigest()


def _source_scope(record: dict, reference: dict, row: dict) -> str:
    pair = {row["ticker"], row["benchmark_ticker"]}
    if reference["kind"] == "YAHOO_PRICE_OBSERVATION":
        if record["ticker"] not in pair:
            raise ValueError("FOREIGN_PRICE_SOURCE_ARCHIVE")
        return "EXACT_TICKER_PRICE_REFERENCE"
    archive = record["source_archive"]
    issuer_rows = [*archive["observations"], *archive["checks"], *record["attempts"]]
    if any(item.get("ticker") in pair for item in issuer_rows):
        return "RECORDED_TICKER_REFERENCE_ONLY"
    if record["source"] in {"fred", "eia"} and record["shared_context_observations"]:
        return "SHARED_MACRO_CONTEXT_ONLY"
    if record["source"] == "sec13f" and any(item.get("scope") == "DATASET" and item.get("ticker") is None for item in archive["checks"]):
        return "DATASET_CONTEXT_ONLY"
    raise ValueError("FOREIGN_OR_MISSING_SPECIALIST_TICKER_SCOPE")


@contextmanager
def _writer_lock(root: Path):
    path = root / "prospective_source_manifest.lock"
    _safe_path(path)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("SOURCE_MANIFEST_PUBLICATION_BUSY") from None
    identity = os.fstat(descriptor)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass


def _temporary(root: Path, raw: bytes) -> Path:
    with tempfile.NamedTemporaryFile(dir=root, prefix=".prospective_manifest_", suffix=".tmp", delete=False) as stream:
        path = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)
            raise
    return path


def _immutable_version(root: Path, raw: bytes) -> Path:
    directory = root / VERSIONS_DIRECTORY
    _safe_path(directory)
    if not directory.resolve().is_relative_to(root):
        raise ValueError("MANIFEST_VERSION_DIRECTORY_OUTSIDE_ROOT")
    directory.mkdir(exist_ok=True)
    name = "prospective_source_manifest_" + hashlib.sha256(raw).hexdigest() + ".json"
    path = directory / name
    _safe_path(path)
    temporary = _temporary(directory, raw)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            _, prior = _read_json(path, MAX_MANIFEST_BYTES)
            if prior != raw:
                raise ValueError("IMMUTABLE_SOURCE_MANIFEST_VERSION_CONFLICT") from None
        return path
    finally:
        temporary.unlink(missing_ok=True)


class ProspectiveSourceManifestService:
    """Validate explicit archive selections and publish versioned configuration.

    ``observation_clock`` exists only for deterministic unit fixtures. Normal
    callers use the physical UTC clock; there is no historical/as_of input.
    Preparation does not verify feature completeness or a stored forecast.
    """

    def __init__(self, audit_root: Path, *, observation_clock=None):
        self.root = _root(audit_root)
        self._clock = observation_clock or physical_clock
        self._clock_highwater = None
        self._clock_lock = threading.Lock()

    def _observe(self) -> datetime:
        with self._clock_lock:
            observed = _time(self._clock())
            if self._clock_highwater is not None and observed < self._clock_highwater:
                raise ValueError("SOURCE_MANIFEST_PHYSICAL_CLOCK_REGRESSION")
            self._clock_highwater = observed
            return observed

    def current_manifest_sha256(self) -> str | None:
        return _current(_root(self.root))[2]

    def prepare(self, *, forecast_sources: list[dict], label_observations: list[dict] | None = None) -> dict:
        root = _root(self.root)
        clock = self._observe()
        manifest, raw = _manifest_body(forecast_sources, [] if label_observations is None else label_observations)
        selected, sizes, total = {}, {}, 0
        for row in manifest["forecast_sources"]:
            for relative in row["source_archive_paths"]:
                path = _relative(root, relative)
                selected.setdefault(relative, (path, MAX_CAPTURE_BYTES if path.name.startswith("yahoo_price_") else MAX_RUN_ARCHIVE_BYTES, False))
        for row in manifest["label_observations"]:
            path = _relative(root, row["path"], label=True)
            if _LABEL_FILE.fullmatch(path.name).group(1) != row["snapshot_id"]:
                raise ValueError("EXPLICIT_LABEL_MANIFEST_IDENTITY_MISMATCH")
            selected.setdefault(row["path"], (path, MAX_LABEL_OBSERVATION_BYTES, True))
        # Bound the complete explicitly selected set before any body read.
        for relative, (path, limit, _) in selected.items():
            size = path.stat().st_size
            if not 0 < size <= limit:
                raise ValueError("SELECTED_SOURCE_ARCHIVE_BYTE_LIMIT")
            total += size
            sizes[relative] = size
            if total > MAX_SELECTED_BYTES:
                raise ValueError("SELECTED_SOURCE_TOTAL_BYTE_LIMIT")
        sources, label_pins, records = {}, [], {}
        for relative, (path, limit, is_label) in sorted(selected.items()):
            record, encoded = _read_json(path, limit)
            if len(encoded) != sizes[relative]:
                raise ValueError("SELECTED_SOURCE_CHANGED_DURING_READ")
            pin = {"path": relative, "file_sha256": hashlib.sha256(encoded).hexdigest(), "file_bytes": len(encoded)}
            if is_label:
                checked = load_label_observation(path)
                if checked != record:
                    raise ValueError("SELECTED_LABEL_CHANGED_DURING_READ")
                if (checked.get("ticker") not in {row["ticker"] for row in load_canonical_ticker_records()}
                        or not isinstance(checked.get("benchmark_ticker"), str) or _TICKER.fullmatch(checked["benchmark_ticker"]) is None):
                    raise ValueError("FOREIGN_LABEL_TICKER_SCOPE")
                stamps = [_time(checked.get(key)) for key in ("snapshot_as_of", "observed_at", "available_at", "captured_at")]
                if not stamps[0] <= stamps[1] <= stamps[2] <= stamps[3] <= clock:
                    raise ValueError("FUTURE_OR_INVALID_SELECTED_LABEL_CLOCKS")
                _check_input_clocks(checked, clock)
                label_pins.append({**pin, "snapshot_id": checked["snapshot_id"], "observation_hash": checked["observation_hash"],
                                   "observed_at": checked["observed_at"], "available_at": checked["available_at"],
                                   "captured_at": checked["captured_at"], "evidence_origin": checked["evidence_origin"]})
            else:
                references = _source_references([path], prediction_at=clock)
                if len(references) != 1 or (references[0]["file_sha256"], references[0]["file_bytes"]) != (pin["file_sha256"], pin["file_bytes"]):
                    raise ValueError("SELECTED_SOURCE_CHANGED_DURING_READ")
                checked = load_observation(path) if path.name.startswith("yahoo_price_") else load_specialist_source_run_archive(path)
                if checked != record:
                    raise ValueError("SELECTED_SOURCE_CHANGED_DURING_READ")
                sources[relative] = {**pin, "reference": references[0]}
                records[relative] = record
        scopes = []
        for row in manifest["forecast_sources"]:
            scopes.append({"ticker": row["ticker"], "benchmark_ticker": row["benchmark_ticker"],
                "snapshot_id": row.get("snapshot_id"), "sources": [
                    {"path": path, "scope": _source_scope(records[path], sources[path]["reference"], row)} for path in row["source_archive_paths"]]})
        current_sha = _current(root)[2]
        observed = self._observe()
        result = {"schema_version": PREPARATION_SCHEMA_VERSION, "audit_root": str(root), "prepared_at": observed.isoformat(),
            "source_validation_cutoff": clock.isoformat(),
            "manifest": manifest, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "current_manifest_sha256": current_sha, "source_pins": list(sources.values()),
            "label_pins": label_pins, "forecast_source_scopes": scopes, "selected_total_file_bytes": total,
            "source_discovery_performed": False, "source_http_requests": 0, "forecasts_created": 0, "labels_created": 0,
            "analysis_only": True, "scores_changed": False, "fitter_changed": False,
            "feature_source_coverage_verified": False, "historical_acceptance_verified": False,
            "publisher_authenticity_independently_verified": False}
        result["preparation_hash"] = canonical_hash(result)
        _encoded(result)
        return result

    def publish(self, prepared: dict, *, activate: bool = False, expected_current_sha256: str | None = None) -> dict:
        root = _root(self.root)
        if type(activate) is not bool:
            raise ValueError("EXPLICIT_MANIFEST_ACTIVATION_REQUIRED")
        if expected_current_sha256 is not None:
            _hash(expected_current_sha256)
        if not activate and expected_current_sha256 is not None:
            raise ValueError("EXPECTED_CURRENT_SHA_REQUIRES_ACTIVATION")
        _encoded(prepared)
        if (not isinstance(prepared, dict) or prepared.get("schema_version") != PREPARATION_SCHEMA_VERSION
                or prepared.get("audit_root") != str(root)
                or prepared.get("preparation_hash") != canonical_hash({key:value for key,value in prepared.items() if key != "preparation_hash"})):
            raise ValueError("INVALID_SOURCE_MANIFEST_PREPARATION")
        if _time(prepared["prepared_at"]) > self._observe():
            raise ValueError("SOURCE_MANIFEST_PREPARATION_FROM_FUTURE")
        manifest = prepared["manifest"]
        current = self.prepare(forecast_sources=manifest["forecast_sources"], label_observations=manifest["label_observations"])
        if any(current[key] != prepared[key] for key in ("manifest", "manifest_sha256", "source_pins", "label_pins", "forecast_source_scopes")):
            raise ValueError("SELECTED_SOURCE_CHANGED_AFTER_PREPARATION")
        _, raw = _manifest_body(manifest["forecast_sources"], manifest["label_observations"])
        temporary = None
        publication_clock = self._observe()
        with _writer_lock(root):
            _, previous_raw, previous_sha = _current(root)
            if activate and previous_sha != expected_current_sha256:
                raise ValueError("ACTIVE_SOURCE_MANIFEST_COMPARE_AND_SWAP_FAILED")
            if activate and previous_raw is not None:
                publication_clock = self._observe()
                _immutable_version(root, previous_raw)
            publication_clock = self._observe()
            version = _immutable_version(root, raw)
            changed = False
            if activate and previous_raw != raw:
                publication_clock = self._observe()
                temporary = _temporary(root, raw)
                try:
                    if _current(root)[2] != expected_current_sha256:
                        raise ValueError("ACTIVE_SOURCE_MANIFEST_COMPARE_AND_SWAP_FAILED")
                    _safe_path(root / MANIFEST_FILENAME)
                    publication_clock = self._observe()
                    os.replace(temporary, root / MANIFEST_FILENAME)
                    temporary = None
                    changed = True
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            return {"status": "ACTIVATED" if changed else "ACTIVE_ALREADY_MATCHES" if activate else "VERSION_PREPARED",
                "manifest_sha256": current["manifest_sha256"], "version_path": version.relative_to(root).as_posix(),
                "active_manifest_path": MANIFEST_FILENAME if activate else None, "active_manifest_changed": changed,
                "previous_active_sha256": previous_sha, "published_at": publication_clock.isoformat(),
                "publication_clock_basis": "PHYSICAL_CHECK_BEFORE_ATOMIC_PUBLICATION",
                "source_http_requests": 0, "source_discovery_performed": False, "forecasts_created": 0, "labels_created": 0,
                "analysis_only": True, "scores_changed": False, "fitter_changed": False, "historical_acceptance_verified": False}
