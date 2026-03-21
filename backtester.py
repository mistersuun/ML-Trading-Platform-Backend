"""
Backtesting Engine — powered by VectorBT for speed + custom classic engine.

VectorBT handles the fast parameter sweeps.
Classic engine handles detailed single-strategy analysis.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)

# Try VectorBT — fall back to pure pandas if not installed
try:
    import vectorbt as vbt
    HAS_VBT = True
except ImportError:
    HAS_VBT = False
    logger.info("VectorBT not installed — using classic backtester only")

# Try Backtesting.py
try:
    from backtesting import Backtest, Strategy
    from backtesting.lib import crossover
    HAS_BT = True
except ImportError:
    HAS_BT = False


# ══════════════════════════════════════════════════════════════
#  VECTORBT ENGINE — fast parameter sweeps & optimization
# ══════════════════════════════════════════════════════════════

def vbt_backtest_signals(
    df: pd.DataFrame,
    signals_df: pd.DataFrame,
    initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
    sl_pct: float = config.STOP_LOSS_PCT,
    tp_pct: float = config.TAKE_PROFIT_PCT,
    commission: float = config.COMMISSION_PCT,
) -> Optional[dict]:
    """
    Backtest using VectorBT's Portfolio engine.
    Fast, vectorized, handles SL/TP natively.
    """
    if not HAS_VBT:
        return None

    close = df["Close"]
    entries = (signals_df["signal"] == 1)
    exits = (signals_df["signal"] == -1)

    try:
        pf = vbt.Portfolio.from_signals(
            close,
            entries=entries,
            exits=exits,
            init_cash=initial_capital,
            fees=commission,
            sl_stop=sl_pct,
            tp_stop=tp_pct,
            freq="1D",
        )

        stats = pf.stats()

        return {
            "engine": "vectorbt",
            "total_return": float(stats.get("Total Return [%]", 0)) / 100,
            "sharpe_ratio": float(stats.get("Sharpe Ratio", 0)),
            "sortino_ratio": float(stats.get("Sortino Ratio", 0)),
            "max_drawdown": float(stats.get("Max Drawdown [%]", 0)) / -100,
            "win_rate": float(stats.get("Win Rate [%]", 0)) / 100,
            "profit_factor": float(stats.get("Profit Factor", 0)),
            "total_trades": int(stats.get("Total Trades", 0)),
            "avg_trade_pnl": float(stats.get("Avg Winning Trade [%]", 0)) / 100,
            "expectancy": float(stats.get("Expectancy", 0)),
            "calmar_ratio": float(stats.get("Calmar Ratio", 0)),
            "equity_curve": pf.value(),
            "trade_records": pf.trades.records_readable if pf.trades.count() > 0 else None,
        }

    except Exception as e:
        logger.warning(f"VectorBT backtest failed: {e}")
        return None


def vbt_parameter_sweep(
    close: pd.Series,
    fast_range: range = range(5, 30, 5),
    slow_range: range = range(20, 100, 10),
    commission: float = config.COMMISSION_PCT,
) -> Optional[pd.DataFrame]:
    """
    VectorBT parameter sweep for EMA crossover.
    Tests all combinations of fast/slow periods instantly.
    """
    if not HAS_VBT:
        return None

    try:
        fast_ema, slow_ema = vbt.MA.run_combs(
            close, window=fast_range, r=2, short_names=["fast", "slow"]
        )

        entries = fast_ema.ma_crossed_above(slow_ema)
        exits = fast_ema.ma_crossed_below(slow_ema)

        pf = vbt.Portfolio.from_signals(
            close, entries, exits, fees=commission, freq="1D"
        )

        results = pf.stats(agg_func=None)
        return results

    except Exception as e:
        logger.warning(f"VectorBT parameter sweep failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  CLASSIC ENGINE — detailed single-strategy analysis
# ══════════════════════════════════════════════════════════════

@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: Optional[pd.Timestamp] = None
    direction: int = 1
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    pnl_abs: float = 0.0
    exit_reason: str = ""
    bars_held: int = 0


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
    profit_factor: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    avg_trade_duration_days: float = 0.0
    expectancy: float = 0.0
    equity_curve: pd.Series = field(default_factory=pd.Series)

    @property
    def is_valid(self) -> bool:
        return (
            self.total_trades >= config.MIN_TRADES
            and self.win_rate >= config.MIN_WIN_RATE
            and self.profit_factor >= config.MIN_PROFIT_FACTOR
            and self.sharpe_ratio >= config.MIN_SHARPE
        )

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "pattern": self.pattern_name,
            "total_trades": self.total_trades,
            "win_rate": f"{self.win_rate:.1%}",
            "avg_win": f"{self.avg_win_pct:.2%}",
            "avg_loss": f"{self.avg_loss_pct:.2%}",
            "profit_factor": f"{self.profit_factor:.2f}",
            "total_return": f"{self.total_return_pct:.2%}",
            "max_drawdown": f"{self.max_drawdown_pct:.2%}",
            "sharpe": f"{self.sharpe_ratio:.2f}",
            "sortino": f"{self.sortino_ratio:.2f}",
            "calmar": f"{self.calmar_ratio:.2f}",
            "expectancy": f"{self.expectancy:.4f}",
            "avg_duration_days": f"{self.avg_trade_duration_days:.1f}",
            "valid": self.is_valid,
        }


def classic_backtest(
    df: pd.DataFrame,
    symbol: str,
    pattern_name: str,
    stop_loss: float = config.STOP_LOSS_PCT,
    take_profit: float = config.TAKE_PROFIT_PCT,
    commission: float = config.COMMISSION_PCT,
    slippage: float = config.SLIPPAGE_PCT,
) -> BacktestResult:
    """
    Detailed event-driven backtest with full trade tracking.
    """
    result = BacktestResult(symbol=symbol, pattern_name=pattern_name)

    if "signal" not in df.columns or len(df) < 10:
        return result

    trades: list[Trade] = []
    position: Optional[Trade] = None
    capital = config.BACKTEST_INITIAL_CAPITAL
    equity = [capital]

    for i in range(1, len(df)):
        row = df.iloc[i]
        signal = int(row.get("signal", 0))
        close, high, low = row["Close"], row["High"], row["Low"]
        date = df.index[i]

        if position is not None:
            # Check exits
            if position.direction == 1:
                if low <= position.entry_price * (1 - stop_loss):
                    position.exit_price = position.entry_price * (1 - stop_loss)
                    position.exit_reason = "stop_loss"
                elif high >= position.entry_price * (1 + take_profit):
                    position.exit_price = position.entry_price * (1 + take_profit)
                    position.exit_reason = "take_profit"
                elif signal == -1:
                    position.exit_price = close * (1 - slippage)
                    position.exit_reason = "signal"
            else:
                if high >= position.entry_price * (1 + stop_loss):
                    position.exit_price = position.entry_price * (1 + stop_loss)
                    position.exit_reason = "stop_loss"
                elif low <= position.entry_price * (1 - take_profit):
                    position.exit_price = position.entry_price * (1 - take_profit)
                    position.exit_reason = "take_profit"
                elif signal == 1:
                    position.exit_price = close * (1 + slippage)
                    position.exit_reason = "signal"

            if position.exit_reason:
                position.exit_date = date
                position.bars_held = i - trades[-1].bars_held if trades else i
                raw_pnl = (position.exit_price - position.entry_price) / position.entry_price
                raw_pnl *= position.direction
                position.pnl_pct = raw_pnl - (2 * commission)
                position.pnl_abs = capital * config.MAX_POSITION_SIZE_PCT * position.pnl_pct
                capital += position.pnl_abs
                trades.append(position)
                position = None

        if position is None and signal != 0:
            position = Trade(
                entry_date=date,
                direction=signal,
                entry_price=close * (1 + slippage * signal),
            )

        equity.append(capital)

    # Close remaining
    if position is not None:
        position.exit_price = df["Close"].iloc[-1]
        position.exit_date = df.index[-1]
        position.exit_reason = "end_of_data"
        raw_pnl = (position.exit_price - position.entry_price) / position.entry_price * position.direction
        position.pnl_pct = raw_pnl - (2 * commission)
        position.pnl_abs = capital * config.MAX_POSITION_SIZE_PCT * position.pnl_pct
        capital += position.pnl_abs
        trades.append(position)
        equity.append(capital)

    # ── Compute metrics ──
    result.trades = trades
    result.total_trades = len(trades)

    if not trades:
        result.equity_curve = pd.Series(equity[:len(df)], index=df.index[:len(equity)])
        return result

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.win_rate = len(wins) / len(pnls)
    result.avg_win_pct = np.mean(wins) if wins else 0
    result.avg_loss_pct = np.mean(losses) if losses else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1e-10
    result.profit_factor = gross_profit / gross_loss

    result.total_return_pct = (capital - config.BACKTEST_INITIAL_CAPITAL) / config.BACKTEST_INITIAL_CAPITAL

    # Drawdown
    eq = pd.Series(equity)
    peak = eq.cummax()
    dd = (eq - peak) / (peak + 1e-10)
    result.max_drawdown_pct = dd.min()

    # Sharpe & Sortino
    returns = eq.pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        excess = returns - config.RISK_FREE_RATE / 252
        result.sharpe_ratio = (excess.mean() / returns.std()) * np.sqrt(252)
        downside = returns[returns < 0].std()
        result.sortino_ratio = (excess.mean() / (downside + 1e-10)) * np.sqrt(252)
    else:
        result.sharpe_ratio = 0.0
        result.sortino_ratio = 0.0

    # Calmar
    if result.max_drawdown_pct != 0:
        annualized_return = result.total_return_pct * (252 / max(len(df), 1))
        result.calmar_ratio = annualized_return / abs(result.max_drawdown_pct)

    # Expectancy
    result.expectancy = (
        result.win_rate * result.avg_win_pct +
        (1 - result.win_rate) * result.avg_loss_pct
    )

    # Duration
    durations = [
        (t.exit_date - t.entry_date).days for t in trades if t.exit_date is not None
    ]
    result.avg_trade_duration_days = np.mean(durations) if durations else 0

    result.equity_curve = pd.Series(equity[:len(df)], index=df.index[:len(equity)])

    return result


def backtest_pattern(
    df: pd.DataFrame, symbol: str, pattern_name: str
) -> BacktestResult:
    """
    Backtest a pattern using VectorBT (if available) enhanced with classic metrics.
    Falls back to classic engine if VectorBT isn't installed.
    """
    # Always run classic for detailed trade records
    result = classic_backtest(df, symbol, pattern_name)

    # Optionally enhance with VectorBT metrics
    vbt_result = vbt_backtest_signals(df, df)
    if vbt_result and vbt_result.get("total_trades", 0) > 0:
        # Use VectorBT's more accurate metrics where available
        result.sortino_ratio = vbt_result.get("sortino_ratio", result.sortino_ratio)
        result.calmar_ratio = vbt_result.get("calmar_ratio", result.calmar_ratio)

    return result


def walk_forward_validate(
    df: pd.DataFrame, symbol: str, pattern_func: Callable, pattern_name: str,
    n_splits: int = 4, train_pct: float = 0.7,
) -> list[BacktestResult]:
    """Walk-forward validation across multiple windows."""
    results = []
    split_size = len(df) // n_splits

    for i in range(n_splits - 1):
        train_start = i * split_size
        train_end = train_start + int(split_size * train_pct)
        test_end = min((i + 2) * split_size, len(df))

        test_df = df.iloc[train_end:test_end]
        if len(test_df) < 20:
            continue

        try:
            signals = pattern_func(test_df)
            result = classic_backtest(signals, symbol, f"{pattern_name}_oos_{i}")
            results.append(result)
        except Exception as e:
            logger.warning(f"Walk-forward fold {i} failed: {e}")

    return results
