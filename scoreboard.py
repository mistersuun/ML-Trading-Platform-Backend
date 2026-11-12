"""Master scoreboard: one pre-registered test for every paper arm (rules frozen in docs/scoreboard/PREREG.md).

Pure functions on daily series; no file I/O here (scripts/scoreboard.py reads the journals and writes the report).
For each arm: daily excess return over its pre-named benchmark, a day-block bootstrap CI for the mean, the same with the
best 2 days removed, OLS beta/alpha with a Newey-West t-stat, the effective number of independent days, and the share
of P&L from the best 2 days. Arms whose own daily returns correlate above 0.7 count as one bet in a Holm family-wise test.
This module only reports: it never changes an arm's rules (PREREG rule zero).

Every statistic is None ("n/a") when the data cannot support it; nothing is imputed.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

SEED = 20261112
N_BOOT = 10_000
BLOCK = 5
NW_LAG = 5
MIN_DAYS = 10                    # fewer return days than this: no inference
CORR_LINK = 0.7
MIN_OVERLAP = 10                 # common clean days needed to compare two arms
LOOKS = ("2026-12-31", "2027-02-26", "2027-04-15")
ALPHA_FAMILY = 0.05
ALPHA_LOOK = ALPHA_FAMILY / len(LOOKS)
MIN_INDEPENDENT = 60             # independent days, or trades for trade-based arms


def block_bootstrap_means(x, n_boot: int = N_BOOT, block: int = BLOCK, seed: int = SEED) -> np.ndarray:
    """Means of `n_boot` circular block-bootstrap resamples of x (blocks of `block` consecutive days)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    rng = np.random.default_rng(seed)
    nb = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, nb))
    idx = ((starts[:, :, None] + np.arange(block)) % n).reshape(n_boot, nb * block)[:, :n]
    return x[idx].mean(axis=1)


def bootstrap_mean(x, n_boot: int = N_BOOT, block: int = BLOCK, seed: int = SEED) -> dict:
    """Mean, 95% percentile CI and one-sided p (mean > 0, null = resamples re-centred at zero)."""
    x = np.asarray(x, dtype=float)
    if len(x) < MIN_DAYS:
        return {"mean": float(x.mean()) if len(x) else None, "lo": None, "hi": None, "p": None}
    m = float(x.mean())
    boots = block_bootstrap_means(x, n_boot, block, seed)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    p = (1 + int(np.sum(boots - m >= m))) / (n_boot + 1)
    return {"mean": m, "lo": float(lo), "hi": float(hi), "p": float(p)}


def drop_top(x, k: int = 2) -> np.ndarray:
    """x without its k largest values (the 'best days')."""
    x = np.asarray(x, dtype=float)
    if len(x) <= k:
        return x[:0]
    return np.sort(x)[:-k]


