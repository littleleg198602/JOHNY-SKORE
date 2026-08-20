from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

from market_checker_app.collectors.sec_edgar_client import SecEdgarClient
from market_checker_app.collectors.short_report_client import ShortReportClient
from market_checker_app.config import ShortReportSourceConfig
from market_checker_app.weekly_shadow_runner import DEFAULT_RSS_SOURCE


DEFAULT_EXTERNAL_REPORT_URL = (
    "https://muddywatersresearch.com/research/tal/mw-is-short-tal/"
)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _run_check(
    name: str,
    operation: Callable[[], dict[str, object]],
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        details = operation()
        return {
            "name": name,
            "status": "PASS",
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "details": details,
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "FAIL",
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error_type": type(exc).__name__,
            "error": str(exc)[:2_000],
        }


def run_live_source_smoke(
    *,
    tickers: list[str],
    output_path: Path,
    sec_user_agent: str,
    external_report_url: str = DEFAULT_EXTERNAL_REPORT_URL,
    yahoo_client: object | None = None,
    rss_client: object | None = None,
    sec_client: object | None = None,
    report_client: object | None = None,
) -> dict[str, object]:
    """Verify live Yahoo, RSS, SEC and one official short-report source.

    The result contains counts, timestamps, hashes and MIME data only.  Source
    bodies, Yahoo payloads and the SEC contact User-Agent are never persisted.
    """

    normalized_tickers = list(
        dict.fromkeys(str(ticker).strip().upper() for ticker in tickers if ticker)
    )
    if not normalized_tickers:
        raise ValueError("Live smoke vyžaduje alespoň jeden ticker.")
    declared_sec_user_agent = str(sec_user_agent or "").strip()

    if yahoo_client is None:
        from market_checker_app.collectors.yahoo_client import YahooClient

        yahoo = YahooClient(retry_attempts=2, retry_delay_seconds=1.0)
    else:
        yahoo = yahoo_client
    if rss_client is None:
        from market_checker_app.collectors.rss_client import RSSClient

        rss = RSSClient(
            max_items_per_source=10,
            request_timeout_seconds=15.0,
            max_workers=min(6, len(normalized_tickers)),
        )
    else:
        rss = rss_client

    def check_yahoo() -> dict[str, object]:
        rows_by_ticker: dict[str, int] = {}
        warnings: list[str] = []
        for ticker in normalized_tickers:
            history, warning = yahoo.fetch_ohlc_only(
                ticker,
                period="1mo",
                interval="1d",
            )
            if warning:
                warnings.append(str(warning))
            if history is None or history.empty or "Close" not in history.columns:
                raise RuntimeError(f"Yahoo OHLC je prázdné pro {ticker}.")
            rows_by_ticker[ticker] = int(len(history))
        snapshot, metadata_warning = yahoo.fetch_metadata(normalized_tickers[0])
        if snapshot.status not in {"ok", "partial"}:
            raise RuntimeError(
                f"Yahoo metadata mají stav {snapshot.status} pro {normalized_tickers[0]}."
            )
        if metadata_warning:
            warnings.append(str(metadata_warning))
        return {
            "ticker_rows": rows_by_ticker,
            "metadata_ticker": normalized_tickers[0],
            "metadata_status": snapshot.status,
            "warning_count": len(warnings),
        }

    def check_rss() -> dict[str, object]:
        sources = [
            DEFAULT_RSS_SOURCE.replace("{ticker}", ticker)
            for ticker in normalized_tickers
        ]
        items, warnings = rss.collect(sources, normalized_tickers)
        observed = {item.ticker for item in items}
        missing = sorted(set(normalized_tickers).difference(observed))
        if not items:
            raise RuntimeError("Živý RSS nevrátil žádnou datovanou položku.")
        if missing:
            raise RuntimeError(
                "Živý RSS nevrátil položku pro tickery: " + ", ".join(missing)
            )
        return {
            "source_count": len(sources),
            "item_count": len(items),
            "covered_tickers": sorted(observed),
            "warning_count": len(warnings),
        }

    def check_sec() -> dict[str, object]:
        if not declared_sec_user_agent or "@" not in declared_sec_user_agent:
            raise RuntimeError(
                "JOHNY_SKORE_SEC_USER_AGENT musí obsahovat název aplikace "
                "a kontaktní e-mail podle SEC fair-access pravidel."
            )
        client = sec_client or SecEdgarClient(
            user_agent=declared_sec_user_agent,
            timeout_seconds=20.0,
            min_request_interval_seconds=0.125,
        )
        ticker = normalized_tickers[0]
        bundle = client.fetch_company_bundle(
            ticker,
            allowed_forms=("10-K", "10-Q", "8-K"),
            max_filings=3,
            concepts=("Revenues", "NetIncomeLoss", "Assets"),
            max_facts_per_concept=2,
        )
        if bundle is None:
            raise RuntimeError(f"SEC nerozpoznal ticker {ticker}.")
        if not bundle.filings:
            raise RuntimeError(f"SEC nevrátil filing pro {ticker}.")
        if not bundle.facts:
            raise RuntimeError(f"SEC nevrátil žádný XBRL fakt pro {ticker}.")
        return {
            "ticker": ticker,
            "cik": bundle.company.cik,
            "filing_count": len(bundle.filings),
            "fact_count": len(bundle.facts),
            "warning_count": len(bundle.warnings),
        }

    def check_external_report() -> dict[str, object]:
        client = report_client or ShortReportClient(
            user_agent="JohnySkore/2.1 production-source-smoke",
            timeout_seconds=20.0,
            max_download_bytes=8_000_000,
            max_text_characters=500_000,
        )
        fetched = client.fetch(
            ShortReportSourceConfig(
                ticker="TAL",
                publisher="Muddy Waters Research",
                published_at=datetime(2018, 6, 13, tzinfo=timezone.utc),
                url=external_report_url,
                discovery_method="production_smoke",
            )
        )
        searchable = f"{fetched.title} {fetched.text}".casefold()
        if len(fetched.text) < 200 or "tal" not in searchable:
            raise RuntimeError(
                "Externí report neobsahuje očekávaný strojově čitelný obsah."
            )
        return {
            "publisher": "Muddy Waters Research",
            "ticker": "TAL",
            "final_url": fetched.final_url,
            "mime_type": fetched.mime_type,
            "content_hash": fetched.content_hash,
            "size_bytes": fetched.size_bytes,
            "extractor": fetched.extractor,
        }

    checks = [
        _run_check("yahoo", check_yahoo),
        _run_check("rss", check_rss),
        _run_check("sec_edgar", check_sec),
        _run_check("external_short_report", check_external_report),
    ]
    if declared_sec_user_agent:
        for check in checks:
            if "error" in check:
                check["error"] = str(check["error"]).replace(
                    declared_sec_user_agent,
                    "<redacted-sec-user-agent>",
                )
    failed = [check["name"] for check in checks if check["status"] != "PASS"]
    payload: dict[str, object] = {
        "schema_version": 1,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not failed else "FAIL",
        "ticker_count": len(normalized_tickers),
        "tickers": normalized_tickers,
        "checks": checks,
        "failed_checks": failed,
        "raw_source_content_persisted": False,
        "sec_user_agent_persisted": False,
    }
    _atomic_json(output_path, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Produkční smoke živého Yahoo, RSS, SEC a short-report zdroje."
    )
    parser.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT", "SOFI"])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/live_source_smoke_latest.json"),
    )
    parser.add_argument(
        "--external-report-url",
        default=os.getenv(
            "JOHNY_SKORE_SMOKE_SHORT_REPORT_URL",
            DEFAULT_EXTERNAL_REPORT_URL,
        ),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    payload = run_live_source_smoke(
        tickers=args.tickers,
        output_path=args.output,
        sec_user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
        external_report_url=args.external_report_url,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload["status"] != "PASS":
        failed = ", ".join(str(item) for item in payload["failed_checks"])
        raise SystemExit(f"[LIVE SMOKE CHYBA] Selhaly zdroje: {failed}")


if __name__ == "__main__":
    main()
