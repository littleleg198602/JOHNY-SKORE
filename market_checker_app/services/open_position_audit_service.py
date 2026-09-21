from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


AUDIT_COLUMNS = [
    "position_ticket", "ticker", "side", "volume", "opened_at", "entry_price",
    "current_price", "price_change_pct", "stop_loss", "take_profit", "profit", "swap",
    "comment", "forecast", "action", "final_total_score", "final_confidence", "risk_score",
    "data_quality_score", "audit_status", "audit_reasons", "previous_audit_status",
    "status_changed",
]


def _number(value: object, default: float = 0.0) -> float:
    """Return a numeric value while treating missing MT5/pandas values as empty."""
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_open_position_audit(
    positions: pd.DataFrame,
    signals: pd.DataFrame,
    previous_audits: pd.DataFrame | None = None,
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """Create an explainable, read-only review of open MT5 positions.

    The result deliberately uses MONITOR/ATTENTION/REVIEW, never a command to
    trade.  Position data are matched to the current analysis only by symbol.
    """
    observed_at = observed_at or datetime.now(timezone.utc)
    if positions is None or positions.empty:
        return {"status": "NO_OPEN_POSITIONS", "observed_at": observed_at.isoformat(), "analysis_only": True, "positions": []}

    signal_columns = [
        "ticker", "forecast", "action", "final_total_score", "final_confidence",
        "risk_score", "data_quality_score", "warnings", "risk_flags",
    ]
    available = [column for column in signal_columns if column in signals.columns]
    signal_map = (
        signals[available].drop_duplicates("ticker").set_index("ticker").to_dict("index")
        if "ticker" in available else {}
    )
    previous_map: dict[str, str] = {}
    if previous_audits is not None and not previous_audits.empty:
        previous_map = {
            str(row.position_ticket): str(row.audit_status)
            for row in previous_audits.itertuples(index=False)
            if getattr(row, "position_ticket", None)
        }

    records: list[dict[str, object]] = []
    for position in positions.to_dict(orient="records"):
        ticker = str(position.get("ticker") or "").upper()
        side = str(position.get("side") or "").upper()
        matched = signal_map.get(ticker)
        reasons: list[str] = []
        status = "MONITOR"
        entry = _number(position.get("entry_price"))
        current = _number(position.get("current_price"))
        change = None
        if entry > 0 and current > 0:
            raw_change = (current - entry) / entry * 100
            change = raw_change if side == "BUY" else -raw_change
        if _number(position.get("stop_loss")) <= 0:
            reasons.append("chybí stop-loss")
            status = "ATTENTION"
        if matched is None:
            reasons.append("pozice nebyla v tomto běhu analyzována")
            status = "REVIEW"
            matched = {}
        else:
            action = str(matched.get("action") or "NO_TRADE").upper()
            forecast = str(matched.get("forecast") or "FLAT").upper()
            opposite = (side == "BUY" and action == "SELL") or (side == "SELL" and action == "BUY")
            if opposite:
                reasons.append(f"analytická akce {action} je v rozporu se směrem pozice {side}")
                status = "REVIEW"
            elif (forecast == "DOWN" and side == "BUY") or (forecast == "UP" and side == "SELL"):
                reasons.append(f"forecast {forecast} je proti směru pozice {side}")
                status = "REVIEW"
            if _number(matched.get("risk_score")) >= 70:
                reasons.append("vysoké rizikové skóre")
                if status == "MONITOR":
                    status = "ATTENTION"
            if _number(matched.get("data_quality_score"), 100.0) < 60:
                reasons.append("nízká kvalita vstupních dat")
                if status == "MONITOR":
                    status = "ATTENTION"
        if not reasons:
            reasons.append("bez nového konfliktu; průběžně sledovat další týdenní běh")
        ticket = str(position.get("position_ticket") or "")
        previous = previous_map.get(ticket, "")
        records.append({
            **{column: position.get(column) for column in AUDIT_COLUMNS if column in position},
            "position_ticket": ticket, "ticker": ticker, "side": side,
            "price_change_pct": round(change, 2) if change is not None else None,
            "forecast": matched.get("forecast", "UNANALYZED"),
            "action": matched.get("action", "UNANALYZED"),
            "final_total_score": matched.get("final_total_score"),
            "final_confidence": matched.get("final_confidence"),
            "risk_score": matched.get("risk_score"),
            "data_quality_score": matched.get("data_quality_score"),
            "audit_status": status, "audit_reasons": reasons,
            "previous_audit_status": previous or None, "status_changed": bool(previous and previous != status),
        })
    frame = pd.DataFrame(records, columns=AUDIT_COLUMNS)
    return {
        "status": "SUCCESS", "observed_at": observed_at.isoformat(), "analysis_only": True,
        "automated_trading": {"enabled": False, "execution_path": "absent"},
        "summary": {"positions": len(frame), **{key.lower(): int((frame["audit_status"] == key).sum()) for key in ("MONITOR", "ATTENTION", "REVIEW")}},
        "positions": frame.to_dict(orient="records"),
    }
