"""Stress testing endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from data_fetcher import fetch_ohlcv
from patterns import PATTERN_REGISTRY
from stress_test import full_stress_test, detect_regimes, monte_carlo_analysis, parameter_sensitivity
from backtester import classic_backtest
from routes.helpers import json_response

router = APIRouter()


class StressRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = 730


@router.post("/full")
def run_stress_test(req: StressRequest):
    """Run the complete stress test suite."""
    if req.pattern_name not in PATTERN_REGISTRY:
        return {"error": f"Unknown pattern: {req.pattern_name}"}

    df = fetch_ohlcv(req.symbol, period_days=req.period_days)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    func = PATTERN_REGISTRY[req.pattern_name]
    report = full_stress_test(df, req.symbol, func, req.pattern_name)
    return json_response(report)


@router.post("/regimes")
def get_regimes(req: StressRequest):
    """Detect market regimes for a symbol."""
    df = fetch_ohlcv(req.symbol, period_days=req.period_days)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    regimes = detect_regimes(df)
    summary = {}
    for col in regimes.columns:
        counts = regimes[col].value_counts().to_dict()
        summary[col] = {str(k): int(v) for k, v in counts.items()}

    return json_response({"symbol": req.symbol, "regimes": summary})


@router.post("/sensitivity")
def get_sensitivity(req: StressRequest):
    """Run parameter sensitivity analysis."""
    if req.pattern_name not in PATTERN_REGISTRY:
        return {"error": f"Unknown pattern: {req.pattern_name}"}

    df = fetch_ohlcv(req.symbol, period_days=req.period_days)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    result = parameter_sensitivity(df, req.symbol, req.pattern_name)
    if result.empty:
        return {"error": "No sensitivity data"}

    return json_response({"data": result.to_dict("records")})
