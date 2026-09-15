from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

import pandas as pd

from market_checker_app.services.us_equity_calendar import (
    latest_closed_us_equity_session,
    us_equity_session,
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
    """Validate daily OHLC against actual closed US equity sessions.

    Daily provider timestamps are exchange-session labels, not evidence that a
    session has already closed. Unclosed/future rows are ignored, and only the
    latest actually closed session may supply the current price. Duplicate
    sessions, non-finite prices, holidays and missing expected sessions are
    handled explicitly.

    ``max_close_age`` remains in the signature for backward compatibility. The
    authoritative freshness rule is the expected closed exchange session,
    which is stricter and holiday/DST aware.
    """
    del max_close_age
    warnings: list[str] = []
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _empty_quality("OHLC data chybí nebo jsou prázdná.")
    if "Close" not in frame.columns:
        return _empty_quality("OHLC data nemají sloupec Close.")

    now = _as_utc(as_of)
    working = frame.copy()
    session_dates = [_session_date(value) for value in working.index]
    closes = pd.to_numeric(working["Close"], errors="coerce")

    valid_positions: list[int] = []
    valid_session_dates = []
    session_closes: list[datetime] = []
    invalid_close_count = 0
    invalid_session_count = 0
    non_session_count = 0
    unclosed_session_dates: set[str] = set()

    for position, (session_date, raw_close) in enumerate(zip(session_dates, closes)):
        close = _finite_positive(raw_close)
        if close is None:
            invalid_close_count += 1
            continue
        if session_date is None:
            invalid_session_count += 1
            continue
        session = us_equity_session(session_date)
        if session is None:
            non_session_count += 1
            continue
        if session.close_at > now:
            unclosed_session_dates.add(session_date.isoformat())
            continue
        valid_positions.append(position)
        valid_session_dates.append(session_date)
        session_closes.append(session.close_at)

    if not valid_positions:
        if unclosed_session_dates:
            return _empty_quality(
                "OHLC neobsahuje žádnou plně uzavřenou použitelnou seanci; "
                "neuzavřené/budoucí řádky byly odmítnuty."
            )
        return _empty_quality(
            "OHLC neobsahuje kladnou konečnou cenu na platné americké obchodní seanci."
        )

    normalized = working.iloc[valid_positions].copy()
    normalized["Close"] = [
        float(closes.iloc[position]) for position in valid_positions
    ]
    normalized["_session_date"] = valid_session_dates
    normalized["_session_close_at"] = session_closes
    normalized = normalized.sort_values(
        ["_session_date", "_session_close_at"],
        kind="stable",
    )

    duplicate_count = int(normalized.duplicated("_session_date", keep="last").sum())
    normalized = normalized.drop_duplicates("_session_date", keep="last")
    normalized = normalized.sort_values("_session_date", kind="stable")

    canonical_close_index = pd.DatetimeIndex(
        normalized["_session_close_at"].tolist(),
        name=frame.index.name,
    )
    normalized = normalized.drop(columns=["_session_date", "_session_close_at"])
    normalized.index = canonical_close_index

    if invalid_close_count:
        warnings.append(
            f"OHLC obsahuje {invalid_close_count} řádků s neplatnou nebo nekonečnou Close."
        )
    if invalid_session_count:
        warnings.append(
            f"OHLC obsahuje {invalid_session_count} řádků bez platného data seance."
        )
    if non_session_count:
        warnings.append(
            f"OHLC obsahuje {non_session_count} řádků mimo obchodní kalendář; byly vynechány."
        )
    if unclosed_session_dates:
        warnings.append(
            "OHLC obsahuje dosud neuzavřenou nebo budoucí seanci; byla ignorována: "
            + ", ".join(sorted(unclosed_session_dates))
            + "."
        )
    if duplicate_count:
        warnings.append(
            f"OHLC obsahuje {duplicate_count} duplicitních seancí; počítá se pouze poslední řádek každé seance."
        )

    expected = latest_closed_us_equity_session(now)
    count = len(normalized)
    latest_at = normalized.index[-1].to_pydatetime()
    latest_session_date = latest_at.date()
    latest_close = float(normalized["Close"].iloc[-1])

    price_usable = latest_session_date == expected.session_date
    if not price_usable:
        warnings.append(
            "Chybí poslední očekávaná uzavřená US seance "
            f"{expected.session_date.isoformat()}; poslední použitelná seance je "
            f"{latest_session_date.isoformat()}."
        )

    required_columns = ("Open", "High", "Low")
    missing_columns = [column for column in required_columns if column not in normalized.columns]
    ohlc_shape_usable = not missing_columns
    if missing_columns:
        warnings.append(
            "Pro technické indikátory chybí OHLC sloupce: "
            + ", ".join(missing_columns)
            + "."
        )
    else:
        recent = normalized.tail(max(1, int(min_history_rows))).copy()
        for column in required_columns:
            recent[column] = pd.to_numeric(recent[column], errors="coerce")
        finite_mask = recent[list(required_columns)].apply(
            lambda column: column.map(
                lambda value: (
                    pd.notna(value)
                    and math.isfinite(float(value))
                    and float(value) > 0.0
                )
            )
        ).all(axis=1)
        logical_mask = (
            (recent["High"] >= recent[["Open", "Low", "Close"]].max(axis=1))
            & (recent["Low"] <= recent[["Open", "High", "Close"]].min(axis=1))
        )
        if not bool((finite_mask & logical_mask).all()):
            ohlc_shape_usable = False
            warnings.append(
                "Technická OHLC historie obsahuje neplatné nebo nelogické Open/High/Low hodnoty."
            )

    if "Volume" in normalized.columns:
        volume = pd.to_numeric(normalized["Volume"], errors="coerce")
        valid_volume = volume.map(
            lambda value: (
                pd.notna(value)
                and math.isfinite(float(value))
                and float(value) >= 0.0
            )
        )
        invalid_volume = int((~valid_volume).sum())
        if invalid_volume:
            normalized.loc[~valid_volume, "Volume"] = pd.NA
            warnings.append(
                f"OHLC obsahuje {invalid_volume} neplatných hodnot Volume; objemové indikátory je nesmí použít."
            )

    available_lookbacks = tuple(
        lookback for lookback in TECHNICAL_LOOKBACKS if count >= lookback
    )
    missing_lookbacks = tuple(
        lookback for lookback in TECHNICAL_LOOKBACKS if count < lookback
    )
    history_usable = (
        price_usable
        and ohlc_shape_usable
        and count >= max(1, int(min_history_rows))
    )
    if price_usable and count < max(1, int(min_history_rows)):
        warnings.append(
            f"OHLC historie má jen {count} unikátních uzavřených seancí; "
            f"pro základní technickou historii je potřeba alespoň {min_history_rows}."
        )
    if missing_lookbacks:
        warnings.append(
            "Nedostupné technické lookbacky (seance): "
            + ", ".join(str(value) for value in missing_lookbacks)
            + "."
        )

    return OhlcQuality(
        normalized=normalized,
        close=latest_close if price_usable else None,
        close_at=latest_at,
        observation_count=count,
        price_usable=price_usable,
        history_usable=history_usable,
        warnings=tuple(warnings),
        available_lookbacks=available_lookbacks,
        missing_lookbacks=missing_lookbacks,
    )
