from __future__ import annotations

import altair as alt
import pandas as pd


def signal_bar_chart(df: pd.DataFrame, title: str):
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("signal:N", title="Signál", sort=["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]),
            y=alt.Y("count:Q", title="Počet tickerů"),
            color=alt.Color("signal:N", title="Signál"),
            tooltip=["signal:N", "count:Q"],
        )
        .properties(title=title, height=280)
    )


def histogram_chart(df: pd.DataFrame, title: str, bucket_title: str):
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("bucket:N", title=bucket_title),
            y=alt.Y("count:Q", title="Počet tickerů"),
            tooltip=["bucket:N", "count:Q"],
        )
        .properties(title=title, height=280)
    )


def top_bottom_bar_chart(df: pd.DataFrame, value_col: str, title: str, positive_color: str = "#1f77b4", negative_color: str = "#d62728"):
    if df.empty:
        return alt.Chart(pd.DataFrame({"ticker": [], value_col: []})).mark_bar().properties(title=title)
    temp = df.copy()
    temp["dir"] = temp[value_col].apply(lambda v: "up" if float(v) >= 0 else "down")
    return (
        alt.Chart(temp)
        .mark_bar()
        .encode(
            y=alt.Y("ticker:N", sort="-x", title="Ticker"),
            x=alt.X(f"{value_col}:Q", title="Hodnota"),
            color=alt.Color("dir:N", scale=alt.Scale(domain=["up", "down"], range=[positive_color, negative_color]), legend=None),
            tooltip=["ticker:N", f"{value_col}:Q"],
        )
        .properties(title=title, height=320)
    )


def scatter_score_confidence(df: pd.DataFrame, title: str):
    size_field = "point_size:Q" if "point_size" in df.columns else alt.value(80)
    tooltip = [
        alt.Tooltip("ticker:N", title="Ticker"),
        alt.Tooltip("signal:N", title="Signál"),
        alt.Tooltip("final_total_score:Q", title="FinalTotalScore", format=".2f"),
        alt.Tooltip("final_confidence:Q", title="FinalConfidence", format=".2f"),
    ]
    for col, label in [("risk_score", "RiskScore"), ("rank_in_watchlist", "Rank"), ("market_cap_usd", "MarketCap"), ("news_count_48h", "News 48h")]:
        if col in df.columns:
            tooltip.append(alt.Tooltip(f"{col}:Q", title=label, format=".2f"))

    return (
        alt.Chart(df)
        .mark_circle(opacity=0.85)
        .encode(
            x=alt.X("final_confidence:Q", title="FinalConfidence"),
            y=alt.Y("final_total_score:Q", title="FinalTotalScore"),
            color=alt.Color("signal:N", title="Signál"),
            size=size_field,
            tooltip=tooltip,
        )
        .properties(title=title, height=360)
        .interactive()
    )


def line_chart(df: pd.DataFrame, x: str, y: str, title: str, color: str | None = None):
    chart = alt.Chart(df).mark_line(point=True).encode(x=alt.X(f"{x}:T", title="Čas"), y=alt.Y(f"{y}:Q", title=y), tooltip=[x, y])
    if color:
        chart = chart.encode(color=f"{color}:N")
    return chart.properties(title=title, height=300)


def multi_line_chart(df: pd.DataFrame, title: str):
    return (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("finished_at:T", title="Čas"),
            y=alt.Y("score:Q", title="Skóre"),
            color=alt.Color("module:N", title="Modul"),
            tooltip=["finished_at:T", "module:N", alt.Tooltip("score:Q", format=".2f")],
        )
        .properties(title=title, height=320)
    )
