"""Current role review qualification; all original batch10 artifacts stay immutable."""
import hashlib
from pathlib import Path
import json

from evidence.batch10_sec_20261018.build_review import (
    BODY_PATHS, PINS, SELECTION, load_inputs,
    build_review_artifact as _original_producer,
    build_review_plan as _original_plan,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def build_review_artifact(*, replay_clocks=None):
    report = _original_producer(replay_clocks=replay_clocks)
    report.update(role_review_revision=2,
        current_producer_pin=pin(Path(__file__)),
        original_producer_pin=pin(REPO / "evidence/batch10_sec_20261018/build_review.py"),
        original_review_v1_pin=pin(REPO / "evidence/batch10_sec_20261018/person_role_claim_review_v1.json"),
        guard_changes=["Selected role cell must contain the first visible career-table content, with no other biography markers",
            "Original filing-day and acceptance labels/values must be visible in their own index div scopes",
            "Interpretation completed after an explicit cutoff returns no source-supported role facts"],
        original_role_source_bytes_reused_without_new_http=True)
    return report


def build_review_plan():
    return _original_plan()


if __name__ == "__main__":
    for name, report in (("person_role_claim_review_v2.json", build_review_artifact()),
                         ("actual_review_plan.json", build_review_plan())):
        with (ROOT / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(name)
