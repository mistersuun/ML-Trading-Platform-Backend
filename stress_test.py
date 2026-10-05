"""
Stress Testing — validates patterns under different market regimes (Phase 2, WS2.4).

- detect_regimes: CAUSAL regime labels (expanding-window percentile thresholds, shifted one bar).
- stress_test_regimes: the pattern runs ONCE on the full contiguous frame; trades (by entry bar)
  and daily mark-to-market returns are attributed to regimes known at the prior close.
- monte_carlo_analysis: stationary block bootstrap (arch) of daily MTM returns at real position
  size; CAGR / max-drawdown / Sharpe bands. No trade-count annualisation.
- parameter_sensitivity: SL/TP grid evaluated on the pre-HOLDOUT_START period only; reports the
  share of parameter sets with positive out-of-sample Sharpe and the number of trials.
"""

import logging
from typing import Callable, Optional

import numpy as np
import pandas as pd

import config
import metrics
from backtester import classic_backtest, BacktestResult

logger = logging.getLogger(__name__)

MIN_REGIME_TRADES = 10
OOS_FRACTION = 0.3          # tail share of the pre-hold-out period used as the OOS segment


# ══════════════════════════════════════════════════════════════
#  REGIME DETECTION (causal)
# ══════════════════════════════════════════════════════════════

def _expanding_regime(feature: pd.Series, window: int, labels: tuple[str, str, str]) -> pd.Series:
    """Label feature_t against the 25th/75th percentiles of feature over bars < t (expanding, shifted 1)."""
    q_lo = feature.expanding(min_periods=window).quantile(0.25).shift(1)
    q_hi = feature.expanding(min_periods=window).quantile(0.75).shift(1)
    ok = feature.notna() & q_lo.notna() & q_hi.notna()
    out = np.where(feature < q_lo, labels[0], np.where(feature > q_hi, labels[2], labels[1]))
    s = pd.Series(out, index=feature.index, dtype=object).where(ok)
    return pd.Series(pd.Categorical(s, categories=list(labels)), index=feature.index)


