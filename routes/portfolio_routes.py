"""Owner portfolio overview (mount at /api/portfolio)."""
from typing import Literal

from fastapi import APIRouter, Depends, Query

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import reply
from services import models as M
from services import portfolio
from services.providers import DataProvider, get_provider

router = APIRouter()


@router.get("/overview", response_model=M.OverviewResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def get_overview(range: Literal["1M", "3M", "YTD", "1Y", "ALL"] = Query("1Y"),
                 provider: DataProvider = Depends(get_provider)):
    """Value, day change, accounts, growth of 100 vs the monthly-rebalanced 60/40, drawdown and allocation by
    asset group, from the owner's holdings CSV (404 `no_holdings` when it is missing). Fetches prices, so it
    shares the heavy-endpoint cap (429 when busy)."""
    return reply(M.OverviewResponse, portfolio.overview(provider, range))
