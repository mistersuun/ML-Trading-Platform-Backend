"""Backtest services: single backtest, walk-forward, and frame-level helpers for the dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import backtester
import config
from patterns import PATTERN_REGISTRY
from services.common import clean, cut_holdout, equity_points, holdout_info, require_data
from services.models import BacktestResponse, WalkForwardResponse
from services.providers import DataProvider

BacktestResult = backtester.BacktestResult


@dataclass
class BacktestRun:
    signals: pd.DataFrame
    result: "backtester.BacktestResult"


def run_on_frame(df: pd.DataFrame, symbol: str, pattern: str, stop_loss: Optional[float] = None,
                 take_profit: Optional[float] = None) -> BacktestRun:
    """Pattern signals + classic backtest of `df` (execution convention: config.EXECUTION_MODE)."""
    signals = PATTERN_REGISTRY[pattern](df)
    kw = {}
    if stop_loss is not None:
        kw["stop_loss"] = stop_loss
    if take_profit is not None:
        kw["take_profit"] = take_profit
    return BacktestRun(signals, backtester.classic_backtest(signals, symbol, pattern, **kw))


def backtest_signals(signals_df: pd.DataFrame, symbol: str, name: str):
    """Backtest an externally produced signal frame (e.g. the ML predictions) -> BacktestResult."""
    return backtester.classic_backtest(signals_df, symbol, name)


def compare_patterns(df: pd.DataFrame, symbol: str) -> list:
    """BacktestResult of every registered pattern that traded on `df` (dashboard comparison table)."""
    out = []
    for pn in PATTERN_REGISTRY:
        try:
            r = run_on_frame(df, symbol, pn).result
            if r.total_trades > 0:
                out.append(r)
        except Exception:
            pass
    return out


def walk_forward_results(df: pd.DataFrame, symbol: str, pattern: str, n_splits: int = 4) -> list:
    return backtester.walk_forward_validate(df, symbol, PATTERN_REGISTRY[pattern], pattern, n_splits=n_splits)


def run(provider: DataProvider, symbol: str, pattern: str, period_days: int = 730,
        stop_loss: float = config.STOP_LOSS_PCT, take_profit: float = config.TAKE_PROFIT_PCT,
        include_holdout: bool = True) -> BacktestResponse:
    """Backtest a symbol + pattern combination; the hold-out share of the window is labelled."""
    df = require_data(provider.ohlcv(symbol, period_days), symbol)
    holdout = holdout_info(df)
    if not include_holdout:
        df = require_data(cut_holdout(df), symbol)

    result = run_on_frame(df, symbol, pattern, stop_loss, take_profit).result
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

    return BacktestResponse.model_validate(clean({
        "symbol": symbol, "pattern": pattern,
        "metrics": result.metrics_payload(),         # numeric fractions; non-finite -> null
        "metrics_display": result.summary(),         # legacy formatted strings, kept for one release
        "equity_curve": equity_points(result.equity_curve),
        "trades": trades,
        "is_valid": result.is_valid,                 # in-sample display heuristic, NOT an eligibility gate
        "validation_status": "unvalidated",
        "holdout": holdout,
    }))


def walk_forward(provider: DataProvider, symbol: str, pattern: str, n_splits: int = 4,
                 period_days: int = config.LOOKBACK_DAYS) -> WalkForwardResponse:
    df = require_data(provider.ohlcv(symbol, period_days), symbol)
    results = walk_forward_results(df, symbol, pattern, n_splits)
    folds = [{"fold": i + 1, "metrics": r.metrics_payload(), "metrics_display": r.summary()}
             for i, r in enumerate(results)]
    return WalkForwardResponse.model_validate(clean({
        "symbol": symbol, "pattern": pattern, "n_folds": len(folds), "folds": folds,
        "fold_scheme": "contiguous_non_overlapping_test_windows"}))
