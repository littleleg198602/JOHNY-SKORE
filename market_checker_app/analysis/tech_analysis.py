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


def analyze_tech(ticker: str, ohlc: pd.DataFrame, source: str = "yfinance") -> TechAnalysisResult:
    if ohlc.empty or "Close" not in ohlc.columns:
        return TechAnalysisResult(ticker, 50.0, 20.0, 50, 50, 50, 50, 50, 40, 0, source, 0, "mixed", ["insufficient OHLC history"], ["No OHLC candles available."])

    df = ohlc.copy().dropna(subset=["Close"]).astype(float)
    close = df["Close"]
    high = df["High"] if "High" in df.columns else close
    low = df["Low"] if "Low" in df.columns else close
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(dtype=float)

    latest = float(close.iloc[-1])
    sma20, sma50, sma100, sma200 = [float(close.rolling(n).mean().iloc[-1]) if len(close) >= n else None for n in [20, 50, 100, 200]]
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(close) >= 20 else None
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None
    rsi14 = float(_rsi(close, 14).iloc[-1]) if len(close) >= 20 else None
    rsi7 = float(_rsi(close, 7).iloc[-1]) if len(close) >= 10 else None

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    stoch_k = (((close - low.rolling(14).min()) / (high.rolling(14).max() - low.rolling(14).min()).replace(0, pd.NA)) * 100).iloc[-1] if len(close) >= 20 else None
    williams_r = (-100 * (high.rolling(14).max() - close) / (high.rolling(14).max() - low.rolling(14).min()).replace(0, pd.NA)).iloc[-1] if len(close) >= 20 else None
    atr14 = float(_atr(df.assign(High=high, Low=low, Close=close), 14).iloc[-1]) if len(close) >= 20 else None

    p1w = ((latest / float(close.iloc[-6])) - 1) * 100 if len(close) >= 6 else 0.0
    p1m = ((latest / float(close.iloc[-22])) - 1) * 100 if len(close) >= 22 else 0.0
    p3m = ((latest / float(close.iloc[-66])) - 1) * 100 if len(close) >= 66 else 0.0

    trend_score = 30 + 12 * sum(1 for level in [sma20, sma50, sma100, sma200, ema20, ema50] if level is not None and latest > level)
    momentum_score = max(0.0, min(100.0, 50 + p1w * 1.0 + p1m * 0.9 + p3m * 0.5))
    oscillator_score = max(0.0, min(100.0, 50 + (8 if rsi14 and 45 <= rsi14 <= 68 else -6) + (6 if stoch_k and 30 <= stoch_k <= 80 else -5) + (5 if williams_r and -80 <= williams_r <= -20 else -4)))
    macd_score = 50 + (14 if macd.iloc[-1] > macd_signal.iloc[-1] else -10) + (10 if macd_hist.iloc[-1] > 0 else -8)

    high20 = float(close.tail(20).max()) if len(close) >= 20 else latest
    low20 = float(close.tail(20).min()) if len(close) >= 20 else latest
    high52 = float(close.tail(252).max()) if len(close) >= 120 else float(close.max())
    low52 = float(close.tail(252).min()) if len(close) >= 120 else float(close.min())
    breakout_score = 50 + (16 if latest >= 0.99 * high20 else 0) + (12 if latest >= 0.97 * high52 else 0) - (12 if latest <= 1.01 * low20 else 0) - (8 if latest <= 1.03 * low52 else 0)

    warnings: list[str] = []
    if volume.empty or volume.isna().all():
        volume_confirmation_score = 42.0
        warnings.append("missing volume data")
    else:
        volume_confirmation_score = 45 + (15 if float(volume.iloc[-1]) > float(volume.tail(20).mean()) else -5)

    realized_vol = float(close.pct_change().tail(20).std()) if len(close) >= 22 else 0.02
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
        reasons=[f"Trend regime {regime}, 1M move {p1m:.2f}%.", f"MACD hist {float(macd_hist.iloc[-1]):.4f}, RSI14 {rsi14 if rsi14 else 0:.1f}."] ,
        indicators={"sma20": sma20, "sma50": sma50, "sma100": sma100, "sma200": sma200, "ema20": ema20, "ema50": ema50, "rsi14": rsi14, "rsi7": rsi7, "atr14": atr14, "p1w": p1w, "p1m": p1m, "p3m": p3m, "realized_volatility": realized_vol},
    )
