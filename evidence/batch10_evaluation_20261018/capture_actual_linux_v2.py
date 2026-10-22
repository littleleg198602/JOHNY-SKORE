"""Capture the final null-on-unobserved producer without altering prior proof.

This reuses the byte-pinned original offline runner with a NEW output directory.
No actual source acquisition, SQL initialization, journal replay or run occurs.
"""
from pathlib import Path
import importlib.util


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("original_actual_capture_runner", HERE / "capture_actual_linux.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.HERE = HERE / "actual_linux_v2"
runner.HERE.mkdir(exist_ok=False)
runner.OUTPUT = runner.HERE / "actual_linux_readiness.json"
runner.REPORT = runner.HERE / "actual_capture_execution.json"
runner.main()
