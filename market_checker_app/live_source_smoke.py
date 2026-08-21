from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

from market_checker_app.collectors.gleif_client import GLEIF_API_ROOT, GleifClient
from market_checker_app.collectors.sec_edgar_client import (
    SEC_SUBMISSIONS_URL,
    SecEdgarClient,
)
from market_checker_app.collectors.short_report_client import ShortReportClient
from market_checker_app.config import ShortReportSourceConfig
from market_checker_app.services.agent_runtime_service import AgentRuntimeService
from market_checker_app.services.company_intelligence_manifest_service import (
    parse_identity_records,
)
from market_checker_app.services.short_report_manifest_service import (
    parse_short_report_sources,
)
from market_checker_app.services.watchlist_service import (
    DEFAULT_PRODUCTION_WATCHLIST_PATH,
    WatchlistError,
    apply_ticker_limit,
    load_watchlist,
    normalize_watchlist,
)
from market_checker_app.weekly_shadow_runner import DEFAULT_RSS_SOURCE


DEFAULT_RUNTIME_CONFIG_PATH = Path("market_checker_app/autonomous_runtime.json")


def _exact_name(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def verify_company_identity_pilot(
    *,
    identity_records: Mapping[str, Mapping[str, object]],
    sec_user_agent: str,
    minimum_records: int = 10,
    universe_tickers: Sequence[str] | None = None,
    sec_client: object | None = None,
    gleif_client: object | None = None,
) -> dict[str, object]:
    """Resolve exact production identities against SEC or GLEIF.

    A name is never used to discover an entity. It is compared only after an
    exact CIK/LEI/ISIN lookup succeeds. Any mismatch fails the whole canary.
    """

    records = {
        str(ticker).strip().upper(): dict(record)
        for ticker, record in identity_records.items()
        if str(ticker).strip()
    }
    if len(records) < max(1, int(minimum_records)):
        raise RuntimeError(
            "Identity pilot vyžaduje alespoň "
            f"{max(1, int(minimum_records))} přesných produkčních identit; "
            f"nalezeno {len(records)}."
        )

    normalized_universe: list[str] = []
    if universe_tickers is not None:
        normalized_universe = normalize_watchlist(universe_tickers)
        outside_universe = sorted(set(records).difference(normalized_universe))
        if outside_universe:
            raise RuntimeError(
                "Identity pilot obsahuje tickery mimo produkční watchlist: "
                + ", ".join(outside_universe)
            )

    needs_sec = any(
        str(item.get("cik") or "").strip() for item in records.values()
    )
    needs_gleif = any(
        str(item.get("lei") or item.get("isin") or "").strip()
        for item in records.values()
    )
    if needs_sec and sec_client is None:
        declared_user_agent = str(sec_user_agent or "").strip()
        if not declared_user_agent or "@" not in declared_user_agent:
            raise RuntimeError(
                "JOHNY_SKORE_SEC_USER_AGENT musí obsahovat název aplikace "
                "a kontaktní e-mail pro SEC identity canary."
            )
        sec_client = SecEdgarClient(
            user_agent=declared_user_agent,
            timeout_seconds=20.0,
            min_request_interval_seconds=0.125,
        )
    if needs_gleif and gleif_client is None:
        gleif_client = GleifClient(timeout_seconds=20.0)

    verified: list[dict[str, object]] = []
    for ticker in sorted(records):
        record = records[ticker]
        expected_name = str(record.get("name") or "").strip()
        cik = str(record.get("cik") or "").strip() or None
        isin = str(record.get("isin") or "").strip() or None
        lei = str(record.get("lei") or "").strip() or None
        country_code = str(record.get("country_code") or "").strip() or None
        exchange = str(record.get("exchange") or "").strip() or None
        source_url = str(record.get("source_url") or "").strip()
        source_host = (urlparse(source_url).hostname or "").casefold()
        registries: list[str] = []

        if cik is not None:
            if sec_client is None or not hasattr(sec_client, "resolve_company"):
                raise RuntimeError("SEC identity client nepodporuje exact ticker lookup.")
            company = sec_client.resolve_company(ticker)
            if company is None:
                raise RuntimeError(f"SEC nerozpoznal pilotní ticker {ticker}.")
            if str(company.cik).zfill(10) != cik:
                raise RuntimeError(
                    f"SEC CIK konflikt pro {ticker}: manifest {cik}, registry "
                    f"{str(company.cik).zfill(10)}."
                )
            if _exact_name(company.name) != _exact_name(expected_name):
                raise RuntimeError(
                    f"SEC legal-name konflikt pro {ticker}: manifest "
                    f"{expected_name!r}, registry {company.name!r}."
                )
            if exchange and _exact_name(company.exchange) != _exact_name(exchange):
                raise RuntimeError(
                    f"SEC exchange konflikt pro {ticker}: manifest {exchange!r}, "
                    f"registry {company.exchange!r}."
                )
            expected_source_url = SEC_SUBMISSIONS_URL.format(cik=cik)
            if source_host not in {"data.sec.gov", "www.sec.gov"}:
                raise RuntimeError(f"SEC identita {ticker} nemá primární SEC source URL.")
            if source_url != expected_source_url:
                raise RuntimeError(
                    f"SEC source URL pro {ticker} neodpovídá přesnému CIK endpointu."
                )
            registries.append("SEC_EDGAR")

        if lei is not None or isin is not None:
            if gleif_client is None or not hasattr(gleif_client, "resolve"):
                raise RuntimeError("GLEIF identity client nepodporuje exact lookup.")
            # Prefer the exact instrument identifier when available.  The
            # filtered ISIN endpoint proves the ISIN -> LEI edge directly;
            # optional reverse /lei-records/{lei}/isins data may be absent.
            identity = gleif_client.resolve(
                lei=None if isin else lei,
                isin=isin,
            )
            if identity is None:
                raise RuntimeError(
                    f"GLEIF nerozpoznal pilotní identitu {ticker} přes "
                    "přesný identifikátor."
                )
            if lei is not None and identity.lei != lei:
                raise RuntimeError(
                    f"GLEIF LEI konflikt pro {ticker}: manifest {lei}, registry "
                    f"{identity.lei}."
                )
            if isin is not None and isin not in set(identity.isins):
                raise RuntimeError(
                    f"GLEIF ISIN mapping konflikt pro {ticker}: {isin} není navázán "
                    f"na LEI {identity.lei}."
                )
            if _exact_name(identity.legal_name) != _exact_name(expected_name):
                raise RuntimeError(
                    f"GLEIF legal-name konflikt pro {ticker}: manifest "
                    f"{expected_name!r}, registry {identity.legal_name!r}."
                )
            if country_code and identity.country_code != country_code:
                raise RuntimeError(
                    f"GLEIF country konflikt pro {ticker}: manifest {country_code}, "
                    f"registry {identity.country_code}."
                )
            if (
                identity.registration_status != "ISSUED"
                or identity.entity_status != "ACTIVE"
            ):
                raise RuntimeError(
                    f"GLEIF identita {ticker} není aktivní/issued: "
                    f"{identity.entity_status}/{identity.registration_status}."
                )
            if source_host != "api.gleif.org":
                raise RuntimeError(
                    f"GLEIF identita {ticker} nemá primární GLEIF source URL."
                )
            expected_source_url = f"{GLEIF_API_ROOT}/lei-records/{identity.lei}"
            if source_url != expected_source_url:
                raise RuntimeError(
                    f"GLEIF source URL pro {ticker} neodpovídá přesnému LEI endpointu."
                )
            registries.append("GLEIF")

        if not registries:
            raise RuntimeError(f"Identita {ticker} nemá CIK, LEI ani ISIN.")
        verified.append(
            {
                "ticker": ticker,
                "legal_name": expected_name,
                "cik": cik,
                "isin": isin,
                "lei": lei,
                "registries": registries,
                "source_url": source_url,
                "status": "RESOLVED",
            }
        )

    return {
        "production_universe_count": len(normalized_universe),
        "configured_identity_count": len(records),
        "resolved_identity_count": len(verified),
        "unresolved_identity_count": 0,
        "quarantined_conflict_count": 0,
        "name_matching_used": False,
        "identities": verified,
    }


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
    identity_records: Mapping[str, Mapping[str, object]] | None = None,
    identity_universe_tickers: Sequence[str] | None = None,
    minimum_identity_records: int = 10,
    external_report_source: ShortReportSourceConfig,
    yahoo_client: object | None = None,
    rss_client: object | None = None,
    sec_client: object | None = None,
    gleif_client: object | None = None,
    report_client: object | None = None,
) -> dict[str, object]:
    """Verify live identities, Yahoo, RSS, SEC and a short-report source.

    The result contains counts, timestamps, hashes and MIME data only.  Source
    bodies, Yahoo payloads and the SEC contact User-Agent are never persisted.
    """

    normalized_tickers = list(
        dict.fromkeys(str(ticker).strip().upper() for ticker in tickers if ticker)
    )
    if not normalized_tickers:
        raise ValueError("Live smoke vyžaduje alespoň jeden ticker.")
    declared_sec_user_agent = str(sec_user_agent or "").strip()
    production_universe = (
        normalize_watchlist(identity_universe_tickers)
        if identity_universe_tickers is not None
        else []
    )
    if (
        production_universe
        and external_report_source.ticker not in production_universe
    ):
        raise ValueError(
            "Short-report smoke ticker není v produkčním watchlistu: "
            f"{external_report_source.ticker}"
        )

    def check_company_identity_pilot() -> dict[str, object]:
        if identity_records is None:
            raise RuntimeError(
                "Produkční identity manifest nebyl předán live smoke běhu."
            )
        return verify_company_identity_pilot(
            identity_records=identity_records,
            sec_user_agent=declared_sec_user_agent,
            minimum_records=minimum_identity_records,
            universe_tickers=identity_universe_tickers,
            sec_client=sec_client,
            gleif_client=gleif_client,
        )

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
            external_report_source
        )
        searchable = f"{fetched.title} {fetched.text}".casefold()
        if (
            len(fetched.text) < 200
            or external_report_source.ticker.casefold() not in searchable
        ):
            raise RuntimeError(
                "Externí report neobsahuje očekávaný strojově čitelný obsah."
            )
        return {
            "publisher": external_report_source.publisher,
            "ticker": external_report_source.ticker,
            "published_at": external_report_source.published_at.isoformat(),
            "final_url": fetched.final_url,
            "mime_type": fetched.mime_type,
            "content_hash": fetched.content_hash,
            "size_bytes": fetched.size_bytes,
            "extractor": fetched.extractor,
        }

    checks = []
    if identity_records is not None:
        checks.append(
            _run_check("company_identity_pilot", check_company_identity_pilot)
        )
    checks.extend(
        [
            _run_check("yahoo", check_yahoo),
            _run_check("rss", check_rss),
            _run_check("sec_edgar", check_sec),
            _run_check("external_short_report", check_external_report),
        ]
    )
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
        description=(
            "Produkční smoke identit a živého Yahoo, RSS, SEC a short-report zdroje."
        )
    )
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument(
        "--ticker-file",
        type=Path,
        default=DEFAULT_PRODUCTION_WATCHLIST_PATH,
        help="Produkční ticker universe; explicitní --tickers mají přednost.",
    )
    parser.add_argument(
        "--ticker-limit",
        type=int,
        default=3,
        help="Počet tickerů z universe pro Yahoo/RSS/SEC smoke.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/live_source_smoke_latest.json"),
    )
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=DEFAULT_RUNTIME_CONFIG_PATH,
        help="Trvalá agentní konfigurace obsahující produkční identity manifest.",
    )
    parser.add_argument(
        "--minimum-identity-records",
        type=int,
        default=10,
        help="Minimální počet přesných identit požadovaný live canary.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings, runtime_warning = AgentRuntimeService(args.runtime_config).load()
    if runtime_warning:
        raise SystemExit(f"[LIVE SMOKE CHYBA] {runtime_warning}")
    identity_records, identity_errors = parse_identity_records(
        settings.identity_records_text
    )
    if identity_errors:
        raise SystemExit(
            "[LIVE SMOKE CHYBA] Identity manifest není platný: "
            + "; ".join(identity_errors)
        )
    short_report_sources, short_report_errors = parse_short_report_sources(
        settings.short_report_sources_text
    )
    if short_report_errors:
        raise SystemExit(
            "[LIVE SMOKE CHYBA] Short-report manifest není platný: "
            + "; ".join(short_report_errors)
        )
    if len(short_report_sources) != 1:
        raise SystemExit(
            "[LIVE SMOKE CHYBA] Produkční smoke vyžaduje právě jeden "
            "explicitní short-report zdroj v runtime konfiguraci."
        )
    try:
        universe_tickers = load_watchlist(args.ticker_file)
        smoke_tickers = apply_ticker_limit(
            args.tickers if args.tickers else universe_tickers,
            args.ticker_limit,
        )
        payload = run_live_source_smoke(
            tickers=smoke_tickers,
            output_path=args.output,
            sec_user_agent=os.getenv("JOHNY_SKORE_SEC_USER_AGENT", ""),
            identity_records=identity_records,
            identity_universe_tickers=universe_tickers,
            minimum_identity_records=args.minimum_identity_records,
            external_report_source=short_report_sources[0],
        )
    except WatchlistError as exc:
        raise SystemExit(f"[LIVE SMOKE CHYBA] {exc}") from exc
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload["status"] != "PASS":
        failed = ", ".join(str(item) for item in payload["failed_checks"])
        raise SystemExit(f"[LIVE SMOKE CHYBA] Selhaly zdroje: {failed}")


if __name__ == "__main__":
    main()
