"""Selection-bias statistics. All Sharpe ratios are PER PERIOD (not annualised); kurtosis is NON-excess.

* ``psr``  Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012).
* ``expected_max_sharpe``  SR0 = sqrt(V) [(1-g) Phi^-1(1-1/N) + g Phi^-1(1-1/(N e))] (Bailey & Lopez de Prado 2014).
* ``dsr``  Deflated Sharpe Ratio (2014): PSR evaluated against SR0, the expected maximum Sharpe of N trials whose
  Sharpe variance is V. It is a probability; ``dsr_p_value = 1 - dsr`` is the one-sided p-value.
* ``pbo_cscv``  Probability of Backtest Overfitting by Combinatorially Symmetric Cross-Validation
  (Bailey, Borwein, Lopez de Prado & Zhu 2015). Advisory only: reported, never gated.

Pure numpy / scipy, no randomness (CSCV enumerates every combination), so results are deterministic.
"""
from __future__ import annotations

import itertools
import math
from typing import Optional

import numpy as np
from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329
_EPS = 1e-12


def _clean(x) -> np.ndarray:
    a = np.asarray(x, dtype=float).ravel()
    return a[np.isfinite(a)]


def _moments(r: np.ndarray) -> tuple[float, float, float]:
    """(per-period Sharpe with ddof=1 std, skewness, non-excess kurtosis) of a return series."""
    sd = r.std(ddof=1)
    sr = float(r.mean() / sd) if sd > _EPS else 0.0
    d = r - r.mean()
    s2 = float(np.mean(d ** 2))
    if s2 < _EPS ** 2:
        return sr, 0.0, 3.0
    return sr, float(np.mean(d ** 3) / s2 ** 1.5), float(np.mean(d ** 4) / s2 ** 2)


def psr(sr: float, T: int, skew: float = 0.0, kurt: float = 3.0, sr_benchmark: float = 0.0) -> Optional[float]:
    """PSR = Phi((SR - SR*) sqrt(T-1) / sqrt(1 - skew SR + (kurt-1)/4 SR^2)). None when undefined."""
    if T is None or T < 2 or not math.isfinite(sr):
        return None
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if not math.isfinite(denom) or denom <= 0:
        return None
    return float(norm.cdf((sr - sr_benchmark) * math.sqrt(T - 1) / math.sqrt(denom)))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected maximum Sharpe of ``n_trials`` independent zero-skill trials with cross-trial Sharpe variance
    ``var_sr``. 0.0 for a single trial (no selection) or zero variance."""
    if n_trials is None or n_trials < 2 or not var_sr or var_sr <= 0:
        return 0.0
    return float(math.sqrt(var_sr) * ((1 - EULER_GAMMA) * norm.ppf(1 - 1.0 / n_trials)
                                      + EULER_GAMMA * norm.ppf(1 - 1.0 / (n_trials * math.e))))


def dsr(sr: float, n_trials: int, var_sr: float, T: int, skew: float = 0.0, kurt: float = 3.0) -> Optional[float]:
    """Deflated Sharpe Ratio: PSR of ``sr`` (T observations, given skew / kurtosis) against SR0(N, V)."""
    return psr(sr, T, skew, kurt, expected_max_sharpe(n_trials, var_sr))


def dsr_p_value(sr: float, n_trials: int, var_sr: float, T: int, skew: float = 0.0, kurt: float = 3.0
                ) -> Optional[float]:
    """One-sided p-value of the deflated Sharpe: 1 - DSR (None when DSR is undefined)."""
    d = dsr(sr, n_trials, var_sr, T, skew, kurt)
    return None if d is None else float(1.0 - d)


def sharpe_variance(returns_matrix, min_obs: int = 3) -> float:
    """Cross-trial variance (ddof=1) of the per-period Sharpe of every column of a (date x trial) matrix.
    NaN entries are dropped per column; columns with fewer than ``min_obs`` observations are skipped.
    0.0 when fewer than two columns qualify."""
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim == 1:
        m = m[:, None]
    srs = []
    for j in range(m.shape[1]):
        r = _clean(m[:, j])
        if len(r) >= min_obs:
            srs.append(_moments(r)[0])
    return float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0


def dsr_from_returns(returns, n_trials: int, var_sr: float) -> Optional[float]:
    """DSR of a return series using its own Sharpe / skew / kurtosis / length."""
    r = _clean(returns)
    if len(r) < 3 or r.std(ddof=1) <= _EPS:
        return None
    sr, sk, ku = _moments(r)
    return dsr(sr, n_trials, var_sr, len(r), sk, ku)


def pbo_cscv(returns_matrix, S: int = 16, *, return_logits: bool = False):
    """Probability of Backtest Overfitting (CSCV). ``returns_matrix`` is (T observations x N strategies).

    The T rows are cut into S contiguous equal blocks (leftover rows at the END are dropped); for every one of the
    C(S, S/2) ways to pick S/2 blocks as in-sample, the in-sample best strategy n* (Sharpe) is ranked among all N
    out-of-sample (the complementary blocks, Sharpe): w = rank / (N + 1) (average rank for ties),
    logit = ln(w / (1 - w)). PBO = share of combinations with logit <= 0. NaN returns count as 0 (flat).
    Returns the PBO in [0, 1], or ``(pbo, logits)`` when ``return_logits``.
    """
    if S < 2 or S % 2:
        raise ValueError("S must be an even integer >= 2")
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise ValueError("returns_matrix must be 2-D with at least 2 strategies")
    L = m.shape[0] // S
    if L < 2:
        raise ValueError(f"need at least {2 * S} observations for S={S}")
    m = np.nan_to_num(m[: L * S], nan=0.0, posinf=0.0, neginf=0.0)
    n = m.shape[1]
    blocks = m.reshape(S, L, n)
    s1, s2 = blocks.sum(1), (blocks ** 2).sum(1)                      # (S, N) per-block sums
    combos = np.array(list(itertools.combinations(range(S), S // 2)))
    ind = np.zeros((len(combos), S))
    ind[np.arange(len(combos))[:, None], combos] = 1.0
    cnt = L * (S // 2)

    def sharpe(sum1, sum2):
        mean = sum1 / cnt
        var = np.maximum((sum2 - cnt * mean ** 2) / (cnt - 1), 0.0)
        sd = np.sqrt(var)
        return np.where(sd > _EPS, mean / np.where(sd > _EPS, sd, 1.0), 0.0)

    logits = np.empty(len(combos))
    for lo in range(0, len(combos), 1024):
        a = ind[lo:lo + 1024]
        is_sr = sharpe(a @ s1, a @ s2)
        oos_sr = sharpe((1 - a) @ s1, (1 - a) @ s2)
        star = np.argmax(is_sr, axis=1)                               # first max on ties: deterministic
        v = oos_sr[np.arange(len(a)), star][:, None]
        rank = 1.0 + (oos_sr < v).sum(1) + 0.5 * ((oos_sr == v).sum(1) - 1)
        w = rank / (n + 1.0)
        logits[lo:lo + 1024] = np.log(w / (1.0 - w))
    pbo = float(np.mean(logits <= 0))
    return (pbo, logits) if return_logits else pbo
