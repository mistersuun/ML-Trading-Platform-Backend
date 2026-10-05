"""Backtesting endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

import config
from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_pattern, check_symbol
from services import models as M
from services import backtest
from services.providers import DataProvider, get_provider

router = APIRouter()


class BacktestRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = Field(730, ge=PERIOD_MIN, le=PERIOD_MAX)
    stop_loss: float = Field(config.STOP_LOSS_PCT, gt=0, le=0.5)
    take_profit: float = Field(config.TAKE_PROFIT_PCT, gt=0, le=1.0)
    include_holdout: bool = True      # False: only bars before config.HOLDOUT_START

    _sym = field_validator("symbol")(check_symbol)
    _pat = field_validator("pattern_name")(check_pattern)


@router.post("/run", response_model=M.BacktestResponse, responses=ERROR_RESPONSES)
def run_backtest(req: BacktestRequest, provider: DataProvider = Depends(get_provider)):
    """Run a backtest on a symbol + pattern combination (execution convention: config.EXECUTION_MODE)."""
    return reply(M.BacktestResponse, backtest.run(provider, req.symbol, req.pattern_name, req.period_days,
                                                  req.stop_loss, req.take_profit, req.include_holdout))


class WalkForwardRequest(BaseModel):
    symbol: str
    pattern_name: str
    n_splits: int = Field(4, ge=2, le=12)
    period_days: int = Field(config.LOOKBACK_DAYS, ge=PERIOD_MIN, le=PERIOD_MAX)

    _sym = field_validator("symbol")(check_symbol)
    _pat = field_validator("pattern_name")(check_pattern)


@router.post("/walk-forward", response_model=M.WalkForwardResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def run_walk_forward(req: WalkForwardRequest, provider: DataProvider = Depends(get_provider)):
    """Run walk-forward validation."""
    return reply(M.WalkForwardResponse,
                 backtest.walk_forward(provider, req.symbol, req.pattern_name, req.n_splits, req.period_days))
