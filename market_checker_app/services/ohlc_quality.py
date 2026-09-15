from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import math

import pandas as pd

from market_checker_app.services.us_equity_calendar_service import (
    last_completed_session,
    session_label,
    sessions_between,
)


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
    """Validate daily OHLC against completed NYSE sessions.

    The provider may return duplicate or unfinished daily bars.  Only a
    positive finite close on the latest completed NYSE session is a usable
    current price; technical history counts unique exchange sessions.
    """
    del max_close_age  # Session calendar is stricter and handles holidays.
    empty = pd.DataFrame()
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC data chybí nebo jsou prázdná.",))
    if "Close" not in frame.columns:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC data nemají sloupec Close.",))

    rows: list[dict[str, object]] = []
    for timestamp, raw_close in frame["Close"].items():
        session = session_label(timestamp)
        try:
            close = float(raw_close)
        except (TypeError, ValueError):
            continue
        if session is None or not math.isfinite(close) or close <= 0.0:
            continue
        rows.append({"session": session, "close": close})

    if rows:
        valid_sessions = set(sessions_between(min(row["session"] for row in rows), max(row["session"] for row in rows)))
        rows = [row for row in rows if row["session"] in valid_sessions]
    if not rows:
        return OhlcQuality(empty, None, None, 0, False, False, ("OHLC Close neobsahuje kladnou konečnou cenu na platné seanci.",))

    normalized = pd.DataFrame(rows).sort_values("session")
    duplicate_count = int(normalized.duplicated("session", keep="last").sum())
    normalized = normalized.drop_duplicates("session", keep="last").set_index("session")
    latest = normalized.index[-1]
    expected = last_completed_session(as_of)
    warnings: list[str] = []
    if duplicate_count:
        warnings.append(f"OHLC obsahuje {duplicate_count} duplicitních seancí; pro výpočet byla použita poslední verze.")

    price_usable = expected is not None and latest == expected
    if expected is None:
        warnings.append("Nelze určit poslední dokončenou NYSE seanci.")
    elif latest > expected:
        warnings.append("Poslední OHLC seance ještě nebyla uzavřena nebo leží v budoucnosti.")
    elif latest < expected:
        warnings.append(f"Poslední OHLC close neodpovídá poslední dokončené NYSE seanci {expected.date().isoformat()}.")

    count = int(len(normalized))
    required = max(1, int(min_history_rows))
    session_start = normalized.index[max(0, count - required)]
    required_sessions = sessions_between(session_start, latest)
    history_usable = price_usable and count >= required and tuple(normalized.index[-required:]) == required_sessions[-required:]
    if price_usable and not history_usable:
        warnings.append(f"OHLC historie nemá {required} souvislých platných NYSE seancí pro technické indikátory.")

    latest_close = float(normalized.iloc[-1]["close"])
    return OhlcQuality(
        normalized=normalized.rename(columns={"close": "Close"}),
        close=latest_close if price_usable else None,
        close_at=latest.to_pydatetime(),
        observation_count=count,
        price_usable=price_usable,
        history_usable=history_usable,
        warnings=tuple(warnings),
    )

