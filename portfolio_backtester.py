"""Minimal monthly portfolio backtester (pure pandas/numpy, no I/O).

Conventions
-----------
* ``returns`` are monthly *total* returns (decimal), one column per asset.
* Month-t weights are the weights held at the START of month t; month-t portfolio
  return is sum(w_start * r_t) minus trading cost.
* The first month starts exactly at target weights (initial purchase is free).
* Turnover in month t = sum(|w_target - w_drifted|) over assets (one-way notional as a
  fraction of NAV, counting both buys and sells); cost = turnover * cost_bps / 1e4.
* Band rebalance (5/25 style): rebalance to target when ANY asset's drifted weight
  deviates from target by more than ``band_abs`` (absolute) OR by more than ``band_rel``
  (relative to its target weight); otherwise let weights drift and trade nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PortfolioResult:
    returns: pd.Series      # net monthly portfolio returns
    equity: pd.Series       # cumulative growth of 1.0 (value at end of each month)
    turnover: pd.Series     # per-month turnover (see module docstring)
    weights: pd.DataFrame   # weights at start of each month (after any rebalance)


def backtest_weights(returns: pd.DataFrame, weights: dict, rebalance: str = "monthly",
                     band_abs: float = 0.05, band_rel: float = 0.25,
                     cost_bps: float = 0.0) -> PortfolioResult:
    if rebalance not in ("monthly", "bands"):
        raise ValueError("rebalance must be 'monthly' or 'bands'")
    cols = list(weights)
    missing = [c for c in cols if c not in returns.columns]
    if missing:
        raise KeyError(f"assets missing from returns: {missing}")
    target = np.array([weights[c] for c in cols], dtype=float)
    if abs(target.sum() - 1.0) > 1e-9:
        raise ValueError("weights must sum to 1")
    R = returns[cols].to_numpy(dtype=float)
    n = len(R)
    port = np.zeros(n)
    turn = np.zeros(n)
    wstart = np.zeros((n, len(cols)))
    drift = target.copy()
    for t in range(n):
        if t == 0:
            w = target.copy()
        else:
            if rebalance == "monthly":
                trade = True
            else:
                dev = np.abs(drift - target)
                trade = bool(np.any(dev > band_abs + 1e-12) or
                             np.any(dev > band_rel * np.abs(target) + 1e-12))
            if trade:
                turn[t] = np.abs(target - drift).sum()
                w = target.copy()
            else:
                w = drift
        wstart[t] = w
        gross = float((w * R[t]).sum())
        port[t] = gross - turn[t] * cost_bps / 1e4
        drift = w * (1 + R[t]) / (1 + gross)
    idx = returns.index
    ret = pd.Series(port, index=idx, name="portfolio")
    return PortfolioResult(ret, (1 + ret).cumprod(), pd.Series(turn, index=idx, name="turnover"),
                           pd.DataFrame(wstart, index=idx, columns=cols))


def stats(returns: pd.Series, rf: pd.Series | None = None) -> dict:
    """cagr, vol, max_drawdown, sharpe (annualised, excess over rf if given), worst_year."""
    s = pd.Series(returns).astype(float)
    n = len(s)
    eq = np.concatenate([[1.0], (1 + s.to_numpy()).cumprod()])
    peak = np.maximum.accumulate(eq)
    ex = s if rf is None else s - pd.Series(rf).reindex(s.index).astype(float)
    sd = ex.std()
    years = pd.Series(s.to_numpy(), index=s.index).groupby(s.index.year).apply(lambda x: (1 + x).prod() - 1)
    return {
        "cagr": float(eq[-1] ** (12 / n) - 1),
        "vol": float(s.std() * 12 ** 0.5),
        "max_drawdown": float((eq / peak - 1).min()),
        "sharpe": float(ex.mean() / sd * 12 ** 0.5) if sd > 0 else float("nan"),
        "worst_year": float(years.min()),
    }


def trend_sleeve(prices: pd.DataFrame, returns: pd.DataFrame, assets: list,
                 lookbacks=(8, 10, 12), cash: str = "SHY") -> pd.Series:
    """Faber average-of-lookbacks trend sleeve, equal weight over ``assets``.

    At month-end t each asset gets score = fraction of lookbacks L with
    price_t > SMA_L(price)_t (SMA includes month t). That score decides the allocation
    for month t+1 (score of the asset, remainder to ``cash`` return). While an SMA is
    still warming up its vote counts as "invested". No look-ahead: the return in month m
    uses only prices up to month m-1.
    """
    votes = []
    for L in lookbacks:
        sma = prices[assets].rolling(L).mean()
        v = (prices[assets] > sma).astype(float)
        v = v.where(sma.notna(), 1.0)
        votes.append(v)
    score = sum(votes) / len(lookbacks)
    score = score.shift(1)                      # signal at t applied to t+1
    score = score.reindex(returns.index)
    score = score.fillna(1.0)
    w = 1.0 / len(assets)
    out = sum(w * (score[a] * returns[a] + (1 - score[a]) * returns[cash]) for a in assets)
    return out.rename("trend_sleeve")
