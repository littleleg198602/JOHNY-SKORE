from __future__ import annotations

from datetime import datetime, timezone

from market_checker_app.config import ShortReportSourceConfig
from market_checker_app.utils.source_validation import public_https_reference
from market_checker_app.utils.text import normalize_ticker


def parse_short_report_sources(
    value: str,
) -> tuple[tuple[ShortReportSourceConfig, ...], list[str]]:
    """Parse explicit report manifests with the shared public-source policy."""

    sources: list[ShortReportSourceConfig] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 3)]
        if len(parts) != 4:
            errors.append(
                f"Short report řádek {line_number}: očekávám TICKER | vydavatel | datum | HTTPS URL."
            )
            continue
        raw_ticker, publisher, raw_published_at, raw_url = parts
        ticker = normalize_ticker(raw_ticker)
        try:
            if not ticker or not publisher:
                raise ValueError("chybí platný ticker nebo vydavatel")
            published_at = datetime.fromisoformat(
                raw_published_at.replace("Z", "+00:00")
            )
            if published_at.tzinfo is None or published_at.utcoffset() is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            else:
                published_at = published_at.astimezone(timezone.utc)
            url = public_https_reference(raw_url)
        except (TypeError, ValueError) as exc:
            errors.append(f"Short report řádek {line_number}: {exc}.")
            continue
        key = (ticker, url, published_at.isoformat())
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            ShortReportSourceConfig(
                ticker=ticker,
                publisher=publisher,
                published_at=published_at,
                url=url,
            )
        )
    return tuple(sources), errors
