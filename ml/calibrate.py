"""Probability calibration and the trading threshold.

* `fit_sigmoid` calibrates a FITTED model on a held-out, time-ordered validation block with
  sklearn's CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid"): the base model is not
  refitted, only the two sigmoid parameters are learned.
* `choose_thresholds` replaces the fixed 0.60 / 0.40 pair: for each side it picks the probability cut
  that maximises the validation block's total expected value AFTER round-trip costs, subject to a
  minimum trade count and a minimum t-statistic of the net mean return. Probabilities between the two
  cuts are an abstain band; when no cut has a positive, significant net expected value that side
  never trades (cut = +/-inf).
* `ece` / `reliability` are the usual calibration diagnostics.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

MIN_CLASS_COUNT = 10        # each class needs this many validation rows for a sigmoid fit
THRESH_GRID = tuple(np.round(np.arange(0.52, 0.801, 0.01), 2))
THRESH_MIN_TRADES = 20
THRESH_MIN_T = 2.0          # net mean return t-statistic required of the chosen side (grid search -> multiplicity)


def _p1(model, X) -> np.ndarray:
    proba = model.predict_proba(X)
    classes = list(model.classes_)
    return proba[:, classes.index(1)] if 1 in classes else np.zeros(len(X))


def fit_sigmoid(model, X_val, y_val):
    """(calibrated_model, True), or (model, False) when the validation block cannot support a fit."""
    y = np.asarray(y_val).astype(int)
    if len(y) == 0 or min((y == 0).sum(), (y == 1).sum()) < MIN_CLASS_COUNT or len(getattr(model, "classes_", [])) < 2:
        return model, False
    cal = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
    cal.fit(X_val, y)
    return cal, True


def reliability(y, p, n_bins: int = 10, min_n: int = 1) -> list[dict]:
    """Equal-width probability buckets: n, mean predicted, observed frequency (buckets with n < min_n dropped)."""
    y = np.asarray(y, float)
    p = np.asarray(p, float)
    idx = np.minimum((p * n_bins).astype(int), n_bins - 1)
    out = []
    for b in range(n_bins):
        m = idx == b
        if m.sum() >= min_n and m.sum() > 0:
            out.append({"bin": b, "n": int(m.sum()), "mean_p": float(p[m].mean()), "freq": float(y[m].mean())})
    return out


def ece(y, p, n_bins: int = 10) -> float:
    """Expected calibration error: bucket-size weighted mean |observed frequency - mean predicted|."""
    rel = reliability(y, p, n_bins)
    n = sum(r["n"] for r in rel)
    return float(sum(r["n"] * abs(r["freq"] - r["mean_p"]) for r in rel) / n) if n else float("nan")


def _best_cut(p, net, side: int, grid, min_trades: int, min_t: float) -> Optional[dict]:
    best = None
    for t in grid:
        m = p >= t if side > 0 else p <= 1.0 - t
        n = int(m.sum())
        if n < min_trades:
            continue
        r = net[m]
        sd = r.std(ddof=1)
        mean = float(r.mean())
        tstat = mean / (sd / np.sqrt(n)) if sd > 0 else (np.inf if mean > 0 else 0.0)
        if mean <= 0 or tstat < min_t:
            continue
        total = float(r.sum())
        if best is None or total > best["total_net"]:
            best = {"cut": float(t), "n": n, "mean_net": mean, "total_net": total, "t": float(tstat)}
    return best


def choose_thresholds(p, fwd_ret, cost: float, *, grid=THRESH_GRID, min_trades: int = THRESH_MIN_TRADES,
                      min_t: float = THRESH_MIN_T) -> dict:
    """Pick the long and short cuts on a validation block.

    p: calibrated P(up); fwd_ret: the realised forward return of the label horizon; cost: round-trip cost as a
    fraction (applied once per trade). Long when p >= t_long, short when p <= t_short (= 1 - the short cut),
    nothing in between. A side with no qualifying cut gets t_long = +inf / t_short = -inf."""
    p = np.asarray(p, float)
    r = np.asarray(fwd_ret, float)
    ok = np.isfinite(p) & np.isfinite(r)
    p, r = p[ok], r[ok]
    long_ = _best_cut(p, r - cost, +1, grid, min_trades, min_t)
    short = _best_cut(p, -r - cost, -1, grid, min_trades, min_t)
    return {"t_long": long_["cut"] if long_ else float("inf"),
            "t_short": (1.0 - short["cut"]) if short else float("-inf"),
            "long": long_, "short": short, "n_val": int(len(p)), "cost": float(cost)}
