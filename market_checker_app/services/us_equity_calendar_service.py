from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal


NYSE_CALENDAR = "NYSE"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def session_label(value: object) -> pd.Timestamp | None:
    """Return a UTC-midnight label for a daily US-equity session."""
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.normalize()


@lru_cache(maxsize=1)
def _nyse_calendar():
    return mcal.get_calendar(NYSE_CALENDAR)


def sessions_between(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.Timestamp, ...]:
    """Return actual NYSE sessions, including holidays and early closes."""
    if end < start:
        return ()
    schedule = _nyse_calendar().schedule(start_date=start.date(), end_date=end.date())
    return tuple(
        label
        for value in schedule.index
        if (label := session_label(value)) is not None and start <= label <= end
    )


def last_completed_session(as_of: datetime) -> pd.Timestamp | None:
    """Return the latest NYSE session whose official close had happened."""
    clock = _utc(as_of)
    start = session_label(clock - timedelta(days=14))
    end = session_label(clock)
    if start is None or end is None:
        return None
    schedule = _nyse_calendar().schedule(start_date=start.date(), end_date=end.date())
    if schedule.empty:
        return None
    closes = pd.to_datetime(schedule["market_close"], utc=True, errors="coerce")
    completed = schedule.index[(closes <= pd.Timestamp(clock)).to_numpy()]
    if len(completed) == 0:
        return None
    return session_label(completed[-1])


def expected_sessions(
    *,
    snapshot_as_of: datetime,
    evaluation_as_of: datetime,
    horizon: int,
) -> tuple[pd.Timestamp, ...]:
    """Return t0 plus the exact future NYSE sessions visible at evaluation time."""
    if horizon < 1:
        raise ValueError("horizon must be positive")
    base = last_completed_session(snapshot_as_of)
    latest = last_completed_session(evaluation_as_of)
    if base is None or latest is None or latest < base:
        return ()
    return sessions_between(base, latest)
