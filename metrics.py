"""Performance metrics (WS2.2).

Conventions (decisions D9/D11):
  * Sharpe/Sortino use rf = 0 on per-bar mark-to-market returns of the ACTUAL position size
    (the equity curve returns, not returns on notional). Annualised by sqrt(bars_per_year).
  * Sortino uses downside deviation over ALL observations: sqrt(mean(min(r, 0)^2)).
  * PSR / deflated Sharpe work in PER-PERIOD (non-annualised) Sharpe units and use
    NON-excess kurtosis (normal = 3), as in Bailey & Lopez de Prado (2012, 2014).
  * Undefined ratios are None (never NaN/inf); flat/zero-variance series give Sharpe 0.0.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from scipy.stats import norm

import config

EULER_GAMMA = 0.5772156649015329
_EPS = 1e-12


def _arr(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    return a[np.isfinite(a)]


def _f(x) -> Optional[float]:
    """Finite float or None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ------------------------------------------------------------------ returns
def equity_returns(equity, initial: Optional[float] = None) -> np.ndarray:
    """Per-bar returns of an equity curve. If `initial` is given it is prepended as bar -1."""
    e = np.asarray(equity, dtype=float)
    if initial is not None:
        e = np.concatenate([[float(initial)], e])
    if len(e) < 2:
        return np.array([], dtype=float)
    prev = e[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(prev > 0, e[1:] / prev - 1.0, 0.0)
    return r


# ------------------------------------------------------------------ ratios
def sharpe(returns, bars_per_year: int = 252) -> float:
    r = _arr(returns)
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd < _EPS:
        return 0.0
    return float(r.mean() / sd * math.sqrt(bars_per_year))


def sortino(returns, bars_per_year: int = 252) -> float:
    r = _arr(returns)
    if len(r) < 2:
        return 0.0
    dd = math.sqrt(float(np.mean(np.minimum(r, 0.0) ** 2)))
    if dd < _EPS:
        return 0.0
    return float(r.mean() / dd * math.sqrt(bars_per_year))


def cagr(returns, bars_per_year: int = 252) -> Optional[float]:
    r = _arr(returns)
    if len(r) == 0:
        return None
    growth = float(np.prod(1.0 + r))
    if growth <= 0:
        return -1.0
    return float(growth ** (bars_per_year / len(r)) - 1.0)


def drawdown_series(equity) -> np.ndarray:
    e = np.asarray(equity, dtype=float)
    if len(e) == 0:
        return e
    peak = np.maximum.accumulate(e)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(peak > 0, e / peak - 1.0, 0.0)


def max_drawdown(equity) -> tuple[float, int]:
    """(max drawdown as a non-positive fraction, longest time under water in bars)."""
    dd = drawdown_series(equity)
    if len(dd) == 0:
        return 0.0, 0
    longest = cur = 0
    for v in dd:
        if v < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return float(dd.min()), int(longest)


def calmar(returns, equity=None, bars_per_year: int = 252) -> Optional[float]:
    """CAGR / |max drawdown|. None when there is no drawdown or no CAGR."""
    r = _arr(returns)
    eq = np.cumprod(1.0 + r) if equity is None else np.asarray(equity, dtype=float)
    mdd, _ = max_drawdown(np.concatenate([[1.0], eq]) if equity is None else eq)
    c = cagr(r, bars_per_year)
    if c is None or mdd >= -_EPS:
        return None
    return float(c / abs(mdd))


# ------------------------------------------------------------------ PSR / DSR
def skew_kurt(returns) -> tuple[float, float]:
    """(skewness, NON-excess kurtosis) using population moments. (0, 3) if degenerate."""
    r = _arr(returns)
    if len(r) < 3:
        return 0.0, 3.0
    m = r.mean()
    d = r - m
    s2 = float(np.mean(d ** 2))
    if s2 < _EPS ** 2:
        return 0.0, 3.0
    return float(np.mean(d ** 3) / s2 ** 1.5), float(np.mean(d ** 4) / s2 ** 2)


def psr_from_moments(sr: float, T: int, skew: float = 0.0, kurt: float = 3.0,
                     sr_benchmark: float = 0.0) -> Optional[float]:
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012):

        PSR = Phi( (SR - SR*) sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )

    `sr` and `sr_benchmark` are per-period; `kurt` is non-excess. None if undefined.
    """
    if T is None or T < 2:
        return None
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if not math.isfinite(denom) or denom <= 0:
        return None
    z = (sr - sr_benchmark) * math.sqrt(T - 1) / math.sqrt(denom)
    return _f(norm.cdf(z))


def psr(returns, sr_benchmark: float = 0.0) -> Optional[float]:
    """PSR of a return series against a per-period benchmark Sharpe (default 0)."""
    r = _arr(returns)
    if len(r) < 3:
        return None
    sd = r.std(ddof=1)
    if sd < _EPS:
        return None
    sk, ku = skew_kurt(r)
    return psr_from_moments(float(r.mean() / sd), len(r), sk, ku, sr_benchmark)


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """SR0 = sqrt(V)[(1-g) Phi^-1(1-1/N) + g Phi^-1(1-1/(N e))] (per-period units)."""
    if n_trials is None or n_trials < 2 or var_sr <= 0:
        return 0.0
    return float(math.sqrt(var_sr) * ((1 - EULER_GAMMA) * norm.ppf(1 - 1.0 / n_trials)
                                      + EULER_GAMMA * norm.ppf(1 - 1.0 / (n_trials * math.e))))


def deflated_sharpe(sr: float, n_trials: int, var_sr: float, T: int,
                    skew: float = 0.0, kurt: float = 3.0) -> Optional[float]:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014): PSR against SR0, the expected
    maximum Sharpe among `n_trials` independent trials whose Sharpe variance is `var_sr`.
    All Sharpe quantities per-period; `kurt` non-excess."""
    return psr_from_moments(sr, T, skew, kurt, expected_max_sharpe(n_trials, var_sr))


