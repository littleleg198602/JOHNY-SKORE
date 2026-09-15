from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Exceptional full-day NYSE closures are not derivable from recurring holiday
# rules. Keeping the small explicit set makes historical replay deterministic.
_KNOWN_SPECIAL_CLOSURES = {
    date(2001, 9, 11),
    date(2001, 9, 12),
    date(2001, 9, 13),
    date(2001, 9, 14),
    date(2004, 6, 11),  # Ronald Reagan funeral
    date(2007, 1, 2),   # Gerald Ford funeral
    date(2012, 10, 29), # Hurricane Sandy
    date(2012, 10, 30),
    date(2018, 12, 5),  # George H. W. Bush funeral
    date(2025, 1, 9),   # Jimmy Carter funeral
}


@dataclass(frozen=True, slots=True)
class MarketSession:
    session_date: date
    close_at: datetime
    early_close: bool = False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _observed_fixed_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@lru_cache(maxsize=None)
def us_equity_full_day_holidays(year: int) -> frozenset[date]:
    holidays: set[date] = set()

    for fixed in (
        date(year, 1, 1),
        date(year, 7, 4),
        date(year, 12, 25),
    ):
        observed = _observed_fixed_holiday(fixed)
        if observed.year == year:
            holidays.add(observed)

    # A Saturday January 1 is observed on December 31 of the prior year.
    next_new_year = _observed_fixed_holiday(date(year + 1, 1, 1))
    if next_new_year.year == year:
        holidays.add(next_new_year)

    if year >= 1998:
        holidays.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr.
    holidays.add(_nth_weekday(year, 2, 0, 3))      # Washington's Birthday
    holidays.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
    holidays.add(_last_weekday(year, 5, 0))        # Memorial Day
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(date(year, 6, 19)))  # Juneteenth
    holidays.add(_nth_weekday(year, 9, 0, 1))      # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))     # Thanksgiving

    holidays.update(day for day in _KNOWN_SPECIAL_CLOSURES if day.year == year)
    return frozenset(holidays)


def is_us_equity_session(day: date) -> bool:
    return day.weekday() < 5 and day not in us_equity_full_day_holidays(day.year)


def is_us_equity_early_close(day: date) -> bool:
    if not is_us_equity_session(day):
        return False
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    if day == thanksgiving + timedelta(days=1):
        return True
    if day.month == 12 and day.day == 24:
        return True
    if day.month == 7 and day.day == 3:
        return True
    return False


def us_equity_session(day: date) -> MarketSession | None:
    if not is_us_equity_session(day):
        return None
    early = is_us_equity_early_close(day)
    local_close = datetime.combine(
        day,
        EARLY_CLOSE if early else REGULAR_CLOSE,
        tzinfo=NEW_YORK,
    )
    return MarketSession(
        session_date=day,
        close_at=local_close.astimezone(timezone.utc),
        early_close=early,
    )


def latest_closed_us_equity_session(as_of: datetime) -> MarketSession:
    clock = _as_utc(as_of)
    cursor = clock.astimezone(NEW_YORK).date()
    for _ in range(3700):
        session = us_equity_session(cursor)
        if session is not None and session.close_at <= clock:
            return session
        cursor -= timedelta(days=1)
    raise RuntimeError("Unable to find a closed US equity session in search range.")


def next_us_equity_sessions(after: date, count: int) -> tuple[MarketSession, ...]:
    if count < 0:
        raise ValueError("count must not be negative")
    sessions: list[MarketSession] = []
    cursor = after + timedelta(days=1)
    while len(sessions) < count:
        session = us_equity_session(cursor)
        if session is not None:
            sessions.append(session)
        cursor += timedelta(days=1)
    return tuple(sessions)


def previous_us_equity_sessions(on_or_before: date, count: int) -> tuple[MarketSession, ...]:
    if count < 0:
        raise ValueError("count must not be negative")
    sessions: list[MarketSession] = []
    cursor = on_or_before
    while len(sessions) < count:
        session = us_equity_session(cursor)
        if session is not None:
            sessions.append(session)
        cursor -= timedelta(days=1)
    sessions.reverse()
    return tuple(sessions)


def target_us_equity_window(
    prediction_as_of: datetime,
    horizon: int,
) -> tuple[MarketSession, tuple[MarketSession, ...]]:
    if horizon < 1:
        raise ValueError("horizon must be positive")
    base = latest_closed_us_equity_session(prediction_as_of)
    return base, next_us_equity_sessions(base.session_date, horizon)
