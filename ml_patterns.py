"""
ML pattern recognition: trains tree models on stationary features to predict next-bar
direction, validated only out of sample.

Leakage controls (WS2.5):
  * labels come from ml.labels.make_labels; no target column can reach X
  * no scaler (tree models are scale-invariant)
  * CV uses PurgedTimeSeriesSplit (gap = horizon + embargo, fixed test block size)
  * walk_forward_predict retrains every ML_RETRAIN_DAYS bars on strictly past, purged data;
    only those OOS predictions are ever backtested (oos_start is reported)
  * metrics: log-loss, Brier, AUC, accuracy minus the majority-class rate

Models: Random Forest, XGBoost, LightGBM (soft-voting ensemble of whichever are installed).
"""

import logging
import pickle
import re
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

try:
    from xgboost import XGBClassifier
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from lightgbm import LGBMClassifier
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

import config
from features import prepare_ml_data, compute_features, get_feature_columns, horizon_from_target
from ml.labels import make_labels
from ml.splits import PurgedTimeSeriesSplit

logger = logging.getLogger(__name__)

MIN_TRAIN_ROWS = 100


class _ConstantClassifier:
    """Used when a training window contains a single class: explicit p_up in {0, 1}."""

    def __init__(self, cls: int):
        self.classes_ = np.array([cls])

    def predict_proba(self, X):
        return np.ones((len(X), 1))

    def predict(self, X):
        return np.full(len(X), self.classes_[0])


def p_up(model, X) -> np.ndarray:
    """P(class 1) from any fitted model, handling single-class models explicitly."""
    proba = model.predict_proba(X)
    classes = list(model.classes_)
    if 1 in classes:
        return proba[:, classes.index(1)]
    return np.zeros(len(X))  # only class 0 seen -> p_up = 0


