from __future__ import annotations

from collections.abc import Mapping, Sequence


class FullUniverseAcceptanceError(ValueError):
    """The weekly artifact does not faithfully describe its requested universe."""


def _normalized(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        ticker = str(value or "").strip().upper()
        if not ticker:
            continue
        if ticker in result:
            raise FullUniverseAcceptanceError(f"duplicitní ticker: {ticker}")
        result.append(ticker)
    return result


def validate_full_universe_report(
    summary: Mapping[str, object],
    *,
    expected_tickers: Sequence[object],
) -> dict[str, object]:
    """Validate report completeness without hiding acknowledged data failures.

    This is an operational-contract check, not a quality or performance claim.
    It requires the report to name the exact canonical universe and to account
    for every member either by a per-ticker row or an explicit missing list.
    """

    expected = _normalized(expected_tickers)
    requested = _normalized(list(summary.get("requested_tickers") or []))
    if requested != expected:
        raise FullUniverseAcceptanceError(
            "requested_tickers neodpovídá kanonickému production watchlistu"
        )

    rows = summary.get("ticker_results")
    if not isinstance(rows, list):
        raise FullUniverseAcceptanceError("ticker_results chybí nebo nemá tvar seznamu")
    reported = _normalized(
        [
            row.get("ticker")
            for row in rows
            if isinstance(row, Mapping)
        ]
    )
    unexpected = sorted(set(reported) - set(expected))
    if unexpected:
        raise FullUniverseAcceptanceError(
            "report obsahuje tickery mimo canonical universe: "
            + ", ".join(unexpected)
        )
    missing = [ticker for ticker in expected if ticker not in reported]

    coverage = summary.get("universe_coverage")
    if not isinstance(coverage, Mapping):
        raise FullUniverseAcceptanceError("universe_coverage chybí")
    expected_coverage = {
        "requested": len(expected),
        "reported": len(reported),
        "missing": len(missing),
        "missing_tickers": missing,
    }
    for key, value in expected_coverage.items():
        if coverage.get(key) != value:
            raise FullUniverseAcceptanceError(
                f"universe_coverage.{key} neodpovídá reportu"
            )

    return {
        "status": "ACCEPTED",
        "analysis_only": True,
        "expected_tickers": len(expected),
        "reported_tickers": len(reported),
        "missing_tickers": len(missing),
        "missing_symbols": missing,
        "pipeline_status": str(summary.get("pipeline_status") or "UNKNOWN"),
        "quality_gate_decision": str(
            summary.get("quality_gate_decision") or "UNKNOWN"
        ),
        "note": (
            "Strukturální akceptace neznamená úspěch zdrojů, QualityGate ani "
            "prokázanou predikční přesnost; tyto stavy jsou uvedeny odděleně."
        ),
    }
