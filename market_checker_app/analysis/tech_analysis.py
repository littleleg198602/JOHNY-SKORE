from __future__ import annotations

import pandas as pd

from market_checker_app.models import TechAnalysisResult


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        numeric = float(value)
        if pd.isna(numeric):
            return None
        return numeric
    except (TypeError, ValueError):
        return None


def _last_float(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return _safe_float(series.iloc[-1])


def analyze_tech(ticker: str, ohlc: pd.DataFrame, source: str = "yfinance") -> TechAnalysisResult:
    if ohlc.empty or "Close" not in ohlc.columns:
        return TechAnalysisResult(ticker, 50.0, 20.0, 50, 50, 50, 50, 50, 40, 0, source, 0, "mixed", ["insufficient OHLC history"], ["No OHLC candles available."])

    df = ohlc.copy().dropna(subset=["Close"]).astype(float)
    close = df["Close"]
    high = df["High"] if "High" in df.columns else close
    low = df["Low"] if "Low" in df.columns else close
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(dtype=float)

    latest = _safe_float(close.iloc[-1]) or 0.0
    sma20, sma50, sma100, sma200 = [_safe_float(close.rolling(n).mean().iloc[-1]) if len(close) >= n else None for n in [20, 50, 100, 200]]
    ema20 = _safe_float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(close) >= 20 else None
    ema50 = _safe_float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None
    rsi14 = _last_float(_rsi(close, 14)) if len(close) >= 20 else None
    rsi7 = _last_float(_rsi(close, 7)) if len(close) >= 10 else None

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    stoch_k = _safe_float((((close - low.rolling(14).min()) / (high.rolling(14).max() - low.rolling(14).min()).replace(0, pd.NA)) * 100).iloc[-1]) if len(close) >= 20 else None
    williams_r = _safe_float((-100 * (high.rolling(14).max() - close) / (high.rolling(14).max() - low.rolling(14).min()).replace(0, pd.NA)).iloc[-1]) if len(close) >= 20 else None
    atr14 = _last_float(_atr(df.assign(High=high, Low=low, Close=close), 14)) if len(close) >= 20 else None

    p1w = ((latest / (_safe_float(close.iloc[-6]) or latest)) - 1) * 100 if len(close) >= 6 else 0.0
    p1m = ((latest / (_safe_float(close.iloc[-22]) or latest)) - 1) * 100 if len(close) >= 22 else 0.0
    p3m = ((latest / (_safe_float(close.iloc[-66]) or latest)) - 1) * 100 if len(close) >= 66 else 0.0

    trend_score = 30 + 12 * sum(1 for level in [sma20, sma50, sma100, sma200, ema20, ema50] if level is not None and latest > level)
    momentum_score = max(0.0, min(100.0, 50 + p1w * 1.0 + p1m * 0.9 + p3m * 0.5))
    oscillator_score = max(0.0, min(100.0, 50 + (8 if (rsi14 is not None and 45 <= rsi14 <= 68) else -6) + (6 if (stoch_k is not None and 30 <= stoch_k <= 80) else -5) + (5 if (williams_r is not None and -80 <= williams_r <= -20) else -4)))
    macd_last = _safe_float(macd.iloc[-1])
    macd_sig_last = _safe_float(macd_signal.iloc[-1])
    macd_hist_last = _safe_float(macd_hist.iloc[-1])
    macd_score = 50 + (14 if (macd_last is not None and macd_sig_last is not None and macd_last > macd_sig_last) else -10) + (10 if (macd_hist_last is not None and macd_hist_last > 0) else -8)

    high20 = _safe_float(close.tail(20).max()) if len(close) >= 20 else latest
    low20 = _safe_float(close.tail(20).min()) if len(close) >= 20 else latest
    high52 = _safe_float(close.tail(252).max()) if len(close) >= 120 else _safe_float(close.max())
    low52 = _safe_float(close.tail(252).min()) if len(close) >= 120 else _safe_float(close.min())
    high20 = high20 if high20 is not None else latest
    low20 = low20 if low20 is not None else latest
    high52 = high52 if high52 is not None else latest
    low52 = low52 if low52 is not None else latest

    breakout_score = 50 + (16 if latest >= 0.99 * high20 else 0) + (12 if latest >= 0.97 * high52 else 0) - (12 if latest <= 1.01 * low20 else 0) - (8 if latest <= 1.03 * low52 else 0)

    warnings: list[str] = []
    if volume.empty or volume.isna().all():
        volume_confirmation_score = 42.0
        warnings.append("missing volume data")
    else:
        vol_last = _safe_float(volume.iloc[-1])
        vol_avg = _safe_float(volume.tail(20).mean()) if len(volume) >= 20 else vol_last
        volume_confirmation_score = 45 + (15 if (vol_last is not None and vol_avg is not None and vol_last > vol_avg) else -5)

    realized_vol = _safe_float(close.pct_change().tail(20).std()) if len(close) >= 22 else 0.02
    realized_vol = realized_vol if realized_vol is not None else 0.02
    volatility_adj = -6 if realized_vol > 0.04 else (4 if realized_vol < 0.015 else 0)

    regime = "mixed"
    if p1m > 6:
        regime = "trending_up"
    elif p1m < -6:
        regime = "trending_down"
    elif abs(p1m) <= 3 and realized_vol <= 0.018:
        regime = "sideways_low_vol"
    elif abs(p1m) <= 4 and realized_vol > 0.018:
        regime = "sideways_high_vol"

    tech_score = max(0.0, min(100.0, trend_score * 0.26 + momentum_score * 0.2 + oscillator_score * 0.14 + macd_score * 0.16 + breakout_score * 0.14 + volume_confirmation_score * 0.1 + volatility_adj))
    indicator_count = sum(value is not None for value in [sma20, sma50, sma100, sma200, ema20, ema50, rsi14, rsi7, stoch_k, williams_r, atr14])
    tech_conf = max(0.0, min(100.0, 32 + min(1.0, len(close) / 252) * 30 + indicator_count * 2.8 + (12 if source == "mt5" else 6) - (8 if "missing volume data" in warnings else 0)))

    return TechAnalysisResult(
        ticker=ticker,
        tech_score=round(tech_score, 2),
        tech_confidence=round(tech_conf, 2),
        trend_score=round(max(0.0, min(100.0, trend_score)), 2),
        momentum_score=round(momentum_score, 2),
        oscillator_score=round(oscillator_score, 2),
        macd_score=round(max(0.0, min(100.0, macd_score)), 2),
        breakout_score=round(max(0.0, min(100.0, breakout_score)), 2),
        volume_confirmation_score=round(max(0.0, min(100.0, volume_confirmation_score)), 2),
        volatility_context_adjustment=round(volatility_adj, 2),
        source=source,
        candles_count=len(df),
        regime=regime,
        warnings=warnings,
        reasons=[f"Trend regime {regime}, 1M move {p1m:.2f}%.", f"MACD hist {(macd_hist_last if macd_hist_last is not None else 0):.4f}, RSI14 {(rsi14 if rsi14 is not None else 0):.1f}."],
        indicators={"sma20": sma20, "sma50": sma50, "sma100": sma100, "sma200": sma200, "ema20": ema20, "ema50": ema50, "rsi14": rsi14, "rsi7": rsi7, "atr14": atr14, "p1w": p1w, "p1m": p1m, "p3m": p3m, "realized_volatility": realized_vol},
    )
