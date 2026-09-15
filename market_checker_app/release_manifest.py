from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


RELEASE_MANIFEST_SCHEMA_VERSION = "release_manifest_v1"
BUILD_DATE = "2026-09-15"

# The legacy v2.1 identifiers are frozen for historical comparison only.
LEGACY_BASELINE_MODEL_ID = "legacy_v2.1_heuristic"
LEGACY_BASELINE_MODEL_VERSION = "v2.1_guarded_consensus"

# This is still a heuristic analytical model, not a trained or calibrated ML
# model. The version is new because the scoring/data contract has changed.
ACTIVE_SCORING_VERSION = "v2.2_session_aware_consensus"
ACTIVE_MODEL_ID = "heuristic_consensus"
ACTIVE_MODEL_VERSION = "v2.2_session_aware_consensus"
FEATURE_SET_VERSION = "features_v2_session_aware_target_v3"


def _json_default(value: Any) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def config_hash(config: object | None) -> str | None:
    """Hash an effective runtime config without exporting its raw contents."""

    if config is None:
        return None
    if is_dataclass(config):
        payload = asdict(config)
    elif isinstance(config, Mapping):
        payload = dict(config)
    else:
        payload = vars(config) if hasattr(config, "__dict__") else str(config)
    return canonical_hash(payload)


def _committed_runtime_config_hash() -> str:
    """Hash the versioned unattended runtime configuration as safe fallback."""

    path = Path(__file__).resolve().parent / "autonomous_runtime.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return canonical_hash({"autonomous_runtime": "UNAVAILABLE"})
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
    return canonical_hash(payload)


def _git_sha() -> str | None:
    for env_name in ("GITHUB_SHA", "JOHNY_SKORE_CODE_SHA"):
        value = str(os.getenv(env_name) or "").strip()
        if value:
            return value
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def build_release_manifest(
    *,
    config: object | None = None,
    code_sha: str | None = None,
    target_version: str | None = None,
) -> dict[str, object]:
    """Build a traceable, non-secret release/version identity for one result."""

    if target_version is None:
        # Lazy import avoids a cycle: prediction_contract imports the version
        # constants above for new snapshots.
        from market_checker_app.prediction_contract import PRIMARY_TARGET_VERSION

        target_version = PRIMARY_TARGET_VERSION

    effective_config_hash = config_hash(config)
    config_hash_kind = "effective_runtime"
    if effective_config_hash is None:
        effective_config_hash = _committed_runtime_config_hash()
        config_hash_kind = "committed_runtime_config"

    manifest: dict[str, object] = {
        "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
        "built_at": BUILD_DATE,
        "code_sha": str(code_sha or _git_sha() or "UNKNOWN").strip(),
        "config_hash": effective_config_hash,
        "config_hash_kind": config_hash_kind,
        "scoring_version": ACTIVE_SCORING_VERSION,
        "model_id": ACTIVE_MODEL_ID,
        "model_version": ACTIVE_MODEL_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "target_version": str(target_version),
        "legacy_baseline_model_id": LEGACY_BASELINE_MODEL_ID,
        "legacy_baseline_model_version": LEGACY_BASELINE_MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "generated_at"}
    )
    return manifest
