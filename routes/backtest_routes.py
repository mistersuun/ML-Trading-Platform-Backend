"""Backtesting endpoints."""

import pandas as pd
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter

import config
from api.serialize import ok
from backtester import classic_backtest, walk_forward_validate
from data_fetcher import fetch_ohlcv
from patterns import PATTERN_REGISTRY
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_pattern, check_symbol, cut_holdout, finite, holdout_info, metrics_payload, require_data

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


@router.post("/run")
def run_backtest(req: BacktestRequest):
    """Run a backtest on a symbol + pattern combination (execution convention: config.EXECUTION_MODE)."""
    df = require_data(fetch_ohlcv(req.symbol, period_days=req.period_days), req.symbol)
    holdout = holdout_info(df)
    if not req.include_holdout:
        df = require_data(cut_holdout(df), req.symbol)

    signals = PATTERN_REGISTRY[req.pattern_name](df)
    result = classic_backtest(signals, req.symbol, req.pattern_name,
                              stop_loss=req.stop_loss, take_profit=req.take_profit)

    equity_data = []
    if result.equity_curve is not None and not result.equity_curve.empty:
        for idx, val in result.equity_curve.items():
            v = finite(val)
            if v is not None:
                equity_data.append({"date": idx.isoformat(), "value": round(v, 2)})  # dollars

    trades = [{
        "entry_date": t.entry_date.isoformat(),
        "exit_date": t.exit_date.isoformat() if t.exit_date else None,
        "direction": "LONG" if t.direction == 1 else "SHORT",
        "entry_price": round(t.entry_price, 4),
        "exit_price": round(t.exit_price, 4),
        "pnl_pct": t.pnl_pct,                      # fraction, net of costs, unrounded
        "exit_reason": t.exit_reason,
        "bars_held": t.bars_held,
        "in_holdout": t.entry_date >= pd.Timestamp(config.HOLDOUT_START, tz=t.entry_date.tz),
    } for t in result.trades]

    return ok({
        "symbol": req.symbol,
        "pattern": req.pattern_name,
        "metrics": metrics_payload(result),          # numeric fractions; non-finite -> null
        "metrics_display": result.summary(),         # legacy formatted strings, kept for one release
        "equity_curve": equity_data,
        "trades": trades,
        "is_valid": result.is_valid,                 # in-sample display heuristic, NOT an eligibility gate
        "validation_status": "unvalidated",
        "holdout": holdout,                          # label: part of the window is the fixed hold-out
    })


class WalkForwardRequest(BaseModel):
    symbol: str
    pattern_name: str
    n_splits: int = Field(4, ge=2, le=12)
    period_days: int = Field(config.LOOKBACK_DAYS, ge=PERIOD_MIN, le=PERIOD_MAX)

    _sym = field_validator("symbol")(check_symbol)
    _pat = field_validator("pattern_name")(check_pattern)


@router.post("/walk-forward")
def run_walk_forward(req: WalkForwardRequest):
    """Run walk-forward validation."""
    df = require_data(fetch_ohlcv(req.symbol, period_days=req.period_days), req.symbol)

    results = walk_forward_validate(df, req.symbol, PATTERN_REGISTRY[req.pattern_name], req.pattern_name,
                                    n_splits=req.n_splits)
    folds = [{"fold": i + 1, "metrics": metrics_payload(r), "metrics_display": r.summary()}
             for i, r in enumerate(results)]
    return ok({"symbol": req.symbol, "pattern": req.pattern_name, "n_folds": len(folds), "folds": folds,
               "fold_scheme": "contiguous_non_overlapping_test_windows"})
