"""Archive actual completed batch5 reports without repeating source I/O.

The collection happened before the new runtime hook was adopted. This capture
has today's physical local availability; it does not backdate an archive to the
earlier source completion and is not another LIVE observation.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from market_checker_app.scout_runner import _record_completed_source_run
from market_checker_app.storage.scout_store import ScoutStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-existing", type=Path, required=True)
    args = parser.parse_args()
    if not args.db_existing.is_file():
        parser.error("Retained shared database required; never create or reset quota history")
    output = Path(__file__).resolve().parent
    receipt = output / "source_run_archival.json"
    if receipt.exists():
        print("Existing archival receipt retained; no source or database calls")
        return
    root = output.parents[1]
    store = ScoutStore(args.db_existing)
    records = []
    with patch("socket.socket.connect", side_effect=AssertionError("Offline source archival attempted HTTP")):
        for source, relative, key in (("rss", "evidence/batch5_ir_20261014/runtime_report.json", None),
                ("nhtsa", "evidence/batch5_regulatory_20261014/live_run.json", "result")):
            original = root / relative
            encoded = original.read_bytes()
            document = json.loads(encoded)
            summary = document[key] if key else document
            local_started = datetime.now(timezone.utc)
            archived = _record_completed_source_run(store, source, summary, started_at=local_started)
            if archived["source_archive_status"] != "SAVED":
                raise ValueError("Completed report archive failed")
            name = archived["source_archive_filename"]
            stored = store.db_path.parent / "specialist_source_runs" / source / name
            exported = output / "source_runs" / source / name
            exported.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stored, exported)
            records.append({"source": source, "original_report_path": relative,
                "original_report_sha256": hashlib.sha256(encoded).hexdigest(),
                "original_source_completed_at": document.get("completed_at", document.get("recorded_at")),
                "local_archival_started_at": local_started.isoformat(),
                "local_archival_finished_at": datetime.now(timezone.utc).isoformat(),
                "export_path": exported.relative_to(root).as_posix(),
                "export_sha256": hashlib.sha256(exported.read_bytes()).hexdigest(),
                "status": summary["status"], "original_attempted_requests": summary["attempted_requests"]})
        proof = {"kind": "LATER_LOCAL_ARCHIVAL_OF_COMPLETED_SOURCE_REPORTS", "records": records,
            "created_at": datetime.now(timezone.utc).isoformat(), "new_http_requests": 0,
            "original_source_reports_changed": False, "immediate_at_original_completion": False,
            "source_run_hook_immediate_for_future_invocations": True,
            "local_archive_is_new_live_capture": False, "historical_availability_reconstructed": False,
            "make_model_scope_is_issuer_identity": False, "issuer_attribution_verified": False,
            "publisher_revision_verified": False, "oos_verified": False, "specialists_accepted": 0}
        receipt.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sources": [row["source"] for row in records], "new_http_requests": 0,
                      "receipt": receipt.relative_to(root).as_posix()}))


if __name__ == "__main__":
    main()
