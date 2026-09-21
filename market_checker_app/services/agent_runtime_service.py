from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from market_checker_app.config import DEFAULT_OUTPUT_DIR


RUNTIME_CONFIG_ENV = "JOHNY_SKORE_AGENT_RUNTIME_CONFIG"
DEFAULT_RUNTIME_CONFIG_PATH = DEFAULT_OUTPUT_DIR / "agent_runtime.json"
MAX_SOURCE_TEXT_CHARACTERS = 2_000_000
_MANIFEST_LINE_ERROR = re.compile(r"\břádek\s+(\d+)\b", re.IGNORECASE)


def quarantine_invalid_manifest_lines(value: str, errors: list[str]) -> str:
    """Remove parser-rejected rows before persisting an optional manifest.

    Manifest parsers report one-based line numbers.  Keeping the original text
    in the Streamlit widget lets the operator see and correct it during the
    current render, while the durable unattended configuration receives only
    rows that were not rejected.  If an error has no identifiable line number,
    fail closed for that manifest and persist no rows at all.
    """

    if not errors:
        return str(value or "")
    rejected: set[int] = set()
    for error in errors:
        match = _MANIFEST_LINE_ERROR.search(str(error))
        if match is None:
            return ""
        rejected.add(int(match.group(1)))
    return "\n".join(
        line
        for line_number, line in enumerate(str(value or "").splitlines(), start=1)
        if line_number not in rejected
    )


@dataclass(slots=True)
class AgentRuntimeSettings:
    """Durable, non-secret switches and source manifests used by the UI/runner."""

    stage4_shadow_enabled: bool = True
    identity_records_text: str = ""
    sec_fundamentals_enabled: bool = False
    european_filings_enabled: bool = False
    european_filing_sources_text: str = ""
    european_filing_feeds_text: str = ""
    european_allowed_hosts_text: str = ""
    financial_forensics_enabled: bool = True
    short_reports_enabled: bool = False
    auto_discover_short_reports: bool = True
    verify_short_report_claims: bool = True
    short_report_sources_text: str = ""
    supply_chain_enabled: bool = False
    auto_discover_supply_chain_from_sec: bool = True
    supply_chain_sources_text: str = ""
    counterparty_health_sources_text: str = ""
    commodity_energy_enabled: bool = False
    auto_discover_commodity_energy_from_sec: bool = True
    commodity_energy_sources_text: str = ""
    macro_observations_text: str = ""
    regulatory_contract_enabled: bool = False
    auto_discover_regulatory_events: bool = True
    regulatory_contract_sources_text: str = ""

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> AgentRuntimeSettings:
        allowed = {item.name: item for item in fields(cls)}
        unknown = sorted(set(raw).difference(allowed))
        if unknown:
            raise ValueError(f"neznámé položky: {', '.join(unknown)}")
        values: dict[str, Any] = {}
        defaults = cls()
        for name in allowed:
            default = getattr(defaults, name)
            value = raw.get(name, default)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{name} musí být true/false")
            elif isinstance(default, str):
                if not isinstance(value, str):
                    raise ValueError(f"{name} musí být text")
                if len(value) > MAX_SOURCE_TEXT_CHARACTERS:
                    raise ValueError(f"{name} překročil bezpečný limit")
            values[name] = value
        return cls(**values)


def default_runtime_config_path() -> Path:
    raw = os.getenv(RUNTIME_CONFIG_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_RUNTIME_CONFIG_PATH


class AgentRuntimeService:
    """Load and atomically save agent settings without persisting secrets."""

    schema_version = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or default_runtime_config_path())

    def load(self) -> tuple[AgentRuntimeSettings, str | None]:
        if not self.path.exists():
            return AgentRuntimeSettings(), None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("kořen konfigurace není JSON objekt")
            version = payload.get("schema_version", self.schema_version)
            if version != self.schema_version:
                raise ValueError(f"nepodporovaná verze konfigurace {version}")
            if "settings" in payload:
                unknown_root = sorted(
                    set(payload).difference(
                        {"schema_version", "updated_at", "settings"}
                    )
                )
                if unknown_root:
                    raise ValueError(
                        f"neznámé kořenové položky: {', '.join(unknown_root)}"
                    )
                settings_raw = payload["settings"]
            else:
                settings_raw = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"schema_version", "updated_at"}
                }
            if not isinstance(settings_raw, dict):
                raise ValueError("settings není JSON objekt")
            return AgentRuntimeSettings.from_mapping(settings_raw), None
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return (
                AgentRuntimeSettings(),
                f"Agentní nastavení {self.path} nelze načíst; používám bezpečné výchozí hodnoty: {exc}",
            )

    def save(self, settings: AgentRuntimeSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "settings": asdict(settings),
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
