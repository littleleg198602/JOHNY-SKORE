"""Read strict original test blocks from local, hosted or original artifact logs."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--methods", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--run", type=int)
    parser.add_argument("--job", type=int)
    parser.add_argument("--strip-timestamps", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.log.read_bytes()
    text = raw.decode("utf-8")
    if args.strip_timestamps:
        text = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?", "", text, flags=re.M)
    selection = json.loads(args.methods.read_bytes())
    planned = selection["methods"]
    assert len(planned) == selection["new_exact_method_count"]
    starts = list(re.finditer(r"(?m)^test_[^\r\n]+\([^\r\n]+\) \.\.\. ", text))
    verified = []
    for row in planned:
        heading = f"{row['method']} ({row['module']}.{row['class']}.{row['method']}) ... "
        hits = [(i, match) for i, match in enumerate(starts) if match.group(0) == heading]
        assert len(hits) == 1, heading
        i, match = hits[0]
        end = starts[i + 1].start() if i + 1 < len(starts) else text.index("\n----------------------------------------------------------------------", match.start())
        block = text[match.start():end].strip()
        assert re.search(r"(?:\.\.\. |\r?\n)ok\Z", block), heading
        verified.append({"module": row["module"], "class": row["class"], "method": row["method"],
            "original_ok_block_sha256": hashlib.sha256(block.encode()).hexdigest()})
    full = selection["full_discovered_and_executed"]
    assert re.search(rf"(?m)^Ran {full} tests in [\d.]+s\r?\n\r?\nOK\r?$", text)
    proof = {"head_sha": args.head, "run_id": args.run, "job_id": args.job,
        "original_log_bytes": len(raw), "original_log_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_full_test_count": full, "new_exact_methods_original_ok": len(verified), "methods": verified,
        "strict_test_boundary_blocks_used": True, "original_log_rewritten": False,
        "unit_contract_tests_are_not_full_specialist_acceptance": True}
    with args.output.open("x", encoding="utf-8") as stream: stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({k: v for k, v in proof.items() if k != "methods"}))


if __name__ == "__main__":
    main()
