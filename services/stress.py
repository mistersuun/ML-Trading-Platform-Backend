"""Stress services: full report, regimes, parameter sensitivity."""
from __future__ import annotations

import pandas as pd

import config
from api.errors import InsufficientHistory
from patterns import PATTERN_REGISTRY
from services.common import PERIOD_MAX, clean, require_data
from services.models import RegimesResponse, SensitivityResponse, StressReport
from services.providers import DataProvider
from stress_test import detect_regimes, full_stress_test, parameter_sensitivity


def research_days(period_days: int) -> int:
    """Stress/sensitivity need pre-hold-out history: never fetch less than RESEARCH_LOOKBACK_DAYS."""
    return min(PERIOD_MAX, max(period_days, config.RESEARCH_LOOKBACK_DAYS))


def stress_report(df: pd.DataFrame, symbol: str, pattern: str) -> dict:
    """Raw full_stress_test report for a frame (CLI --stress, scan --stress, dashboard)."""
    return full_stress_test(df, symbol, PATTERN_REGISTRY[pattern], pattern)


def full(provider: DataProvider, symbol: str, pattern: str, period_days: int) -> StressReport:
    df = require_data(provider.ohlcv(symbol, research_days(period_days)), symbol)
    return StressReport.model_validate(clean(stress_report(df, symbol, pattern)))


def regimes(provider: DataProvider, symbol: str, period_days: int) -> RegimesResponse:
    df = require_data(provider.ohlcv(symbol, period_days), symbol)
    reg = detect_regimes(df)
    summary = {col: {str(k): int(v) for k, v in reg[col].value_counts().to_dict().items()} for col in reg.columns}
    return RegimesResponse(symbol=symbol, regimes=summary)


def sensitivity(provider: DataProvider, symbol: str, pattern: str, period_days: int) -> SensitivityResponse:
    """Parameter sensitivity (bars before HOLDOUT_START only)."""
    df = require_data(provider.ohlcv(symbol, research_days(period_days)), symbol)
    result = parameter_sensitivity(df, symbol, pattern)
    if result.empty:
        raise InsufficientHistory("No sensitivity data: too few bars before the hold-out start",
                                  {"reason": result.attrs.get("reason"), "holdout_start": config.HOLDOUT_START})
    return SensitivityResponse.model_validate(clean({"data": result.to_dict("records"), "meta": dict(result.attrs)}))
