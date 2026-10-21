"""Actual explicit CLI/UI source actions, with physical clocks and no providers."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from market_checker_app.specialist_evidence_runner import main

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "runtime"
PLANS = ["SWKS_zalohy_na_vyrobni_kapacitu.json"]


def pin(path):
    body = (ROOT / path).read_bytes()
    return {"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def now():
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    OUT.mkdir(exist_ok=False)
    rows = []
    started = now()
    with patch("socket.socket.connect", side_effect=AssertionError("Source action cannot use HTTP")) as network, \
         patch("sqlite3.connect", side_effect=AssertionError("Source action cannot use SQL")) as sql:
        for name in PLANS:
            plan_name = "evidence/specialist_review_plans/" + name
            cli = OUT / "cli" / name.removesuffix(".json")
            stream = io.StringIO()
            began = now()
            with redirect_stdout(stream):
                main(["review", "--source-root", str(ROOT), "--plan", plan_name,
                      "--archive-root", str(cli)])
            original_stdout = stream.getvalue().encode()
            (cli / "stdout.json").write_bytes(original_stdout)
            report = json.loads(original_stdout)
            rows.append({"action": "CLI", "plan": plan_name, "started_at": began, "finished_at": now(),
                         "archive": str((cli / report["archive_filename"]).relative_to(ROOT)),
                         "archive_sha256": report["archive_sha256"], "kind": report["kind"],
                         "status": report["review"].get("status"),
                         "stdout_sha256": hashlib.sha256(original_stdout).hexdigest()})
            ui = OUT / "ui" / name.removesuffix(".json")
            code = ("from pathlib import Path\nimport streamlit as st\n"
                    "from market_checker_app.specialist_evidence_ui import render_specialist_evidence_actions\n"
                    f"render_specialist_evidence_actions(st,source_root=Path({str(ROOT)!r}),"
                    f"db_path=Path({str(ui / 'never-opened.db')!r}),output_dir=Path({str(ui)!r}))\n")
            began = now()
            app = AppTest.from_string(code).run(timeout=40)
            assert not list(app.exception)
            next(item for item in app.selectbox if item.label == "Uložená vazba k posouzení").set_value(plan_name)
            app.run(timeout=40)
            next(item for item in app.button if item.label == "Zkontrolovat vybrané uložené doklady").click().run(timeout=40)
            assert not list(app.exception)
            archives = list((ui / "captured_identity_reviews").glob("review-*.json"))
            assert len(archives) == 1
            body = archives[0].read_bytes()
            report = json.loads(body)
            assert report["source_http_requests"] == report["registry_adoptions"] == 0
            assert report["full_specialist_acceptance"] is False and not (ui / "never-opened.db").exists()
            rows.append({"action": "UI", "plan": plan_name, "started_at": began, "finished_at": now(),
                         "archive": str(archives[0].relative_to(ROOT)),
                         "archive_sha256": hashlib.sha256(body).hexdigest(), "kind": report["kind"],
                         "status": report["review"].get("status"), "UI_exceptions": 0})
        assert network.call_count == sql.call_count == 0
        result = {"started_at": started, "finished_at": now(), "physical_clocks": True,
                  "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "actions": rows, "HTTP_attempts": network.call_count, "SQL_operations": sql.call_count,
                  "registry_adoptions": 0, "model_or_forecast_runs": 0,
                  "native_user_Windows_run": False, "full_specialist_acceptance": "0/21",
                  "source_pins": [pin(path) for path in (
                     "market_checker_app/services/wafer_capacity_deposit_review.py",
                     "market_checker_app/services/captured_identity_review_service.py",
                     "market_checker_app/specialist_evidence_ui.py")]}
    with (OUT / "runtime_capture.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"actions": len(rows), "HTTP_attempts": 0, "SQL_operations": 0,
                      "archive_count": sum(1 for row in rows if row.get("archive"))}))
