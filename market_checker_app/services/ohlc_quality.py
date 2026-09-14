from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd


DEFAULT_MIN_HISTORY_ROWS = 60
DEFAULT_MAX_CLOSE_AGE = timedelta(days=7)


@dataclass(frozen=True)
class OhlcQuality:
    """Evidence about whether a daily OHLC frame can support a price or indicators."""

    normalized: pd.DataFrame
    close: float | None
    close_at: datetime | None
    observation_count: int
    price_usable: bool
    history_usable: bool
    warnings: tuple[str, ...]


def _as_utc(value: object) -> datetime | None:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.to_pydatetime()


def assess_daily_ohlc(
    frame: pd.DataFrame | None,
    *,
    as_of: datetime,
    min_history_rows: int = DEFAULT_MIN_HISTORY_ROWS,
    max_close_age: timedelta = DEFAULT_MAX_CLOSE_AGE,
) -> OhlcQuality:
    """Validate an OHLC frame without inventing a price or session.

    A Friday close remains usable on a Monday and around ordinary holidays;
    older or future-dated closes are explicitly rejected.  A short but fresh
    frame may provide a dated price, but is not allowed to support technical
    indicators that require a meaningful history.
    """
    warnings: list[str] = []
    empty = pd.DataFrame()
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC data chybí nebo jsou prázdná.",))
    if "Close" not in frame.columns:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC data nemají sloupec Close.",))

    normalized = frame.copy()
    close = pd.to_numeric(normalized["Close"], errors="coerce")
    normalized = normalized.loc[close.notna() & (close > 0)].copy()
    if normalized.empty:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC Close neobsahuje kladnou číselnou cenu.",))

    normalized["Close"] = pd.to_numeric(normalized["Close"], errors="coerce")
    timestamps = [_as_utc(value) for value in normalized.index]
    valid_rows = [
        (position, value)
        for position, value in enumerate(timestamps)
        if value is not None
    ]
    if not valid_rows:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC data nemají platná časová razítka seancí.",))

    latest_position, latest_at = max(valid_rows, key=lambda item: item[1])
    assert latest_at is not None
    normalized = normalized.iloc[[position for position, _ in valid_rows]].copy()
    normalized = normalized.sort_index()
    latest_close = float(pd.to_numeric(normalized["Close"], errors="coerce").iloc[-1])
    now = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)

    price_usable = True
    if latest_at > now + timedelta(minutes=5):
        price_usable = False
        warnings.append("Poslední OHLC seance leží v budoucnosti vůči času běhu.")
    elif now - latest_at > max_close_age:
        price_usable = False
        warnings.append(
            f"Poslední OHLC close je zastaralý ({(now - latest_at).days} dní); cena se nepoužije."
        )

    count = len(normalized)
    history_usable = price_usable and count >= max(1, int(min_history_rows))
    if price_usable and not history_usable:
        warnings.append(
            f"OHLC historie má jen {count} platných seancí; pro technické indikátory je potřeba alespoň {min_history_rows}."
        )

    return OhlcQuality(
        normalized=normalized,
        close=latest_close if price_usable else None,
        close_at=latest_at,
        observation_count=count,
        price_usable=price_usable,
        history_usable=history_usable,
        warnings=tuple(warnings),
    )
