"""
Statistical Arbitrage / Pairs Trading (WS2.6)

Pipeline
--------
1. ``compute_spread_stats``  causal rolling OLS of log(A) on log(B) (with intercept).
2. ``analyze_pair``          Engle-Granger on log prices (both orderings, max p), half-life,
                             return correlation, registry-derived bars_per_year / executability.
3. ``generate_pair_signals`` z-score entries, exits and a z stop that is FROZEN at entry.
4. ``backtest_pair``         two-leg daily engine (beta-weighted gross), costs on both legs,
                             time stop, open trades marked at the end.
5. ``scan_all_pairs``        runs 2-4 over the default pairs and applies Benjamini-Hochberg across
                             every pair scanned.

Conventions (read before trusting a number)
-------------------------------------------
* Causality: for bar t the hedge parameters (beta_t, alpha_t) and the residual scale sigma_t are
  fitted on the ``window`` bars ending at t-1.  The spread uses bar t's prices:
  ``spread_t = logA_t - alpha_t - beta_t * logB_t`` and ``z_t = spread_t / sigma_t``.
  Nothing after bar t enters row t (tested bit-for-bit by appending random bars).
* Timing: the signal at bar t is decided from the close of bar t and the position is assumed filled
  at that close (market-on-close), so it earns the return of bar t+1:
  ``ret[t] = pos[t-1] * (w_a*r_a[t] - sign(b)*w_b*r_b[t])``.  A signal on the final bar is never
  executed.  This is mildly optimistic versus a next-open fill.
* Weights are BETA-WEIGHTED GROSS NORMALISATION: ``w_a = 1/(1+|b|)``, ``w_b = |b|/(1+|b|)``,
  ``w_a + w_b = 1`` (the return is a return on gross exposure).  Leg B's dollar exposure is |b|
  times leg A's (the hedge ratio from log prices is an elasticity), so the book is dollar-neutral
  only when |b| = 1.  b is frozen at entry for the life of a trade.
* Returns are on gross: multiplying both price series by k leaves them unchanged; ADDING a constant
  to both series does not (a 1100 -> 1110 move is 0.9%, not 10%).  Equity return per bar is
  ``gross_fraction * strategy_return`` with gross_fraction = config.MAX_POSITION_SIZE_PCT.
* Costs: ``commission + slippage`` per side per leg, charged on the turnover of both legs (entry
  = 1.0 of gross, exit = 1.0 of gross) on the bar where the position changes.  The final open
  position is liquidated (and charged) at the last close.
* Sharpe uses rf = 0 on daily mark-to-market equity returns, annualised with the instrument
  registry's ``bars_per_year`` (252 equities, 365 crypto).
* Time stop: ``ceil(PAIRS_TIME_STOP_HALF_LIVES * half_life)`` bars.  It lives in ``backtest_pair``
  (not in the signal) because it needs the full-sample half-life from ``analyze_pair``; keeping it
  out of ``generate_pair_signals`` keeps the signals causal.  Phase 4 replaces the full-sample
  half-life with a walk-forward estimate.
* Pairs are a diagnostic only (D3: nothing is shortable).  Legs that are not executable
  equities/ETFs are labelled ``non_executable``.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from statsmodels.regression.linear_model import OLS
from statsmodels.stats.multitest import multipletests
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import adfuller, coint

import config
import instruments
import metrics

logger = logging.getLogger(__name__)

MIN_RETURN_CORR = 0.3  # a cointegrated pair whose daily returns are uncorrelated is not a hedge
_EPS = 1e-12


class InsufficientData(ValueError):
    """Fewer than PAIRS_MIN_ALIGNED_BARS aligned bars (or non-positive prices)."""


@dataclass
class PairAnalysis:
    """Results of pair cointegration analysis (all fields plain Python types)."""
    symbol_a: str
    symbol_b: str
    is_cointegrated: bool            # single-test p < PAIRS_COINT_PVALUE; scan_all_pairs overrides with BH
    coint_pvalue: float              # max over the two Engle-Granger orderings
    hedge_ratio: float               # full-sample OLS slope of logA on logB (reporting only)
    half_life: Optional[float]       # bars; None unless 0 < phi < 1
    spread_mean: float               # in-window mean of the last causal residuals (0 by construction)
    spread_std: float                # last in-window residual std
    current_zscore: Optional[float]  # last causal z (same series the signal/backtest use)
    adf_stat: Optional[float] = None     # informational only (fitted residuals => invalid p); NOT a gate
    adf_pvalue: Optional[float] = None   # informational only
    correlation: float = 0.0         # correlation of daily log returns
    n_obs: int = 0
    bars_per_year: int = 252
    adj_pvalue: Optional[float] = None   # BH-adjusted p across the scanned pairs
    non_executable: bool = True
    non_executable_reason: str = ""


# ----------------------------------------------------------------------------- spread stats
def compute_spread_stats(log_a, log_b, window: Optional[int] = None) -> pd.DataFrame:
    """Causal rolling OLS ``logA = alpha + beta*logB`` with an intercept.

    Row t uses ONLY bars t-window..t-1 to fit (alpha_t, beta_t, sigma_t); the spread and z use bar
    t's prices.  Returns a DataFrame with columns beta, alpha, spread, sigma, z (NaN during the
    first ``window`` bars or when a window is degenerate).  Row t is bit-identical whatever
    follows bar t.
    """
    w = int(window or config.PAIRS_ROLLING_WINDOW)
    if w < 5:
        raise ValueError("window must be >= 5")
    la = pd.Series(log_a, dtype=float)
    lb = pd.Series(log_b, dtype=float)
    if not la.index.equals(lb.index):
        raise ValueError("log_a and log_b must share an index")
    y = la.to_numpy()
    x = lb.to_numpy()
    n = len(y)
    out = np.full((n, 5), np.nan)
    if n > w:
        wx = sliding_window_view(x, w)[: n - w]   # window i covers bars i..i+w-1 -> row t=i+w
        wy = sliding_window_view(y, w)[: n - w]
        xm = wx.mean(axis=1)
        ym = wy.mean(axis=1)
        xc = wx - xm[:, None]
        yc = wy - ym[:, None]
        sxx = (xc * xc).sum(axis=1)
        sxy = (xc * yc).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            beta = np.where(sxx > _EPS, sxy / sxx, np.nan)
            alpha = ym - beta * xm
            resid = yc - beta[:, None] * xc
            sigma = np.sqrt((resid * resid).sum(axis=1) / (w - 2))
            spread = y[w:] - alpha - beta * x[w:]
            z = np.where(sigma > _EPS, spread / sigma, np.nan)
        out[w:, 0], out[w:, 1], out[w:, 2], out[w:, 3], out[w:, 4] = beta, alpha, spread, sigma, z
    return pd.DataFrame(out, index=la.index, columns=["beta", "alpha", "spread", "sigma", "z"])


def compute_hedge_ratio(prices_a: pd.Series, prices_b: pd.Series) -> float:
    """Full-sample OLS slope of log(A) on log(B). Reporting only; signals use rolling stats."""
    la, lb = np.log(prices_a.astype(float)), np.log(prices_b.astype(float))
    return float(OLS(la.to_numpy(), add_constant(lb.to_numpy())).fit().params[1])


def compute_spread(prices_a: pd.Series, prices_b: pd.Series, hedge_ratio: float) -> pd.Series:
    """Log-price spread with a FIXED hedge ratio and the sample-mean intercept removed (diagnostic)."""
    s = np.log(prices_a.astype(float)) - hedge_ratio * np.log(prices_b.astype(float))
    return s - s.mean()


def compute_half_life(spread: pd.Series) -> Optional[float]:
    """-ln(2)/ln(phi) from the AR(1) fit s_t = c + phi*s_{t-1}; None unless 0 < phi < 1."""
    s = np.asarray(pd.Series(spread).dropna(), dtype=float)
    if len(s) < 20:
        return None
    try:
        phi = float(OLS(s[1:], add_constant(s[:-1])).fit().params[1])
    except Exception:
        return None
    if not (0.0 < phi < 1.0):
        return None
    return float(-math.log(2) / math.log(phi))


# ----------------------------------------------------------------------------- registry helpers
def _leg_info(symbol: str):
    try:
        return instruments.get(symbol)
    except instruments.UnknownSymbol:
        return None


def pair_bars_per_year(sym_a: str, sym_b: str) -> int:
    """Annualisation factor from the instrument registry (min of the legs; 252 if unknown)."""
    vals = []
    for s in (sym_a, sym_b):
        inst = _leg_info(s)
        vals.append(inst.bars_per_year if inst else 252)
    return int(min(vals))


def pair_executability(sym_a: str, sym_b: str) -> tuple[bool, str]:
    """(non_executable, reason). Any leg that is not an executable equity/ETF makes the pair non_executable."""
    reasons = []
    for s in (sym_a, sym_b):
        inst = _leg_info(s)
        if inst is None:
            reasons.append(f"{s}: not in instrument registry")
        elif not inst.executable:
            reasons.append(f"{s}: {inst.asset_class} is research/alert only")
    return bool(reasons), "; ".join(reasons)


# ----------------------------------------------------------------------------- analysis
def _align(df_a: pd.DataFrame, df_b: pd.DataFrame):
    common = df_a.index.intersection(df_b.index)
    return df_a.loc[common], df_b.loc[common]


def analyze_pair(df_a: pd.DataFrame, df_b: pd.DataFrame, symbol_a: str, symbol_b: str) -> PairAnalysis:
    """Cointegration diagnostics for a pair (log prices, Engle-Granger both orderings, max p).

    Raises InsufficientData with fewer than config.PAIRS_MIN_ALIGNED_BARS aligned bars.
    """
    df_a, df_b = _align(df_a, df_b)
    n = len(df_a)
    if n < config.PAIRS_MIN_ALIGNED_BARS:
        raise InsufficientData(f"{n} aligned bars < {config.PAIRS_MIN_ALIGNED_BARS}")
    pa, pb = df_a["Close"].astype(float), df_b["Close"].astype(float)
    if not ((pa > 0).all() and (pb > 0).all() and np.isfinite(pa).all() and np.isfinite(pb).all()):
        raise InsufficientData("non-positive or non-finite prices")
    la, lb = np.log(pa), np.log(pb)

    p_ab = float(coint(la, lb)[1])
    p_ba = float(coint(lb, la)[1])
    pvalue = max(p_ab, p_ba)

    fit = OLS(la.to_numpy(), add_constant(lb.to_numpy())).fit()
    alpha_f, beta_f = float(fit.params[0]), float(fit.params[1])
    spread_full = la - alpha_f - beta_f * lb
    half_life = compute_half_life(spread_full)

    adf_stat = adf_p = None  # informational only: residual-based ADF p-values are not valid
    try:
        res = adfuller(spread_full.to_numpy(), maxlag=20, result_object=False)
        adf_stat, adf_p = float(res[0]), float(res[1])
    except Exception:
        pass

    rets = pd.concat([la.diff(), lb.diff()], axis=1).dropna()
    corr = float(rets.iloc[:, 0].corr(rets.iloc[:, 1])) if len(rets) > 2 else 0.0
    if not math.isfinite(corr):
        corr = 0.0

    stats = compute_spread_stats(la, lb)
    last = stats.iloc[-1]
    z_last = float(last["z"]) if math.isfinite(last["z"]) else None
    non_exec, reason = pair_executability(symbol_a, symbol_b)
    return PairAnalysis(
        symbol_a=symbol_a, symbol_b=symbol_b,
        is_cointegrated=bool(pvalue < config.PAIRS_COINT_PVALUE),
        coint_pvalue=float(pvalue), hedge_ratio=beta_f, half_life=half_life,
        spread_mean=0.0, spread_std=float(last["sigma"]) if math.isfinite(last["sigma"]) else 0.0,
        current_zscore=z_last, adf_stat=adf_stat, adf_pvalue=adf_p, correlation=corr,
        n_obs=int(n), bars_per_year=pair_bars_per_year(symbol_a, symbol_b),
        non_executable=non_exec, non_executable_reason=reason,
    )


def is_valid_pair(analysis: PairAnalysis) -> bool:
    """Cointegrated (BH-adjusted when scanned), half-life inside its band, returns correlated.

    No ADF gate on the fitted residuals (invalid p-values; Engle-Granger already covers it).
    """
    hl = analysis.half_life
    return bool(
        analysis.is_cointegrated
        and hl is not None
        and config.PAIRS_MIN_HALF_LIFE <= hl <= config.PAIRS_MAX_HALF_LIFE
        and abs(analysis.correlation) >= MIN_RETURN_CORR
    )


# ----------------------------------------------------------------------------- signals
def generate_pair_signals(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    analysis: Optional[PairAnalysis] = None,
    lookback: Optional[int] = None,
) -> pd.DataFrame:
    """Causal signals for a pair.

    Columns: price_a, price_b, beta, alpha, spread, zscore, signal, hedge_beta, z_frozen, exit_reason.
    ``signal`` is the desired position AFTER bar t's close: 1 = long spread (buy A, sell B),
    -1 = short spread, 0 = flat.  ``analysis`` is accepted for API compatibility but not used: every
    number here depends only on bars up to t (so the output is causal under truncation).

    Entry: |z_t| in (PAIRS_ZSCORE_ENTRY, PAIRS_ZSCORE_STOP).  After entry the exit rules use z
    FROZEN at entry (z_f = (logA - alpha_e - beta_e*logB)/sigma_e): reversion when z_f is back
    within PAIRS_ZSCORE_EXIT of zero, stop when z_f passes PAIRS_ZSCORE_STOP on the adverse side.
    A rolling z is blind to a drifting spread (its mean follows the drift), the frozen one is not.
    """
    df_a, df_b = _align(df_a, df_b)
    pa, pb = df_a["Close"].astype(float), df_b["Close"].astype(float)
    la, lb = np.log(pa), np.log(pb)
    st = compute_spread_stats(la, lb, lookback)
    z = st["z"].to_numpy()
    beta, alpha, sigma = st["beta"].to_numpy(), st["alpha"].to_numpy(), st["sigma"].to_numpy()
    xa, xb = la.to_numpy(), lb.to_numpy()
    n = len(st)

    entry, exit_, stop = config.PAIRS_ZSCORE_ENTRY, config.PAIRS_ZSCORE_EXIT, config.PAIRS_ZSCORE_STOP
    signal = np.zeros(n, dtype=int)
    hedge_beta = np.full(n, np.nan)
    z_frozen = np.full(n, np.nan)
    reason = [""] * n

    pos = 0
    a_e = b_e = s_e = 0.0
    for t in range(n):
        if pos == 0:
            zt = z[t]
            if np.isfinite(zt) and entry < abs(zt) < stop:
                pos = 1 if zt < 0 else -1
                a_e, b_e, s_e = alpha[t], beta[t], sigma[t]
                z_frozen[t] = zt
        else:
            zf = (xa[t] - a_e - b_e * xb[t]) / s_e
            z_frozen[t] = zf
            if pos == 1:
                if zf < -stop:
                    pos, reason[t] = 0, "stop_z"
                elif zf >= -exit_:
                    pos, reason[t] = 0, "reversion"
            else:
                if zf > stop:
                    pos, reason[t] = 0, "stop_z"
                elif zf <= exit_:
                    pos, reason[t] = 0, "reversion"
        signal[t] = pos
        if pos != 0:
            hedge_beta[t] = b_e

    out = pd.DataFrame(index=df_a.index)
    out["price_a"], out["price_b"] = pa, pb
    out["beta"], out["alpha"] = beta, alpha
    out["spread"], out["zscore"] = st["spread"].to_numpy(), z
    out["signal"] = signal
    out["hedge_beta"], out["z_frozen"] = hedge_beta, z_frozen
    out["exit_reason"] = reason
    return out


# ----------------------------------------------------------------------------- backtest
def _max_hold_bars(half_life: Optional[float]) -> int:
    hl = half_life if (half_life is not None and math.isfinite(half_life) and half_life > 0) \
        else float(config.PAIRS_MAX_HALF_LIFE)
    return max(1, int(math.ceil(config.PAIRS_TIME_STOP_HALF_LIVES * hl)))


def _apply_time_stop(raw: np.ndarray, max_hold: Optional[int]):
    """Positions after the time stop. A forced exit blocks re-entry until the raw signal resets."""
    n = len(raw)
    pos = np.zeros(n, dtype=int)
    forced = np.zeros(n, dtype=bool)
    prev_raw, age, blocked = 0, 0, False
    for t in range(n):
        r = int(raw[t])
        if r != prev_raw:
            age, blocked = 0, False
        elif r != 0:
            age += 1
        if r == 0:
            pos[t] = 0
        elif blocked:
            pos[t] = 0
        elif max_hold is not None and age >= max_hold:
            pos[t], blocked, forced[t] = 0, True, True
        else:
            pos[t] = r
        prev_raw = r
    return pos, forced


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def backtest_pair(
    signals_df: pd.DataFrame,
    analysis: PairAnalysis,
    initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
    commission: float = config.COMMISSION_PCT,
    slippage: float = config.SLIPPAGE_PCT,
    gross_fraction: Optional[float] = None,
    bars_per_year: Optional[int] = None,
    time_stop: bool = True,
) -> dict:
    """Two-leg daily backtest of the signals (conventions in the module docstring).

    ``signals_df`` needs ``signal`` and ``zscore``; with ``price_a``/``price_b`` the legs are
    marked to market, otherwise (hand-built frames) the log-spread change stands in:
    ``ret = diff(spread) / (1 + |b|)``.  Returns a dict of plain Python values (None, never inf/NaN).
    """
    df = signals_df.dropna(subset=["zscore"]).copy()
    if len(df) < 20:
        return {"error": "insufficient_data"}
    n = len(df)
    gf = float(config.MAX_POSITION_SIZE_PCT if gross_fraction is None else gross_fraction)
    bpy = int(bars_per_year or getattr(analysis, "bars_per_year", 252) or 252)
    rate = float(commission) + float(slippage)

    raw = df["signal"].to_numpy(dtype=int)
    if "hedge_beta" in df.columns:
        hb = df["hedge_beta"].ffill().fillna(analysis.hedge_ratio).to_numpy(dtype=float)
    else:
        hb = np.full(n, float(analysis.hedge_ratio))
    abs_b = np.abs(hb)
    w_a = 1.0 / (1.0 + abs_b)
    w_b = abs_b / (1.0 + abs_b)
    sgn_b = np.where(hb >= 0, 1.0, -1.0)

    if {"price_a", "price_b"} <= set(df.columns):
        r_a = df["price_a"].pct_change().fillna(0.0).to_numpy()
        r_b = df["price_b"].pct_change().fillna(0.0).to_numpy()
        leg_ret = w_a * r_a - sgn_b * w_b * r_b          # indexed by the bar the return happens on
    else:
        leg_ret = np.zeros(n)
        leg_ret[1:] = np.diff(df["spread"].to_numpy(dtype=float)) / (1.0 + abs_b[1:])

    max_hold = _max_hold_bars(analysis.half_life) if time_stop else None
    pos, forced = _apply_time_stop(raw, max_hold)
    if n >= 2 and pos[-1] != 0 and pos[-2] == 0:
        pos[-1] = 0                                       # a signal on the final bar is never executed

    prev = np.concatenate([[0], pos[:-1]])
    gross = prev * leg_ret                                # pos[t-1] * (w_a r_a - w_b r_b)
    cost = rate * np.abs(pos - prev)
    net = gross - cost
    liquidation = pos[-1] != 0
    if liquidation:                                       # open trade marked (and closed) at the last close
        net[-1] -= rate

    equity = initial_capital * np.cumprod(1.0 + gf * net)

    # ---- trades
    reasons_col = df["exit_reason"].to_numpy() if "exit_reason" in df.columns else np.array([""] * n)
    trades = []
    t_e = None
    for t in range(n):
        p_now, p_prev = pos[t], (pos[t - 1] if t else 0)
        if p_now != 0 and p_prev == 0:
            t_e = t
        if t_e is not None and ((p_now == 0 and p_prev != 0) or (t == n - 1 and p_now != 0)):
            ended_open = p_now != 0
            segment = net[t_e:t + 1]
            ret = float(np.prod(1.0 + segment) - 1.0)
            cap_before = float(equity[t_e - 1]) if t_e > 0 else float(initial_capital)
            if ended_open:
                why = "end_of_data"
            elif forced[t]:
                why = "time_stop"
            else:
                why = str(reasons_col[t]) or "signal"
            trades.append({
                "entry_date": df.index[t_e].isoformat() if hasattr(df.index[t_e], "isoformat") else str(df.index[t_e]),
                "exit_date": df.index[t].isoformat() if hasattr(df.index[t], "isoformat") else str(df.index[t]),
                "direction": int(pos[t_e]),
                "entry_idx": int(t_e), "exit_idx": int(t), "bars_held": int(t - t_e),
                "hedge_beta": float(hb[t_e]),
                "pnl_pct": ret,                           # net, on gross exposure
                "pnl_abs": cap_before * gf * ret,
                "capital_before": cap_before,
                "exit_reason": why,
            })
            t_e = None

    pnls = [tr["pnl_pct"] for tr in trades]
    m = metrics.summary(equity, pnls, [tr["pnl_abs"] for tr in trades],
                        [tr["capital_before"] for tr in trades], initial_capital, bpy)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    z_last = _f(df["zscore"].iloc[-1])
    reason_counts: dict = {}
    for tr in trades:
        reason_counts[tr["exit_reason"]] = reason_counts.get(tr["exit_reason"], 0) + 1

    return {
        "symbol_a": analysis.symbol_a,
        "symbol_b": analysis.symbol_b,
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "profit_factor": m["profit_factor"],              # None when there are no losses
        "total_return": m["total_return"],                # fractions
        "max_drawdown": m["max_drawdown"],                # negative fraction
        "sharpe_ratio": m["sharpe"],
        "cagr": m["cagr"],
        "psr": m["psr"],
        "half_life": _f(analysis.half_life),
        "correlation": _f(analysis.correlation),
        "coint_pvalue": _f(analysis.coint_pvalue),
        "current_zscore": z_last,                         # last causal z == what /scan reports
        "bars_per_year": bpy,
        "n_bars": int(n),
        "exit_reasons": reason_counts,
        "non_executable": bool(getattr(analysis, "non_executable", True)),
        "trades": trades,
    }


# ----------------------------------------------------------------------------- scan
def scan_all_pairs(
    data: dict[str, pd.DataFrame],
    pairs: Optional[list[tuple]] = None,
) -> list[dict]:
    """Scan pairs (default: instruments.default_pairs()) and return the VALID ones.

    Multiple-testing control: Benjamini-Hochberg at config.FDR_ALPHA across EVERY pair that had
    enough aligned bars (valid or not).  ``is_cointegrated`` on a scanned analysis is the BH
    decision.  Results carry ``adj_pvalue`` and ``n_tested``.
    """
    pairs = list(pairs) if pairs else instruments.default_pairs()
    analysed: list[tuple[PairAnalysis, pd.DataFrame, pd.DataFrame]] = []

    for sym_a, sym_b in pairs:
        if sym_a not in data or sym_b not in data:
            logger.info("  Skipping %s/%s: data missing", sym_a, sym_b)
            continue
        try:
            df_a, df_b = _align(data[sym_a], data[sym_b])
            analysis = analyze_pair(df_a, df_b, sym_a, sym_b)
        except InsufficientData as e:
            logger.info("  Skipping %s/%s: %s", sym_a, sym_b, e)
            continue
        except Exception as e:
            logger.warning("  Error analyzing %s/%s: %s", sym_a, sym_b, e)
            continue
        analysed.append((analysis, df_a, df_b))

    if not analysed:
        return []
    pvals = np.array([a.coint_pvalue for a, _, _ in analysed])
    reject, adj, _, _ = multipletests(pvals, alpha=config.FDR_ALPHA, method="fdr_bh")
    results = []
    for (analysis, df_a, df_b), rej, padj in zip(analysed, reject, adj):
        analysis.is_cointegrated = bool(rej)
        analysis.adj_pvalue = float(padj)
        if not is_valid_pair(analysis):
            logger.info("  %s/%s: NOT valid (p=%.4f adj=%.4f hl=%s corr=%.3f)", analysis.symbol_a, analysis.symbol_b,
                        analysis.coint_pvalue, padj, analysis.half_life, analysis.correlation)
            continue
        try:
            signals = generate_pair_signals(df_a, df_b, analysis)
            bt = backtest_pair(signals, analysis)
        except Exception as e:
            logger.warning("  Error backtesting %s/%s: %s", analysis.symbol_a, analysis.symbol_b, e)
            continue
        if "error" in bt:
            continue
        z = bt["current_zscore"]
        bt["has_signal"] = bool(z is not None and abs(z) > config.PAIRS_ZSCORE_ENTRY
                                and abs(z) < config.PAIRS_ZSCORE_STOP)
        bt["signal_direction"] = ("LONG_SPREAD" if bt["has_signal"] and z < 0
                                  else "SHORT_SPREAD" if bt["has_signal"] else "NONE")
        bt["adj_pvalue"] = float(padj)
        bt["n_tested"] = len(analysed)
        bt["hedge_ratio"] = float(analysis.hedge_ratio)
        bt["non_executable_reason"] = analysis.non_executable_reason
        results.append(bt)
    return results