# ------------------------------------------------------------------ trade stats
def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def profit_factor(pnls: Sequence[float]) -> Optional[float]:
    """gross profit / gross loss; None when there are no losses (undefined) or no trades."""
    p = _arr(pnls)
    gp = float(p[p > 0].sum())
    gl = float(-p[p <= 0].sum())
    if gl <= 0:
        return None
    return gp / gl


def expectancy(pnls_pct: Sequence[float], pnls_abs: Sequence[float] = (),
               capital_before: Sequence[float] = ()) -> dict:
    """Expectancy per trade at notional scale (mean net return on position notional) and at
    equity scale (mean of pnl_abs / account equity before the trade)."""
    pp = _arr(pnls_pct)
    out = {"expectancy_notional": float(pp.mean()) if len(pp) else None, "expectancy_equity": None}
    pa, cb = np.asarray(pnls_abs, dtype=float), np.asarray(capital_before, dtype=float)
    if len(pa) and len(pa) == len(cb) and np.all(cb > 0):
        out["expectancy_equity"] = _f(np.mean(pa / cb))
    return out


# ------------------------------------------------------------------ summary
def summary(equity, trade_pnls_pct: Sequence[float] = (), trade_pnls_abs: Sequence[float] = (),
            capital_before: Sequence[float] = (), initial_capital: Optional[float] = None,
            bars_per_year: int = 252) -> dict:
    """JSON-safe metrics dict: every value is a finite float/int/None (allow_nan=False OK)."""
    eq = np.asarray(equity, dtype=float)
    init = float(initial_capital) if initial_capital is not None else (float(eq[0]) if len(eq) else 0.0)
    r = equity_returns(eq, init) if len(eq) else np.array([])
    full = np.concatenate([[init], eq]) if len(eq) else np.array([init])
    mdd, dur = max_drawdown(full)
    pnl = _arr(trade_pnls_pct)
    n = len(pnl)
    wins = int((pnl > 0).sum())
    lo, hi = wilson_ci(wins, n)
    sk, ku = skew_kurt(r)
    exp = expectancy(trade_pnls_pct, trade_pnls_abs, capital_before)
    pf = profit_factor(pnl)
    sh = sharpe(r, bars_per_year)
    total_ret = float(eq[-1] / init - 1.0) if len(eq) and init > 0 else 0.0
    out = {
        "n_bars": int(len(eq)),
        "total_trades": n,
        "total_return": total_ret,
        "cagr": cagr(r, bars_per_year),
        "sharpe": sh,
        "sortino": sortino(r, bars_per_year),
        "calmar": calmar(r, bars_per_year=bars_per_year),
        "max_drawdown": mdd,
        "max_drawdown_duration_bars": dur,
        "psr": psr(r),
        "skew": sk,
        "kurtosis": ku,
        "win_rate": wins / n if n else None,
        "win_rate_ci_low": lo,
        "win_rate_ci_high": hi,
        "profit_factor": pf,
        "profit_factor_capped": min(pf, 10.0) if pf is not None else (10.0 if wins else 0.0),
        "expectancy_notional": exp["expectancy_notional"],
        "expectancy_equity": exp["expectancy_equity"],
    }
    return {k: (v if isinstance(v, (int, str)) or v is None else _f(v)) for k, v in out.items()}
