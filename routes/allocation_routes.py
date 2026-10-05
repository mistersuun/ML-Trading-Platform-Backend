"""Advisory allocation proposal (mount at /api/allocation). Proposals only: no order is ever placed (D1)."""
from fastapi import APIRouter, Depends, Query

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import reply
from services import allocation_view
from services import models as M
from services.providers import DataProvider, get_provider

router = APIRouter()


@router.get("/proposal", response_model=M.ProposalResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def get_proposal(contribution: float = Query(0.0, ge=0, le=1e9, description="dollars to add on top of cash"),
                 provider: DataProvider = Depends(get_provider)):
    """Whole-share rebalance proposal toward the 90% core + 10% trend targets, with per-ETF drift against its band,
    group mix and the trend sleeve detail. Advisory only (404 `no_holdings` when the CSV is missing)."""
    return reply(M.ProposalResponse, allocation_view.proposal(provider, contribution))