def effective_days(x) -> Optional[float]:
    """n (1 - r1) / (1 + r1), r1 = lag-1 autocorrelation floored at 0, so at most n."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < MIN_DAYS:
        return None
    d = x - x.mean()
    den = float(d @ d)
    r1 = float(d[1:] @ d[:-1]) / den if den > 0 else 0.0
    r1 = min(max(r1, 0.0), 0.99)
    return n * (1 - r1) / (1 + r1)


def newey_west_ols(y, x, lag: int = NW_LAG) -> dict:
    """OLS y = alpha + beta x with a Bartlett-weighted Newey-West covariance. Returns alpha, beta and their t-stats."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    n = len(y)
    out = {"alpha": None, "beta": None, "t_alpha": None, "t_beta": None}
    if n < MIN_DAYS or np.ptp(x) == 0:
        return out
    X = np.column_stack([np.ones(n), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    b = xtx_inv @ X.T @ y
    u = y - X @ b
    if np.max(np.abs(u)) < 1e-12:          # y is the benchmark itself: nothing to test
        return out
    z = X * u[:, None]
    s = z.T @ z
    for k in range(1, min(lag, n - 1) + 1):
        g = z[k:].T @ z[:-k]
        s += (1 - k / (lag + 1)) * (g + g.T)
    cov = xtx_inv @ s @ xtx_inv
    se = np.sqrt(np.diag(cov))
    out.update(alpha=float(b[0]), beta=float(b[1]))
    if se[0] > 0:
        out["t_alpha"] = float(b[0] / se[0])
    if se[1] > 0:
        out["t_beta"] = float(b[1] / se[1])
    return out


def holm_adjust(pvals: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, returned in the input order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def build_returns(equity: pd.Series, bench: pd.Series, sessions: Optional[pd.DatetimeIndex] = None) -> pd.DataFrame:
    """Daily returns on the dates both series have. A return between non-adjacent dates is kept and flagged gap=True
    (adjacent = consecutive in `sessions`, the benchmark's trading dates). Columns: arm, bench, excess, pnl, gap."""
    eq = equity.dropna()
    bn = bench.dropna()
    common = eq.index.intersection(bn.index).sort_values()
    eq, bn = eq.loc[common], bn.loc[common]
    out = pd.DataFrame({"arm": eq.pct_change(), "bench": bn.pct_change(), "pnl": eq.diff()}).iloc[1:]
    out["excess"] = out["arm"] - out["bench"]
    gap = pd.Series(False, index=out.index)
    if sessions is not None and len(common) > 1:
        pos = pd.Series(sessions.get_indexer(common), index=common)
        step = pos.diff().iloc[1:]
        known = (pos >= 0) & (pos.shift(1) >= 0)
        gap = (step > 1) & known.iloc[1:]
    out["gap"] = gap.astype(bool)
    return out[["arm", "bench", "excess", "pnl", "gap"]]


def top2_pnl_share(pnl) -> Optional[float]:
    """Sum of the 2 best days' P&L over total P&L; None unless the total is positive."""
    p = np.asarray(pnl, dtype=float)
    if len(p) < 3 or p.sum() <= 0:
        return None
    return float(np.sort(p)[-2:].sum() / p.sum())


def arm_stats(ret: pd.DataFrame, start_value: float, equity: pd.Series, bench: pd.Series) -> dict:
    """All PREREG statistics for one arm. `equity`/`bench` are the level series the returns came from;
    total return is last equity over `start_value`, benchmark total is first to last common level."""
    n = len(ret)
    st = {"n_days": n, "n_gap": int(ret["gap"].sum()) if n else 0, "first": None, "last": None,
          "total": None, "bench_total": None, "excess_total": None}
    common = equity.dropna().index.intersection(bench.dropna().index).sort_values()
    if len(common):
        st["first"], st["last"] = common[0], common[-1]
        st["total"] = float(equity.loc[common[-1]] / start_value - 1)
        st["bench_total"] = float(bench.loc[common[-1]] / bench.loc[common[0]] - 1)
        st["excess_total"] = st["total"] - st["bench_total"]
    ex = ret["excess"].to_numpy() if n else np.array([])
    full = bootstrap_mean(ex)
    cut = bootstrap_mean(drop_top(ex))
    ols = newey_west_ols(ret["arm"].to_numpy(), ret["bench"].to_numpy()) if n else {}
    st.update(mean=full["mean"], ci_lo=full["lo"], ci_hi=full["hi"], p=full["p"],
              mean_x2=cut["mean"] if n >= MIN_DAYS else None, ci_lo_x2=cut["lo"], ci_hi_x2=cut["hi"],
              alpha=ols.get("alpha"), beta=ols.get("beta"), t_alpha=ols.get("t_alpha"),
              n_eff=effective_days(ex), top2_share=top2_pnl_share(ret["pnl"].to_numpy()) if n else None)
    return st


def clusters(returns: dict[str, pd.Series], link: float = CORR_LINK, min_overlap: int = MIN_OVERLAP) -> list[list[str]]:
    """Connected groups of arms whose daily returns correlate above `link` on at least `min_overlap` common days.
    Every arm appears in exactly one group (singletons included); ids are sorted within and across groups."""
    names = sorted(returns)
    if not names:
        return []
    frame = pd.DataFrame({k: returns[k] for k in names})
    corr = frame.corr(min_periods=min_overlap)
    parent = {k: k for k in names}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            c = corr.loc[a, b]
            if pd.notna(c) and c > link:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for k in names:
        groups.setdefault(find(k), []).append(k)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def family_test(pvals: dict[str, Optional[float]], groups: list[list[str]]) -> dict[str, Optional[float]]:
    """Holm-adjusted p per arm. Each cluster's p = min(1, size x its smallest member p); Holm runs across clusters;
    every member gets its cluster's adjusted p. Arms with no p-value (n/a) are not tested and get None."""
    cl, members = [], []
    for g in groups:
        ps = [pvals[k] for k in g if pvals.get(k) is not None]
        if ps:
            cl.append(min(1.0, len(ps) * min(ps)))
            members.append(g)
    adj = holm_adjust(cl)
    out: dict[str, Optional[float]] = {k: None for k in pvals}
    for g, a in zip(members, adj):
        for k in g:
            if pvals.get(k) is not None:
                out[k] = a
    return out


def promotion(st: dict, adj_p: Optional[float], trades: Optional[int] = None, alpha: float = ALPHA_LOOK,
              min_independent: int = MIN_INDEPENDENT) -> dict:
    """The decision rule, every condition shown. `trades` is given for trade-based arms (their sample size is trades,
    not independent days). Missing data fails the condition. promoted needs all three."""
    p_ok = adj_p is not None and adj_p < alpha
    top2_ok = st.get("ci_lo_x2") is not None and st["ci_lo_x2"] > 0
    size = trades if trades is not None else st.get("n_eff")
    size_ok = size is not None and size >= min_independent
    return {"p_ok": p_ok, "top2_ok": top2_ok, "size_ok": size_ok, "size": size, "promoted": p_ok and top2_ok and size_ok}


def look_status(today: str, looks: tuple = LOOKS) -> dict:
    """Is `today` (YYYY-MM-DD) a look date, and which look is next."""
    nxt = next((d for d in looks if d >= today), None)
    return {"is_look": today in looks, "number": looks.index(today) + 1 if today in looks else None, "next": nxt}
