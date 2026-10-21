"""Build the expected method selection; this does not execute a test suite."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from walk(item)
        else:
            yield item.id()


if __name__ == "__main__":
    files = sorted(p for p in (ROOT / "tests").glob("test_*.py")
                   if p.name in {"test_sourcefront21_coordinator_integration.py", "test_wafer_capacity_deposit_review.py", "test_wafer_capacity_deposit_peer.py"})
    methods, pins = [], []
    for path in files:
        suite = unittest.defaultTestLoader.loadTestsFromName("tests." + path.stem)
        if unittest.defaultTestLoader.errors:
            raise RuntimeError(unittest.defaultTestLoader.errors)
        ids = list(walk(suite))
        for name in ids:
            module, cls, method = name.removeprefix("tests.").rsplit(".", 2)
            methods.append({"module": module, "class": cls, "method": method})
        body = path.read_bytes()
        pins.append({"path": path.relative_to(ROOT).as_posix(), "bytes": len(body),
                     "sha256": hashlib.sha256(body).hexdigest(), "expected_methods": len(ids)})
    assert len(files) == 3 and len(methods) == len({tuple(row.values()) for row in methods}) >= 46
    result = {"prepared_at": datetime.now(timezone.utc).isoformat(), "status": "EXPECTED_SELECTION_ONLY",
              "baseline_methods": 2767, "new_exact_method_count": len(methods),
              "expected_full_test_count": 2767 + len(methods), "methods": methods, "files": pins,
              "functional_tests_executed_by_this_script": 0}
    target = ROOT / "evidence/sourcefront21_expected_methods_20261021.json"
    with target.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "new_exact_method_count", "expected_full_test_count")}))
