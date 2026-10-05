"""Baseline-first model selection under the purged CV, and the calibrated fitting pipeline.

Candidates (all with class_weight="balanced", so the class prior is treated the same everywhere and the
sigmoid calibration afterwards restores the probability scale):
    logreg    standardised, L2-regularised logistic regression            (the baseline)
    lgbm      shallow LightGBM (depth 3, 7 leaves)
    ensemble  soft vote of a random forest and that shallow LightGBM

Every candidate is evaluated as the FULL pipeline (fit on the early part of each purged training fold,
sigmoid calibration on its time-ordered tail, log-loss on the test block), so the comparison is about
calibrated probabilities. A more complex model replaces the incumbent only if its mean log-loss is
lower by more than one fold standard deviation of the incumbent's log-loss (and at least 2 folds
exist); with no usable CV the baseline is kept. The model abstains ('no_skill') when even the chosen
model does not beat the training base rate (a constant prior) on mean log-loss.
This module never imports the detector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config
from ml import calibrate

try:
    from lightgbm import LGBMClassifier
    HAS_LGBM = True
except ImportError:  # pragma: no cover
    HAS_LGBM = False

CANDIDATES = ("logreg", "lgbm", "ensemble")
MODEL_NAMES = {"logreg": ["logreg"], "lgbm": ["lgbm"], "ensemble": ["rf", "lgbm"]}
VAL_FRAC = 0.2
MIN_VAL = 60
MIN_FIT = 100
_CLIP = 1e-6


class ConstantClassifier:
    """A training window with a single class: explicit p_up in {0, 1}."""

    def __init__(self, cls: int):
        self.classes_ = np.array([cls])

    def predict_proba(self, X):
        return np.ones((len(X), 1))

    def predict(self, X):
        return np.full(len(X), self.classes_[0])


def cost_round_trip() -> float:
    return 2.0 * (config.COMMISSION_PCT + config.SLIPPAGE_PCT)


def build(name: str, n_estimators: int = config.ML_N_ESTIMATORS, seed: int = config.SEED):
    if name == "logreg":
        return Pipeline([("scale", StandardScaler()),
                         ("lr", LogisticRegression(C=0.1, class_weight="balanced", max_iter=1000,
                                                   random_state=seed))])
    if name in ("lgbm", "ensemble") and not HAS_LGBM:
        raise RuntimeError("lightgbm is not installed")
    lgbm = LGBMClassifier(n_estimators=n_estimators, max_depth=3, num_leaves=7, learning_rate=0.05,
                          min_child_samples=30, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                          class_weight="balanced", random_state=seed, n_jobs=1, verbose=-1,
                          deterministic=True, force_row_wise=True)
    if name == "lgbm":
        return lgbm
    if name == "ensemble":
        rf = RandomForestClassifier(n_estimators=n_estimators, max_depth=8, min_samples_leaf=20,
                                    class_weight="balanced", random_state=seed, n_jobs=1)
        return VotingClassifier([("rf", rf), ("lgbm", lgbm)], voting="soft")
    raise ValueError(f"unknown model {name!r}")


def _p1(model, X) -> np.ndarray:
    return calibrate._p1(model, X)


@dataclass
class PipelineFit:
    model: Any
    name: str
    calibrated: bool
    n_fit: int
    n_val: int
    t_long: float = float("inf")
    t_short: float = float("-inf")
    threshold_info: dict = field(default_factory=dict)
    reason: str = ""          # '' | 'no_validation'


def split_sizes(n: int, gap: int, val_frac: float = VAL_FRAC, min_val: int = MIN_VAL, min_fit: int = MIN_FIT):
    """(n_fit, n_val) for a time-ordered block of n rows: fit rows first, then `gap` purged rows, then the
    validation block. None when the block cannot hold min_fit fit rows and min_val validation rows."""
    n_val = max(min_val, int(round(n * val_frac)))
    n_fit = n - n_val - gap
    return (n_fit, n_val) if n_fit >= min_fit else None


def fit_calibrated(name: str, X, y, gap: int, *, n_estimators: int = config.ML_N_ESTIMATORS,
                   val_frac: float = VAL_FRAC):
    """Fit `name` on the early rows and sigmoid-calibrate on the time-ordered tail (purged by `gap` rows).

    Returns (model, calibrated: bool, n_fit, n_val, val_slice). With too little data the model is fitted on all
    rows, uncalibrated, and val_slice is None."""
    n = len(X)
    sizes = split_sizes(n, gap, val_frac)
    if sizes is None:
        return _fit_one(name, X, y, n_estimators), False, n, 0, None
    n_fit, n_val = sizes
    val = slice(n - n_val, n)
    model = _fit_one(name, X.iloc[:n_fit], y.iloc[:n_fit], n_estimators)
    model, cal = calibrate.fit_sigmoid(model, X.iloc[val], y.iloc[val])
    return model, cal, n_fit, n_val, val


def _fit_one(name, X, y, n_estimators):
    if y.nunique() < 2:
        return ConstantClassifier(int(y.iloc[0]))
    m = build(name, n_estimators)
    m.fit(X, y)
    return m


def fit_pipeline(name: str, X, y, ret, gap: int, *, n_estimators: int = config.ML_N_ESTIMATORS,
                 cost: Optional[float] = None) -> PipelineFit:
    """Calibrated model plus the validation-chosen thresholds. Without a usable validation block the model is
    uncalibrated and never trades (reason 'no_validation')."""
    model, cal, n_fit, n_val, val = fit_calibrated(name, X, y, gap, n_estimators=n_estimators)
    if val is None:
        return PipelineFit(model, name, False, n_fit, 0, reason="no_validation")
    pv = _p1(model, X.iloc[val])
    th = calibrate.choose_thresholds(pv, np.asarray(ret.iloc[val], float), cost_round_trip() if cost is None else cost)
    return PipelineFit(model, name, cal, n_fit, n_val, th["t_long"], th["t_short"], th)


def cv_predictions(names, X, y, folds, gap: int, *, n_estimators: int = config.ML_N_ESTIMATORS) -> dict:
    """{name: [(y_test, p_test), ...]} plus the prior's predictions under 'prior', one entry per purged fold."""
    out: dict[str, list] = {n: [] for n in names}
    out["prior"] = []
    for tr, te in folds:
        Xtr, ytr, Xte, yte = X.iloc[tr], y.iloc[tr], X.iloc[te], y.iloc[te]
        out["prior"].append((yte.to_numpy(), np.full(len(yte), float(ytr.mean()))))
        for n in names:
            model, _, _, _, _ = fit_calibrated(n, Xtr, ytr, gap, n_estimators=n_estimators)
            out[n].append((yte.to_numpy(), _p1(model, Xte)))
    return out


