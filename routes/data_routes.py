"""Data fetching endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from api.serialize import ok
from routes.helpers import PERIOD_MAX, PERIOD_MIN, SYMBOL_RE
from services import models as M
from services import market
from services.providers import DataProvider, get_provider

router = APIRouter()

SymbolPath = Annotated[str, Path(pattern=SYMBOL_RE.pattern)]


@router.get("/watchlist/symbols", response_model=M.WatchlistSymbolsResponse, responses=ERROR_RESPONSES)
def get_watchlist_symbols():
    """Get all configured symbols grouped by market."""
    return reply(M.WatchlistSymbolsResponse, market.watchlist_symbols())


@router.get("/watchlist/fetch", responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def fetch_watchlist_data(markets: str = Query(default=""), provider: DataProvider = Depends(get_provider)):
    """Fetch data for all watchlist symbols. Returns just symbols that loaded successfully."""
    market_list = [m.strip() for m in markets.split(",") if m.strip()] or None
    return ok(market.watchlist_fetch(provider, market_list).dump())


@router.get("/{symbol}", response_model=M.OhlcvResponse, responses=ERROR_RESPONSES)
def get_ohlcv(symbol: SymbolPath, period_days: int = Query(default=730, ge=PERIOD_MIN, le=PERIOD_MAX),
              provider: DataProvider = Depends(get_provider)):
    """Fetch OHLCV data for a symbol (404 when the data layer has nothing valid for it)."""
    return reply(M.OhlcvResponse, market.ohlcv(provider, symbol, period_days))