def detect_regimes(df: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    """
    Classify each bar into market regimes. CAUSAL: the label at bar t uses data up to t only
    (the feature at t versus expanding-window percentile thresholds computed through t-1).
    Bars before enough history exists are NaN. Returns a DataFrame of categorical labels.
    """
    regimes = pd.DataFrame(index=df.index)
    close = df["Close"].astype(float)

    vol = close.pct_change().rolling(window).std() * np.sqrt(252)
    regimes["vol_regime"] = _expanding_regime(vol, window, ("low_vol", "normal_vol", "high_vol"))

    regimes["trend_regime"] = _expanding_regime(close.pct_change(window), window, ("bear", "neutral", "bull"))

    volume = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series(0.0, index=df.index)
    vmean = volume.rolling(window).mean()
    vreg = _expanding_regime(vmean, window, ("low_volume", "normal_volume", "high_volume"))
    # no volume information seen so far -> neutral (decided bar by bar, so still causal)
    no_vol = volume.cumsum() <= 0
    vreg = vreg.astype(object).where(~no_vol, "normal_volume")
    regimes["volume_regime"] = pd.Categorical(vreg, categories=["low_volume", "normal_volume", "high_volume"])
    return regimes


# ══════════════════════════════════════════════════════════════
#  REGIME-BASED STRESS TEST
# ══════════════════════════════════════════════════════════════

REGIME_CONFIGS = [
    ("vol_regime", "high_vol", "high_volatility"),
    ("vol_regime", "low_vol", "low_volatility"),
    ("trend_regime", "bull", "bull_market"),
    ("trend_regime", "bear", "bear_market"),
    ("volume_regime", "high_volume", "high_volume"),
    ("volume_regime", "low_volume", "low_volume"),
]


def _subset_result(symbol: str, name: str, trades: list, rets: np.ndarray, index,
                   bars_per_year: int = 252) -> BacktestResult:
    """BacktestResult for the trades/returns attributed to one regime (equity = compounded regime returns)."""
    init = float(config.BACKTEST_INITIAL_CAPITAL)
    res = BacktestResult(symbol=symbol, pattern_name=name)
    res.bars_per_year = bpy = bars_per_year
    res.trades = list(trades)
    res.total_trades = len(trades)
    eq = init * np.cumprod(1.0 + rets) if len(rets) else np.array([])
    res.equity_curve = pd.Series(eq, index=index) if len(rets) else pd.Series(dtype=float)
    if len(rets):
        full = np.concatenate([[init], eq])
        res.total_return_pct = float(eq[-1] / init - 1.0)
        res.max_drawdown_pct, res.max_drawdown_duration_bars = metrics.max_drawdown(full)
        res.sharpe_ratio = metrics.sharpe(rets, bpy)
        res.sortino_ratio = metrics.sortino(rets, bpy)
        res.cagr = metrics.cagr(rets, bpy)
        res.calmar_ratio = metrics.calmar(rets, full, bars_per_year=bpy) or 0.0
        res.psr = metrics.psr(rets)
    if trades:
        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        res.winning_trades, res.losing_trades = len(wins), len(losses)
        res.win_rate = len(wins) / len(pnls)
        res.win_rate_ci = metrics.wilson_ci(len(wins), len(pnls))
        res.avg_win_pct = float(np.mean(wins)) if wins else 0.0
        res.avg_loss_pct = float(np.mean(losses)) if losses else 0.0
        pf = metrics.profit_factor(pnls)
        if pf is not None:
            res.profit_factor = pf
        elif wins:
            res.profit_factor = metrics.summary([], pnls)["profit_factor_capped"]
            res.profit_factor_undefined = True
        res.expectancy = float(np.mean(pnls))
        res.avg_trade_duration_days = float(np.mean([(t.exit_date - t.entry_date).days for t in trades]))
    return res


def regime_attribution(
    df: pd.DataFrame, symbol: str, pattern_func: Callable, pattern_name: str,
) -> tuple[dict[str, BacktestResult], dict]:
    """
    Run the pattern ONCE on the full frame, then attribute trades (by entry bar) and daily MTM
    returns to regimes. The regime used for bar t is the one known at the prior close (label t-1).
    Returns (results, detail); detail[label] = {n_trades, n_bars, insufficient}.
    """
    results: dict[str, BacktestResult] = {}
    detail: dict[str, dict] = {}
    signals = pattern_func(df)
    full = classic_backtest(signals, symbol, f"{pattern_name}_full")
    results["full_period"] = full

    regimes = detect_regimes(df).shift(1)  # label known before bar t trades
    eq = full.equity_curve
    rets = pd.Series(metrics.equity_returns(eq.to_numpy(), config.BACKTEST_INITIAL_CAPITAL), index=eq.index)

    for col, val, label in REGIME_CONFIGS:
        bar_mask = (regimes[col] == val).to_numpy()
        trades = [t for t in full.trades if 0 <= t.entry_index < len(bar_mask) and bar_mask[t.entry_index]]
        r = rets[bar_mask]
        res = _subset_result(symbol, f"{pattern_name}_{label}", trades, r.to_numpy(), r.index, full.bars_per_year)
        insufficient = len(trades) < MIN_REGIME_TRADES
        res.insufficient = insufficient  # ad-hoc flag read by full_stress_test / UI
        results[label] = res
        detail[label] = {"n_trades": len(trades), "n_bars": int(bar_mask.sum()), "insufficient": insufficient}
        logger.info("  %s: trades=%d bars=%d%s", label, len(trades), int(bar_mask.sum()),
                    " (insufficient)" if insufficient else "")
    return results, detail


def stress_test_regimes(
    df: pd.DataFrame,
    symbol: str,
    pattern_func: Callable,
    pattern_name: str,
) -> dict[str, BacktestResult]:
    """
    Per-regime performance of ONE full-frame run. Regimes with fewer than 10 trades are kept in
    the dict but flagged (`result.insufficient is True`) and must not be read as evidence.
    """
    try:
        return regime_attribution(df, symbol, pattern_func, pattern_name)[0]
    except Exception as e:
        logger.warning(f"Regime stress test failed: {e}")
        return {}


# ══════════════════════════════════════════════════════════════
#  MONTE CARLO SIMULATION (stationary block bootstrap)
# ══════════════════════════════════════════════════════════════

def _mc_returns(backtest_result: BacktestResult) -> tuple[np.ndarray, str]:
    """Daily MTM returns at real size; falls back to per-trade equity-scale returns if no curve."""
    eq = backtest_result.equity_curve
    if eq is not None and len(eq) >= 20:
        return metrics.equity_returns(eq.to_numpy(), config.BACKTEST_INITIAL_CAPITAL), "daily_mtm"
    out = []
    for t in backtest_result.trades:
        frac = t.notional / t.capital_before if t.notional > 0 and t.capital_before > 0 else config.MAX_POSITION_SIZE_PCT
        out.append(frac * t.pnl_pct)
    return np.asarray(out, dtype=float), "trades_at_position_size"


def monte_carlo_analysis(
    backtest_result: BacktestResult,
    n_simulations: int = config.MONTE_CARLO_SIMULATIONS,
    confidence: float = config.MONTE_CARLO_CONFIDENCE,
) -> dict:
    """
    Stationary block bootstrap (arch.bootstrap.StationaryBootstrap, seed config.SEED) of daily
    mark-to-market returns at the ACTUAL position size. Reports bands for total return, CAGR,
    max drawdown and Sharpe. Sharpe is annualised per bar (sqrt(252)), never by trade count.
    """
    from arch.bootstrap import StationaryBootstrap

    if not backtest_result.trades or len(backtest_result.trades) < 5:
        return {"error": "insufficient_trades"}

    rets, source = _mc_returns(backtest_result)
    n = len(rets)
    if n < 5:
        return {"error": "insufficient_trades"}
    bars_per_year = getattr(backtest_result, "bars_per_year", 252) if source == "daily_mtm" else None

    held = [t.bars_held for t in backtest_result.trades if t.bars_held > 0]
    block = int(max(2, round(n ** (1 / 3)), np.median(held) if held else 0))
    block = min(block, max(2, n // 2))
    bs = StationaryBootstrap(block, rets, seed=config.SEED)

    tot, cg, dd, sh = [], [], [], []
    for pos, _ in bs.bootstrap(n_simulations):
        r = np.asarray(pos[0], dtype=float)
        eq = np.cumprod(1.0 + r)
        tot.append(eq[-1] - 1.0)
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        dd.append(float(np.min(eq / peak - 1.0)))
        if bars_per_year:
            c = metrics.cagr(r, bars_per_year)
            cg.append(np.nan if c is None else c)
            sh.append(metrics.sharpe(r, bars_per_year))
        else:  # per-trade fallback: no meaningful calendar annualisation
            cg.append(np.nan)
            sh.append(metrics.sharpe(r, 1))
    tot, cg, dd, sh = (np.asarray(x, dtype=float) for x in (tot, cg, dd, sh))

    alpha = (1 - confidence) / 2 * 100
    hi = 100 - alpha

    def band(x):
        x = x[np.isfinite(x)]
        if not len(x):
            return None, None, None
        return float(np.percentile(x, alpha)), float(np.median(x)), float(np.percentile(x, hi))

    c_lo, c_med, c_hi = band(cg)
    out = {
        "n_simulations": n_simulations,
        "n_trades": len(backtest_result.trades),
        "n_bars": n,
        "method": "stationary_block_bootstrap",
        "returns_source": source,
        "block_size": block,
        "seed": config.SEED,
        "confidence_level": confidence,
        "return_mean": float(np.mean(tot)),
        "return_median": float(np.median(tot)),
        "return_std": float(np.std(tot)),
        "return_ci_low": float(np.percentile(tot, alpha)),
        "return_ci_high": float(np.percentile(tot, hi)),
        "prob_positive": float(np.mean(tot > 0)),
        "return_5th_pct": float(np.percentile(tot, 5)),
        "return_95th_pct": float(np.percentile(tot, 95)),
        "cagr_ci_low": c_lo, "cagr_median": c_med, "cagr_ci_high": c_hi,
        "drawdown_mean": float(np.mean(dd)),
        "drawdown_median": float(np.median(dd)),
        "drawdown_worst_5pct": float(np.percentile(dd, 5)),
        "drawdown_ci_low": float(np.percentile(dd, alpha)),
        "drawdown_ci_high": float(np.percentile(dd, hi)),
        "sharpe_mean": float(np.mean(sh)),
        "sharpe_median": float(np.median(sh)),
        "sharpe_ci_low": float(np.percentile(sh, alpha)),
        "sharpe_ci_high": float(np.percentile(sh, hi)),
    }
    return out


# ══════════════════════════════════════════════════════════════
#  ROBUSTNESS / PARAMETER SENSITIVITY
# ══════════════════════════════════════════════════════════════

def parameter_sensitivity(
    df: pd.DataFrame,
    symbol: str,
    pattern_name: str,
    base_sl: float = config.STOP_LOSS_PCT,
    base_tp: float = config.TAKE_PROFIT_PCT,
    pattern_func: Optional[Callable] = None,
    holdout_start: Optional[str] = None,
) -> pd.DataFrame:
    """
    SL/TP grid on the period BEFORE the hold-out (never touches hold-out bars). Per grid point:
    full pre-hold-out metrics plus `oos_sharpe` / `oos_trades` over the last OOS_FRACTION of that
    period (simple validation-free split; signals are causal so one run serves both segments).
    `df.attrs` carries n_trials, share_positive_oos_sharpe and neighbour share.
    Empty frame (attrs["reason"]) if there are too few pre-hold-out bars.
    """
    if pattern_func is None:
        from patterns import PATTERN_REGISTRY
        if pattern_name not in PATTERN_REGISTRY:
            return pd.DataFrame()
        pattern_func = PATTERN_REGISTRY[pattern_name]

    cut = pd.Timestamp(holdout_start or config.HOLDOUT_START)
    idx = df.index
    if idx.tz is not None:
        cut = cut.tz_localize(idx.tz)
    pre = df[idx < cut]
    if len(pre) < 100:
        out = pd.DataFrame()
        out.attrs.update({"reason": "insufficient_pre_holdout_bars", "n_trials": 0})
        return out

    signals = pattern_func(pre)
    oos_start = int(len(pre) * (1 - OOS_FRACTION))

    sl_mults = np.arange(0.5, 2.5, 0.25)
    rows = []
    for sm in sl_mults:
        for tm in sl_mults:
            sl, tp = base_sl * sm, base_tp * tm
            result = classic_backtest(signals, symbol, pattern_name, stop_loss=sl, take_profit=tp)
            eq = result.equity_curve.to_numpy()
            r = metrics.equity_returns(eq, config.BACKTEST_INITIAL_CAPITAL)
            oos_r = r[oos_start:]
            oos_trades = sum(1 for t in result.trades if t.entry_index >= oos_start)
            rows.append({
                "stop_loss": sl, "take_profit": tp, "rr_ratio": tp / sl,
                "sl_mult": float(sm), "tp_mult": float(tm),
                "win_rate": result.win_rate, "profit_factor": result.profit_factor,
                "total_return": result.total_return_pct, "sharpe": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown_pct, "total_trades": result.total_trades,
                "oos_sharpe": metrics.sharpe(oos_r), "oos_trades": oos_trades,
            })
    out = pd.DataFrame(rows)
    near = (np.abs(out["sl_mult"] - 1) <= 0.25) & (np.abs(out["tp_mult"] - 1) <= 0.25)
    out.attrs.update({
        "n_trials": len(out),
        "share_positive_oos_sharpe": float((out["oos_sharpe"] > 0).mean()),
        "share_positive_oos_sharpe_neighbours": float((out.loc[near, "oos_sharpe"] > 0).mean()),
        "holdout_start": str(cut.date()),
        "oos_bars": int(len(pre) - oos_start),
    })
    return out


# ══════════════════════════════════════════════════════════════
#  FULL STRESS TEST SUITE
# ══════════════════════════════════════════════════════════════

def full_stress_test(
    df: pd.DataFrame,
    symbol: str,
    pattern_func: Callable,
    pattern_name: str,
) -> dict:
    """Run the complete stress test suite. Existing keys are unchanged; new keys are additive."""
    report = {"symbol": symbol, "pattern": pattern_name}

    # Regimes and Monte Carlo only ever see bars BEFORE the fixed hold-out date (D11: the hold-out is not
    # displayed or tuned against); parameter_sensitivity applies the same cut itself.
    import validation
    full_df = df
    df = full_df.iloc[:validation.holdout_position(full_df)]
    report["holdout_excluded_from"] = config.HOLDOUT_START

    logger.info("  Running regime stress test...")
    try:
        regime_results, regime_detail = regime_attribution(df, symbol, pattern_func, pattern_name)
    except Exception as e:
        logger.warning(f"Regime stress test failed: {e}")
        regime_results, regime_detail = {}, {}
    # numeric (fraction) metrics; the old formatted strings live under `metrics_display` (WS2.7)
    report["regimes"] = {
        name: {**result.metrics_payload(), "insufficient": bool(getattr(result, "insufficient", False)),
               "metrics_display": result.summary()}
        for name, result in regime_results.items()
    }
    report["regime_detail"] = regime_detail

    full_result = regime_results.get("full_period")
    if full_result and full_result.total_trades >= 5:
        logger.info(f"  Running Monte Carlo simulation ({config.MONTE_CARLO_SIMULATIONS} sims)...")
        report["monte_carlo"] = monte_carlo_analysis(full_result)
    else:
        report["monte_carlo"] = {"error": "insufficient_trades"}

    logger.info("  Running parameter sensitivity...")
    sensitivity = parameter_sensitivity(full_df, symbol, pattern_name, pattern_func=pattern_func)
    if not sensitivity.empty:
        std = float(sensitivity["sharpe"].std())
        share = sensitivity.attrs["share_positive_oos_sharpe"]
        cols = ["stop_loss", "take_profit", "rr_ratio", "win_rate", "profit_factor", "total_return",
                "sharpe", "max_drawdown", "total_trades", "oos_sharpe"]
        report["sensitivity"] = {
            "best_params": sensitivity.sort_values("sharpe", ascending=False).head(3)[cols].to_dict("records"),
            "worst_params": sensitivity.sort_values("sharpe").head(3)[cols].to_dict("records"),
            "sharpe_std_across_params": std if np.isfinite(std) else 0.0,
            "is_robust": bool(std < 1.0 and share >= 0.5),
            "n_trials": sensitivity.attrs["n_trials"],
            "share_positive_oos_sharpe": share,
            "share_positive_oos_sharpe_neighbours": sensitivity.attrs["share_positive_oos_sharpe_neighbours"],
            "holdout_start": sensitivity.attrs["holdout_start"],
        }
    else:
        report["sensitivity"] = {"error": sensitivity.attrs.get("reason", "no_data"), "n_trials": 0,
                                 "is_robust": False}

    mc = report.get("monte_carlo", {})
    report["assessment"] = {
        "regimes_consistent": _check_regime_consistency(regime_results),
        "mc_prob_positive": mc.get("prob_positive", 0),
        "mc_worst_case_5pct": mc.get("return_5th_pct", 0),
        "param_robust": report["sensitivity"].get("is_robust", False),
    }
    return report


def _check_regime_consistency(results: dict[str, BacktestResult]) -> bool:
    """Check if strategy works across most regimes."""
    profitable_regimes = 0
    total_regimes = 0

    for name, result in results.items():
        if name == "full_period" or getattr(result, "insufficient", False):
            continue
        total_regimes += 1
        if result.total_return_pct > 0 and result.profit_factor > 1.0:
            profitable_regimes += 1

    if total_regimes == 0:
        return False

    return (profitable_regimes / total_regimes) >= 0.5
