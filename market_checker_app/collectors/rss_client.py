from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import math
import re
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import feedparser

from market_checker_app.models import NewsItem


RSSProgressCallback = Callable[[int, int, str], None]


class RSSClient:
    def __init__(
        self,
        max_items_per_source: int = 30,
        request_timeout_seconds: float = 5.0,
        max_workers: int = 24,
    ) -> None:
        self.max_items_per_source = max_items_per_source
        self.request_timeout_seconds = max(1.0, request_timeout_seconds)
        self.max_workers = max(1, max_workers)

    @staticmethod
    def _sentiment_score(text: str) -> float:
        positive = {
            "beat", "beats", "growth", "upgrade", "upgraded", "surge", "strong", "record", "profit", "profits", "buyback"
        }
        negative = {
            "miss", "misses", "downgrade", "downgraded", "lawsuit", "probe", "drop", "falls", "fall", "weak", "loss", "losses"
        }
        words = {w.strip(".,:;!?()[]{}\"'").lower() for w in text.split()}
        raw = sum(1 for w in words if w in positive) - sum(1 for w in words if w in negative)
        raw = max(-4, min(4, raw))
        return raw / 4.0

    @staticmethod
    def _recency_weight(published_at: datetime, now: datetime) -> float:
        age_days = max(0.0, (now - published_at).total_seconds() / 86400.0)
        decay = math.exp(-math.log(2.0) * age_days / 14.0)
        return max(0.05, decay)

    @staticmethod
    def _ticker_hint_from_source(source: str, ticker_set: set[str]) -> str | None:
        parsed_url = urlparse(source)
        query = parse_qs(parsed_url.query)
        values = query.get("s", [])
        if len(values) == 1 and "," not in values[0]:
            candidate = values[0].strip().upper()
            if candidate in ticker_set:
                return candidate

        # Google News has no account/API-key requirement. Its search RSS URL
        # carries the requested symbol in q={ticker} stock, so results from an
        # expanded per-ticker URL can be assigned without matching common-word
        # symbols such as A or ALL against arbitrary article prose.
        if parsed_url.hostname == "news.google.com":
            search_values = query.get("q", [])
            if len(search_values) == 1:
                candidate = search_values[0].split(maxsplit=1)[0].strip().upper()
                if candidate in ticker_set:
                    return candidate
        return None

    @staticmethod
    def _contains_ticker(text_upper: str, ticker: str) -> bool:
        return re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", text_upper) is not None

    def _download(self, source: str) -> bytes:
        request = Request(source, headers={"User-Agent": "Mozilla/5.0 (MarketChecker/1.0)"})
        with urlopen(request, timeout=self.request_timeout_seconds) as response:
            return response.read(2_000_000)

    def collect(
        self,
        rss_sources: list[str],
        tickers: list[str],
        progress_callback: RSSProgressCallback | None = None,
    ) -> tuple[list[NewsItem], list[str]]:
        if not rss_sources:
            return [], []

        ticker_set = set(tickers)
        now = datetime.now(timezone.utc)
        cutoff_3m = now - timedelta(days=90)
        items: list[NewsItem] = []
        warnings: list[str] = []
        total_sources = len(rss_sources)

        with ThreadPoolExecutor(max_workers=min(self.max_workers, total_sources)) as executor:
            futures = {
                executor.submit(self._collect_source, source, ticker_set, now, cutoff_3m): source
                for source in rss_sources
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                source = futures[future]
                try:
                    source_items, source_warnings = future.result()
                    items.extend(source_items)
                    warnings.extend(source_warnings)
                except Exception as exc:
                    warnings.append(f"RSS načtení selhalo ({source}). Zdroj byl přeskočen. Detail: {exc}")
                if progress_callback:
                    progress_callback(completed, total_sources, source)

        return items, warnings

    def _collect_source(
        self,
        source: str,
        ticker_set: set[str],
        now: datetime,
        cutoff_3m: datetime,
    ) -> tuple[list[NewsItem], list[str]]:
        warnings: list[str] = []
        items: list[NewsItem] = []
        try:
            payload = self._download(source)
            parsed = feedparser.parse(payload)
        except Exception as exc:
            return [], [f"RSS načtení selhalo ({source}). Zdroj byl přeskočen. Detail: {exc}"]

        if getattr(parsed, "bozo", False):
            bozo_exc = getattr(parsed, "bozo_exception", "neznámá chyba parseru")
            warnings.append(
                f"RSS parser hlásí problém pro {source}. Pokračuji s dostupnými položkami. Detail: {bozo_exc}"
            )

        entries = list(getattr(parsed, "entries", []))
        if not entries:
            warnings.append(f"RSS zdroj {source} nevrátil žádné položky.")
            return [], warnings

        ticker_hint = self._ticker_hint_from_source(source, ticker_set)
        undated_count = 0
        future_count = 0
        for entry in entries[: self.max_items_per_source]:
            title = str(getattr(entry, "title", ""))
            summary = str(getattr(entry, "summary", ""))
            published_parsed = getattr(entry, "published_parsed", None)
            if published_parsed is None:
                undated_count += 1
                continue
            published_at = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            if published_at > now + timedelta(minutes=5):
                future_count += 1
                continue
            if published_at < cutoff_3m:
                continue

            text = f"{title} {summary}"
            text_upper = text.upper()
            sentiment = self._sentiment_score(text)
            recency = self._recency_weight(published_at, now)
            sentiment_weight = round(recency * sentiment, 4)
            matched_tickers = (
                [ticker_hint]
                if ticker_hint
                else [ticker for ticker in ticker_set if self._contains_ticker(text_upper, ticker)]
            )

            for ticker in matched_tickers:
                items.append(
                    NewsItem(
                        ticker=ticker,
                        source=source,
                        title=title,
                        summary=summary,
                        published_at=published_at,
                        sentiment_weight=sentiment_weight,
                        url=str(getattr(entry, "link", "")),
                    )
                )
        if undated_count:
            warnings.append(
                f"RSS zdroj {source}: přeskočeno {undated_count} položek bez data publikace."
            )
        if future_count:
            warnings.append(
                f"RSS zdroj {source}: přeskočeno {future_count} položek s budoucím datem."
            )
        return items, warnings

    def _collect_html_fallback(
        self,
        source: str,
        ticker_set: set[str],
        now: datetime,
        payload: bytes | None = None,
    ) -> list[NewsItem]:
        if not any(
            domain in source
            for domain in ("nasdaq.com", "stockanalysis.com", "marketscreener.com", "investing.com", "benzinga.com", "barchart.com")
        ):
            return []
        try:
            raw = payload if payload is not None else self._download(source)
            html = raw[:300_000].decode("utf-8", errors="ignore")
        except Exception:
            return []

        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        meta_match = re.search(
            r'<meta[^>]+name=["\\\']description["\\\'][^>]*content=["\\\'](.*?)["\\\']',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        summary = re.sub(r"\s+", " ", meta_match.group(1)).strip() if meta_match else ""
        text = f"{title} {summary}".strip()
        if not text:
            return []

        text_upper = text.upper()
        sentiment = self._sentiment_score(text)
        sentiment_weight = round(self._recency_weight(now, now) * sentiment, 4)
        ticker_hint = self._ticker_hint_from_source(source, ticker_set)
        matched = (
            [ticker_hint]
            if ticker_hint
            else [ticker for ticker in ticker_set if self._contains_ticker(text_upper, ticker)]
        )

        return [
            NewsItem(
                ticker=ticker,
                source=source,
                title=title or source,
                summary=summary,
                published_at=now,
                sentiment_weight=sentiment_weight,
                url=source,
            )
            for ticker in matched
        ]
