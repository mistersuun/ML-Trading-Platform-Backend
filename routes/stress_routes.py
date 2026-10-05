"""Stress testing endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

import config
from api.errors import InsufficientHistory
from api.serialize import ok
from data_fetcher import fetch_ohlcv
from patterns import PATTERN_REGISTRY
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_pattern, check_symbol, require_data
from stress_test import detect_regimes, full_stress_test, parameter_sensitivity

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


def _research_days(req: StressRequest) -> int:
    """Stress/sensitivity need pre-hold-out history: never fetch less than RESEARCH_LOOKBACK_DAYS."""
    return min(PERIOD_MAX, max(req.period_days, config.RESEARCH_LOOKBACK_DAYS))


@router.post("/full")
def run_stress_test(req: StressRequest):
    """Run the complete stress test suite."""
    df = require_data(fetch_ohlcv(req.symbol, period_days=_research_days(req)), req.symbol)
    report = full_stress_test(df, req.symbol, PATTERN_REGISTRY[req.pattern_name], req.pattern_name)
    return ok(report)


@router.post("/regimes")
def get_regimes(req: RegimeRequest):
    """Detect market regimes for a symbol."""
    df = require_data(fetch_ohlcv(req.symbol, period_days=req.period_days), req.symbol)
    regimes = detect_regimes(df)
    summary = {col: {str(k): int(v) for k, v in regimes[col].value_counts().to_dict().items()}
               for col in regimes.columns}
    return ok({"symbol": req.symbol, "regimes": summary})


@router.post("/sensitivity")
def get_sensitivity(req: StressRequest):
    """Run parameter sensitivity analysis (bars before HOLDOUT_START only)."""
    df = require_data(fetch_ohlcv(req.symbol, period_days=_research_days(req)), req.symbol)
    result = parameter_sensitivity(df, req.symbol, req.pattern_name)
    if result.empty:
        raise InsufficientHistory("No sensitivity data: too few bars before the hold-out start",
                                  {"reason": result.attrs.get("reason"), "holdout_start": config.HOLDOUT_START})
    return ok({"data": result.to_dict("records"), "meta": dict(result.attrs)})
