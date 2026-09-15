from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


PRICE_RETURN_METHOD = "price_return"
PRICE_BASIS = "split_adjusted_close_ex_dividend"
PRICE_METHOD_VERSION = "split_adjusted_price_v1"
YAHOO_PROVIDER = "yfinance"
YAHOO_INTERVAL = "1d"
YAHOO_ADJUSTMENT = "split_only"
CACHE_FORMAT_VERSION = "ohlc_cache_v2"


@dataclass(frozen=True, slots=True)
class PriceMethodology:
    return_method: str = PRICE_RETURN_METHOD
    price_basis: str = PRICE_BASIS
    version: str = PRICE_METHOD_VERSION
    provider: str = YAHOO_PROVIDER
    interval: str = YAHOO_INTERVAL
    adjustment: str = YAHOO_ADJUSTMENT
    dividends_included: bool = False
    split_adjusted: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "return_method": self.return_method,
            "price_basis": self.price_basis,
            "version": self.version,
            "provider": self.provider,
            "interval": self.interval,
            "adjustment": self.adjustment,
            "dividends_included": self.dividends_included,
            "split_adjusted": self.split_adjusted,
        }


PRIMARY_PRICE_METHODOLOGY = PriceMethodology()


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def normalize_split_adjusted_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a deterministic price-return frame adjusted for splits only.

    Yahoo ``auto_adjust=False`` data can carry corporate actions in ``Stock
    Splits`` and ``Dividends``. For a price-return target, historical OHLC before
    a split is divided by all later split ratios so a split cannot look like a
    crash. Dividends are deliberately *not* applied, which keeps this distinct
    from a total-return series.

    Frames without a ``Stock Splits`` column are treated as already expressed
    on a split-comparable basis. This is required for cached/provider fixtures
    and other canonical loaders; provenance/versioning still identifies the
    methodology used by the cache.
    """

    if not isinstance(frame, pd.DataFrame) or frame.empty or "Close" not in frame.columns:
        raise ValueError("price methodology requires a non-empty frame with Close")

    result = frame.copy().sort_index(kind="stable")
    for column in ("Open", "High", "Low", "Close"):
        if column in result.columns:
            result[column] = _numeric(result[column])

    close = result["Close"]
    finite_positive = close.map(
        lambda value: pd.notna(value) and math.isfinite(float(value)) and float(value) > 0.0
    )
    result = result.loc[finite_positive].copy()
    if result.empty:
        raise ValueError("price methodology requires at least one finite positive Close")

    if "Stock Splits" in result.columns:
        split = _numeric(result["Stock Splits"]).fillna(0.0)
        split_factor = split.map(
            lambda value: float(value)
            if math.isfinite(float(value)) and float(value) > 0.0
            else 1.0
        )
        reverse_product = split_factor.iloc[::-1].cumprod().iloc[::-1]
        historical_factor = reverse_product / split_factor
        historical_factor = historical_factor.replace(0.0, 1.0)
        for column in ("Open", "High", "Low", "Close"):
            if column in result.columns:
                result[column] = result[column] / historical_factor
        if "Volume" in result.columns:
            volume = _numeric(result["Volume"])
            result["Volume"] = volume * historical_factor

    result.attrs["return_method"] = PRICE_RETURN_METHOD
    result.attrs["price_basis"] = PRICE_BASIS
    result.attrs["price_method_version"] = PRICE_METHOD_VERSION
    result.attrs["dividends_included"] = False
    result.attrs["split_adjusted"] = True
    return result
