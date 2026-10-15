"""Explicit post-persistence forecast audit, without source discovery or HTTP.

The adapter consumes an already stored normal forecast and a bounded explicit
manifest. It never produces a forecast, scans for source files or substitutes
an event/as_of clock for physical registration. Audit settings are archived
separately from model configuration and cannot change a model cohort or score.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
import hashlib
from itertools import islice
from pathlib import Path, PurePosixPath
import re
import sqlite3

from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.prospective_specialist_history import (
    MAX_SOURCE_REFERENCES, ProspectiveSpecialistHistory, _hash,
    _no_symlink, _publish, _read_json, _seal, _time, physical_clock,
    read_stored_prediction_snapshot,
)
from market_checker_app.services.yahoo_price_provenance_service import load_observation


MANIFEST_SCHEMA_VERSION = "prospective_forecast_source_manifest_v1"
ADAPTER_REPORT_VERSION = "prospective_forecast_registration_adapter_v1"
SETTINGS_SCHEMA_VERSION = "prospective_forecast_audit_settings_v1"
BINDING_SCHEMA_VERSION = "prospective_forecast_audit_binding_v1"
MAX_FORECASTS = 687
MAX_MANIFEST_BYTES = 512_000
MANIFEST_FILENAME = "prospective_source_manifest.json"
_SOURCE_FILE = re.compile(r"(?:yahoo_price_[a-f0-9]{64}|specialist_source_run_[a-f0-9]{64}_[a-f0-9]{64})\.json")
_LABEL_FILE = re.compile(r"prediction_label_([a-f0-9]{64})_[a-f0-9]{64}\.json")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{1,100}")


def _reason(error: Exception) -> str:
    # Never echo arbitrary source bodies, path values or exception excerpts.
    if isinstance(error, (KeyError, TypeError, OverflowError)):
        return "MALFORMED_PROSPECTIVE_ARCHIVE_RECORD"
    message = str(error) if isinstance(error, ValueError) else ""
    return message if _REASON.fullmatch(message) else "PROSPECTIVE_AUDIT_IO_OR_VALIDATION_FAILED"


def _bounded(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_FORECASTS:
        raise ValueError("INVALID_ADAPTER_FORECAST_BOUND")


def _relative_path(root: Path, value: object, *, label: bool = False) -> Path:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_./-]{1,512}", value):
        raise ValueError("INVALID_EXPLICIT_ARCHIVE_PATH")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("EXPLICIT_ARCHIVE_PATH_OUTSIDE_ROOT")
    pattern = _LABEL_FILE if label else _SOURCE_FILE
    if pattern.fullmatch(relative.name) is None:
        raise ValueError("UNSUPPORTED_EXPLICIT_ARCHIVE_FILENAME")
    path = root.joinpath(*relative.parts)
    _no_symlink(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("EXPLICIT_ARCHIVE_PATH_OUTSIDE_ROOT")
    return path


def _load_manifest(root: Path) -> tuple[dict, bytes]:
    _no_symlink(root)
    if not root.is_dir():
        raise ValueError("PROSPECTIVE_AUDIT_ROOT_UNAVAILABLE")
    path = root / MANIFEST_FILENAME
    if not path.is_file():
        raise ValueError("MISSING_EXPLICIT_SOURCE_MANIFEST")
    record, raw = _read_json(path, MAX_MANIFEST_BYTES)
    if (record.get("schema_version") != MANIFEST_SCHEMA_VERSION
            or set(record) != {"schema_version", "forecast_sources", "label_observations"}):
        raise ValueError("UNSUPPORTED_PROSPECTIVE_SOURCE_MANIFEST")
    forecasts, labels = record["forecast_sources"], record["label_observations"]
    if not isinstance(forecasts, list) or not isinstance(labels, list) or len(forecasts) > MAX_FORECASTS or len(labels) > MAX_FORECASTS:
        raise ValueError("PROSPECTIVE_SOURCE_MANIFEST_LIMIT")
    identities = set()
    for row in forecasts:
        if (not isinstance(row, dict) or not {"ticker", "benchmark_ticker", "source_archive_paths"} <= set(row)
                or set(row) - {"ticker", "benchmark_ticker", "source_archive_paths", "snapshot_id"}
                or any(not isinstance(row[key], str) or not re.fullmatch(r"[A-Z0-9.-]{1,20}", row[key]) for key in ("ticker", "benchmark_ticker"))):
            raise ValueError("INVALID_FORECAST_SOURCE_MANIFEST_ENTRY")
        if "snapshot_id" in row:
            _hash(row["snapshot_id"])
        identity = (row["ticker"], row["benchmark_ticker"], row.get("snapshot_id"))
        if identity in identities:
            raise ValueError("AMBIGUOUS_FORECAST_SOURCE_MANIFEST_ENTRY")
        identities.add(identity)
        paths = row["source_archive_paths"]
        if not isinstance(paths, list) or len(paths) > MAX_SOURCE_REFERENCES:
            raise ValueError("FORECAST_SOURCE_REFERENCE_LIMIT")
        for value in paths:
            _relative_path(root, value)
    seen_labels = set()
    for row in labels:
        if not isinstance(row, dict) or set(row) != {"snapshot_id", "path"}:
            raise ValueError("INVALID_EXPLICIT_LABEL_MANIFEST_ENTRY")
        identifier = _hash(row["snapshot_id"])
        path = _relative_path(root, row["path"], label=True)
        if _LABEL_FILE.fullmatch(path.name).group(1) != identifier:
            raise ValueError("EXPLICIT_LABEL_MANIFEST_IDENTITY_MISMATCH")
        if identifier in seen_labels:
            raise ValueError("AMBIGUOUS_EXPLICIT_LABEL_OBSERVATIONS")
        seen_labels.add(identifier)
    return record, raw


def _load_audit(path: Path, *, schema: str, identity: str) -> tuple[dict, int]:
    record, raw = _read_json(path)
    if (record.get("schema_version") != schema or record.get("input_id") != identity
            or record.get("content_hash") != canonical_hash({key: value for key, value in record.items() if key != "content_hash"})
            or record.get("available_at") != record.get("observed_at")):
        raise ValueError("INVALID_PROSPECTIVE_ADAPTER_AUDIT")
    _time(record["observed_at"])
    if record.get("analysis_only") is not True or any(record.get(flag) is not False for flag in (
            "scores_changed", "fitter_changed", "activation_allowed", "historical_acceptance_verified")):
        raise ValueError("PROSPECTIVE_ADAPTER_CANNOT_ASSERT_ACCEPTANCE")
    if schema == SETTINGS_SCHEMA_VERSION:
        encoded = record.get("manifest_raw_utf8")
        if (not isinstance(encoded, str) or hashlib.sha256(encoded.encode()).hexdigest() != record.get("manifest_sha256")
                or canonical_hash({"manifest_sha256": record["manifest_sha256"], "settings": record["settings"]}) != identity):
            raise ValueError("PROSPECTIVE_ADAPTER_SETTINGS_HASH_MISMATCH")
    return record, len(raw)


class ProspectiveForecastRegistrationAdapter:
    """Use only the explicitly configured archive root and manifest entries.

    Clock injection is solely for marked deterministic fixtures. Production
    does not receive any clock from a forecast or its event/as_of fields.
    Existing source archives stay outside the model/fitter configuration.
    """

    def __init__(self, *, db_path: Path, audit_archive_root: Path | None,
                 maximum_forecasts: int = MAX_FORECASTS,
                 observation_clock: Callable[[], datetime] | None = None):
        _bounded(maximum_forecasts)
        self.db_path = Path(db_path)
        self.root = Path(audit_archive_root) if audit_archive_root is not None else None
        self.maximum = maximum_forecasts
        self._raw_clock = observation_clock or physical_clock
        self._last_physical_observation: datetime | None = None
        self._clock = self._physical_now
        self._manifest = None
        self._settings_audit = None
        self.history = (ProspectiveSpecialistHistory(self.root / "prospective_specialist_history",
            observation_clock=self._clock) if self.root is not None else None)

    def _physical_now(self) -> datetime:
        # The recorder and adapter share the same physical high-water mark.
        # A clock rollback cannot make a newly observed manifest prospective
        # after its first forward session has already opened.
        observed = _time(self._raw_clock())
        if self._last_physical_observation is not None and observed < self._last_physical_observation:
            raise ValueError("PROSPECTIVE_PHYSICAL_CLOCK_ROLLBACK")
        self._last_physical_observation = observed
        return observed

    def _report(self, action: str) -> dict:
        return {"report_version": ADAPTER_REPORT_VERSION, "action": action,
            "status": "WAIT", "reason": "", "maximum_forecasts": self.maximum,
            "requested_count": 0, "processed_count": 0, "deferred_at_least": 0,
            "request_count_is_lower_bound": False,
            "registered_count": 0, "registration_replay_count": 0,
            "attached_count": 0, "attachment_replay_count": 0,
            "rows": [], "reason_counts": {}, "manifest_sha256": None,
            "settings_audit_id": None, "settings_observed_at": None,
            "analysis_only": True, "scores_changed": False, "fitter_changed": False,
            "activation_allowed": False, "historical_acceptance_verified": False,
            "source_http_requests": 0, "source_discovery_performed": False,
            "forecasts_created": 0, "feature_source_dependencies_independently_verified": False}

    def _manifest_and_settings(self, report: dict) -> dict:
        if self.root is None:
            raise ValueError("PROSPECTIVE_AUDIT_ROOT_NOT_CONFIGURED")
        read_at = _time(self._clock())
        if self._manifest is None:
            manifest, raw = _load_manifest(self.root)
            read_at = _time(self._clock())
            settings = {"prospective_history_enabled": True,
                "prospective_audit_archive_root": str(self.root.resolve()),
                "maximum_forecasts": self.maximum, "source_selection": "EXPLICIT_MANIFEST_ONLY"}
            raw_hash = hashlib.sha256(raw).hexdigest()
            identity = canonical_hash({"manifest_sha256": raw_hash, "settings": settings})
            record = _seal({"schema_version": SETTINGS_SCHEMA_VERSION, "input_id": identity,
                "observed_at": read_at.isoformat(), "available_at": read_at.isoformat(),
                "settings": settings, "manifest_sha256": raw_hash,
                "manifest_raw_utf8": raw.decode("utf-8"), "analysis_only": True,
                "scores_changed": False, "fitter_changed": False, "activation_allowed": False,
                "historical_acceptance_verified": False}, "content_hash")
            path = self.history.directory / "adapter_audits" / f"settings_{identity}.json"
            saved = _publish(record, path,
                replay_matches=lambda prior: prior["settings"] == settings and prior["manifest_sha256"] == raw_hash
                    and prior["manifest_raw_utf8"] == raw.decode("utf-8"),
                loader=lambda item: _load_audit(item, schema=SETTINGS_SCHEMA_VERSION, identity=identity))
            self._manifest, self._settings_audit = manifest, saved
        if _time(self._settings_audit["observed_at"]) > read_at:
            raise ValueError("AUDIT_SETTINGS_OBSERVATION_AFTER_PHYSICAL_READ")
        report.update(manifest_sha256=self._settings_audit["manifest_sha256"],
            settings_audit_id=self._settings_audit["input_id"], settings_observed_at=self._settings_audit["observed_at"],
            current_manifest_read_at=read_at.isoformat())
        return self._manifest

    def _binding(self, registration: dict) -> dict:
        identity_body = {"registration_id": registration["registration_id"],
            "registration_hash": registration["registration_hash"],
            "settings_audit_id": self._settings_audit["input_id"]}
        identity = canonical_hash(identity_body)
        clock = _time(self._clock())
        if clock < max(_time(registration["registered_at"]), _time(self._settings_audit["observed_at"])):
            raise ValueError("AUDIT_BINDING_BEFORE_PHYSICAL_REGISTRATION")
        record = _seal({"schema_version": BINDING_SCHEMA_VERSION, "input_id": identity,
            **identity_body, "snapshot_id": registration["snapshot_id"],
            "observed_at": clock.isoformat(), "available_at": clock.isoformat(),
            "manifest_sha256": self._settings_audit["manifest_sha256"],
            "registered_at": registration["registered_at"], "analysis_only": True,
            "scores_changed": False, "fitter_changed": False, "activation_allowed": False,
            "historical_acceptance_verified": False}, "content_hash")
        return _publish(record, self.history.directory / "adapter_audits" / f"binding_{identity}.json",
            replay_matches=lambda saved: all(saved[key] == value for key, value in identity_body.items())
                and saved["snapshot_id"] == registration["snapshot_id"],
            loader=lambda path: _load_audit(path, schema=BINDING_SCHEMA_VERSION, identity=identity))

    @staticmethod
    def _finish(report: dict, *, completed: int) -> dict:
        reasons = Counter(row["reason"] for row in report["rows"] if row.get("reason"))
        report["reason_counts"] = dict(sorted(reasons.items()))
        report["processed_count"] = len(report["rows"])
        report["status"] = ("PARTIAL" if report["deferred_at_least"] or reasons and completed
                            else "WAIT" if reasons or not completed else "RECORDED")
        return report

    def register_after_persistence(self, snapshot_ids: Iterable[str]) -> dict:
        """Re-read exact already stored rows; partial commits remain recoverable."""
        report = self._report("REGISTER_STORED_FORECASTS")
        identifiers = list(islice(iter(snapshot_ids), self.maximum + 1))
        report["requested_count"] = len(identifiers)
        report["deferred_at_least"] = max(0, len(identifiers) - self.maximum)
        report["request_count_is_lower_bound"] = bool(report["deferred_at_least"])
        if not identifiers:
            report["reason"] = "NO_PERSISTED_FORECAST_IDS_SUPPLIED"
            return report
        try:
            for identifier in identifiers:
                _hash(identifier)
            manifest = self._manifest_and_settings(report)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, OverflowError) as error:
            report.update(reason=_reason(error))
            return report
        for identifier in dict.fromkeys(identifiers[:self.maximum]):
            row = {"snapshot_id": identifier, "status": "WAIT", "reason": ""}
            report["rows"].append(row)
            try:
                snapshot = read_stored_prediction_snapshot(self.db_path, identifier)
                matches = [entry for entry in manifest["forecast_sources"]
                    if (entry["ticker"], entry["benchmark_ticker"]) == (snapshot["ticker"], snapshot["benchmark_ticker"])
                    and entry.get("snapshot_id", identifier) == identifier]
                if len(matches) != 1:
                    raise ValueError("MISSING_OR_AMBIGUOUS_EXPLICIT_FORECAST_SOURCE_PATHS")
                if not matches[0]["source_archive_paths"]:
                    raise ValueError("MISSING_EXPLICIT_FORECAST_SOURCE_ARCHIVES")
                paths = [_relative_path(self.root, value) for value in matches[0]["source_archive_paths"]]
                if any(not path.is_file() for path in paths):
                    raise ValueError("EXPLICIT_FORECAST_SOURCE_ARCHIVE_UNAVAILABLE")
                for path in paths:
                    if path.name.startswith("yahoo_price_"):
                        source = load_observation(path)
                        if source["ticker"] not in {snapshot["ticker"], snapshot["benchmark_ticker"]}:
                            raise ValueError("FOREIGN_PRICE_SOURCE_ARCHIVE")
                destination = self.history.directory / "forecasts" / f"forecast_{identifier}.json"
                existed = destination.exists()
                # The recorder rereads/validates the complete original snapshot
                # and every source, preserving its strict physical/session gates.
                registration = self.history.register_stored_snapshot(self.db_path, identifier,
                    source_archive_paths=paths)
                row.update(status="REGISTRATION_REPLAY" if existed else "REGISTERED",
                    registration_id=registration["registration_id"], registration_hash=registration["registration_hash"],
                    registered_at=registration["registered_at"],
                    source_reference_count=len(registration["source_references"]),
                    fixture_only=registration["forecast"]["provenance"].get("evidence_origin") == "DETERMINISTIC_TEST_FIXTURE")
                report["registration_replay_count" if existed else "registered_count"] += 1
                binding = self._binding(registration)
                row.update(binding_hash=binding["content_hash"])
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, OverflowError) as error:
                row.update(reason=_reason(error))
                if "registration_id" in row:
                    row["status"] = "REGISTRATION_SAVED_BINDING_PARTIAL"
        return self._finish(report, completed=report["registered_count"] + report["registration_replay_count"])

    def attach_explicit_labels(self) -> dict:
        """Attach only manifest-listed existing label sidecars; never load prices."""
        report = self._report("ATTACH_EXPLICIT_LABEL_OBSERVATIONS")
        try:
            manifest = self._manifest_and_settings(report)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, OverflowError) as error:
            report.update(reason=_reason(error))
            return report
        entries = manifest["label_observations"]
        report["requested_count"] = len(entries)
        report["deferred_at_least"] = max(0, len(entries) - self.maximum)
        for entry in entries[:self.maximum]:
            identifier = entry["snapshot_id"]
            row = {"snapshot_id": identifier, "status": "WAIT", "reason": ""}
            report["rows"].append(row)
            try:
                if not (self.history.directory / "forecasts" / f"forecast_{identifier}.json").is_file():
                    raise ValueError("NO_PRIOR_PROSPECTIVE_FORECAST_REGISTRATION")
                outcome = self.history.attach_label(self.db_path, identifier,
                    label_observation_path=_relative_path(self.root, entry["path"], label=True))
                row.update(status=outcome["status"], reason=outcome.get("reason", ""))
                if outcome["status"] == "LABEL_ATTACHED":
                    record = outcome["record"]
                    row.update(attachment_hash=record["attachment_hash"], usable_at=record["usable_at"],
                        fixture_only=record["label_observation"]["evidence_origin"] == "DETERMINISTIC_TEST_FIXTURE")
                    report["attachment_replay_count" if outcome["replayed"] else "attached_count"] += 1
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, OverflowError) as error:
                row.update(reason=_reason(error))
        if not entries:
            report["reason"] = "NO_EXPLICIT_LABEL_OBSERVATIONS"
        return self._finish(report, completed=report["attached_count"] + report["attachment_replay_count"])

    def read_history_report(self) -> dict:
        if self.history is None:
            return {"status": "WAIT", "reason": "PROSPECTIVE_AUDIT_ROOT_NOT_CONFIGURED",
                    "eligible_real_label_count": 0, "analysis_only": True, "activation_allowed": False}
        try:
            return self.history.read_report(as_of=_time(self._clock()), maximum=self.maximum)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, OverflowError) as error:
            return {"status": "PARTIAL", "reason": _reason(error), "eligible_real_label_count": 0,
                    "analysis_only": True, "activation_allowed": False}


__all__ = ["ProspectiveForecastRegistrationAdapter", "MANIFEST_SCHEMA_VERSION",
           "MANIFEST_FILENAME", "ADAPTER_REPORT_VERSION"]
