"""Prepare or execute one explicitly selected bounded market-input acquisition."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Připraví nebo provede jednu vybranou dávku původních cenových vstupů.")
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--plan-file", type=Path,
        help="Existující přesný plán; nutný pro výslovné provedení dávky.")
    parser.add_argument("--execute", action="store_true",
        help="Provede výslovně vybraný plán; nejvýše13 požadavků bez opakování.")
    args = parser.parse_args(argv)
    if args.execute and args.plan_file is None:
        parser.error("Pro stažení vyber existující plán pomocí --plan-file.")
    from market_checker_app.services.captured_market_input_acquisition import CapturedMarketInputAcquisition
    acquisition = CapturedMarketInputAcquisition(archive_root=args.archive_root, enabled=args.execute)
    if args.plan_file is None:
        result = acquisition.plan()
    else:
        from market_checker_app.services.prospective_specialist_history import _read_json
        plan, _ = _read_json(args.plan_file, 65_536)
        if args.execute:
            result = acquisition.acquire(plan, execute=True)
        else:
            result = {"status": "PREVIEW_ONLY", "source_http_requests": 0, "plan": plan}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
