"""Compatibility API backed by the same NYSE calendar as price features."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from market_checker_app.services.us_equity_calendar_service import _nyse_calendar

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class MarketSession:
    session_date: date
    close_at: datetime
    early_close: bool = False


@lru_cache(maxsize=64)
def _year_sessions(year: int) -> dict[date, MarketSession]:
    schedule = _nyse_calendar().schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
    return {
        stamp.date(): MarketSession(
            stamp.date(), row.market_close.to_pydatetime(),
            row.market_close.tz_convert(NEW_YORK).hour < 16,
        )
        for stamp, row in schedule.iterrows()
    }


def us_equity_full_day_holidays(year: int) -> frozenset[date]:
    start = date(year, 1, 1)
    return frozenset(
        day for offset in range((date(year + 1, 1, 1) - start).days)
        if (day := start + timedelta(days=offset)).weekday() < 5
        and day not in _year_sessions(year)
    )


def us_equity_session(day: date) -> MarketSession | None:
    return _year_sessions(day.year).get(day)


def is_us_equity_session(day: date) -> bool:
    return us_equity_session(day) is not None


def is_us_equity_early_close(day: date) -> bool:
    session = us_equity_session(day)
    return session is not None and session.early_close


def latest_closed_us_equity_session(as_of: datetime) -> MarketSession:
    clock = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    cursor = clock.astimezone(NEW_YORK).date()
    for _ in range(3700):
        session = us_equity_session(cursor)
        if session is not None and session.close_at <= clock:
            return session
        cursor -= timedelta(days=1)
    raise RuntimeError("No closed NYSE session in calendar range")


def next_us_equity_sessions(after: date, count: int) -> tuple[MarketSession, ...]:
    return _collect_sessions(after + timedelta(days=1), count, 1)


def previous_us_equity_sessions(on_or_before: date, count: int) -> tuple[MarketSession, ...]:
    return tuple(reversed(_collect_sessions(on_or_before, count, -1)))


def _collect_sessions(cursor: date, count: int, direction: int) -> tuple[MarketSession, ...]:
    if count < 0:
        raise ValueError("count must not be negative")
    result = []
    while len(result) < count:
        session = us_equity_session(cursor)
        if session is not None:
            result.append(session)
        cursor += timedelta(days=direction)
    return tuple(result)


def target_us_equity_window(prediction_as_of: datetime, horizon: int) -> tuple[MarketSession, tuple[MarketSession, ...]]:
    if horizon < 1:
        raise ValueError("horizon must be positive")
    base = latest_closed_us_equity_session(prediction_as_of)
    return base, next_us_equity_sessions(base.session_date, horizon)
