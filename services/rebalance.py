"""Inputs for the advisory rebalance proposal (prices + monthly closes), fetched through the provider."""
from __future__ import annotations

from typing import Optional

import pandas as pd

import allocation
from services.providers import DataProvider, default_provider


def rebalance_inputs(provider: Optional[DataProvider] = None):
    """(prices, monthly_frame, failed_symbols) for allocation.propose_rebalance."""
    provider = provider or default_provider()
    symbols = sorted(set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE))
    prices: dict[str, float] = {}
    monthly: dict[str, pd.Series] = {}
    failed = []
    for s in symbols:
        try:
            df = provider.ohlcv(s, 500)
        except Exception:
            df = None
        if df is None or df.empty:
            failed.append(s)
            continue
        prices[s] = float(df["Close"].iloc[-1])
        if s in allocation.TREND_UNIVERSE:
            close = df["Close"]
            if getattr(close.index, "tz", None) is not None:
                close = close.tz_localize(None)
            monthly[s] = close.resample("ME").last().dropna().tail(14)  # 13 completed months + current
    return prices, pd.DataFrame(monthly), failed
