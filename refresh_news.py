# -*- coding: utf-8 -*-
"""refresh_news_auto.py

JEDEN skript, který pokaždé vytvoří NOVÝ Excel a naplní ho:
- tickery: z MT5 (viditelné symboly v MarketWatch)
- news: RSS podle šablon (zdroje jsou definované v kódu a zároveň se zapíšou do sheetu Sources)
- technika: počítá se z MT5 (D1) -> TechScore(0–50)
- Yahoo: yfinance -> YahooScore(-20..20) + detail sloupce
- MarketCap+Rank: volitelné (pokud existuje soubor market_watch_symbols_enriched_yahoo.xlsx)

Pozn:
- Použitá logika TotalScore + Signal je převzatá z tvé "refresh_news - kopie.py"
  (tj. STRONG BUY/BUY/HOLD/SELL/STRONG SELL).
- V Excelu NEZAPISUJEME timezone-aware datetime (Excel/openpyxl to neumí).

Spuštění:
  python refresh_news_auto.py

Volitelné argumenty:
  python refresh_news_auto.py --outdir "C:\\Users\\...\\Documents\\JOHNY" \
      --marketcap "market_watch_symbols_enriched_yahoo.xlsx"

"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font


# -----------------------------
# SOURCES (fundamentální / news)
# -----------------------------

SOURCES = [
    {"source": "Reuters", "type": "newswire", "paywall": "paid/limited", "info_level": 4, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Bloomberg", "type": "news+analysis", "paywall": "paid", "info_level": 5, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "The Wall Street Journal", "type": "business news", "paywall": "paid", "info_level": 4, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Financial Times", "type": "business+macro", "paywall": "paid", "info_level": 4, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Barron's", "type": "investing", "paywall": "paid/limited", "info_level": 4, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Morningstar", "type": "fundamentals", "paywall": "limited", "info_level": 4, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "MarketWatch", "type": "news+commentary", "paywall": "limited", "info_level": 3, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Yahoo Finance", "type": "aggregator", "paywall": "limited", "info_level": 3, "template": "https://finance.yahoo.com/rss/headline?s={ticker}", "notes": "RSS podporováno skriptem"},
    {"source": "Seeking Alpha", "type": "analysis (contributors)", "paywall": "limited", "info_level": 3, "template": "https://seekingalpha.com/api/sa/combined/{ticker}.xml", "notes": "RSS podporováno skriptem"},
    {"source": "Zacks", "type": "ratings+earnings", "paywall": "limited", "info_level": 3, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "TheStreet", "type": "news+commentary", "paywall": "limited", "info_level": 2, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Investing.com", "type": "news+transcripts", "paywall": "limited", "info_level": 3, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "Benzinga", "type": "news+market moving", "paywall": "limited", "info_level": 2, "template": "https://www.benzinga.com/markets/feed", "notes": "RSS podporováno skriptem"},
    {"source": "TipRanks", "type": "analyst aggregation", "paywall": "limited", "info_level": 2, "template": "", "notes": "Ruční / API / vyžaduje přihlášení"},
    {"source": "The Motley Fool", "type": "retail stock-picks", "paywall": "limited", "info_level": 2, "template": "", "notes": "RSS podporováno skriptem"},
    {"source": "StockAnalysis.com", "type": "fundamentals+news", "paywall": "free", "info_level": 3, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "StockTitan", "type": "news+press releases", "paywall": "free", "info_level": 3, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "FINVIZ", "type": "screener+news links", "paywall": "free", "info_level": 2, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "Nasdaq.com", "type": "news+data", "paywall": "free", "info_level": 3, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "MarketScreener", "type": "news+fundamentals", "paywall": "free", "info_level": 3, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "CompaniesMarketCap", "type": "fundamentals", "paywall": "free", "info_level": 2, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "SEC EDGAR", "type": "filings", "paywall": "free", "info_level": 5, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "PR Newswire", "type": "press releases", "paywall": "free", "info_level": 3, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
    {"source": "Business Wire", "type": "press releases", "paywall": "free", "info_level": 3, "template": "", "notes": "Bez RSS v šabloně – zatím ručně"},
]


def now_local_naive() -> dt.datetime:
    return dt.datetime.now().replace(tzinfo=None)


def excel_safe(v):
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None)
    return v


def slugify_filename(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s.strip("_")


def print_bar(prefix: str, i: int, n: int, width: int = 26):
    if n <= 0:
        return
    frac = min(max(i / n, 0.0), 1.0)
    filled = int(round(frac * width))
    bar = "#" * filled + "-" * (width - filled)
    pct = int(round(frac * 100))
    sys.stdout.write(f"\r{prefix} [{bar}] {pct:3d}% ({i}/{n})")
    sys.stdout.flush()
    if i >= n:
        sys.stdout.write("\n")


def load_env_from_code_env(path: str = "code.env") -> Dict[str, str]:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            env[k] = v
            os.environ.setdefault(k, v)
    return env


def mt5_connect():
    try:
        import MetaTrader5 as mt5
    except Exception as e:
        raise RuntimeError("Chybí balík MetaTrader5. Nainstaluj: pip install MetaTrader5") from e

    if not mt5.initialize():
        err = mt5.last_error()
        raise RuntimeError(f"MT5 initialize failed: {err}")

    return mt5


def mt5_visible_symbols(mt5) -> List[str]:
    syms = []
    for s in mt5.symbols_get():
        if getattr(s, "visible", False):
            syms.append(s.name)
    return sorted(set(syms))


def mt5_copy_rates(mt5, symbol: str, timeframe, count: int = 300):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    return rates


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def tech_score_from_mt5(mt5, symbol: str) -> Tuple[Optional[float], Dict[str, Optional[float]], str]:
    tf = mt5.TIMEFRAME_D1
    rates = mt5_copy_rates(mt5, symbol, tf, 250)
    if rates is None or len(rates) < 60:
        return None, {"Close": None, "MA20": None, "MA50": None, "RSI14": None}, "missing"

    closes = [float(r["close"]) for r in rates]
    close = closes[-1]
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    rsi14 = rsi(closes, 14)

    score = 0.0

    if ma20 is not None and ma50 is not None:
        if ma20 > ma50:
            score += 12
        else:
            score += 4
        if close > ma20:
            score += 8
        else:
            score += 2

    if rsi14 is not None:
        if 45 <= rsi14 <= 65:
            score += 15
        elif 35 <= rsi14 < 45 or 65 < rsi14 <= 75:
            score += 10
        elif 25 <= rsi14 < 35 or 75 < rsi14 <= 85:
            score += 6
        else:
            score += 3

    if ma20 is not None:
        dist = (close - ma20) / ma20 * 100.0
        if dist >= 8:
            score += 2
        elif dist >= 3:
            score += 6
        elif dist >= -3:
            score += 10
        elif dist >= -8:
            score += 6
        else:
            score += 2

    score = max(0.0, min(50.0, score))
    return score, {"Close": close, "MA20": ma20, "MA50": ma50, "RSI14": rsi14}, "ok_mt"


def yahoo_details_and_score(symbol: str) -> Tuple[float, Dict[str, Optional[float]], str]:
    try:
        import yfinance as yf
    except Exception as e:
        raise RuntimeError("Chybí yfinance. Nainstaluj: pip install yfinance") from e

    ysym = symbol
    try:
        t = yf.Ticker(ysym)
        info = getattr(t, "info", {}) or {}

        price = info.get("currentPrice") or info.get("regularMarketPrice") or None
        target = info.get("targetMeanPrice") or info.get("targetMedianPrice") or None
        reco = info.get("recommendationMean") or None
        reco_key = info.get("recommendationKey") or None

        upside = None
        if price and target and price != 0:
            upside = (float(target) / float(price) - 1.0) * 100.0

        score = 0.0
        if reco is not None:
            rm = float(reco)
            if rm <= 1.6:
                score += 8
            elif rm <= 2.2:
                score += 5
            elif rm <= 2.8:
                score += 2
            else:
                score -= 2

        if upside is not None:
            if upside >= 40:
                score += 10
            elif upside >= 20:
                score += 7
            elif upside >= 5:
                score += 3
            elif upside >= -5:
                score += 0
            elif upside >= -15:
                score -= 4
            else:
                score -= 8

        score = max(-20.0, min(20.0, score))

        details = {
            "YahooPrice": float(price) if price is not None else None,
            "YahooTarget": float(target) if target is not None else None,
            "YahooUpsidePct": float(upside) if upside is not None else None,
            "YahooRatingKey": reco_key,
            "YahooRatingMean": float(reco) if reco is not None else None,
        }
        return score, details, "ok_yf"
    except Exception:
        return 0.0, {"YahooPrice": None, "YahooTarget": None, "YahooUpsidePct": None, "YahooRatingKey": None, "YahooRatingMean": None}, "missing"


@dataclass
class NewsItem:
    ticker: str
    source: str
    title: str
    link: str
    published_utc: Optional[dt.datetime]
    weight: float


def parse_published_dt(entry) -> Optional[dt.datetime]:
    for key in ("published_parsed", "updated_parsed"):
        if hasattr(entry, key) and getattr(entry, key):
            try:
                st = getattr(entry, key)
                return dt.datetime.fromtimestamp(time.mktime(st), tz=dt.timezone.utc)
            except Exception:
                pass
    return None


def source_weight(info_level: int) -> float:
    return 0.5 + (float(info_level) / 5.0) * 1.5


def fetch_rss_items_for_ticker(ticker: str, max_per_source: int = 12) -> List[NewsItem]:
    try:
        import feedparser
    except Exception as e:
        raise RuntimeError("Chybí feedparser. Nainstaluj: pip install feedparser") from e

    items: List[NewsItem] = []

    for src in SOURCES:
        template = (src.get("template") or "").strip()
        if "{ticker}" not in template:
            continue

        url = template.format(ticker=ticker)
        feed = feedparser.parse(url)

        if getattr(feed, "bozo", False) and getattr(feed, "bozo_exception", None):
            continue

        info_level = int(src.get("info_level") or 3)
        w = source_weight(info_level)

        entries = getattr(feed, "entries", []) or []
        for e in entries[:max_per_source]:
            title = getattr(e, "title", "") or ""
            link = getattr(e, "link", "") or ""
            pub = parse_published_dt(e)
            items.append(
                NewsItem(
                    ticker=ticker,
                    source=str(src.get("source")),
                    title=title,
                    link=link,
                    published_utc=pub,
                    weight=w,
                )
            )

    return items


def news_metrics_48h(items: List[NewsItem], now_utc: dt.datetime) -> Tuple[float, int]:
    cutoff = now_utc - dt.timedelta(hours=48)
    wsum = 0.0
    cnt = 0
    for it in items:
        if it.published_utc is None:
            continue
        if it.published_utc >= cutoff:
            cnt += 1
            age_h = (now_utc - it.published_utc).total_seconds() / 3600.0
            recency = max(0.05, 1.0 - (age_h / 48.0))
            wsum += it.weight * recency
    return wsum, cnt


def news_score_0_50(news_weighted_48h: float, news_volume_48h: int) -> float:
    base = 0.0
    base += min(30.0, news_weighted_48h * 4.0)
    base += min(20.0, math.log1p(news_volume_48h) * 5.0)
    return max(0.0, min(50.0, base))


def compute_total_score_macro_logic(news_0_50: float, tech_0_50: float, yahoo_minus20_20: float) -> float:
    total = 0.0
    total += float(news_0_50)
    total += float(tech_0_50)
    total += (float(yahoo_minus20_20) + 20.0) * 0.5
    return max(0.0, min(100.0, total))


def signal_from_total_score_macro_logic(total_0_100: float) -> str:
    if total_0_100 >= 85:
        return "STRONG BUY"
    if total_0_100 >= 70:
        return "BUY"
    if total_0_100 >= 55:
        return "HOLD"
    if total_0_100 >= 40:
        return "SELL"
    return "STRONG SELL"


def try_load_marketcap_map(path: Optional[str]) -> Dict[str, Tuple[Optional[float], Optional[int]]]:
    if path is None:
        for guess in ("market_watch_symbols_enriched_yahoo.xlsx", "market_watch_symbols_enriched_yahoo.csv"):
            if os.path.exists(guess):
                path = guess
                break

    if path is None or not os.path.exists(path):
        return {}

    cap_map: Dict[str, Tuple[Optional[float], Optional[int]]] = {}

    try:
        if path.lower().endswith(".xlsx"):
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            headers = {}
            for c in range(1, ws.max_column + 1):
                v = ws.cell(1, c).value
                if v:
                    headers[str(v).strip().lower()] = c

            def col(*names):
                for n in names:
                    if n in headers:
                        return headers[n]
                return None

            c_symbol = col("symbol", "yahoo_symbol", "ticker")
            c_cap = col("marketcap_usd", "marketcap", "market_cap")
            c_rank = col("pořadí", "poradi", "rank", "rankmarketcap")

            if not c_symbol:
                return {}

            for r in range(2, ws.max_row + 1):
                sym = ws.cell(r, c_symbol).value
                if not sym:
                    continue
                sym = str(sym).strip()
                cap = ws.cell(r, c_cap).value if c_cap else None
                rk = ws.cell(r, c_rank).value if c_rank else None
                try:
                    cap_f = float(cap) if cap is not None and cap != "" else None
                except Exception:
                    cap_f = None
                try:
                    rk_i = int(rk) if rk is not None and rk != "" else None
                except Exception:
                    rk_i = None
                cap_map[sym] = (cap_f, rk_i)

        else:
            import csv
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sym = (row.get("symbol") or row.get("yahoo_symbol") or row.get("ticker") or "").strip()
                    if not sym:
                        continue
                    cap = row.get("marketcap_usd") or row.get("marketcap") or ""
                    rk = row.get("poradi") or row.get("pořadí") or row.get("rank") or row.get("rankmarketcap") or ""
                    cap_f = None
                    rk_i = None
                    try:
                        cap_f = float(cap) if cap else None
                    except Exception:
                        cap_f = None
                    try:
                        rk_i = int(float(rk)) if rk else None
                    except Exception:
                        rk_i = None
                    cap_map[sym] = (cap_f, rk_i)

    except Exception:
        return {}

    return cap_map


def create_workbook_template() -> Workbook:
    wb = Workbook()

    ws = wb.active
    ws.title = "Signals"

    headers = [
        "Ticker",
        "MT5Symbol",
        "UpdatedAt",
        "MarketCapUSD",
        "RankMarketCap",
        "NewsWeighted48h",
        "NewsVolume48h",
        "NewsScore(0-50)",
        "TechScore(0-50)",
        "YahooScore(-20..20)",
        "TotalScore(0-100)",
        "Signal",
        "TechStatus",
        "YahooStatus",
        "Close",
        "MA20",
        "MA50",
        "RSI14",
        "YahooPrice",
        "YahooTarget",
        "YahooUpsidePct",
        "YahooRatingKey",
        "YahooRatingMean",
    ]

    ws.append(headers)
    for c in range(1, len(headers) + 1):
        ws.cell(1, c).font = Font(bold=True)
        ws.cell(1, c).alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"

    ws_src = wb.create_sheet("Sources")
    ws_src.append(["Source", "Type", "Paywall", "InfoLevel(1-5)", "RSS/Feed template (optional)", "Notes"])
    for c in range(1, 7):
        ws_src.cell(1, c).font = Font(bold=True)
    for s in SOURCES:
        ws_src.append([s["source"], s["type"], s["paywall"], s["info_level"], s["template"], s["notes"]])
    ws_src.freeze_panes = "A2"

    ws_art = wb.create_sheet("Articles")
    ws_art.append(["Ticker", "Source", "PublishedUTC", "Title", "Link", "Weight"])
    for c in range(1, 7):
        ws_art.cell(1, c).font = Font(bold=True)
    ws_art.freeze_panes = "A2"

    return wb


# =========================
# ✅ ADDED: Dashboard builder
# =========================
def build_dashboard(wb: Workbook):
    # vytvoř / vyčisti sheet
    if "Dashboard" in wb.sheetnames:
        ws = wb["Dashboard"]
        ws.delete_rows(1, ws.max_row)
    else:
        ws = wb.create_sheet("Dashboard")

    ws["A1"] = "GeneratedAt"
    ws["B1"] = now_local_naive().strftime("%Y-%m-%d %H:%M:%S")
    ws["A1"].font = Font(bold=True)

    # načti data ze Signals
    ws_sig = wb["Signals"]
    headers = [ws_sig.cell(1, c).value for c in range(1, ws_sig.max_column + 1)]
    idx = {str(h): i for i, h in enumerate(headers) if h}

    def get(row, name, default=None):
        col = idx.get(name)
        if col is None:
            return default
        return row[col]

    data = []
    for r in range(2, ws_sig.max_row + 1):
        row = [ws_sig.cell(r, c).value for c in range(1, ws_sig.max_column + 1)]
        ticker = get(row, "Ticker")
        mcap = get(row, "MarketCapUSD")
        if not ticker:
            continue
        data.append({
            "Ticker": ticker,
            "MarketCapUSD": mcap,
            "RankMarketCap": get(row, "RankMarketCap"),
            "TotalScore": get(row, "TotalScore(0-100)"),
            "Signal": get(row, "Signal"),
            "NewsScore": get(row, "NewsScore(0-50)"),
            "TechScore": get(row, "TechScore(0-50)"),
            "YahooScore": get(row, "YahooScore(-20..20)"),
        })

    def write_section(title, start_row, cols, rows):
        ws[f"A{start_row}"] = title
        ws[f"A{start_row}"].font = Font(bold=True)
        header_row = start_row + 1

        for i, h in enumerate(cols, 1):
            cell = ws.cell(header_row, i, h)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        for j, rr in enumerate(rows, 1):
            for i, h in enumerate(cols, 1):
                ws.cell(header_row + j, i, rr.get(h))

        return header_row + len(rows) + 2

    # Top 20 by TotalScore
    top_total = sorted(
        [d for d in data if d["TotalScore"] is not None],
        key=lambda x: float(x["TotalScore"]),
        reverse=True
    )[:20]
    top_total_rows = []
    for i, d in enumerate(top_total, 1):
        top_total_rows.append({
            "Rank": i,
            "Ticker": d["Ticker"],
            "TotalScore(0-100)": d["TotalScore"],
            "Signal": d["Signal"],
            "MarketCapUSD": d["MarketCapUSD"],
            "NewsScore(0-50)": d["NewsScore"],
            "TechScore(0-50)": d["TechScore"],
            "YahooScore(-20..20)": d["YahooScore"],
        })

    r = 3
    r = write_section(
        "Top 20 by TotalScore",
        r,
        ["Rank", "Ticker", "TotalScore(0-100)", "Signal", "MarketCapUSD", "NewsScore(0-50)", "TechScore(0-50)", "YahooScore(-20..20)"],
        top_total_rows
    )

    # Top 20 by MarketCap
    top_mcap = sorted(
        [d for d in data if d["MarketCapUSD"] is not None],
        key=lambda x: float(x["MarketCapUSD"]),
        reverse=True
    )[:20]
    top_mcap_rows = []
    for i, d in enumerate(top_mcap, 1):
        top_mcap_rows.append({
            "Rank": i,
            "Ticker": d["Ticker"],
            "MarketCapUSD": d["MarketCapUSD"],
            "RankMarketCap": d["RankMarketCap"],
            "TotalScore(0-100)": d["TotalScore"],
            "Signal": d["Signal"],
        })

    r = write_section(
        "Top 20 by MarketCap",
        r,
        ["Rank", "Ticker", "MarketCapUSD", "RankMarketCap", "TotalScore(0-100)", "Signal"],
        top_mcap_rows
    )

    # ✅ Bottom 20 by MarketCap
    bottom_mcap = sorted(
        [d for d in data if d["MarketCapUSD"] is not None],
        key=lambda x: float(x["MarketCapUSD"])
    )[:20]
    bottom_mcap_rows = []
    for i, d in enumerate(bottom_mcap, 1):
        bottom_mcap_rows.append({
            "Rank": i,
            "Ticker": d["Ticker"],
            "MarketCapUSD": d["MarketCapUSD"],
            "RankMarketCap": d["RankMarketCap"],
            "TotalScore(0-100)": d["TotalScore"],
            "Signal": d["Signal"],
        })

    write_section(
        "Bottom 20 by MarketCap",
        r,
        ["Rank", "Ticker", "MarketCapUSD", "RankMarketCap", "TotalScore(0-100)", "Signal"],
        bottom_mcap_rows
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default=".", help="Kam uložit výstupní Excel (default: aktuální složka)")
    parser.add_argument("--marketcap", default=None, help="Volitelný soubor s marketcap/rank (xlsx/csv)")
    args = parser.parse_args()

    env = load_env_from_code_env("code.env")
    if env:
        print(f"ENV: loaded {len(env)} vars from code.env")

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    cap_map = try_load_marketcap_map(args.marketcap)
    if cap_map:
        print(f"MarketCap: loaded {len(cap_map)} symbols from file")

    print("Step 1/4: MT5 watchlist symbols ...")
    mt5 = mt5_connect()
    symbols = mt5_visible_symbols(mt5)
    print(f"MT5 symbols (visible): {len(symbols)}")

    print("Step 2/4: Collect RSS news (supported sources only) ...")
    now_utc = dt.datetime.now(dt.timezone.utc)
    all_items: Dict[str, List[NewsItem]] = {}
    n = len(symbols)
    for i, sym in enumerate(symbols, 1):
        print_bar("RSS", i, n)
        try:
            items = fetch_rss_items_for_ticker(sym, max_per_source=10)
        except Exception:
            items = []
        all_items[sym] = items

    print("Step 3/4: Compute signals (news + tech + yahoo + marketcap/rank) ...")
    wb = create_workbook_template()
    ws = wb["Signals"]
    ws_art = wb["Articles"]

    for i, sym in enumerate(symbols, 1):
        print_bar("Signals", i, n)

        items = all_items.get(sym, []) or []
        for it in items[:50]:
            pub = it.published_utc.isoformat() if it.published_utc else ""
            ws_art.append([it.ticker, it.source, pub, it.title, it.link, it.weight])

        news_w48, news_v48 = news_metrics_48h(items, now_utc)
        news_score = news_score_0_50(news_w48, news_v48)

        tech_score, tech_details, tech_status = tech_score_from_mt5(mt5, sym)
        if tech_score is None:
            tech_score = 0.0

        yahoo_score, ydetails, ystatus = yahoo_details_and_score(sym)

        total = compute_total_score_macro_logic(news_score, tech_score, yahoo_score)
        signal = signal_from_total_score_macro_logic(total)

        cap, rank = cap_map.get(sym, (None, None))

        updated = now_local_naive().strftime("%Y-%m-%d %H:%M")

        ws.append([
            sym,
            sym,
            updated,
            cap,
            rank,
            round(news_w48, 3),
            int(news_v48),
            round(news_score, 1),
            round(tech_score, 1),
            round(yahoo_score, 1),
            round(total, 1),
            signal,
            tech_status,
            ystatus,
            tech_details.get("Close"),
            tech_details.get("MA20"),
            tech_details.get("MA50"),
            tech_details.get("RSI14"),
            ydetails.get("YahooPrice"),
            ydetails.get("YahooTarget"),
            ydetails.get("YahooUpsidePct"),
            ydetails.get("YahooRatingKey"),
            ydetails.get("YahooRatingMean"),
        ])

    print("\nStep 4/4: SAVE workbook ...")

    # =========================
    # ✅ ADDED: Build Dashboard
    # =========================
    build_dashboard(wb)

    ts = now_local_naive().strftime("%Y%m%d_%H%M%S")
    outname = f"market_checker_watchlist_{ts}.xlsx"
    outpath = os.path.join(outdir, outname)
    outpath = os.path.abspath(outpath)
    wb.save(outpath)

    print(f"OK: saved -> {outpath}")


if __name__ == "__main__":
    raise SystemExit(main())
