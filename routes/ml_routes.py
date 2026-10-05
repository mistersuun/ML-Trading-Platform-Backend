"""ML pattern detection endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_symbol
from services import models as M
from services import ml
from services.providers import DataProvider, get_provider

router = APIRouter()


class MLRequest(BaseModel):
    symbol: str
    period_days: int = Field(730, ge=PERIOD_MIN, le=PERIOD_MAX)
    include_holdout: bool = True      # False: only bars before config.HOLDOUT_START

    _sym = field_validator("symbol")(check_symbol)


@router.post("/predict", response_model=M.MLPredictResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def ml_predict(req: MLRequest, provider: DataProvider = Depends(get_provider)):
    """Walk-forward ML predictions; the backtest and signals cover ONLY out-of-sample bars."""
    return reply(M.MLPredictResponse, ml.predict(provider, req.symbol, req.period_days, req.include_holdout))
