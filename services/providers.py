"""DataProvider protocol and the default provider over data_fetcher (injectable for tests)."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import pandas as pd

import data_fetcher


@runtime_checkable
class DataProvider(Protocol):
    def ohlcv(self, symbol: str, period_days: Optional[int] = None) -> pd.DataFrame:
        """OHLCV frame for `symbol`; an empty frame when no valid data is available."""

    def watchlist(self, markets: Optional[list] = None) -> dict:
        """{symbol: frame} for the configured watchlist (symbols without data are left out)."""

    def pair(self, sym_a: str, sym_b: str, period_days: Optional[int] = None, min_aligned: int = 60):
        """Aligned (df_a, df_b) from one source for both legs, or None."""

    def bars(self, symbol: str, period_days: Optional[int] = None):
        """Validated bars with metadata (``.df``, ``.source``, ``.adjusted``, ``.quality`` ...).
        Raises DataQualityError / DataUnavailableError."""


class DefaultProvider:
    """Wraps data_fetcher. Looks the functions up at call time, so patching data_fetcher.* works."""

    def ohlcv(self, symbol, period_days=None):
        if period_days is None:
            return data_fetcher.fetch_ohlcv(symbol)
        return data_fetcher.fetch_ohlcv(symbol, period_days=period_days)

    def watchlist(self, markets=None):
        return data_fetcher.fetch_watchlist(markets)

    def pair(self, sym_a, sym_b, period_days=None, min_aligned=60):
        if period_days is None:
            return data_fetcher.fetch_pair(sym_a, sym_b, min_aligned=min_aligned)
        return data_fetcher.fetch_pair(sym_a, sym_b, period_days=period_days, min_aligned=min_aligned)

    def bars(self, symbol, period_days=None):
        if period_days is None:
            return data_fetcher.fetch_bars(symbol)
        return data_fetcher.fetch_bars(symbol, period_days=period_days)


_DEFAULT = DefaultProvider()


def default_provider() -> DataProvider:
    return _DEFAULT


def get_provider() -> DataProvider:
    """FastAPI dependency (override with app.dependency_overrides in tests)."""
    return _DEFAULT