def _fold_ll(preds) -> list[float]:
    return [float(log_loss(y, np.clip(p, _CLIP, 1 - _CLIP), labels=[0, 1])) for y, p in preds]


def select_model(cv: dict, *, ensemble: bool = True) -> dict:
    """Decide from `cv_predictions` output.

    Returns {chosen, abstain, reason, logloss: {name: {mean, std, folds}}, prior_logloss, n_folds, margin_rule}.
    `chosen` is always a model name (the one to fit); `abstain` says whether it should trade at all."""
    ll = {n: _fold_ll(p) for n, p in cv.items() if p}
    n_folds = len(ll.get("prior", []))
    stats = {n: {"mean": float(np.mean(v)), "std": float(np.std(v)), "folds": v} for n, v in ll.items()}
    res = {"chosen": "logreg", "abstain": False, "reason": "", "logloss": stats, "n_folds": n_folds,
           "margin_rule": "challenger mean log-loss < incumbent mean - 1 fold std (incumbent)"}
    if n_folds == 0 or "logreg" not in ll:
        res["reason"] = "insufficient_cv: baseline kept"
        return res
    chosen = "logreg"
    if n_folds >= 2:
        for challenger in (["lgbm"] if "lgbm" in ll else []) + (["ensemble"] if ensemble and "ensemble" in ll else []):
            inc = stats[chosen]
            if stats[challenger]["mean"] < inc["mean"] - inc["std"]:
                chosen = challenger
    res["chosen"] = chosen
    if stats[chosen]["mean"] >= stats["prior"]["mean"]:
        res["abstain"], res["reason"] = True, "no_skill"
    return res


def select(X, y, folds, gap: int, *, n_estimators: int = config.ML_N_ESTIMATORS) -> dict:
    """Run the candidates under `folds` (list of (train_idx, test_idx)) and select. HAS_LGBM False -> baseline only."""
    names = list(CANDIDATES) if HAS_LGBM else ["logreg"]
    folds = list(folds)
    cv = cv_predictions(names, X, y, folds, gap, n_estimators=n_estimators)
    return select_model(cv)
