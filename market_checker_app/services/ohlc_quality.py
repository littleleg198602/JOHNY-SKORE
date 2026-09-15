from __future__ import annotations

from dataclasses import dataclass
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_checker_app.services.us_equity_calendar_service import (
    last_completed_session,
    session_label,
    sessions_between,
)



DEFAULT_MIN_HISTORY_ROWS = 66
DEFAULT_MAX_CLOSE_AGE = timedelta(days=7)
TECHNICAL_LOOKBACKS = (6, 10, 20, 22, 26, 50, 66, 100, 200, 252)


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
    available_lookbacks: tuple[int, ...] = ()
    missing_lookbacks: tuple[int, ...] = ()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _session_date(value: object):
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.date()


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def _empty_quality(message: str) -> OhlcQuality:
    return OhlcQuality(
        pd.DataFrame(),
        None,
        None,
        0,
        False,
        False,
        (message,),
        (),
        TECHNICAL_LOOKBACKS,
    )


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
        return _empty_quality("OHLC data chybí nebo jsou prázdná.")
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

    def has_complete_lookback(lookback: int) -> bool:
        if not price_usable or count < lookback:
            return False
        observed = tuple(normalized.index[-lookback:])
        expected_sessions = sessions_between(observed[0], latest)
        return observed == expected_sessions[-lookback:]

    available_lookbacks = tuple(
        lookback for lookback in TECHNICAL_LOOKBACKS if has_complete_lookback(lookback)
    )
    missing_lookbacks = tuple(
        lookback for lookback in TECHNICAL_LOOKBACKS if lookback not in available_lookbacks
    )
    # ``min_history_rows`` is also a public caller contract.  It may be a
    # one-off threshold (for example 30), while ``available_lookbacks`` is
    # only the fixed reporting set exported to the UI.
    history_usable = has_complete_lookback(required)
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
        available_lookbacks=available_lookbacks,
        missing_lookbacks=missing_lookbacks,
    )
