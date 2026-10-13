"""Market-data read services (OHLCV records, watchlist fetch)."""
from __future__ import annotations

from typing import Optional

import config
from services.common import clean, require_data
from services.models import OhlcvResponse, WatchlistFetchResponse
from services.providers import DataProvider


def watchlist_symbols() -> dict:
    return config.WATCHLIST


def watchlist_fetch(provider: DataProvider, markets: Optional[list] = None) -> WatchlistFetchResponse:
    """Fetch the watchlist; reports just the symbols that loaded."""
    data = provider.watchlist(markets or None)
    return WatchlistFetchResponse(count=len(data), symbols=list(data.keys()))


def ohlcv(provider: DataProvider, symbol: str, period_days: int) -> OhlcvResponse:
    """OHLCV records for a symbol (NoData -> 404 when the data layer has nothing valid for it)."""
    df = require_data(provider.ohlcv(symbol, period_days), symbol)
    records = [{
        "date": idx.isoformat(),
        "open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"]),
        "close": float(row["Close"]), "volume": int(row["Volume"]),
    } for idx, row in df.iterrows()]
    return OhlcvResponse.model_validate(clean({"symbol": symbol, "count": len(records), "data": records}))
