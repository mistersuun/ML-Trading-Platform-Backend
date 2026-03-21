"""Data fetching endpoints."""

from fastapi import APIRouter, Query
from data_fetcher import fetch_ohlcv, fetch_watchlist
import config

router = APIRouter()


@router.get("/watchlist/symbols")
def get_watchlist_symbols():
    """Get all configured symbols grouped by market."""
    return config.WATCHLIST


@router.get("/watchlist/fetch")
def fetch_watchlist_data(markets: str = Query(default="")):
    """Fetch data for all watchlist symbols. Returns just symbols that loaded successfully."""
    market_list = [m.strip() for m in markets.split(",") if m.strip()] or None
    data = fetch_watchlist(market_list)
    return {
        "count": len(data),
        "symbols": list(data.keys()),
    }


@router.get("/{symbol}")
def get_ohlcv(symbol: str, period_days: int = Query(default=730)):
    """Fetch OHLCV data for a symbol."""
    df = fetch_ohlcv(symbol, period_days=period_days)
    if df.empty:
        return {"error": f"No data for {symbol}", "data": []}

    records = []
    for idx, row in df.iterrows():
        records.append({
            "date": idx.isoformat(),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row["Volume"]),
        })

    return {"symbol": symbol, "count": len(records), "data": records}
