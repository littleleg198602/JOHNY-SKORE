from __future__ import annotations

import csv
from pathlib import Path

from market_checker_app.utils.text import normalize_ticker


CANONICAL_TICKER_COUNT = 687
CANONICAL_SOURCE_FILE = "market_checker_20260818_213623.xlsx"
DEFAULT_TICKER_UNIVERSE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "market_checker_687_tickers.csv"
)


def load_canonical_ticker_records(
    path: Path | None = None,
) -> list[dict[str, str]]:
    """Load and validate the official NEW ANALYZER ticker universe.

    The CSV is a text projection of the Market Checker Excel export.  Keeping
    the source in the repository makes the ticker universe reproducible for
    both the Streamlit UI and the unattended weekly runner.
    """

    source_path = Path(path or DEFAULT_TICKER_UNIVERSE_PATH)
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        required = {"ticker", "yahoo_ticker"}
        missing = sorted(required - fieldnames)
        if missing:
            raise ValueError(
                f"Ticker universe {source_path} postrádá sloupce: {', '.join(missing)}"
            )

        records: list[dict[str, str]] = []
        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            ticker = normalize_ticker(str(row.get("ticker") or ""))
            yahoo_ticker = normalize_ticker(
                str(row.get("yahoo_ticker") or ticker)
            )
            if not ticker:
                raise ValueError(
                    f"Ticker universe {source_path} obsahuje prázdný ticker na řádku {row_number}"
                )
            if ticker in seen:
                raise ValueError(
                    f"Ticker universe {source_path} obsahuje duplicitu: {ticker}"
                )
            if not yahoo_ticker:
                raise ValueError(
                    f"Ticker universe {source_path} nemá Yahoo ticker pro {ticker}"
                )
            seen.add(ticker)
            records.append(
                {
                    "ticker": ticker,
                    "yahoo_ticker": yahoo_ticker,
                }
            )

    if len(records) != CANONICAL_TICKER_COUNT:
        raise ValueError(
            f"Ticker universe {source_path} obsahuje {len(records)} tickerů; "
            f"očekáváno je přesně {CANONICAL_TICKER_COUNT}."
        )
    return records


def load_canonical_tickers(path: Path | None = None) -> list[str]:
    """Return the official ticker list in the source Excel order."""

    return [record["ticker"] for record in load_canonical_ticker_records(path)]
