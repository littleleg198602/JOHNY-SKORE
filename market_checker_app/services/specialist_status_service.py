"""Versioned implementation inventory, separate from observed source runs."""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_STATUS_PATH = Path(__file__).resolve().parents[1] / "data" / "specialist_status.json"
CODE_STATES = {"NOT_STARTED", "PILOT", "PARTIAL", "DONE"}
LIVE_STATES = {"PENDING", "WAIT_ACCESS", "VERIFIED"}


def load_specialist_status(path: Path = DEFAULT_STATUS_PATH) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not payload.get("completion_rule"):
        raise ValueError("Specialist inventory has no supported schema or completion rule")
    rows = payload.get("specialists")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Specialist inventory is empty")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not all(row.get(key) for key in ("id", "name", "research", "next")):
            raise ValueError("Specialist inventory contains an incomplete row")
        if row["id"] in seen:
            raise ValueError(f"Duplicate specialist ID: {row['id']}")
        seen.add(row["id"])
        if row.get("code") not in CODE_STATES or row.get("live") not in LIVE_STATES:
            raise ValueError(f"Unsupported state for specialist: {row['id']}")
        if row["code"] == "DONE":
            if row["live"] != "VERIFIED" or not all(
                row.get(key) for key in (
                    "identity_evidence",
                    "positive_live_evidence", "negative_live_evidence",
                    "windows_run_evidence", "coverage_evidence",
                    "historical_evaluation_evidence",
                )
            ):
                raise ValueError(
                    f"Specialist {row['id']} cannot be DONE without live, "
                    "Windows, coverage and evaluation evidence"
                )
    return payload
