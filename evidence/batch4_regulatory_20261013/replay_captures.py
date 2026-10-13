"""Offline production-client/collector replay of exact bounded HTTP captures.

The test-local query envelopes are not adopted issuer/product identities.
This script performs no network request and modifies no production registry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from market_checker_app.services.nhtsa_recall_scout_service import NhtsaRecallClient, NhtsaRecallScoutService
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
SCOPES = [
    ("nhtsa_recalls_cadillac_ct4_2026", "GM", "CADILLAC", "CT4", "General Motors, LLC"),
    ("nhtsa_recalls_ford_f650sd_2026", "F", "FORD", "F-650 SD", "Ford Motor Company"),
    ("nhtsa_recalls_chevrolet_trailblazer_2026", "GM", "CHEVROLET", "TRAILBLAZER", "General Motors, LLC"),
    ("nhtsa_recalls_ford_mustang_2026", "F", "FORD", "MUSTANG", "Ford Motor Company"),
]


class ArchivedResponse(BytesIO):
    def __init__(self, body: bytes, metadata: dict):
        super().__init__(body)
        self.metadata = metadata
        self.headers = Message()
        self.headers["Content-Type"] = metadata["content_type"]

    def geturl(self) -> str:
        return self.metadata["final_url"]

    def getcode(self) -> int:
        return self.metadata["http_status"]


def replay() -> dict:
    clock = datetime.now(timezone.utc)
    index = json.loads((FOLDER / "capture_index.json").read_text(encoding="utf-8"))
    captures = {row["capture_id"]: row for row in index["captures"]}
    report = {"schema_version": 1, "kind": "BOUNDED_CAPTURE_OFFLINE_PRODUCTION_REPLAY",
              "replayed_at": clock.isoformat(), "network_requests": 0,
              "production_registry_modified": False, "adopted_identity_records": 0,
              "test_local_query_envelopes_only": True,
              "complete_negative_live": {"status": "NOT_ESTABLISHED", "http200_empty_captures": 0},
              "rows": []}
    for capture_id, ticker, make, model, manufacturer in SCOPES:
        metadata = captures[capture_id]
        body = (ROOT / metadata["archive_path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != metadata["archive_sha256"]:
            raise ValueError("Capture bytes do not match recorded hash")
        entry = {"ticker": ticker, "make": make, "model": model, "model_year": 2026,
                 "manufacturer": manufacturer, "known_at": clock.isoformat(),
                 "product_evidence_url": (
                    "https://www.sec.gov/Archives/edgar/data/37996/000003799626000015/f-20251231.htm" if ticker == "F" else
                    "https://www.sec.gov/Archives/edgar/data/1467858/000119312526344698/d145571d8k.htm")}
        with TemporaryDirectory() as directory:
            store = ScoutStore(Path(directory) / "offline.db")
            if metadata["http_status"] != 200:
                effect = {"side_effect": HTTPError(metadata["requested_url"], metadata["http_status"],
                    "Captured HTTP error", None, BytesIO(body))}
            else:
                effect = {"return_value": ArchivedResponse(body, metadata)}
            with patch("market_checker_app.services.nhtsa_recall_scout_service.urlopen", **effect):
                result = NhtsaRecallScoutService(store, client=NhtsaRecallClient(), models=[entry]).run(as_of=clock)
            with store._connect() as connection:
                checks = [dict(row) for row in connection.execute(
                    "SELECT candidate_count, truncated, checked_at FROM scout_specialist_checks WHERE source='nhtsa'")]
                findings = [dict(row) for row in connection.execute(
                    "SELECT source_object_id, available_at, first_observed_at FROM scout_findings WHERE source='nhtsa'")]
            report["rows"].append({"capture_id": capture_id, "capture_observed_at": metadata["observed_at"],
                "archive_sha256": metadata["archive_sha256"], "original_http_status": metadata["http_status"],
                "query": {"make": make, "model": model, "model_year": 2026}, "result": result,
                "persisted_checks": checks, "persisted_findings": findings,
                "issuer_relationship_adopted": False, "historical_available_time_reconstructed": False,
                "scoring_applied": False})
    return report


if __name__ == "__main__":
    (FOLDER / "replay.json").write_text(json.dumps(replay(), indent=2) + "\n", encoding="utf-8")
