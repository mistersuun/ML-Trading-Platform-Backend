"""Dependence-aware statistics for pooled validation (decision D18). Pure numpy / scipy, seeded, no I/O.

* ``bartlett_t_eff``       effective sample size T_eff = T / (1 + 2 sum_k (1 - k/(q+1)) rho_k), never above T.
* ``psr_teff`` / ``dsr_teff``  PSR / Deflated Sharpe of a date-level series with T replaced by T_eff.
* ``stationary_bootstrap_sharpe``  Politis-Romano stationary block bootstrap CI of the annualised Sharpe.
* ``cluster_bootstrap_mean``       cluster (entry-week) bootstrap CI of the mean R.
* ``cross_sectional_cscv``         advisory cross-sectional CSCV: does the best pattern on half the symbols also
  rank well on the other half? (``pbo_xs``, never a gate).

Sharpe ratios are PER PERIOD unless stated; kurtosis is non-excess (as ``stats.selection``).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from stats import selection

_EPS = 1e-12


def _clean(x) -> np.ndarray:
    a = np.asarray(x, dtype=float).ravel()
    return a[np.isfinite(a)]


def bartlett_t_eff(returns, q: int) -> float:
    """Effective number of observations of an autocorrelated series (Lo 2002 / Bartlett kernel).

    ``T_eff = T / max(1, 1 + 2 sum_{k=1..q} (1 - k/(q+1)) rho_k)``. Positive autocorrelation (overlapping holds)
    shrinks it; it is never allowed to exceed T. ``q`` is clipped to ``[0, T // 4]``."""
    r = _clean(returns)
    T = len(r)
    if T < 3:
        return float(T)
    q = int(max(0, min(int(q), T // 4)))
    d = r - r.mean()
    c0 = float(np.dot(d, d))
    if c0 < _EPS:
        return float(T)
    acc = 0.0
    for k in range(1, q + 1):
        rho = float(np.dot(d[k:], d[:-k])) / c0
        acc += (1.0 - k / (q + 1.0)) * rho
    return float(T / max(1.0, 1.0 + 2.0 * acc))


def psr_teff(returns, t_eff: Optional[float] = None, sr_benchmark: float = 0.0) -> Optional[float]:
    """PSR(SR > benchmark) of the series with its own skew / kurtosis and ``t_eff`` observations."""
    r = _clean(returns)
    if len(r) < 3 or r.std(ddof=1) <= _EPS:
        return None
    sr, sk, ku = selection._moments(r)
    return selection.psr(sr, t_eff if t_eff is not None else len(r), sk, ku, sr_benchmark)


def dsr_teff(returns, n_trials: int, var_sr: float, t_eff: Optional[float] = None) -> Optional[float]:
    """Deflated Sharpe with T_eff: PSR against the expected maximum Sharpe of ``n_trials`` null trials."""
    return psr_teff(returns, t_eff, selection.expected_max_sharpe(n_trials, var_sr))


def sharpe_annual(returns, bars_per_year: int = 252) -> float:
    r = _clean(returns)
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(bars_per_year)) if sd > _EPS else 0.0


def stationary_bootstrap_indices(T: int, draws: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """(draws, T) index matrix of a stationary bootstrap: geometric blocks of mean length ``mean_block``."""
    p = 1.0 / max(1.0, float(mean_block))
    restart = rng.random((draws, T)) < p
    restart[:, 0] = True
    starts = rng.integers(0, T, size=(draws, T))
    pos = np.arange(T)
    last = np.maximum.accumulate(np.where(restart, pos, 0), axis=1)
    base = np.take_along_axis(starts, last, axis=1)
    return (base + (pos - last)) % T


def stationary_bootstrap_sharpe(returns, draws: int, mean_block: float, rng: np.random.Generator,
                                bars_per_year: int = 252) -> Optional[dict]:
    """CI (2.5 / 97.5 %) of the annualised Sharpe under a stationary block bootstrap. None for < 10 observations."""
    r = _clean(returns)
    T = len(r)
    if T < 10 or draws < 1:
        return None
    idx = stationary_bootstrap_indices(T, draws, mean_block, rng)
    x = r[idx]
    sd = x.std(axis=1, ddof=1)
    sr = np.where(sd > _EPS, x.mean(axis=1) / np.where(sd > _EPS, sd, 1.0), 0.0) * math.sqrt(bars_per_year)
    lo, hi = np.percentile(sr, [2.5, 97.5])
    return {"lo": float(lo), "hi": float(hi), "mean": float(sr.mean()), "draws": int(draws)}


def stationary_bootstrap_mean_p(returns, draws: int, mean_block: float, rng: np.random.Generator) -> Optional[float]:
    """One-sided p of 'the mean of the series is <= 0' (centred stationary bootstrap). None for < 10 observations."""
    r = _clean(returns)
    T = len(r)
    if T < 10 or draws < 1:
        return None
    obs = float(r.mean())
    idx = stationary_bootstrap_indices(T, draws, mean_block, rng)
    m = (r - obs)[idx].mean(axis=1)
    return float((1.0 + (m >= obs).sum()) / (1.0 + draws))


def cluster_bootstrap_mean(values, clusters, draws: int, rng: np.random.Generator) -> Optional[dict]:
    """CI of the mean of ``values`` when whole clusters (entry weeks) are resampled with replacement."""
    v = np.asarray(values, dtype=float)
    c = np.asarray(clusters)
    if len(v) < 2 or draws < 1:
        return None
    ids, inv = np.unique(c, return_inverse=True)
    k = len(ids)
    if k < 2:
        return None
    sums = np.bincount(inv, weights=v, minlength=k)
    cnt = np.bincount(inv, minlength=k).astype(float)
    pick = rng.integers(0, k, size=(draws, k))
    m = sums[pick].sum(axis=1) / np.maximum(cnt[pick].sum(axis=1), 1.0)
    lo, hi = np.percentile(m, [2.5, 97.5])
    return {"lo": float(lo), "hi": float(hi), "mean": float(m.mean()), "draws": int(draws)}


def cross_sectional_cscv(components: np.ndarray, splits: int, rng: np.random.Generator,
                         chunk: int = 100) -> Optional[dict]:
    """Advisory cross-sectional CSCV on a (T x K symbols x P patterns) array of daily component returns.

    Each split puts floor(K/2) random symbols in half A (the rest in B). The pattern with the best pooled Sharpe on A
    is ranked among all P patterns on B (w = rank / (P + 1), logit = ln(w / (1 - w))); ``pbo_xs`` is the share of
    splits with logit <= 0. None when K < 4 or P < 2."""
    a = np.nan_to_num(np.asarray(components, dtype=float), nan=0.0)
    if a.ndim != 3:
        return None
    T, K, P = a.shape
    if K < 4 or P < 2 or T < 10 or splits < 1:
        return None
    half = K // 2
    ind = np.zeros((splits, K))
    for s in range(splits):
        ind[s, rng.permutation(K)[:half]] = 1.0
    logits = np.empty(splits)

    def sharpe(x):                        # x: (S, T, P) -> (S, P) per-period Sharpe
        mu = x.mean(axis=1)
        sd = x.std(axis=1, ddof=1)
        return np.where(sd > _EPS, mu / np.where(sd > _EPS, sd, 1.0), 0.0)

    for lo in range(0, splits, chunk):
        w = ind[lo:lo + chunk]                                   # (S, K)
        sa = np.einsum("sk,tkp->stp", w, a)
        sb = np.einsum("sk,tkp->stp", 1.0 - w, a)
        is_sr, oos_sr = sharpe(sa), sharpe(sb)
        star = np.argmax(is_sr, axis=1)
        v = oos_sr[np.arange(len(w)), star][:, None]
        rank = 1.0 + (oos_sr < v).sum(1) + 0.5 * ((oos_sr == v).sum(1) - 1)
        wr = rank / (P + 1.0)
        logits[lo:lo + chunk] = np.log(wr / (1.0 - wr))
    return {"pbo_xs": float(np.mean(logits <= 0)), "splits": int(splits), "symbols": int(K), "patterns": int(P)}
