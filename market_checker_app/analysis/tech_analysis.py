from __future__ import annotations

import math

import pandas as pd

from market_checker_app.models import TechAnalysisResult


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def analyze_tech(ticker: str, ohlc: pd.DataFrame, source: str = "yfinance") -> TechAnalysisResult:
    if ohlc.empty or "Close" not in ohlc.columns:
        return TechAnalysisResult(ticker, 50.0, 20.0, 50, 50, 50, 50, 50, 40, 0, source, 0, ["insufficient OHLC history"], ["No OHLC candles available for technical analysis."])

    df = ohlc.copy().dropna(subset=["Close"])
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(dtype=float)

    sma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma100 = close.rolling(100).mean().iloc[-1] if len(close) >= 100 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    rsi14 = _rsi(close, 14).iloc[-1] if len(close) >= 20 else None
    rsi7 = _rsi(close, 7).iloc[-1] if len(close) >= 10 else None

    latest = close.iloc[-1]
    p1w = ((latest / close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0
    p1m = ((latest / close.iloc[-22]) - 1) * 100 if len(close) >= 22 else 0
    p3m = ((latest / close.iloc[-66]) - 1) * 100 if len(close) >= 66 else 0
    high20 = close.tail(20).max() if len(close) >= 20 else latest
    low20 = close.tail(20).min() if len(close) >= 20 else latest
    high52 = close.tail(252).max() if len(close) >= 100 else close.max()
    low52 = close.tail(252).min() if len(close) >= 100 else close.min()

    trend_points = sum([
        1 if sma20 and latest > sma20 else 0,
        1 if sma50 and latest > sma50 else 0,
        1 if sma100 and latest > sma100 else 0,
        1 if sma200 and latest > sma200 else 0,
    ])
    trend_score = 35 + trend_points * 15
    momentum_score = max(0, min(100, 50 + p1w * 1.2 + p1m * 0.8 + p3m * 0.5))

    osc = 50.0
    if rsi14 is not None:
        osc += 12 if 45 <= rsi14 <= 65 else (-8 if rsi14 > 75 else 5)
    if rsi7 is not None:
        osc += 6 if 45 <= rsi7 <= 70 else -4
    oscillator_score = max(0, min(100, osc))

    macd_score = 55 + (10 if macd.iloc[-1] > macd_signal.iloc[-1] else -10) + (8 if macd_hist.iloc[-1] > 0 else -8)
    breakout_score = 50 + (15 if latest >= high20 * 0.99 else 0) - (10 if latest <= low20 * 1.01 else 0) + (10 if latest >= high52 * 0.97 else 0) - (10 if latest <= low52 * 1.03 else 0)

    vol_score = 50.0
    warnings: list[str] = []
    if volume.empty or volume.isna().all():
        vol_score = 40.0
        warnings.append("missing volume data")
    elif len(volume) > 20:
        vol_score = 45 + (15 if volume.iloc[-1] > volume.tail(20).mean() else -5)

    returns = close.pct_change().tail(20)
    vol = float(returns.std()) if not returns.empty else 0.0
    volatility_adj = -5 if vol > 0.04 else (3 if vol < 0.015 else 0)

    tech = max(0.0, min(100.0, trend_score * 0.28 + momentum_score * 0.2 + oscillator_score * 0.14 + macd_score * 0.16 + breakout_score * 0.12 + vol_score * 0.1 + volatility_adj))

    completeness = min(1.0, len(close) / 220)
    indicator_count = sum(v is not None for v in [sma20, sma50, sma100, sma200, rsi14, rsi7])
    conf = 30 + completeness * 35 + (indicator_count / 6) * 20 + (15 if source == "mt5" else 8)
    if "missing volume data" in warnings:
        conf -= 10
    conf = max(0.0, min(100.0, conf))

    reasons = [
        f"Price regime: {trend_points}/4 key moving averages are below current price.",
        f"Momentum profile: 1W {p1w:.2f}%, 1M {p1m:.2f}%, 3M {p3m:.2f}%.",
    ]
    if macd.iloc[-1] > macd_signal.iloc[-1]:
        reasons.append("Bullish MACD crossover remains active.")

    return TechAnalysisResult(
        ticker=ticker,
        tech_score=round(tech, 2),
        tech_confidence=round(conf, 2),
        trend_score=round(trend_score, 2),
        momentum_score=round(momentum_score, 2),
        oscillator_score=round(oscillator_score, 2),
        macd_score=round(max(0, min(100, macd_score)), 2),
        breakout_score=round(max(0, min(100, breakout_score)), 2),
        volume_confirmation_score=round(max(0, min(100, vol_score)), 2),
        volatility_context_adjustment=round(volatility_adj, 2),
        source=source,
        candles_count=len(df),
        warnings=warnings,
        reasons=reasons,
        indicators={"sma20": sma20, "sma50": sma50, "sma100": sma100, "sma200": sma200, "rsi14": rsi14, "rsi7": rsi7},
    )