def auc_null_se(y: np.ndarray) -> float:
    """Standard error of AUC under the no-skill null (Hanley-McNeil, equal-rank approximation)."""
    n1, n0 = int((y == 1).sum()), int((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float(np.sqrt((n1 + n0 + 1) / (12.0 * n1 * n0)))


def classification_metrics(y_true, p, y_train_majority: int | None = None) -> dict:
    """log-loss, Brier, AUC (None if single class), accuracy, accuracy minus majority-class rate."""
    y_true = np.asarray(y_true).astype(int)
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    pred = (p >= 0.5).astype(int)
    acc = float((pred == y_true).mean())
    maj = y_train_majority if y_train_majority is not None else int(y_true.mean() >= 0.5)
    maj_rate = float((y_true == maj).mean())
    auc = float(roc_auc_score(y_true, p)) if len(np.unique(y_true)) == 2 else None
    return {
        "logloss": float(log_loss(y_true, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y_true, p)),
        "auc": auc,
        "accuracy": acc,
        "acc_minus_majority": acc - maj_rate,
        "n": int(len(y_true)),
    }


def _mean_std(vals):
    v = [x for x in vals if x is not None and np.isfinite(x)]
    return (float(np.mean(v)), float(np.std(v))) if v else (None, None)


class MLPatternDetector:
    """Purged-CV trainer and walk-forward OOS predictor."""

    def __init__(
        self,
        target_col: str = "target_up_1d",
        n_estimators: int = config.ML_N_ESTIMATORS,
        min_confidence: float = config.ML_MIN_CONFIDENCE,
        n_splits: int = 5,
        cv_test_bars: int = config.ML_CV_TEST_BARS,
        embargo: int = config.ML_EMBARGO_BARS,
        retrain_days: int = config.ML_RETRAIN_DAYS,
    ):
        self.target_col = target_col
        self.horizon = horizon_from_target(target_col)
        self.n_estimators = n_estimators
        self.min_confidence = min_confidence
        self.n_splits = n_splits
        self.cv_test_bars = cv_test_bars
        self.embargo = embargo
        self.retrain_days = retrain_days
        self.model = None
        self.model_names: list[str] = []
        self.feature_cols: list[str] = []
        self.feature_importances: Optional[pd.Series] = None
        self.oos_start = None
        self.oos_metrics: dict = {}

    # ------------------------------------------------------------------ models
    @property
    def model_type(self) -> str:
        if not self.model_names:
            return "none"
        if len(self.model_names) == 1:
            return self.model_names[0]
        return "ensemble_" + "_".join(self.model_names)

    @property
    def gap(self) -> int:
        return self.horizon + self.embargo

    def _build_ensemble(self):
        """Soft-voting ensemble of the installed models (no scaler: trees are scale-invariant)."""
        seed = config.SEED
        estimators = [("rf", RandomForestClassifier(
            n_estimators=self.n_estimators, max_depth=8, min_samples_leaf=20,
            class_weight="balanced", random_state=seed, n_jobs=1))]
        if HAS_XGB:
            estimators.append(("xgb", XGBClassifier(
                n_estimators=self.n_estimators, max_depth=6, learning_rate=0.05, subsample=0.8,
                colsample_bytree=0.8, eval_metric="logloss", random_state=seed, n_jobs=1,
                verbosity=0)))
        if HAS_LGBM:
            estimators.append(("lgbm", LGBMClassifier(
                n_estimators=self.n_estimators, max_depth=6, learning_rate=0.05, subsample=0.8,
                subsample_freq=1, colsample_bytree=0.8, class_weight="balanced",
                random_state=seed, n_jobs=1, verbose=-1, deterministic=True,
                force_row_wise=True)))
        self.model_names = [n for n, _ in estimators]
        if len(estimators) > 1:
            return VotingClassifier(estimators=estimators, voting="soft")
        return estimators[0][1]

    def _fit(self, X, y):
        if y.nunique() < 2:
            self.model_names = [f"constant_{int(y.iloc[0])}"]
            return _ConstantClassifier(int(y.iloc[0]))
        model = self._build_ensemble()
        model.fit(X, y)
        return model

    def _importance(self, model, X_oos, y_oos) -> Optional[pd.Series]:
        """Permutation importance (log-loss increase) on an out-of-sample block."""
        if len(X_oos) < 10 or isinstance(model, _ConstantClassifier):
            return None

        def scorer(est, X, y):
            return -log_loss(y, np.clip(p_up(est, X), 1e-6, 1 - 1e-6), labels=[0, 1])

        try:
            res = permutation_importance(model, X_oos, y_oos, scoring=scorer, n_repeats=3,
                                         random_state=config.SEED, n_jobs=1)
            return pd.Series(res.importances_mean, index=X_oos.columns).sort_values(ascending=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("permutation importance failed: %s", e)
            return None

    # ------------------------------------------------------------------ training
    def train(self, df: pd.DataFrame, compute_importance: bool = True) -> dict:
        """Purged walk-forward CV, then fit the final model on all labelled rows.

        Returns CV metrics (mean/std over folds). Note: the final model has seen every row
        of `df`; never score it on `df`.
        """
        X, y, cols = prepare_ml_data(df, self.target_col, horizon=self.horizon)
        self.feature_cols = list(cols)
        if len(X) < MIN_TRAIN_ROWS:
            logger.warning("Insufficient data for ML training")
            return {"error": "insufficient_data"}

        splitter = PurgedTimeSeriesSplit(self.n_splits, self.cv_test_bars, self.horizon,
                                         self.embargo, MIN_TRAIN_ROWS)
        folds, last_model, last_block = [], None, None
        for train_idx, test_idx in splitter.split(X):
            X_tr, y_tr = X.iloc[train_idx], y.iloc[train_idx]
            X_te, y_te = X.iloc[test_idx], y.iloc[test_idx]
            model = self._fit(X_tr, y_tr)
            maj = int(y_tr.mean() >= 0.5)
            folds.append(classification_metrics(y_te, p_up(model, X_te), maj))
            last_model, last_block = model, (X_te, y_te)
        if not folds:
            return {"error": "insufficient_data_for_cv", "n_samples": len(X)}

        if compute_importance and last_model is not None:
            self.feature_importances = self._importance(last_model, *last_block)

        self.model = self._fit(X, y)

        def col(k):
            return [f[k] for f in folds]

        ll_m, ll_s = _mean_std(col("logloss"))
        br_m, br_s = _mean_std(col("brier"))
        auc_m, auc_s = _mean_std(col("auc"))
        acc_m, acc_s = _mean_std(col("accuracy"))
        am_m, am_s = _mean_std(col("acc_minus_majority"))
        metrics = {
            "cv_scores": col("accuracy"),
            "mean_cv_accuracy": acc_m, "std_cv_accuracy": acc_s,
            "mean_cv_logloss": ll_m, "std_cv_logloss": ll_s,
            "mean_cv_brier": br_m, "std_cv_brier": br_s,
            "mean_cv_auc": auc_m, "std_cv_auc": auc_s,
            "mean_cv_acc_minus_majority": am_m, "std_cv_acc_minus_majority": am_s,
            "n_folds": len(folds),
            "n_features": len(self.feature_cols),
            "n_samples": len(X),
            "model_type": self.model_type,
            "top_features": (self.feature_importances.head(10).to_dict()
                             if self.feature_importances is not None else {}),
        }
        logger.info("ML CV: acc=%.3f auc=%s over %d folds", acc_m, auc_m, len(folds))
        return metrics

    # ------------------------------------------------------------------ prediction
    def _empty(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["signal"] = 0
        out["ml_confidence"] = 0.0
        out["oos"] = False
        return out

    def _signals(self, p: pd.Series) -> pd.Series:
        s = pd.Series(0, index=p.index)
        s[p > self.min_confidence] = 1
        s[p < 1 - self.min_confidence] = -1
        return s

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """Signals from the trained model for every row of `df` with valid features.

        The model was trained on its own training frame; rows inside it are IN-SAMPLE.
        Use walk_forward_predict for anything that gets backtested.
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        feat = compute_features(df, include_targets=False)
        X = feat[self.feature_cols]
        X = X.copy()
        X[X.columns[X.isna().all()]] = 0.0  # zero-volume instruments: constant volume columns
        X = X.dropna()
        out = self._empty(df)
        if X.empty:
            return out
        p = pd.Series(p_up(self.model, X), index=X.index)
        out.loc[p.index, "ml_confidence"] = p
        out.loc[p.index, "signal"] = self._signals(p)
        return out

    def walk_forward_predict(
        self,
        df: pd.DataFrame,
        train_pct: float = config.ML_TRAIN_TEST_SPLIT,
        retrain_every: int | None = None,
    ) -> pd.DataFrame:
        """Out-of-sample predictions from an expanding, periodically retrained model.

        The first model trains on rows whose labels resolved before bar `split_idx - gap`;
        every `retrain_every` bars it is refit on strictly earlier (purged) rows. Bars before
        `oos_start` get signal 0. Returned frame adds `oos` (bool); backtest only oos rows.
        """
        retrain_every = retrain_every or self.retrain_days
        n = len(df)
        split_idx = int(n * train_pct)
        out = self._empty(df)
        if split_idx < MIN_TRAIN_ROWS or n - split_idx < 20:
            return out

        X, y, cols = prepare_ml_data(df, self.target_col, horizon=self.horizon, dead_cutoff=split_idx)
        self.feature_cols = list(cols)
        feat = compute_features(df, include_targets=False)[cols]
        # columns with no values in the history available BEFORE oos_start (e.g. zero-volume asset classes) are
        # neutral (0); decided on the training rows only, so the feature schema never depends on future bars
        dead = feat.columns[feat.iloc[:split_idx].isna().all()]
        feat = feat.copy()
        feat[dead] = 0.0
        pos = pd.Series(np.arange(n), index=df.index)
        x_pos = pos.loc[X.index].to_numpy()
        self.oos_start = df.index[split_idx]
        out["oos"] = np.arange(n) >= split_idx

        probs = {}
        first_model, last_model = None, None
        for a in range(split_idx, n, retrain_every):
            b = min(a + retrain_every, n)
            tr = x_pos <= a - self.gap - 1
            if tr.sum() < MIN_TRAIN_ROWS:
                continue
            model = self._fit(X[tr], y[tr])
            Xc = feat.iloc[a:b].dropna()
            if not Xc.empty:
                for idx, val in zip(Xc.index, p_up(model, Xc)):
                    probs[idx] = val
            last_model = model
            if first_model is None:
                first_model = model
        if not probs:
            return out
        p = pd.Series(probs).sort_index()
        out.loc[p.index, "ml_confidence"] = p
        out.loc[p.index, "signal"] = self._signals(p)
        self.model = last_model
        self._oos_metrics(df, p)
        # model_type reflects the models actually used (names set by the last _fit)
        if first_model is not None:
            oos_idx = p.index.intersection(X.index)
            self.feature_importances = self._importance(first_model, feat.loc[oos_idx], y.loc[oos_idx]) \
                if len(oos_idx) else None
        return out

    def _oos_metrics(self, df: pd.DataFrame, p: pd.Series):
        lab = make_labels(df, self.horizon)["y"].reindex(p.index)
        ok = lab.notna()
        yv, pv = lab[ok].astype(int).to_numpy(), p[ok].to_numpy()
        if len(yv) < 2:
            self.oos_metrics = {"n": int(len(yv))}
            return
        train_maj = int(make_labels(df.loc[:self.oos_start], self.horizon)["y"].dropna().mean() >= 0.5)
        m = classification_metrics(yv, pv, train_maj)
        base = 0.5 + 1.645 * auc_null_se(yv) if m["auc"] is not None else None
        m["baseline_auc"] = base
        m["auc_above_baseline"] = bool(m["auc"] is not None and base is not None and m["auc"] > base)
        self.oos_metrics = m

    # ------------------------------------------------------------------ persistence
    def save(self, path: str = "ml_model.pkl"):
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "feature_cols": self.feature_cols,
                         "target_col": self.target_col, "min_confidence": self.min_confidence,
                         "model_names": self.model_names}, f)

    def load(self, path: str = "ml_model.pkl"):
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.feature_cols = data["feature_cols"]
        self.target_col = data["target_col"]
        self.horizon = horizon_from_target(self.target_col)
        self.min_confidence = data["min_confidence"]
        self.model_names = data.get("model_names", [])


def ml_pattern_signal(df: pd.DataFrame) -> pd.DataFrame:
    """Pattern-interface convenience: OOS walk-forward signals (signal 0 before oos_start)."""
    return MLPatternDetector().walk_forward_predict(df)


def ml_scan_candidate(df: pd.DataFrame, symbol: str) -> dict:
    """Walk-forward OOS evaluation + latest signal for the scan.

    Returns dict(direction, confidence, oos_auc, baseline_auc, oos_backtest_summary,
    oos_start) plus extras: symbol, model_type, oos_validated, oos_metrics, backtest.
    direction is 1 (long), -1 (short) or 0 (no recent signal / insufficient data);
    confidence is the model probability for that direction (p_up if long, 1-p_up if short).
    oos_validated = OOS AUC above baseline AND >= MIN_TRADES_OOS OOS trades AND PSR > OOS_PSR_MIN.
    """
    import backtester

    det = MLPatternDetector()
    pred = det.walk_forward_predict(df)
    res = {"symbol": symbol, "direction": 0, "confidence": 0.0, "oos_auc": None,
           "baseline_auc": None, "oos_backtest_summary": None, "oos_start": None,
           "model_type": det.model_type, "oos_validated": False, "oos_metrics": det.oos_metrics,
           "backtest": None, "oos_frame": None}
    if det.oos_start is None or not det.oos_metrics:
        return res
    oos = pred.loc[pred["oos"]]
    bt = backtester.classic_backtest(oos, symbol, "ml_ensemble")
    m = det.oos_metrics
    res.update(oos_auc=m.get("auc"), baseline_auc=m.get("baseline_auc"),
               oos_start=det.oos_start.isoformat(), backtest=bt, oos_frame=oos,
               oos_backtest_summary=bt.numeric_summary())
    win = oos.tail(config.VALIDATION_WINDOW_DAYS)
    nz = win[win["signal"] != 0]
    if len(nz):
        d = int(nz["signal"].iloc[-1])
        p = float(nz["ml_confidence"].iloc[-1])
        res["direction"] = d
        res["confidence"] = p if d == 1 else 1.0 - p
    psr = getattr(bt, "psr", None)
    res["oos_validated"] = bool(m.get("auc_above_baseline") and bt.total_trades >= config.MIN_TRADES_OOS
                                and psr is not None and psr > config.OOS_PSR_MIN)
    return res
