from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata

from market_checker_app.collectors.gleif_client import GleifClient
from market_checker_app.services.company_intelligence_manifest_service import (
    parse_identity_records,
)


def _normalized_name(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return "".join(character for character in ascii_value.upper() if character.isalnum())


def run_identity_pilot(
    manifest_path: Path,
    output_path: Path,
) -> dict[str, object]:
    records, parse_errors = parse_identity_records(
        manifest_path.read_text(encoding="utf-8")
    )
    checks: list[dict[str, object]] = []
    if parse_errors:
        payload = {
            "status": "FAIL",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "manifest": str(manifest_path),
            "configured": len(records),
            "passed": 0,
            "failed": len(parse_errors),
            "parse_errors": parse_errors,
            "checks": checks,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    client = GleifClient()
    for ticker, record in sorted(records.items()):
        expected_isin = str(record.get("isin") or "").strip() or None
        expected_lei = str(record.get("lei") or "").strip() or None
        try:
            identity = client.resolve(lei=expected_lei, isin=expected_isin)
            if identity is None:
                raise RuntimeError("GLEIF exact lookup returned no record")
            if expected_lei and identity.lei != expected_lei:
                raise RuntimeError("GLEIF returned a different LEI")
            if expected_isin and expected_isin not in identity.isins:
                raise RuntimeError("GLEIF mapping does not contain the requested ISIN")
            checks.append(
                {
                    "ticker": ticker,
                    "status": "PASS",
                    "lei": identity.lei,
                    "legal_name": identity.legal_name,
                    "legal_name_match": (
                        _normalized_name(identity.legal_name)
                        == _normalized_name(record.get("name"))
                    ),
                    "source_url": identity.source_url,
                    "name_matching_used": False,
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "ticker": ticker,
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "name_matching_used": False,
                }
            )

    passed = sum(item["status"] == "PASS" for item in checks)
    payload = {
        "status": "PASS" if passed == len(checks) and checks else "FAIL",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "configured": len(records),
        "passed": passed,
        "failed": len(checks) - passed,
        "parse_errors": [],
        "checks": checks,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run exact ten-company GLEIF pilot")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("market_checker_app/data/company_identity_pilot.txt"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/identity_pilot_latest.json"),
    )
    args = parser.parse_args()
    payload = run_identity_pilot(args.manifest, args.output)
    print(
        f"Identity pilot {payload['status']}: "
        f"{payload['passed']}/{payload['configured']} exact GLEIF checks"
    )
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
