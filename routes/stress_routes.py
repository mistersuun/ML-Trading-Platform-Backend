"""Stress testing endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

import config
from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from api.serialize import ok
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_pattern, check_symbol
from services import models as M
from services import stress
from services.providers import DataProvider, get_provider

router = APIRouter()


class StressRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = Field(config.RESEARCH_LOOKBACK_DAYS, ge=PERIOD_MIN, le=PERIOD_MAX)

    _sym = field_validator("symbol")(check_symbol)
    _pat = field_validator("pattern_name")(check_pattern)


class RegimeRequest(BaseModel):
    symbol: str
    period_days: int = Field(730, ge=PERIOD_MIN, le=PERIOD_MAX)

    _sym = field_validator("symbol")(check_symbol)


@router.post("/full", response_model=M.StressReport, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def run_stress_test(req: StressRequest, provider: DataProvider = Depends(get_provider)):
    """Run the complete stress test suite."""
    return reply(M.StressReport, stress.full(provider, req.symbol, req.pattern_name, req.period_days))


@router.post("/regimes")
def get_regimes(req: RegimeRequest, provider: DataProvider = Depends(get_provider)):
    """Detect market regimes for a symbol."""
    return ok(stress.regimes(provider, req.symbol, req.period_days).dump())


@router.post("/sensitivity", responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def get_sensitivity(req: StressRequest, provider: DataProvider = Depends(get_provider)):
    """Run parameter sensitivity analysis (bars before HOLDOUT_START only)."""
    return ok(stress.sensitivity(provider, req.symbol, req.pattern_name, req.period_days).dump())
