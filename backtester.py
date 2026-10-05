"""Backtester compatibility wrapper (WS2.2).

The simulation lives in ``engine.py`` and the metrics in ``metrics.py``. This module keeps the
Phase 1 public surface (``classic_backtest``, ``BacktestResult``, ``Trade``,
``walk_forward_validate``) working for the API, CLI, Streamlit and stress code. The vectorbt /
backtesting.py overlay was removed: it ran on a different timing/cost model and silently
replaced the Sortino and Calmar values.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Callable

import numpy as np
import pandas as pd

import config
import engine
import instruments
import metrics
from engine import Trade  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

PROFIT_FACTOR_CAP = 10.0


@dataclass
class BacktestResult:
    symbol: str
    pattern_name: str
    trades: list[Trade] = field(default_factory=list)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0  # always finite: when there are no losses it equals PROFIT_FACTOR_CAP
    profit_factor_undefined: bool = False  # True when there were no losses (ratio is infinite)
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0   # rf=0, daily mark-to-market returns of the actual position size
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    avg_trade_duration_days: float = 0.0
    expectancy: float = 0.0     # mean net pnl_pct per trade (notional scale)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    # Phase 2 additions
    execution: str = ""
    cagr: Optional[float] = None
    psr: Optional[float] = None
    max_drawdown_duration_bars: int = 0
    expectancy_equity: Optional[float] = None
    win_rate_ci: Optional[tuple] = None
    positions: Optional[pd.Series] = None
    bars_per_year: int = 252    # instrument registry (365 crypto, 260 fx, 252 otherwise)

    @property
    def profit_factor_capped(self) -> float:
        return min(self.profit_factor, PROFIT_FACTOR_CAP)

    @property
    def is_valid(self) -> bool:
        """DISPLAY HEURISTIC ONLY. Order eligibility is decided by validation (WS2.3), never here."""
        return (
            self.total_trades >= config.MIN_TRADES_OOS
            and self.win_rate >= config.MIN_WIN_RATE
            and self.profit_factor >= config.MIN_PROFIT_FACTOR
            and self.sharpe_ratio >= config.MIN_SHARPE
        )

    def metrics_payload(self) -> dict:
        """API metrics: ratios as fractions, every value a finite number / None / bool / str (never a formatted
        string). The display strings of `summary()` belong under `metrics_display`."""
        def fin(x):
            try:
                v = float(x)
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None

        pf = None if self.profit_factor_undefined else fin(self.profit_factor)
        return {
            "symbol": self.symbol, "pattern": self.pattern_name, "total_trades": int(self.total_trades),
            "win_rate": fin(self.win_rate), "avg_win": fin(self.avg_win_pct),
            "avg_loss": fin(self.avg_loss_pct), "profit_factor": pf,
            "profit_factor_capped": fin(self.profit_factor_capped),
            "total_return": fin(self.total_return_pct), "max_drawdown": fin(self.max_drawdown_pct),
            "sharpe": fin(self.sharpe_ratio), "sortino": fin(self.sortino_ratio),
            "calmar": fin(self.calmar_ratio), "cagr": fin(self.cagr), "psr": fin(self.psr),
            "expectancy": fin(self.expectancy), "avg_duration_days": fin(self.avg_trade_duration_days),
            "execution": self.execution, "valid": bool(self.is_valid),
        }

    def numeric_summary(self) -> dict:
        """Finite floats / None only (json.dumps(allow_nan=False) safe)."""
        eq = self.equity_curve
        return metrics.summary(
            eq.to_numpy() if eq is not None and len(eq) else [],
            [t.pnl_pct for t in self.trades], [t.pnl_abs for t in self.trades],
            [t.capital_before for t in self.trades], initial_capital=config.BACKTEST_INITIAL_CAPITAL,
            bars_per_year=self.bars_per_year)

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "pattern": self.pattern_name,
            "total_trades": self.total_trades,
            "win_rate": f"{self.win_rate:.1%}",
            "avg_win": f"{self.avg_win_pct:.2%}",
            "avg_loss": f"{self.avg_loss_pct:.2%}",
            "profit_factor": None if self.profit_factor_undefined else f"{self.profit_factor:.2f}",
            "profit_factor_capped": self.profit_factor_capped,
            "total_return": f"{self.total_return_pct:.2%}",
            "max_drawdown": f"{self.max_drawdown_pct:.2%}",
            "sharpe": f"{self.sharpe_ratio:.2f}",
            "sortino": f"{self.sortino_ratio:.2f}",
            "calmar": f"{self.calmar_ratio:.2f}",
            "expectancy": f"{self.expectancy:.4f}",
            "avg_duration_days": f"{self.avg_trade_duration_days:.1f}",
            "valid": self.is_valid,
        }


def _flat_equity(df: pd.DataFrame) -> pd.Series:
    return pd.Series(float(config.BACKTEST_INITIAL_CAPITAL), index=df.index)


def classic_backtest(
    df: pd.DataFrame,
    symbol: str,
    pattern_name: str,
    stop_loss: float = config.STOP_LOSS_PCT,
    take_profit: float = config.TAKE_PROFIT_PCT,
    commission: float = config.COMMISSION_PCT,
    slippage: float = config.SLIPPAGE_PCT,
    execution: Optional[str] = None,
) -> BacktestResult:
    """Compatibility wrapper over engine.run_backtest + metrics (see engine.py for the model)."""
    result = BacktestResult(symbol=symbol, pattern_name=pattern_name)
    bpy = result.bars_per_year = instruments.bars_per_year(symbol)
    result.equity_curve = _flat_equity(df)
    if "signal" not in df.columns or len(df) < 10:
        return result

    run = engine.run_backtest(df, execution=execution, stop_loss=stop_loss, take_profit=take_profit,
                              commission=commission, slippage=slippage)
    trades = run.trades
    init = run.initial_capital
    eq = run.equity
    result.trades = trades
    result.total_trades = len(trades)
    result.execution = run.execution
    result.equity_curve = eq
    result.positions = run.positions

    rets = metrics.equity_returns(eq.to_numpy(), init)
    full = np.concatenate([[init], eq.to_numpy()])
    result.total_return_pct = float(eq.iloc[-1] / init - 1.0)
    result.max_drawdown_pct, result.max_drawdown_duration_bars = metrics.max_drawdown(full)
    result.sharpe_ratio = metrics.sharpe(rets, bpy)
    result.sortino_ratio = metrics.sortino(rets, bpy)
    result.cagr = metrics.cagr(rets, bpy)
    result.calmar_ratio = metrics.calmar(rets, full, bars_per_year=bpy) or 0.0
    result.psr = metrics.psr(rets)

    if not trades:
        return result

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.win_rate = len(wins) / len(pnls)
    result.win_rate_ci = metrics.wilson_ci(len(wins), len(pnls))
    result.avg_win_pct = float(np.mean(wins)) if wins else 0.0
    result.avg_loss_pct = float(np.mean(losses)) if losses else 0.0

    pf = metrics.profit_factor(pnls)
    if pf is not None:
        result.profit_factor = pf
    elif wins:  # no losing P&L: ratio undefined, never expose a 1e9 sentinel
        result.profit_factor = PROFIT_FACTOR_CAP
        result.profit_factor_undefined = True
    else:
        result.profit_factor = 0.0

    result.expectancy = float(np.mean(pnls))
    result.expectancy_equity = metrics.expectancy(
        pnls, [t.pnl_abs for t in trades], [t.capital_before for t in trades])["expectancy_equity"]
    result.avg_trade_duration_days = float(np.mean([(t.exit_date - t.entry_date).days for t in trades]))
    return result


def backtest_pattern(df: pd.DataFrame, symbol: str, pattern_name: str) -> BacktestResult:
    """Backtest a pattern with the engine (kept for compatibility; the vectorbt overlay is gone)."""
    return classic_backtest(df, symbol, pattern_name)


def walk_forward_validate(
    df: pd.DataFrame, symbol: str, pattern_func: Callable, pattern_name: str,
    n_splits: int = 4, train_pct: float = 0.7,
) -> list[BacktestResult]:
    """Legacy fold-wise out-of-sample backtests (kept for the dashboard / API).

    The pattern is evaluated ONCE on the full contiguous frame (warm-up included, WF-1) and each fold
    backtests only its test window with those signals. The statistically meaningful walk-forward
    (selection on train only, stitched OOS returns, hold-out, null, FDR) lives in validation.py."""
    results = []
    split_size = len(df) // n_splits
    try:
        full = pattern_func(df)
    except Exception as e:
        logger.warning(f"Walk-forward pattern failed: {e}")
        return results

    for i in range(n_splits - 1):
        train_start = i * split_size
        train_end = train_start + int(split_size * train_pct)
        # test windows are contiguous and NON-overlapping: each ends where the next fold's test window starts
        test_end = min((i + 1) * split_size + int(split_size * train_pct), len(df))

        test_df = full.iloc[train_end:test_end]
        if len(test_df) < 20:
            continue

        try:
            results.append(classic_backtest(test_df, symbol, f"{pattern_name}_oos_{i}"))
        except Exception as e:
            logger.warning(f"Walk-forward fold {i} failed: {e}")

    return results
