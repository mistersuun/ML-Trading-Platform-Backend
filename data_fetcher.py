"""Compatibility shim over the ``data`` package (WS2.1).

Old callers keep their signatures and get DataFrames; use ``fetch_bars`` for the full ``Bars``
(source, adjusted, calendar, fetched_at, quality). Alpha Vantage no longer exists as a source.
On any failure the DataFrame helpers log a WARNING and return an empty frame / None, but data
that failed validation is never returned.
"""

import logging
from datetime import datetime, timezone  # noqa: F401  (datetime is the clock the shim reads)
from typing import Optional

import pandas as pd
import requests  # noqa: F401  (re-exported for tests that patch data_fetcher.requests)

import config
from data import adapters as _adapters
from data.adapters import Bars, fetch_pair_bars, pinned_source, reset_pins  # noqa: F401
from data.validate import DataQualityError, DataUnavailableError

logger = logging.getLogger(__name__)


def fetch_bars(
    symbol: str,
    period_days: int = config.LOOKBACK_DAYS,
    interval: str = "1d",
    end_date: Optional[datetime] = None,
    sources: Optional[list] = None,
) -> Bars:
    """Validated bars with metadata. Raises DataQualityError / DataUnavailableError."""
    return _adapters.fetch_bars(symbol, period_days, interval, end_date, sources, now=datetime.now(timezone.utc))


def fetch_ohlcv(
    symbol: str,
    period_days: int = config.LOOKBACK_DAYS,
    interval: str = "1d",
    end_date: Optional[datetime] = None,
    sources: Optional[list] = None,
) -> pd.DataFrame:
    """OHLCV DataFrame (``Bars.df``); empty DataFrame when no valid data is available."""
    try:
        return fetch_bars(symbol, period_days, interval, end_date, sources).df
    except (DataQualityError, DataUnavailableError) as e:
        logger.warning("No usable data for %s: %s", symbol, e)
        return pd.DataFrame()


def fetch_watchlist(markets: Optional[list] = None, interval: str = "1d") -> dict:
    """Fetch all symbols in the watchlist."""
    if markets is None:
        markets = list(config.WATCHLIST.keys())
    symbols = []
    for m in markets:
        symbols.extend(config.WATCHLIST.get(m, []))
    results = {}
    for sym in symbols:
        df = fetch_ohlcv(sym, interval=interval)
        if not df.empty:
            results[sym] = df
    logger.info(f"Fetched {len(results)}/{len(symbols)} symbols")
    return results


def fetch_pair(sym_a: str, sym_b: str, period_days: int = config.PAIRS_LOOKBACK,
               min_aligned: int = 60) -> Optional[tuple]:
    """Aligned (df_a, df_b) from one source for both legs, or None. Pairs code should pass
    ``min_aligned=config.PAIRS_MIN_ALIGNED_BARS`` for research use."""
    try:
        ba, bb = fetch_pair_bars(sym_a, sym_b, period_days, now=datetime.now(timezone.utc))
    except (DataQualityError, DataUnavailableError) as e:
        logger.warning("No usable pair data for %s/%s: %s", sym_a, sym_b, e)
        return None
    if len(ba.df) < min_aligned:
        return None
    return ba.df, bb.df
