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
  out of ``generate_pair_signals`` keeps the signals causal.  This single-window path is IN-SAMPLE
  (full-sample half-life, signal-close fills, daily-rebalanced legs): the WS4.5 ``walk_forward_pairs``
  engine at the end of this module is the OOS one (walk-forward half-life, next-open fills, fixed-share
  legs, rolling cointegration guard) and is what scan / analyze report.
* Pairs are a diagnostic only (D3: nothing is shortable).  Legs that are not executable
  equities/ETFs are labelled ``non_executable``.
"""

import contextlib
import contextvars
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
_ACTIVE_TRIALS: contextvars.ContextVar = contextvars.ContextVar("pairs_active_trials", default=None)


@contextlib.contextmanager
def record_trials(registry):
    """Within the block, ``scan_all_pairs`` / ``walk_forward_pairs`` called WITHOUT an explicit ``trials``
    record into ``registry`` (keeps the two-argument ``scan_all_pairs(data, pairs)`` call shape intact)."""
    token = _ACTIVE_TRIALS.set(registry)
    try:
        yield registry
    finally:
        _ACTIVE_TRIALS.reset(token)


def scan_all_pairs(
    data: dict[str, pd.DataFrame],
    pairs: Optional[list[tuple]] = None,
    trials=None,
) -> list[dict]:
    """Scan pairs (default: instruments.default_pairs()) and return the VALID ones.

    Multiple-testing control: Benjamini-Hochberg at config.FDR_ALPHA across EVERY pair that had
    enough aligned bars (valid or not).  ``is_cointegrated`` on a scanned analysis is the BH
    decision.  Results carry ``adj_pvalue`` and ``n_tested``.

    WS4.5: every analysed pair is walk-forward backtested (``walk_forward_pairs``) and, when ``trials`` is
    given, its OOS return series is recorded as one trial (valid or not, so N is honest).  A pair is valid
    only if the cointegration screen above passes AND ``is_valid_oos``; the ``backtest`` numbers it reports
    are the stitched OOS ones (never the full-sample in-sample backtest).  Pairs stay alert-only (D3).
    """
    pairs = list(pairs) if pairs else instruments.default_pairs()
    if trials is None:
        trials = _ACTIVE_TRIALS.get()
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
        try:
            wf = walk_forward_pairs(df_a, df_b, analysis.symbol_a, analysis.symbol_b, trials=trials)
        except InsufficientData:
            continue
        except Exception as e:
            if trials is not None:
                raise                                   # a registry failure must not drop a trial silently
            logger.warning("  Error walk-forwarding %s/%s: %s", analysis.symbol_a, analysis.symbol_b, e)
            continue
        if not is_valid_pair(analysis) or not is_valid_oos(wf):
            logger.info("  %s/%s: NOT valid (p=%.4f adj=%.4f hl=%s corr=%.3f oos_blocks=%d)", analysis.symbol_a,
                        analysis.symbol_b, analysis.coint_pvalue, padj, analysis.half_life, analysis.correlation,
                        wf.n_blocks)
            continue
        bt = dict(wf.backtest)
        bt["half_life"] = _f(analysis.half_life)
        bt["correlation"] = _f(analysis.correlation)
        bt["coint_pvalue"] = _f(analysis.coint_pvalue)
        bt["bars_per_year"] = int(analysis.bars_per_year)
        bt["non_executable"] = bool(analysis.non_executable)
        bt["current_zscore"] = _f(analysis.current_zscore)  # last causal z (the live alert), not an OOS number
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
    if trials is not None and trials.n_trials:
        trials.flush()
    return results


# ============================================================================= walk-forward (WS4.5)
# Walk-forward pairs engine (WS4.5, the D11-deferred pairs accounting).
#
# Blocks: ``formation`` bars fit the parameters, the next ``trade`` bars trade them OUT OF SAMPLE, the
# window then advances by ``step`` (``step >= trade`` so OOS windows never overlap).  Everything a block
# uses is frozen from its formation window: hedge ratio (beta, alpha), half-life, z lookback
# (``clamp(round(PAIRS_Z_LOOKBACK_HL_MULT * hl), 20, 120)``), time stop (``PAIRS_TIME_STOP_HALF_LIVES * hl``)
# and the tradable flag (formation Engle-Granger max-p < PAIRS_COINT_PVALUE, half-life in band, return
# correlation).  A block only reads bars up to its own last trade bar, so later data cannot change an
# earlier block; the OOS return series is the stitched trade windows (days with no position are 0).
#
# Execution: the signal at the close of bar t is filled at the OPEN of bar t+1 (``Open`` column, ``Close``
# if absent).  Legs are FIXED SHARES set at the fill (dollar weights ``1/(1+|b|)`` and ``|b|/(1+|b|)`` of
# ``gross_fraction * equity``), never rebalanced; costs ``commission + slippage`` are charged on both legs
# at entry and exit.  Equity is cash plus shares marked at the close, so dollar P&L is a sum of
# ``shares * price change`` (invariant to an additive shift of the price level for given shares).  A trade
# still open on the last bar of a block is closed at that bar's close (``block_end``) and a signal on that
# bar is never opened.
#
# Breakdown guard: every ``PAIRS_RETEST_EVERY`` bars an Engle-Granger re-test (both orderings, max p) and a
# half-life are computed on the trailing ``retest_window`` bars.  A block is HEALTHY at the start iff its
# formation is tradable.  Healthy -> unhealthy when p > PAIRS_BREAK_PVALUE (0.15) or the half-life leaves
# its band; unhealthy -> healthy only when p < PAIRS_COINT_PVALUE (0.05) and the half-life is back in band
# (hysteresis).  Unhealthy forces an open trade out (``coint_break``) and blocks entries.  A ``time_stop``
# exit means the frozen parameters did not revert in time and blocks further entries in that block.
PAIRS_BREAK_PVALUE = 0.15         # re-test p above this breaks a trade (hysteresis vs PAIRS_COINT_PVALUE)
PAIRS_RETEST_EVERY = 5            # bars between rolling EG re-tests
PAIRS_RETEST_WINDOW = 252         # trailing bars used by the re-test (EG has little power below ~250 bars)
PAIRS_Z_LOOKBACK_HL_MULT = 2.0    # z lookback = clamp(round(mult * half_life), MIN, MAX)
PAIRS_Z_LOOKBACK_MIN = 20
PAIRS_Z_LOOKBACK_MAX = 120
WF_FORMATION, WF_TRADE, WF_STEP = 252, 63, 63
WF_PATTERN = "pairs_wf"


def z_lookback_for(half_life: Optional[float]) -> int:
    """Rolling-z lookback tied to the half-life, clamped to [20, 120] (120 when the half-life is unknown)."""
    if half_life is None or not math.isfinite(half_life) or half_life <= 0:
        return PAIRS_Z_LOOKBACK_MAX
    return int(min(PAIRS_Z_LOOKBACK_MAX, max(PAIRS_Z_LOOKBACK_MIN, round(PAIRS_Z_LOOKBACK_HL_MULT * half_life))))


def fixed_share_pnl(shares_a: float, shares_b: float, entry_a: float, entry_b: float,
                    exit_a: float, exit_b: float) -> float:
    """Gross dollar P&L of fixed-share legs: ``sa*(Pa_exit-Pa_entry) + sb*(Pb_exit-Pb_entry)`` (signed shares).

    Depends only on price DIFFERENCES, so adding a constant to a price series leaves it unchanged."""
    return float(shares_a * (exit_a - entry_a) + shares_b * (exit_b - entry_b))


def fixed_share_value_path(shares_a: float, shares_b: float, close_a, close_b, entry_a: float, entry_b: float):
    """Mark-to-market gross P&L path (dollars, vs the entry fills) of fixed-share legs on each close."""
    ca = np.asarray(close_a, dtype=float)
    cb = np.asarray(close_b, dtype=float)
    return shares_a * (ca - entry_a) + shares_b * (cb - entry_b)


def _eg_pvalue(la, lb) -> float:
    """Max Engle-Granger p over both orderings; 1.0 when the test cannot be run."""
    try:
        return float(max(coint(la, lb)[1], coint(lb, la)[1]))
    except Exception as e:  # degenerate window: treat as not cointegrated, never as a pass
        logger.debug("EG re-test failed: %s", e)
        return 1.0


def _hl_in_band(hl: Optional[float]) -> bool:
    return hl is not None and config.PAIRS_MIN_HALF_LIFE <= hl <= config.PAIRS_MAX_HALF_LIFE


def _spread_half_life(la, lb) -> tuple[Optional[float], float, float]:
    """OLS fit of la on lb -> (half-life of the residual, alpha, beta)."""
    fit = OLS(la, add_constant(lb)).fit()
    alpha, beta = float(fit.params[0]), float(fit.params[1])
    return compute_half_life(pd.Series(la - alpha - beta * lb)), alpha, beta


@dataclass
class WalkForwardResult:
    symbol_a: str
    symbol_b: str
    formation: int
    trade: int
    step: int
    blocks: list = field(default_factory=list)       # one dict per block (frozen parameters + OOS stats)
    trades: list = field(default_factory=list)       # OOS trades, all blocks
    returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))   # dated OOS daily returns
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))    # stitched OOS equity
    backtest: dict = field(default_factory=dict)     # JSON-safe, same keys as backtest_pair, OOS only
    params: dict = field(default_factory=dict)       # the trial's parameters (registry)
    strategy_version: str = ""
    data_hash: str = ""

    @property
    def n_blocks(self) -> int:
        return len(self.blocks)


def _wf_strategy_version(params: dict) -> str:
    import hashlib
    import json
    return hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _run_block(k, s, e_form, e_trade, idx, la, lb, oa, ob, ca, cb, equity0, gf, rate, retest_every, retest_window,
               max_hold_mult):
    """Trade one block. Returns (block_info, trades, daily_returns, daily_equity, equity_end)."""
    F = slice(s, e_form)
    fa, fb = la[F], lb[F]
    hl, alpha, beta = _spread_half_life(fa, fb)
    p_form = _eg_pvalue(fa, fb)
    ra, rb = np.diff(fa), np.diff(fb)
    corr = float(np.corrcoef(ra, rb)[0, 1]) if ra.std() > 0 and rb.std() > 0 else 0.0
    corr = corr if math.isfinite(corr) else 0.0
    why = []
    if not p_form < config.PAIRS_COINT_PVALUE:
        why.append("formation_not_cointegrated")
    if not _hl_in_band(hl):
        why.append("half_life_out_of_band")
    if abs(corr) < MIN_RETURN_CORR:
        why.append("returns_uncorrelated")
    tradable = not why
    L = z_lookback_for(hl)
    max_hold = max(1, int(math.ceil(max_hold_mult * hl))) if hl else 0
    info = {
        "block": int(k), "formation_start": str(idx[s]), "formation_end": str(idx[e_form - 1]),
        "trade_start": str(idx[e_form]), "trade_end": str(idx[e_trade - 1]),
        "coint_pvalue": p_form, "half_life": _f(hl), "hedge_beta": beta, "hedge_alpha": alpha,
        "correlation": corr, "z_lookback": L, "max_hold": max_hold, "tradable": tradable,
        "untradable_reason": ";".join(why), "n_trades": 0, "pnl_abs": 0.0, "n_retests": 0, "n_breaks": 0,
        "health": [],        # one [bar_idx, eg_pvalue, half_life, healthy_after_retest] per re-test
    }

    n_t = e_trade - e_form
    rets = np.zeros(n_t)
    eq = np.full(n_t, float(equity0))
    trades: list = []
    if not tradable:
        return info, trades, rets, eq, float(equity0)

    spread = la[s:e_trade] - beta * lb[s:e_trade]       # frozen hedge ratio; block-local indexing
    entry_z, exit_z, stop_z = config.PAIRS_ZSCORE_ENTRY, config.PAIRS_ZSCORE_EXIT, config.PAIRS_ZSCORE_STOP
    healthy, blocked = True, False
    pos = 0
    sa = sb = 0.0
    cash = float(equity0)
    pend = None            # ("enter", dir) | ("exit", reason), decided at the previous close
    held = 0
    tr = None
    m_e = s_e = 0.0
    last_close_eq = float(equity0)

    def _close_trade(t_i, px_a, px_b, reason):
        nonlocal pos, sa, sb, cash, tr
        gross = fixed_share_pnl(sa, sb, tr["entry_px_a"], tr["entry_px_b"], px_a, px_b)
        cost_exit = rate * (abs(sa) * px_a + abs(sb) * px_b)
        cash += sa * px_a + sb * px_b - cost_exit
        net = gross - tr["cost_entry"] - cost_exit
        tr.update({"exit_idx": int(t_i), "exit_date": idx[t_i].isoformat(), "bars_held": int(t_i - tr["entry_idx"]),
                   "exit_reason": reason, "gross_pnl_abs": gross, "cost_abs": tr["cost_entry"] + cost_exit,
                   "pnl_abs": net, "pnl_pct": net / tr["gross_notional"]})
        trades.append(tr)
        pos, sa, sb, tr = 0, 0.0, 0.0, None

    for j in range(n_t):
        t = e_form + j                       # global bar index; spread local index is t - s
        li = t - s
        # ---- fills decided at the previous close, executed at this open
        if pend is not None:
            kind, arg = pend
            pend = None
            if kind == "enter" and pos == 0:
                gross_n = gf * last_close_eq
                abs_b = abs(beta)
                wa, wb = 1.0 / (1.0 + abs_b), abs_b / (1.0 + abs_b)
                sgn_b = 1.0 if beta >= 0 else -1.0
                px_a, px_b = oa[t], ob[t]
                sa = arg * gross_n * wa / px_a
                sb = -arg * sgn_b * gross_n * wb / px_b
                cost_in = rate * (abs(sa) * px_a + abs(sb) * px_b)
                cash = last_close_eq - sa * px_a - sb * px_b - cost_in
                pos, held = arg, 0
                tr = {"block": int(k), "entry_idx": int(t), "entry_date": idx[t].isoformat(), "direction": int(arg),
                      "hedge_beta": beta, "shares_a": sa, "shares_b": sb, "entry_px_a": float(px_a),
                      "entry_px_b": float(px_b), "cost_entry": cost_in, "gross_notional": gross_n,
                      "capital_before": last_close_eq}
            elif kind == "exit" and pos != 0:
                _close_trade(t, oa[t], ob[t], arg)
        equity_close = cash + sa * ca[t] + sb * cb[t] if pos != 0 else cash
        # ---- rolling re-test (causal: trailing bars ending at t)
        if j % retest_every == 0 and t - retest_window + 1 >= s:
            w = slice(t - retest_window + 1, t + 1)
            p_now = _eg_pvalue(la[w], lb[w])
            hl_now, _, _ = _spread_half_life(la[w], lb[w])
            info["n_retests"] += 1
            if healthy and (p_now > PAIRS_BREAK_PVALUE or not _hl_in_band(hl_now)):
                healthy = False
                info["n_breaks"] += 1
            elif not healthy and not blocked and p_now < config.PAIRS_COINT_PVALUE and _hl_in_band(hl_now):
                healthy = True
            info["health"].append([int(t), float(p_now), _f(hl_now), bool(healthy)])
        # ---- decisions at this close (executed next open; the last bar closes the book)
        last_bar = j == n_t - 1
        if pos != 0:
            held += 1
            zf = (spread[li] - m_e) / s_e
            reason = None
            if not healthy:
                reason = "coint_break"
            elif (pos == 1 and zf < -stop_z) or (pos == -1 and zf > stop_z):
                reason = "stop_z"
            elif (pos == 1 and zf >= -exit_z) or (pos == -1 and zf <= exit_z):
                reason = "reversion"
            elif held >= max_hold:
                reason = "time_stop"
                blocked = True
            if last_bar:
                _close_trade(t, ca[t], cb[t], reason or "block_end")
                equity_close = cash
            elif reason is not None:
                pend = ("exit", reason)
        elif healthy and not blocked and not last_bar and li - L >= 0:
            win = spread[li - L:li]
            sd = win.std(ddof=1)
            if sd > _EPS:
                z = (spread[li] - win.mean()) / sd
                if entry_z < abs(z) < stop_z:
                    pend = ("enter", 1 if z < 0 else -1)
                    m_e, s_e = float(win.mean()), float(sd)
        rets[j] = equity_close / last_close_eq - 1.0 if last_close_eq > 0 else 0.0
        eq[j] = equity_close
        last_close_eq = equity_close
    info["n_trades"] = len(trades)
    info["pnl_abs"] = float(sum(x["pnl_abs"] for x in trades))
    return info, trades, rets, eq, float(eq[-1])


def walk_forward_pairs(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    symbol_a: str = "A",
    symbol_b: str = "B",
    formation: int = WF_FORMATION,
    trade: int = WF_TRADE,
    step: int = WF_STEP,
    *,
    initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
    commission: float = config.COMMISSION_PCT,
    slippage: float = config.SLIPPAGE_PCT,
    gross_fraction: Optional[float] = None,
    bars_per_year: Optional[int] = None,
    retest_every: int = PAIRS_RETEST_EVERY,
    retest_window: int = PAIRS_RETEST_WINDOW,
    trials=None,
) -> WalkForwardResult:
    """Walk-forward pairs backtest (see the notes above). Only COMPLETE blocks are traded.

    ``trials`` is an optional trial registry (``trials.TrialRegistry``): the stitched OOS daily return
    series is recorded as ONE trial (pattern ``pairs_wf``), whether or not the pair is valid.
    """
    if formation < 60 or trade < 1 or step < trade:
        raise ValueError("need formation >= 60, trade >= 1 and step >= trade (non-overlapping OOS windows)")
    retest_window = min(int(retest_window), int(formation))
    df_a, df_b = _align(df_a, df_b)
    pa, pb = df_a["Close"].astype(float), df_b["Close"].astype(float)
    if len(pa) and not ((pa > 0).all() and (pb > 0).all() and np.isfinite(pa).all() and np.isfinite(pb).all()):
        raise InsufficientData("non-positive or non-finite prices")
    n = len(pa)
    idx = df_a.index
    la, lb = np.log(pa.to_numpy()), np.log(pb.to_numpy())
    ca, cb = pa.to_numpy(), pb.to_numpy()
    oa = df_a["Open"].astype(float).to_numpy() if "Open" in df_a.columns else ca
    ob = df_b["Open"].astype(float).to_numpy() if "Open" in df_b.columns else cb
    gf = float(config.MAX_POSITION_SIZE_PCT if gross_fraction is None else gross_fraction)
    bpy = int(bars_per_year or pair_bars_per_year(symbol_a, symbol_b))
    rate = float(commission) + float(slippage)

    params = {"formation": int(formation), "trade": int(trade), "step": int(step),
              "z_entry": config.PAIRS_ZSCORE_ENTRY, "z_exit": config.PAIRS_ZSCORE_EXIT,
              "z_stop": config.PAIRS_ZSCORE_STOP, "coint_p": config.PAIRS_COINT_PVALUE,
              "break_p": PAIRS_BREAK_PVALUE, "retest_every": int(retest_every), "retest_window": int(retest_window),
              "hl_band": [config.PAIRS_MIN_HALF_LIFE, config.PAIRS_MAX_HALF_LIFE],
              "time_stop_hl": config.PAIRS_TIME_STOP_HALF_LIVES, "z_lookback_hl_mult": PAIRS_Z_LOOKBACK_HL_MULT,
              "z_lookback_clamp": [PAIRS_Z_LOOKBACK_MIN, PAIRS_Z_LOOKBACK_MAX], "gross_fraction": gf,
              "cost_rate": rate}
    res = WalkForwardResult(symbol_a, symbol_b, int(formation), int(trade), int(step), params=params,
                            strategy_version=_wf_strategy_version({"v": 1, **params}))

    equity = float(initial_capital)
    dates, rets_all, eq_all = [], [], []
    k, s = 0, 0
    while s + formation + trade <= n:
        e_form, e_trade = s + formation, s + formation + trade
        info, trades, rets, eq, equity = _run_block(
            k, s, e_form, e_trade, idx, la, lb, oa, ob, ca, cb, equity, gf, rate, int(retest_every),
            int(retest_window), config.PAIRS_TIME_STOP_HALF_LIVES)
        res.blocks.append(info)
        res.trades.extend(trades)
        dates.extend(idx[e_form:e_trade])
        rets_all.extend(rets)
        eq_all.extend(eq)
        k += 1
        s += step

    res.returns = pd.Series(rets_all, index=pd.DatetimeIndex(dates), dtype=float, name=f"{symbol_a}/{symbol_b}")
    res.equity = pd.Series(eq_all, index=pd.DatetimeIndex(dates), dtype=float)
    res.backtest = _wf_backtest_dict(res, float(initial_capital), bpy, symbol_a, symbol_b)
    res.backtest["oos_significant"] = oos_significant(res)
    if trials is not None:
        from trials.registry import data_hash as _dh
        res.data_hash = _dh(pd.DataFrame({"a": pa, "b": pb}))
        trials.record(symbol=f"{symbol_a}/{symbol_b}", pattern=WF_PATTERN, params=params, returns=res.returns,
                      strategy_version=res.strategy_version, data_hash=res.data_hash)
    return res


def _wf_backtest_dict(res: WalkForwardResult, initial_capital: float, bpy: int, sym_a: str, sym_b: str) -> dict:
    """OOS-only metrics in the shape of ``backtest_pair`` (plus ``oos`` markers); plain Python values."""
    trades = res.trades
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = res.equity.to_numpy() if len(res.equity) else np.array([initial_capital])
    m = metrics.summary(eq, pnls, [t["pnl_abs"] for t in trades], [t["capital_before"] for t in trades],
                        initial_capital, bpy)
    reasons: dict = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    tradable = [b for b in res.blocks if b["tradable"]]
    last = res.blocks[-1] if res.blocks else None
    return {
        "symbol_a": sym_a, "symbol_b": sym_b,
        "oos": True, "oos_blocks": len(res.blocks), "oos_blocks_tradable": len(tradable),
        "oos_status": "ok" if res.blocks else "insufficient_history",
        "oos_start": res.blocks[0]["trade_start"] if res.blocks else None,
        "oos_end": res.blocks[-1]["trade_end"] if res.blocks else None,
        "latest_block_tradable": bool(last["tradable"]) if last else False,
        "oos_significant": False,
        "total_trades": len(trades), "winning_trades": len(wins), "losing_trades": len(losses),
        "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "profit_factor": m["profit_factor"], "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
        "sharpe_ratio": m["sharpe"], "cagr": m["cagr"], "psr": m["psr"],
        "half_life": _f(last["half_life"]) if last else None,
        "correlation": _f(last["correlation"]) if last else None,
        "coint_pvalue": _f(last["coint_pvalue"]) if last else None,
        "current_zscore": None,
        "bars_per_year": bpy, "n_bars": int(len(res.returns)), "exit_reasons": reasons,
        "non_executable": True,
        "formation": res.formation, "trade_bars": res.trade, "step": res.step,
        "blocks": res.blocks, "trades": trades,
    }


PAIRS_OOS_REQUIRE_TRADABLE = False   # True: the LATEST block's formation must also be tradable (see is_valid_oos)


def oos_significant(wf: WalkForwardResult) -> bool:
    """Informational: enough OOS trades and OOS PSR strictly above OOS_PSR_MIN (never a gate for alerts)."""
    bt = wf.backtest
    return bool(bt.get("total_trades", 0) >= config.MIN_TRADES_OOS and bt.get("psr") is not None
                and bt["psr"] > config.OOS_PSR_MIN)


def is_valid_oos(wf: WalkForwardResult, require_tradable: Optional[bool] = None) -> bool:
    """OOS-side validity of a pair, from walk-forward output only (no full-sample statistic).

    Default: at least one COMPLETE OOS block exists, i.e. the pair has an out-of-sample record to display.
    With ``require_tradable`` (default ``PAIRS_OOS_REQUIRE_TRADABLE``) the LATEST block's formation window
    (data strictly before its OOS bars) must also be tradable: Engle-Granger p < 0.05 on 252 bars, half-life in
    band, correlated returns.  Whether the pair deserves attention on OOS economics is exposed separately
    (``oos_significant``, ``latest_block_tradable``, PSR in ``wf.backtest``).  Pairs stay alert-only (D3)."""
    if not wf.blocks:
        return False
    req = PAIRS_OOS_REQUIRE_TRADABLE if require_tradable is None else require_tradable
    return bool(wf.blocks[-1]["tradable"]) if req else True
