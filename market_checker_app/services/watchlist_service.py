from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re

from market_checker_app.utils.text import normalize_ticker


DEFAULT_PRODUCTION_WATCHLIST_PATH = (
    Path(__file__).resolve().parents[1] / "production_watchlist.txt"
)
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,23}$")


class WatchlistError(ValueError):
    """Raised when a persisted ticker universe is missing or ambiguous."""


def normalize_watchlist(items: Iterable[object]) -> list[str]:
    """Normalize a watchlist while preserving its declared order.

    Full-line comments beginning with ``#`` are allowed in persisted files.
    Duplicates and malformed symbols fail closed instead of being silently
    removed, because either condition makes a production-universe snapshot
    ambiguous.
    """

    tickers: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    invalid: list[str] = []
    for raw_item in items:
        raw = str(raw_item or "").strip()
        if not raw or raw.startswith("#"):
            continue
        ticker = normalize_ticker(raw)
        if not _TICKER_PATTERN.fullmatch(ticker):
            invalid.append(raw)
            continue
        if ticker in seen:
            duplicates.append(ticker)
            continue
        seen.add(ticker)
        tickers.append(ticker)

    if invalid:
        raise WatchlistError(
            "Watchlist obsahuje neplatné tickery: "
            + ", ".join(dict.fromkeys(invalid))
        )
    if duplicates:
        raise WatchlistError(
            "Watchlist obsahuje duplicitní tickery: "
            + ", ".join(dict.fromkeys(duplicates))
        )
    if not tickers:
        raise WatchlistError("Watchlist neobsahuje žádný platný ticker.")
    return tickers


def load_watchlist(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WatchlistError(f"Watchlist {path} nelze načíst: {exc}") from exc
    return normalize_watchlist(text.splitlines())


def select_watchlist_pilot(
    tickers: Iterable[str],
    limit: int | None,
    *,
    required_tickers: Iterable[str] = (),
) -> list[str]:
    normalized = normalize_watchlist(tickers)
    required = normalize_watchlist(required_tickers) if required_tickers else []
    outside_universe = sorted(set(required).difference(normalized))
    if outside_universe:
        raise WatchlistError(
            "Povinné pilotní tickery nejsou v produkčním universe: "
            + ", ".join(outside_universe)
        )
    if limit is None:
        return normalized
    requested = int(limit)
    if requested <= 0:
        raise WatchlistError("Limit tickerů musí být kladné celé číslo.")
    if len(required) > requested:
        raise WatchlistError(
            "Počet povinných tickerů je vyšší než pilotní limit."
        )

    selected = normalized[:requested]
    missing_required = [ticker for ticker in required if ticker not in selected]
    if not missing_required:
        return selected

    required_set = set(required)
    removable = [ticker for ticker in reversed(selected) if ticker not in required_set]
    if len(removable) < len(missing_required):
        raise WatchlistError("Pilotní limit nemá místo pro všechny povinné tickery.")
    selected_set = set(selected).difference(removable[: len(missing_required)])
    selected_set.update(missing_required)
    return [ticker for ticker in normalized if ticker in selected_set]


def apply_ticker_limit(tickers: Iterable[str], limit: int | None) -> list[str]:
    return select_watchlist_pilot(tickers, limit)
