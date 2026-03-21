"""Backtesting endpoints."""

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from data_fetcher import fetch_ohlcv
from patterns import PATTERN_REGISTRY
from backtester import classic_backtest, walk_forward_validate
from routes.helpers import json_response
import config

router = APIRouter()


class BacktestRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = 730
    stop_loss: float = config.STOP_LOSS_PCT
    take_profit: float = config.TAKE_PROFIT_PCT


@router.post("/run")
def run_backtest(req: BacktestRequest):
    """Run a backtest on a symbol + pattern combination."""
    if req.pattern_name not in PATTERN_REGISTRY:
        return {"error": f"Unknown pattern: {req.pattern_name}"}

    df = fetch_ohlcv(req.symbol, period_days=req.period_days)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    func = PATTERN_REGISTRY[req.pattern_name]
    signals = func(df)
    result = classic_backtest(signals, req.symbol, req.pattern_name,
                              stop_loss=req.stop_loss, take_profit=req.take_profit)

    # Serialize equity curve
    equity_data = []
    if result.equity_curve is not None and not result.equity_curve.empty:
        for idx, val in result.equity_curve.items():
            v = float(val)
            if not np.isnan(v):
                equity_data.append({"date": idx.isoformat(), "value": round(v, 2)})

    # Serialize trades
    trades = []
    for t in result.trades:
        trades.append({
            "entry_date": t.entry_date.isoformat(),
            "exit_date": t.exit_date.isoformat() if t.exit_date else None,
            "direction": "LONG" if t.direction == 1 else "SHORT",
            "entry_price": round(t.entry_price, 4),
            "exit_price": round(t.exit_price, 4),
            "pnl_pct": round(t.pnl_pct, 4),
            "exit_reason": t.exit_reason,
            "bars_held": t.bars_held,
        })

    return json_response({
        "symbol": req.symbol,
        "pattern": req.pattern_name,
        "metrics": result.summary(),
        "equity_curve": equity_data,
        "trades": trades,
        "is_valid": result.is_valid,
    })


class WalkForwardRequest(BaseModel):
    symbol: str
    pattern_name: str
    n_splits: int = 4


@router.post("/walk-forward")
def run_walk_forward(req: WalkForwardRequest):
    """Run walk-forward validation."""
    if req.pattern_name not in PATTERN_REGISTRY:
        return {"error": f"Unknown pattern: {req.pattern_name}"}

    df = fetch_ohlcv(req.symbol)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    func = PATTERN_REGISTRY[req.pattern_name]
    results = walk_forward_validate(df, req.symbol, func, req.pattern_name, n_splits=req.n_splits)

    folds = []
    for i, r in enumerate(results):
        folds.append({
            "fold": i + 1,
            "metrics": r.summary(),
        })

    return json_response({
        "symbol": req.symbol,
        "pattern": req.pattern_name,
        "n_folds": len(folds),
        "folds": folds,
    })
