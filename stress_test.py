"""
Stress Testing — validates patterns under different market regimes.

Includes:
- Regime-based testing (bull/bear, high/low vol, high/low volume)
- Monte Carlo simulation for confidence intervals
- Robustness analysis (parameter sensitivity)
"""

import logging
from typing import Callable, Optional

import numpy as np
import pandas as pd

import config
from backtester import classic_backtest, BacktestResult

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  REGIME DETECTION
# ══════════════════════════════════════════════════════════════

def detect_regimes(df: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    """
    Classify each bar into market regimes based on rolling statistics.
    Returns DataFrame with regime labels.
    """
    regimes = pd.DataFrame(index=df.index)

    returns = df["Close"].pct_change()

    # Volatility regime
    vol = returns.rolling(window).std() * np.sqrt(252)
    vol_pct = vol.rank(pct=True)
    regimes["vol_regime"] = pd.cut(
        vol_pct, bins=[0, 0.25, 0.75, 1.0],
        labels=["low_vol", "normal_vol", "high_vol"]
    )

    # Trend regime (based on rolling return)
    rolling_ret = df["Close"].pct_change(window)
    ret_pct = rolling_ret.rank(pct=True)
    regimes["trend_regime"] = pd.cut(
        ret_pct, bins=[0, 0.25, 0.75, 1.0],
        labels=["bear", "neutral", "bull"]
    )

    # Volume regime
    if df["Volume"].sum() > 0:
        vol_rank = df["Volume"].rolling(window).mean().rank(pct=True)
        regimes["volume_regime"] = pd.cut(
            vol_rank, bins=[0, 0.25, 0.75, 1.0],
            labels=["low_volume", "normal_volume", "high_volume"]
        )
    else:
        regimes["volume_regime"] = "normal_volume"

    return regimes


# ══════════════════════════════════════════════════════════════
#  REGIME-BASED STRESS TEST
# ══════════════════════════════════════════════════════════════

def stress_test_regimes(
    df: pd.DataFrame,
    symbol: str,
    pattern_func: Callable,
    pattern_name: str,
) -> dict[str, BacktestResult]:
    """
    Run the pattern under each market regime separately.
    Shows how the strategy performs in bull vs bear, high vs low vol, etc.
    """
    regimes = detect_regimes(df)
    results = {}

    # Full period baseline
    try:
        full_signals = pattern_func(df)
        results["full_period"] = classic_backtest(full_signals, symbol, f"{pattern_name}_full")
    except Exception as e:
        logger.warning(f"Full period test failed: {e}")

    # Test each regime
    regime_configs = [
        ("vol_regime", "high_vol", "high_volatility"),
        ("vol_regime", "low_vol", "low_volatility"),
        ("trend_regime", "bull", "bull_market"),
        ("trend_regime", "bear", "bear_market"),
        ("volume_regime", "high_volume", "high_volume"),
        ("volume_regime", "low_volume", "low_volume"),
    ]

    for regime_col, regime_val, label in regime_configs:
        try:
            mask = regimes[regime_col] == regime_val
            if mask.sum() < 50:
                logger.debug(f"  {label}: insufficient bars ({mask.sum()})")
                continue

            regime_df = df[mask].copy()
            if len(regime_df) < 30:
                continue

            signals = pattern_func(regime_df)
            result = classic_backtest(signals, symbol, f"{pattern_name}_{label}")
            results[label] = result

            logger.info(
                f"  {label}: trades={result.total_trades}, "
                f"WR={result.win_rate:.1%}, PF={result.profit_factor:.2f}"
            )

        except Exception as e:
            logger.debug(f"  {label} test failed: {e}")

    return results


# ══════════════════════════════════════════════════════════════
#  MONTE CARLO SIMULATION
# ══════════════════════════════════════════════════════════════

def monte_carlo_analysis(
    backtest_result: BacktestResult,
    n_simulations: int = config.MONTE_CARLO_SIMULATIONS,
    confidence: float = config.MONTE_CARLO_CONFIDENCE,
) -> dict:
    """
    Monte Carlo simulation on actual trade results.
    Randomly resamples trade PnLs to estimate confidence intervals
    for total return, max drawdown, etc.

    This answers: "If I replay these trades in random order, what's
    the range of outcomes I might see?"
    """
    if not backtest_result.trades or len(backtest_result.trades) < 5:
        return {"error": "insufficient_trades"}

    pnls = np.array([t.pnl_pct for t in backtest_result.trades])
    n_trades = len(pnls)

    sim_returns = []
    sim_drawdowns = []
    sim_sharpes = []

    for _ in range(n_simulations):
        # Resample trades with replacement
        sampled = np.random.choice(pnls, size=n_trades, replace=True)

        # Build equity curve
        equity = np.cumprod(1 + sampled)
        total_return = equity[-1] - 1
        sim_returns.append(total_return)

        # Max drawdown
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / (peak + 1e-10)
        sim_drawdowns.append(dd.min())

        # Simplified Sharpe
        if sampled.std() > 0:
            sim_sharpes.append(sampled.mean() / sampled.std() * np.sqrt(252 / max(n_trades, 1)))
        else:
            sim_sharpes.append(0)

    sim_returns = np.array(sim_returns)
    sim_drawdowns = np.array(sim_drawdowns)
    sim_sharpes = np.array(sim_sharpes)

    alpha = (1 - confidence) / 2

    return {
        "n_simulations": n_simulations,
        "n_trades": n_trades,
        "confidence_level": confidence,
        # Return distribution
        "return_mean": float(np.mean(sim_returns)),
        "return_median": float(np.median(sim_returns)),
        "return_std": float(np.std(sim_returns)),
        "return_ci_low": float(np.percentile(sim_returns, alpha * 100)),
        "return_ci_high": float(np.percentile(sim_returns, (1 - alpha) * 100)),
        "prob_positive": float(np.mean(sim_returns > 0)),
        "return_5th_pct": float(np.percentile(sim_returns, 5)),
        "return_95th_pct": float(np.percentile(sim_returns, 95)),
        # Drawdown distribution
        "drawdown_mean": float(np.mean(sim_drawdowns)),
        "drawdown_median": float(np.median(sim_drawdowns)),
        "drawdown_worst_5pct": float(np.percentile(sim_drawdowns, 5)),
        # Sharpe distribution
        "sharpe_mean": float(np.mean(sim_sharpes)),
        "sharpe_median": float(np.median(sim_sharpes)),
        "sharpe_ci_low": float(np.percentile(sim_sharpes, alpha * 100)),
        "sharpe_ci_high": float(np.percentile(sim_sharpes, (1 - alpha) * 100)),
    }


# ══════════════════════════════════════════════════════════════
#  ROBUSTNESS / PARAMETER SENSITIVITY
# ══════════════════════════════════════════════════════════════

def parameter_sensitivity(
    df: pd.DataFrame,
    symbol: str,
    pattern_name: str,
    base_sl: float = config.STOP_LOSS_PCT,
    base_tp: float = config.TAKE_PROFIT_PCT,
) -> pd.DataFrame:
    """
    Test how sensitive the strategy is to SL/TP parameters.
    A robust strategy shouldn't collapse with small parameter changes.
    """
    from patterns import PATTERN_REGISTRY

    if pattern_name not in PATTERN_REGISTRY:
        return pd.DataFrame()

    func = PATTERN_REGISTRY[pattern_name]
    signals = func(df)

    sl_range = np.arange(base_sl * 0.5, base_sl * 2.5, base_sl * 0.25)
    tp_range = np.arange(base_tp * 0.5, base_tp * 2.5, base_tp * 0.25)

    rows = []
    for sl in sl_range:
        for tp in tp_range:
            result = classic_backtest(signals, symbol, pattern_name,
                                       stop_loss=sl, take_profit=tp)
            rows.append({
                "stop_loss": sl,
                "take_profit": tp,
                "rr_ratio": tp / sl,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "total_return": result.total_return_pct,
                "sharpe": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown_pct,
                "total_trades": result.total_trades,
            })

    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════
#  FULL STRESS TEST SUITE
# ══════════════════════════════════════════════════════════════

def full_stress_test(
    df: pd.DataFrame,
    symbol: str,
    pattern_func: Callable,
    pattern_name: str,
) -> dict:
    """
    Run the complete stress test suite on a pattern.
    Returns a comprehensive report.
    """
    report = {
        "symbol": symbol,
        "pattern": pattern_name,
    }

    # 1. Regime analysis
    logger.info(f"  Running regime stress test...")
    regime_results = stress_test_regimes(df, symbol, pattern_func, pattern_name)
    report["regimes"] = {
        name: result.summary() for name, result in regime_results.items()
    }

    # 2. Monte Carlo on full backtest
    full_result = regime_results.get("full_period")
    if full_result and full_result.total_trades >= 5:
        logger.info(f"  Running Monte Carlo simulation ({config.MONTE_CARLO_SIMULATIONS} sims)...")
        report["monte_carlo"] = monte_carlo_analysis(full_result)
    else:
        report["monte_carlo"] = {"error": "insufficient_trades"}

    # 3. Parameter sensitivity
    logger.info(f"  Running parameter sensitivity...")
    sensitivity = parameter_sensitivity(df, symbol, pattern_name)
    if not sensitivity.empty:
        report["sensitivity"] = {
            "best_params": sensitivity.sort_values("sharpe", ascending=False).head(3).to_dict("records"),
            "worst_params": sensitivity.sort_values("sharpe").head(3).to_dict("records"),
            "sharpe_std_across_params": float(sensitivity["sharpe"].std()),
            "is_robust": sensitivity["sharpe"].std() < 1.0,  # Low variance = robust
        }

    # 4. Overall assessment
    mc = report.get("monte_carlo", {})
    report["assessment"] = {
        "regimes_consistent": _check_regime_consistency(regime_results),
        "mc_prob_positive": mc.get("prob_positive", 0),
        "mc_worst_case_5pct": mc.get("return_5th_pct", 0),
        "param_robust": report.get("sensitivity", {}).get("is_robust", False),
    }

    return report


def _check_regime_consistency(results: dict[str, BacktestResult]) -> bool:
    """Check if strategy works across most regimes."""
    profitable_regimes = 0
    total_regimes = 0

    for name, result in results.items():
        if name == "full_period":
            continue
        total_regimes += 1
        if result.total_return_pct > 0 and result.profit_factor > 1.0:
            profitable_regimes += 1

    if total_regimes == 0:
        return False

    return (profitable_regimes / total_regimes) >= 0.5
