"""Data fetching endpoints."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

import config
from api.serialize import ok
from data_fetcher import fetch_ohlcv, fetch_watchlist
from routes.helpers import PERIOD_MAX, PERIOD_MIN, SYMBOL_RE, require_data

router = APIRouter()

SymbolPath = Annotated[str, Path(pattern=SYMBOL_RE.pattern)]


@router.get("/watchlist/symbols")
def get_watchlist_symbols():
    """Get all configured symbols grouped by market."""
    return ok(config.WATCHLIST)


@router.get("/watchlist/fetch")
def fetch_watchlist_data(markets: str = Query(default="")):
    """Fetch data for all watchlist symbols. Returns just symbols that loaded successfully."""
    market_list = [m.strip() for m in markets.split(",") if m.strip()] or None
    data = fetch_watchlist(market_list)
    return ok({"count": len(data), "symbols": list(data.keys())})


@router.get("/{symbol}")
def get_ohlcv(symbol: SymbolPath, period_days: int = Query(default=730, ge=PERIOD_MIN, le=PERIOD_MAX)):
    """Fetch OHLCV data for a symbol (404 when the data layer has nothing valid for it)."""
    df = require_data(fetch_ohlcv(symbol, period_days=period_days), symbol)
    records = [{
        "date": idx.isoformat(),
        "open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"]),
        "close": float(row["Close"]), "volume": int(row["Volume"]),
    } for idx, row in df.iterrows()]
    return ok({"symbol": symbol, "count": len(records), "data": records})
