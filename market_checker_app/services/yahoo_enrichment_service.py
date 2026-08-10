from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import sleep

from market_checker_app.collectors.yahoo_client import YahooClient
from market_checker_app.storage.yahoo_cache_store import YahooCacheCoverage, YahooCacheStore


YahooRefreshCallback = Callable[[int, int, str, str, YahooCacheCoverage], None]


@dataclass(frozen=True)
class YahooRefreshResult:
    candidates: int
    attempted: int
    succeeded: int
    partial: int
    failed: int
    rate_limited: bool
    remaining: int
    coverage: YahooCacheCoverage
    warnings: list[str]
    batches: int = 1


class YahooEnrichmentService:
    """Incrementally populate persistent Yahoo metadata without price downloads."""

    def __init__(
        self,
        cache: YahooCacheStore,
        client: YahooClient | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.cache = cache
        self.client = client or YahooClient()
        self._sleep = sleep_fn

    def refresh(
        self,
        watchlist: Iterable[str],
        *,
        max_items: int | None = None,
        delay_seconds: float = 0.75,
        progress_callback: YahooRefreshCallback | None = None,
    ) -> YahooRefreshResult:
        tickers = list(dict.fromkeys(str(ticker).strip().upper() for ticker in watchlist if str(ticker).strip()))
        candidates = self.cache.list_tickers_needing_refresh(tickers)
        if max_items is not None:
            if max_items < 1:
                raise ValueError("max_items must be at least 1 or None")
            candidates = candidates[:max_items]

        attempted = succeeded = partial = failed = 0
        warnings: list[str] = []
        was_rate_limited = False

        for ticker in candidates:
            if self.client.is_rate_limited():
                was_rate_limited = True
                break

            snapshot, warning = self.client.fetch_metadata(ticker)
            attempted += 1
            yahoo_ticker = snapshot.ticker or self.client.normalize_yahoo_symbol(ticker)

            if snapshot.status in {"ok", "partial"} and snapshot.data:
                payload = dict(snapshot.data)
                payload["_market_checker_yahoo_quality"] = snapshot.status
                self.cache.upsert_success(
                    ticker,
                    payload,
                    yahoo_ticker=yahoo_ticker,
                )
                succeeded += 1
                partial += int(snapshot.status == "partial")
                status = snapshot.status
            else:
                message = warning or f"Yahoo metadata nejsou dostupná pro {yahoo_ticker}."
                self.cache.upsert_failure(
                    ticker,
                    message,
                    yahoo_ticker=yahoo_ticker,
                )
                warnings.append(message)
                failed += 1
                status = "failed"

            coverage = self.cache.coverage(tickers)
            if progress_callback:
                progress_callback(attempted, len(candidates), ticker, status, coverage)

            if self.client.is_rate_limited():
                was_rate_limited = True
                break
            if delay_seconds > 0 and attempted < len(candidates):
                self._sleep(delay_seconds)

        coverage = self.cache.coverage(tickers)
        # Report all symbols that still lack a fresh snapshot. Failed entries
        # may be in their retry cooldown and therefore absent from the next
        # immediate candidate list, but they must not be presented as done.
        remaining = coverage.total - coverage.fresh - coverage.unsupported
        return YahooRefreshResult(
            candidates=len(candidates),
            attempted=attempted,
            succeeded=succeeded,
            partial=partial,
            failed=failed,
            rate_limited=was_rate_limited,
            remaining=remaining,
            coverage=coverage,
            warnings=warnings,
        )

    def refresh_all(
        self,
        watchlist: Iterable[str],
        *,
        batch_size: int = 100,
        delay_seconds: float = 0.75,
        progress_callback: YahooRefreshCallback | None = None,
    ) -> YahooRefreshResult:
        """Refresh every currently eligible ticker in automatic batches.

        A batch boundary is only a checkpoint; it does not require another UI
        click.  The loop stops when no ticker is immediately refreshable or
        Yahoo activates its rate-limit guard.  Failed tickers remain stored
        with their retry deadline and are not hammered repeatedly in the same
        run.
        """

        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        tickers = list(
            dict.fromkeys(
                str(ticker).strip().upper()
                for ticker in watchlist
                if str(ticker).strip()
            )
        )
        initial_candidates = self.cache.list_tickers_needing_refresh(tickers)
        total_candidates = len(initial_candidates)
        attempted = succeeded = partial = failed = batches = 0
        warnings: list[str] = []
        rate_limited = False

        while self.cache.list_tickers_needing_refresh(tickers):
            attempted_before_batch = attempted

            def _on_batch_progress(
                completed: int,
                _batch_total: int,
                ticker: str,
                status: str,
                coverage: YahooCacheCoverage,
            ) -> None:
                if progress_callback:
                    progress_callback(
                        attempted_before_batch + completed,
                        total_candidates,
                        ticker,
                        status,
                        coverage,
                    )

            result = self.refresh(
                tickers,
                max_items=batch_size,
                delay_seconds=delay_seconds,
                progress_callback=_on_batch_progress,
            )
            if result.candidates == 0 or result.attempted == 0:
                rate_limited = rate_limited or result.rate_limited
                break

            batches += 1
            attempted += result.attempted
            succeeded += result.succeeded
            partial += result.partial
            failed += result.failed
            warnings.extend(result.warnings)
            rate_limited = rate_limited or result.rate_limited
            if rate_limited:
                break

            if self.cache.list_tickers_needing_refresh(tickers) and delay_seconds > 0:
                self._sleep(delay_seconds)

        coverage = self.cache.coverage(tickers)
        remaining = coverage.total - coverage.fresh - coverage.unsupported
        return YahooRefreshResult(
            candidates=total_candidates,
            attempted=attempted,
            succeeded=succeeded,
            partial=partial,
            failed=failed,
            rate_limited=rate_limited,
            remaining=remaining,
            coverage=coverage,
            warnings=warnings,
            batches=batches,
        )
