"""Feature drift guard: training-time quantiles, PSI over the trailing bars, abstain reasons.

At training time `reference(X)` stores, per feature, the interior quantile edges and the share of
training rows that fall in each resulting bin. At prediction time `psi_frame` computes, for every row,
the Population Stability Index of the trailing `window` bars against that reference (vectorised with
cumulative counts), and `abstain_reasons` turns it into one reason per row:

    "nan"    a feature is missing (or non-finite) on that row
    "drift"  at least `frac` of the features have PSI > `psi_max` over the trailing window
    "stale"  the model is older than `max_model_age_days`, or the last bar is older than
             `max_bar_age_days` (only when `now` is given)

Few bins (quintiles by default) keep the sampling noise of a 60-bar PSI small: for i.i.d. data
PSI ~ chi2(bins - 1) / window, i.e. about 0.07 on average for 5 bins and 60 bars, well below 0.2.
Pure numpy/pandas; no model code.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

WINDOW = 60                 # trailing bars the PSI is computed over
N_BINS = 5                  # quintiles
PSI_MAX = 0.2               # per-feature "significant shift" level
CAP_MAX = 1.0               # features whose own in-sample 60-bar PSI exceeds this cannot be tested in 60 bars
DRIFT_FRAC = 0.25           # share of features over PSI_MAX that makes the whole row drift
MAX_MODEL_AGE_DAYS = 90     # model older than this is stale (retrain_days is 30)
MAX_BAR_AGE_DAYS = 7        # last bar older than this (calendar days) is stale, when `now` is given
_EPS = 1e-4

REASONS = ("nan", "drift", "stale")


def reference(X: pd.DataFrame, n_bins: int = N_BINS, window: int = WINDOW) -> dict:
    """Per-feature interior quantile edges, training bin shares and drift level. JSON-serialisable.

    Tied edges are collapsed, so discrete features get fewer bins; a constant feature gets none
    (edges == []) and is ignored by the PSI."""
    qs = np.linspace(0, 1, n_bins + 1)[1:-1]
    feats = {}
    for c in X.columns:
        v = X[c].to_numpy(float)
        v = v[np.isfinite(v)]
        if len(v) == 0:
            feats[str(c)] = {"edges": [], "share": [1.0]}
            continue
        edges = np.unique(np.quantile(v, qs))
        share = np.bincount(np.searchsorted(edges, v, side="left"), minlength=len(edges) + 1) / len(v)
        feats[str(c)] = {"edges": [float(e) for e in edges], "share": [float(s) for s in share]}
    ref = {"n_bins": n_bins, "n_rows": int(len(X)), "features": feats,
            "quantiles": {str(c): [float(q) for q in np.nanquantile(X[c].to_numpy(float),
                                                                    [0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0])]
                          for c in X.columns if len(X) and np.isfinite(X[c].to_numpy(float)).any()}}
    # Self-calibrated per-feature level: smooth, long-window features (63-day returns, 200-bar SMA distance)
    # have only a handful of effective observations in 60 bars, so their PSI against their own training
    # distribution is routinely > 0.2. Their drift level is the 99th percentile of that in-sample rolling PSI.
    tr = psi_frame(X, ref, window).dropna(how="all")
    caps = {str(c): float(max(PSI_MAX, np.nanquantile(tr[c].to_numpy(float), 0.99)))
            if tr[c].notna().any() else PSI_MAX for c in tr.columns}
    for c, spec in feats.items():
        spec["psi_cap"] = caps.get(c, PSI_MAX)
    return ref


def psi_frame(X: pd.DataFrame, ref: dict, window: int = WINDOW) -> pd.DataFrame:
    """Row-wise PSI of the trailing `window` bars (including the row) against the reference.

    NaN for rows with fewer than `window` finite observations of that feature in the window.
    Row t only uses rows <= t."""
    out = pd.DataFrame(np.nan, index=X.index, columns=[c for c in X.columns if str(c) in ref["features"]])
    for c in out.columns:
        spec = ref["features"][str(c)]
        edges = np.asarray(spec["edges"], float)
        if len(edges) == 0:
            out[c] = 0.0
            continue
        v = X[c].to_numpy(float)
        ok = np.isfinite(v)
        nb = len(edges) + 1
        onehot = np.zeros((len(v), nb))
        idx = np.searchsorted(edges, np.where(ok, v, 0.0), side="left")
        onehot[np.arange(len(v))[ok], idx[ok]] = 1.0
        cs = np.vstack([np.zeros((1, nb)), np.cumsum(onehot, axis=0)])
        counts = cs[window:] - cs[:-window] if len(v) >= window else np.empty((0, nb))
        n_ok = counts.sum(axis=1)
        exp = np.clip(np.asarray(spec["share"], float), _EPS, None)
        act = np.clip(counts / np.maximum(n_ok, 1)[:, None], _EPS, None)
        psi = ((act - exp) * np.log(act / exp)).sum(axis=1)
        psi[n_ok < window] = np.nan
        res = np.full(len(v), np.nan)
        res[window - 1:] = psi
        out[c] = res
    return out


def drift_flags(X: pd.DataFrame, ref: dict, window: int = WINDOW, psi_max: float = PSI_MAX,
                frac: float = DRIFT_FRAC) -> pd.Series:
    """True where >= `frac` of the testable features exceed their drift level (>= `psi_max`, see `reference`)
    over the trailing window. Features too slow to test in `window` bars (cap > CAP_MAX) do not vote."""
    psi = psi_frame(X, ref, window)
    if psi.shape[1] == 0:
        return pd.Series(False, index=X.index)
    cap = pd.Series({c: max(psi_max, ref["features"][str(c)].get("psi_cap", psi_max)) for c in psi.columns})
    testable = [c for c in psi.columns if cap[c] <= CAP_MAX]
    if not testable:
        return pd.Series(False, index=X.index)
    return (psi[testable] > cap[testable]).mean(axis=1) >= frac


def stale_reason(*, model_created: Optional[datetime] = None, last_bar: Optional[pd.Timestamp] = None,
                 now: Optional[datetime] = None, max_model_age_days: float = MAX_MODEL_AGE_DAYS,
                 max_bar_age_days: float = MAX_BAR_AGE_DAYS) -> dict:
    """{'model': bool, 'bar': bool}. The model check uses the wall clock when `now` is None; the bar check
    only runs when `now` is given (historical frames are never 'stale')."""
    wall = now or datetime.now(timezone.utc)
    if wall.tzinfo is None:
        wall = wall.replace(tzinfo=timezone.utc)
    model = False
    if model_created is not None:
        mc = model_created if model_created.tzinfo else model_created.replace(tzinfo=timezone.utc)
        model = (wall - mc).total_seconds() / 86400.0 > max_model_age_days
    bar = False
    if now is not None and last_bar is not None:
        lb = pd.Timestamp(last_bar)
        lb = lb.tz_localize("UTC") if lb.tzinfo is None else lb.tz_convert("UTC")
        bar = (pd.Timestamp(wall) - lb).total_seconds() / 86400.0 > max_bar_age_days
    return {"model": bool(model), "bar": bool(bar)}


def abstain_reasons(X: pd.DataFrame, ref: Optional[dict], *, stale_all: bool = False,
                    stale_last: bool = False, window: int = WINDOW) -> pd.Series:
    """One reason per row ('' when the row may be used). Precedence: stale, nan, drift."""
    reason = pd.Series("", index=X.index, dtype=object)
    if ref is not None:
        reason[drift_flags(X, ref, window)] = "drift"
    reason[~np.isfinite(X.to_numpy(float)).all(axis=1)] = "nan"
    if stale_all:
        reason[:] = "stale"
    elif stale_last and len(reason):
        reason.iloc[-1] = "stale"
    return reason
