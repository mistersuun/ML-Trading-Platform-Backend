"""One scanner candidate in detail (mount at /api/scanner)."""
from fastapi import APIRouter, Depends, Query

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ApiError, reply
from routes.helpers import check_pattern, check_symbol
from services import candidate
from services import models as M
from services.providers import DataProvider, get_provider

router = APIRouter()


@router.get("/candidate", response_model=M.CandidateResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def get_candidate(symbol: str = Query(..., min_length=1, max_length=20), pattern: str = Query(...),
                  provider: DataProvider = Depends(get_provider)):
    """Walk-forward, hold-out, null and cost-stress validation of one (symbol, pattern) executable candidate:
    OOS vs in-sample curves, trade returns, each gate measured against its bar, and the chart's bars and
    trades. Heavy (shares the cap: 429 when busy)."""
    try:
        sym, pat = check_symbol(symbol), check_pattern(pattern)
    except ValueError as e:
        raise ApiError(str(e), {"symbol": symbol, "pattern": pattern}, status_code=422, code="validation_error")
    return reply(M.CandidateResponse, candidate.candidate(provider, sym, pat))
