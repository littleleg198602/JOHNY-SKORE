"""Actual physical-clock CLI/UI reviews; no collection or identity adoption."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market_checker_app.specialist_evidence_runner import main
from streamlit.testing.v1 import AppTest

PLANS = [
    "P_puvodni_certifikat_ClassA_CUSIP.json",
    "PFE_datovany_seznam_dcer_a_applicant.json",
    "AAPL_strany_datovanych_soudnich_dokumentu.json",
    "SWKS_puvodni_konsolidovane_trzby.json",
    "MSFT_datovane_zivotopisne_role.json",
]
DB = ROOT.parent / "JOHNY-batch4-integration-20261013/outputs/market_checker_history.db"
OUT = ROOT / "evidence/batch10_coordinator_20261018/runtime"


def save(name, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with (OUT / name).open("xb") as handle: handle.write(raw)
    return {"path": (OUT / name).relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def check_review(report):
    assert report["source_http_requests"] == report["registry_adoptions"] == 0
    assert report["full_specialist_acceptance"] is False and report["scoring_applied"] is False
    archive = OUT / "source_archives" / report["archive_filename"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == report["archive_sha256"]


def main_capture():
    OUT.mkdir(exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    source_actions = []
    counters = {"network_attempts": 0, "source_review_SQL_attempts": 0, "readiness_readonly_SQL_opens": 0}
    def no_network(*args, **kwargs):
        counters["network_attempts"] += 1
        raise AssertionError("No runtime collection allowed")
    def no_SQL(*args, **kwargs):
        counters["source_review_SQL_attempts"] += 1
        raise AssertionError("No source review database access")
    with patch.object(socket.socket, "connect", no_network), patch("sqlite3.connect", no_SQL):
        for name in PLANS:
            stream = io.StringIO()
            main_args = ["review", "--source-root", str(ROOT), "--plan", "evidence/specialist_review_plans/" + name,
                "--archive-root", str(OUT / "source_archives")]
            with redirect_stdout(stream): main(main_args)
            text = stream.getvalue()
            with (OUT / (name[:-5] + "_CLI.stdout.json")).open("x", encoding="utf-8") as handle: handle.write(text)
            report = json.loads(text); check_review(report)
            source_actions.append({"interface": "CLI", "plan": name, "kind": report["kind"],
                "recorded_at": report["recorded_at"], "archive_sha256": report["archive_sha256"]})
        code = ("from pathlib import Path\nimport streamlit as st\n"
            "from market_checker_app.specialist_evidence_ui import render_specialist_evidence_actions\n"
            f"render_specialist_evidence_actions(st,source_root=Path({str(ROOT)!r}),db_path=Path({str(DB)!r}),output_dir=Path({str(OUT)!r}))\n")
        app = AppTest.from_string(code).run(timeout=40)
        assert not app.exception
        for name in PLANS:
            next(w for w in app.selectbox if w.key == "captured_identity_plan").set_value("evidence/specialist_review_plans/" + name)
            app.run(timeout=40)
            next(b for b in app.button if b.label == "Zkontrolovat vybrané uložené doklady").click().run(timeout=40)
            assert not app.exception and not app.error
            report = app.session_state["captured_identity_review"]["report"]
            archive = OUT / "captured_identity_reviews" / report["archive_filename"]
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == report["archive_sha256"]
            assert report["source_http_requests"] == report["registry_adoptions"] == 0
            assert report["full_specialist_acceptance"] is False and report["scoring_applied"] is False
            output = save(name[:-5] + "_UI.json", {"report": report,
                "rendered_captions": [row.value for row in app.caption], "UI_exceptions": [], "UI_errors": []})
            source_actions.append({"interface": "STREAMLIT_UI", "plan": name, "kind": report["kind"],
                "recorded_at": report["recorded_at"], "output": output, "archive_sha256": report["archive_sha256"]})
    original_connect = sqlite3.connect
    def readonly_SQL(uri, *args, **kwargs):
        assert isinstance(uri, str) and uri.endswith("?mode=ro") and kwargs.get("uri") is True
        counters["readiness_readonly_SQL_opens"] += 1
        return original_connect(uri, *args, **kwargs)
    db_before = hashlib.sha256(DB.read_bytes()).hexdigest()
    sidecars_before = {s: hashlib.sha256(Path(str(DB) + s).read_bytes()).hexdigest() if Path(str(DB) + s).is_file() else None
        for s in ("-wal", "-shm", "-journal")}
    with patch.object(socket.socket, "connect", no_network), patch("sqlite3.connect", readonly_SQL):
        stream = io.StringIO()
        with redirect_stdout(stream):
            main(["windows-readiness", "--enable", "--output", str(OUT / "native_CLI_readiness_receipt.json"),
                "--tickers", "AAPL", "--db-path", str(DB)])
        cli = json.loads(stream.getvalue())
        with (OUT / "native_CLI.stdout.json").open("x", encoding="utf-8") as handle: handle.write(stream.getvalue())
        code = ("from pathlib import Path\nimport streamlit as st\n"
            "from market_checker_app.specialist_evidence_ui import render_user_windows_readiness\n"
            f"render_user_windows_readiness(st,db_path=Path({str(DB)!r}),output_dir=Path({str(OUT)!r}))\n")
        app = AppTest.from_string(code).run(timeout=30)
        assert not app.exception and app.checkbox[0].value is False
        app.checkbox[0].set_value(True).run(timeout=30)
        app.text_input[0].set_value("AAPL")
        next(b for b in app.button if b.label == "Uložit záznam připravenosti").click().run(timeout=30)
        assert not app.exception and not app.error
        ui = app.session_state["saved_user_windows_readiness_capture"]["report"]
        save("native_UI_readiness.json", {"report": ui, "UI_exceptions": [], "UI_errors": []})
        for report in (cli, ui):
            assert report["scope"] == "READINESS_CAPTURE" and report["receipt_created"] is True
            receipt = report["receipt"]
            assert receipt["user_windows_end_to_end_verified"] is False and receipt["full_specialist_acceptance"] is False
            assert receipt["same_process_production_run_receipt"] == "ABSENT"
            if sidecars_before["-journal"]:
                assert receipt["database"]["SQL_opened"] is False
                assert all(row["present"] is None and row["sampled_rows"] is None for row in receipt["database"]["tables"].values())
    assert hashlib.sha256(DB.read_bytes()).hexdigest() == db_before
    assert sidecars_before == {s: hashlib.sha256(Path(str(DB) + s).read_bytes()).hexdigest() if Path(str(DB) + s).is_file() else None
        for s in ("-wal", "-shm", "-journal")}
    assert counters["network_attempts"] == counters["source_review_SQL_attempts"] == 0
    report = {"schema_version": "batch10_actual_runtime_capture_v1", "started_at": started,
        "ended_at": datetime.now(timezone.utc).isoformat(), "actual_source_review_actions": source_actions,
        "actual_readiness_actions": 2, "readiness_is_not_user_Windows_end_to_end": True,
        "retained_db_before_after_sha256": db_before, "unchanged_sidecar_pins": sidecars_before,
        "journal_replayed_or_removed": False, "current_database_table_and_forecast_counts": "UNKNOWN_UNOBSERVED",
        "counters": counters, "new_forecasts_registrations_labels_adoptions": 0,
        "full_specialist_acceptance": "0/21"}
    output = save("runtime_capture.json", report)
    print(json.dumps({"actual_source_reviews": len(source_actions), "actual_readiness_actions": 2, "counters": counters, "output": output}))


if __name__ == "__main__":
    main_capture()
