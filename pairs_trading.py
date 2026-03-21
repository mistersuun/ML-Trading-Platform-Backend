"""
Statistical Arbitrage / Pairs Trading

Implements the classic pairs trading strategy:
1. Test for cointegration between pairs
2. Compute the spread and its z-score
3. Enter when z-score is extreme, exit on mean reversion
4. Risk management with z-score-based stop loss

Uses the Engle-Granger method and Ornstein-Uhlenbeck half-life estimation.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import coint, adfuller
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant

import config

logger = logging.getLogger(__name__)


@dataclass
class PairAnalysis:
    """Results of pair cointegration analysis."""
    symbol_a: str
    symbol_b: str
    is_cointegrated: bool
    coint_pvalue: float
    hedge_ratio: float
    half_life: float
    spread_mean: float
    spread_std: float
    current_zscore: float
    adf_stat: float
    adf_pvalue: float
    correlation: float


def compute_hedge_ratio(prices_a: pd.Series, prices_b: pd.Series) -> float:
    """
    Compute the hedge ratio (beta) using OLS regression.
    spread = prices_a - hedge_ratio * prices_b
    """
    X = add_constant(prices_b)
    model = OLS(prices_a, X).fit()
    return model.params.iloc[1]


def compute_spread(
    prices_a: pd.Series, prices_b: pd.Series, hedge_ratio: float
) -> pd.Series:
    """Compute the spread between two price series."""
    return prices_a - hedge_ratio * prices_b


def compute_half_life(spread: pd.Series) -> float:
    """
    Estimate the Ornstein-Uhlenbeck mean-reversion half-life.
    Uses AR(1) regression: spread_delta = theta * spread_lag + noise
    half_life = -ln(2) / theta
    """
    spread_lag = spread.shift(1).dropna()
    spread_delta = spread.diff().dropna()

    # Align
    common = spread_lag.index.intersection(spread_delta.index)
    if len(common) < 20:
        return float("inf")

    X = add_constant(spread_lag.loc[common])
    y = spread_delta.loc[common]

    try:
        model = OLS(y, X).fit()
        theta = model.params.iloc[1]
        if theta >= 0:
            return float("inf")  # Not mean-reverting
        return -np.log(2) / theta
    except Exception:
        return float("inf")


def analyze_pair(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    symbol_a: str,
    symbol_b: str,
) -> PairAnalysis:
    """
    Full cointegration analysis for a pair.
    Tests if the pair is suitable for stat arb trading.
    """
    prices_a = df_a["Close"]
    prices_b = df_b["Close"]

    # Correlation
    correlation = prices_a.corr(prices_b)

    # Engle-Granger cointegration test
    coint_stat, coint_pvalue, _ = coint(prices_a, prices_b)

    is_cointegrated = coint_pvalue < config.PAIRS_COINT_PVALUE

    # Hedge ratio
    hedge_ratio = compute_hedge_ratio(prices_a, prices_b)

    # Spread
    spread = compute_spread(prices_a, prices_b, hedge_ratio)

    # ADF test on spread (should be stationary if cointegrated)
    adf_result = adfuller(spread.dropna(), maxlag=20)
    adf_stat = adf_result[0]
    adf_pvalue = adf_result[1]

    # Half-life
    half_life = compute_half_life(spread)

    # Z-score
    spread_mean = spread.mean()
    spread_std = spread.std()
    current_zscore = (spread.iloc[-1] - spread_mean) / (spread_std + 1e-10)

    return PairAnalysis(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        is_cointegrated=is_cointegrated,
        coint_pvalue=coint_pvalue,
        hedge_ratio=hedge_ratio,
        half_life=half_life,
        spread_mean=spread_mean,
        spread_std=spread_std,
        current_zscore=current_zscore,
        adf_stat=adf_stat,
        adf_pvalue=adf_pvalue,
        correlation=correlation,
    )


def is_valid_pair(analysis: PairAnalysis) -> bool:
    """Check if a pair meets all criteria for stat arb trading."""
    return (
        analysis.is_cointegrated
        and analysis.half_life >= config.PAIRS_MIN_HALF_LIFE
        and analysis.half_life <= config.PAIRS_MAX_HALF_LIFE
        and abs(analysis.correlation) > 0.5
        and analysis.adf_pvalue < 0.05
    )


def generate_pair_signals(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    analysis: PairAnalysis,
    lookback: int = 60,
) -> pd.DataFrame:
    """
    Generate trading signals for a cointegrated pair.

    Returns DataFrame with:
      - spread, zscore columns
      - signal: 1 = long spread (buy A, sell B), -1 = short spread, 0 = flat
    """
    prices_a = df_a["Close"]
    prices_b = df_b["Close"]

    spread = compute_spread(prices_a, prices_b, analysis.hedge_ratio)

    # Rolling z-score (avoids using future data)
    rolling_mean = spread.rolling(lookback).mean()
    rolling_std = spread.rolling(lookback).std()
    zscore = (spread - rolling_mean) / (rolling_std + 1e-10)

    result = pd.DataFrame(index=df_a.index)
    result["price_a"] = prices_a
    result["price_b"] = prices_b
    result["spread"] = spread
    result["zscore"] = zscore
    result["signal"] = 0

    # Track position state
    position = 0  # 1 = long spread, -1 = short spread, 0 = flat
    signals = []

    for i in range(len(result)):
        z = zscore.iloc[i]
        if pd.isna(z):
            signals.append(0)
            continue

        signal = 0

        if position == 0:
            # Entry signals
            if z < -config.PAIRS_ZSCORE_ENTRY:
                signal = 1   # Long spread: buy A, sell B
                position = 1
            elif z > config.PAIRS_ZSCORE_ENTRY:
                signal = -1  # Short spread: sell A, buy B
                position = -1
        elif position == 1:
            # Exit long spread
            if z >= -config.PAIRS_ZSCORE_EXIT or z < -config.PAIRS_ZSCORE_STOP:
                signal = 0
                position = 0
            else:
                signal = 1  # Hold
        elif position == -1:
            # Exit short spread
            if z <= config.PAIRS_ZSCORE_EXIT or z > config.PAIRS_ZSCORE_STOP:
                signal = 0
                position = 0
            else:
                signal = -1  # Hold

        signals.append(signal)

    result["signal"] = signals
    return result


def backtest_pair(
    signals_df: pd.DataFrame,
    analysis: PairAnalysis,
    initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
    commission: float = config.COMMISSION_PCT,
) -> dict:
    """
    Backtest a pairs trading strategy.

    Returns performance metrics.
    """
    df = signals_df.dropna(subset=["zscore"]).copy()

    if len(df) < 20:
        return {"error": "insufficient_data"}

    capital = initial_capital
    position_size = capital * config.MAX_POSITION_SIZE_PCT
    trades = []
    equity = [capital]
    current_trade = None

    for i in range(1, len(df)):
        sig = df["signal"].iloc[i]
        prev_sig = df["signal"].iloc[i - 1]

        # Position opened
        if sig != 0 and prev_sig == 0:
            current_trade = {
                "entry_idx": i,
                "direction": sig,
                "entry_spread": df["spread"].iloc[i],
                "entry_date": df.index[i],
            }

        # Position closed
        elif sig == 0 and prev_sig != 0 and current_trade is not None:
            exit_spread = df["spread"].iloc[i]
            entry_spread = current_trade["entry_spread"]
            direction = current_trade["direction"]

            # PnL from spread change
            spread_change = (exit_spread - entry_spread) * direction
            pnl_pct = spread_change / (abs(entry_spread) + 1e-10)
            pnl_abs = position_size * pnl_pct - (position_size * commission * 4)  # 2 legs * entry/exit

            capital += pnl_abs

            current_trade["exit_idx"] = i
            current_trade["exit_date"] = df.index[i]
            current_trade["exit_spread"] = exit_spread
            current_trade["pnl_pct"] = pnl_pct
            current_trade["pnl_abs"] = pnl_abs

            trades.append(current_trade)
            current_trade = None

        equity.append(capital)

    # Metrics
    if not trades:
        return {
            "total_trades": 0,
            "symbol_a": analysis.symbol_a,
            "symbol_b": analysis.symbol_b,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    eq = pd.Series(equity)
    peak = eq.cummax()
    drawdown = (eq - peak) / (peak + 1e-10)

    returns = eq.pct_change().dropna()
    sharpe = (returns.mean() / (returns.std() + 1e-10)) * np.sqrt(252) if len(returns) > 1 else 0

    return {
        "symbol_a": analysis.symbol_a,
        "symbol_b": analysis.symbol_b,
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0,
        "avg_win": np.mean(wins) if wins else 0,
        "avg_loss": np.mean(losses) if losses else 0,
        "profit_factor": (sum(wins) / (abs(sum(losses)) + 1e-10)) if losses else float("inf"),
        "total_return": (capital - initial_capital) / initial_capital,
        "max_drawdown": drawdown.min(),
        "sharpe_ratio": sharpe,
        "half_life": analysis.half_life,
        "correlation": analysis.correlation,
        "coint_pvalue": analysis.coint_pvalue,
        "current_zscore": analysis.current_zscore,
    }


def scan_all_pairs(
    data: dict[str, pd.DataFrame],
    pairs: Optional[list[tuple]] = None,
) -> list[dict]:
    """
    Scan all configured pairs for cointegration and trading opportunities.
    """
    pairs = pairs or config.PAIRS
    results = []

    for sym_a, sym_b in pairs:
        if sym_a not in data or sym_b not in data:
            logger.info(f"  Skipping {sym_a}/{sym_b} — data missing")
            continue

        df_a = data[sym_a]
        df_b = data[sym_b]

        # Align dates
        common = df_a.index.intersection(df_b.index)
        if len(common) < 60:
            continue

        df_a_aligned = df_a.loc[common]
        df_b_aligned = df_b.loc[common]

        try:
            analysis = analyze_pair(df_a_aligned, df_b_aligned, sym_a, sym_b)

            if not is_valid_pair(analysis):
                logger.info(
                    f"  {sym_a}/{sym_b}: NOT valid — "
                    f"coint_p={analysis.coint_pvalue:.4f}, "
                    f"half_life={analysis.half_life:.1f}, "
                    f"corr={analysis.correlation:.3f}"
                )
                continue

            signals = generate_pair_signals(df_a_aligned, df_b_aligned, analysis)
            bt_result = backtest_pair(signals, analysis)

            if bt_result.get("total_trades", 0) > 0:
                logger.info(
                    f"  ✅ {sym_a}/{sym_b}: VALID — "
                    f"z={analysis.current_zscore:.2f}, "
                    f"WR={bt_result['win_rate']:.1%}, "
                    f"PF={bt_result['profit_factor']:.2f}, "
                    f"trades={bt_result['total_trades']}"
                )

                bt_result["has_signal"] = abs(analysis.current_zscore) > config.PAIRS_ZSCORE_ENTRY
                bt_result["signal_direction"] = (
                    "LONG_SPREAD" if analysis.current_zscore < -config.PAIRS_ZSCORE_ENTRY
                    else "SHORT_SPREAD" if analysis.current_zscore > config.PAIRS_ZSCORE_ENTRY
                    else "NONE"
                )
                results.append(bt_result)

        except Exception as e:
            logger.warning(f"  Error analyzing {sym_a}/{sym_b}: {e}")

    return results
