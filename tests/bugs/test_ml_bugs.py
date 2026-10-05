"""Bug-pinning tests for the ML pipeline (WS0.3: ML-1, ML-2, ML-3).

xfail(strict) tests assert the CORRECT behaviour and currently fail with AssertionError.
"""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backtester
import main
import ml_patterns
from features import compute_features
from ml_patterns import MLPatternDetector

BUG = dict(strict=True, raises=AssertionError)


@pytest.fixture
def fast_ml(monkeypatch):
    """RandomForest only, tiny forest, so training takes well under a second."""
    monkeypatch.setattr(ml_patterns, "HAS_XGB", False)
    monkeypatch.setattr(ml_patterns, "HAS_LGBM", False)
    orig = MLPatternDetector._build_ensemble

    def small(self):
        self.n_estimators = 5
        return orig(self)

    monkeypatch.setattr(MLPatternDetector, "_build_ensemble", small)


# --------------------------------------------------------------------------- ML-1
def test_ML_1_target_up_nan_on_unresolved_rows(gbm_frame):
    feat = compute_features(gbm_frame)
    # last row has no 1d forward return; last 3 rows have no 3d forward return
    assert feat["target_1d"].iloc[-1:].isna().all()
    assert feat["target_3d"].iloc[-3:].isna().all()
    assert feat["target_up_1d"].iloc[-1:].isna().all(), "unresolved 1d label must be NaN, not 0"
    assert feat["target_up_3d"].iloc[-3:].isna().all(), "unresolved 3d label must be NaN, not 0"


# --------------------------------------------------------------------------- ML-2
def test_ML_2_scaler_not_fit_on_test_folds(monkeypatch, fast_ml, gbm_frame):
    from sklearn.preprocessing import StandardScaler

    fit_sizes = []
    orig_fit = StandardScaler.fit

    def spy_fit(self, X, *a, **k):
        fit_sizes.append(len(X))
        return orig_fit(self, X, *a, **k)

    monkeypatch.setattr(StandardScaler, "fit", spy_fit)
    metrics = MLPatternDetector().train(gbm_frame)
    n = metrics["n_samples"]
    n_splits = 5
    test_size = n // (n_splits + 1)
    first_fold_train = n - n_splits * test_size  # TimeSeriesSplit(5) fold-0 training rows
    if not fit_sizes:
        return  # removing the scaler entirely is a legitimate fix (tree models are scale-invariant)
    # A leak-free CV fits a scaler on training rows only, so some fit must be no larger
    # than the first fold's training window (instead of one fit on all n rows).
    assert min(fit_sizes) <= first_fold_train, (
        f"scaler only ever fit on {fit_sizes} rows; first CV train window is {first_fold_train}")


# --------------------------------------------------------------------------- ML-3
def test_ML_3_ml_route_backtests_only_out_of_sample(monkeypatch, fast_ml, patch_fetch):
    import config
    from server import app

    seen = {}
    orig_bt = backtester.classic_backtest

    def spy_bt(df, *a, **k):
        seen["bt_index"] = df.index
        return orig_bt(df, *a, **k)

    monkeypatch.setattr(backtester, "classic_backtest", spy_bt)
    full = patch_fetch("SPY", 730)
    cutoff = full.index[int(len(full) * config.ML_TRAIN_TEST_SPLIT)]

    r = TestClient(app).post("/api/ml/predict", json={"symbol": "SPY", "period_days": 730})
    assert r.status_code == 200, r.text
    if "bt_index" in seen:  # spy only fires if the route calls backtester.classic_backtest via the module
        assert seen["bt_index"].min() >= cutoff, (
            f"backtest starts {seen['bt_index'].min()} but training ran through {cutoff}")
    curve = r.json()["equity_curve"]
    assert curve, "no equity curve"
    assert pd.Timestamp(curve[0]["date"]) >= cutoff


def test_ML_3_scan_ml_backtests_only_out_of_sample(monkeypatch, fast_ml, gbm_frame):
    seen = {}
    orig_train = MLPatternDetector.train
    orig_bt = main.classic_backtest

    def spy_train(self, df):
        seen.setdefault("train_ends", []).append(df.index[-1])
        m = orig_train(self, df)
        m["mean_cv_accuracy"] = 0.9  # defeat the accuracy gate so the flow reaches the backtest
        return m

    def spy_bt(df, *a, **k):
        seen["bt_start"] = df.index[0]
        return orig_bt(df, *a, **k)

    monkeypatch.setattr(MLPatternDetector, "train", spy_train)
    monkeypatch.setattr(main, "classic_backtest", spy_bt)
    monkeypatch.setattr(main, "check_recent_signal", lambda *a, **k: 1)
    main.scan_ml({"SPY": gbm_frame}, False)
    assert "bt_start" in seen, "scan_ml never reached the backtest"
    # Every training window is recorded; a rolling-retrain fix trains several times, a split fix once.
    # The earliest model must have been trained strictly before the first backtested bar.
    first_train_end = min(seen["train_ends"])
    assert seen["bt_start"] > first_train_end, (
        f"backtest starts {seen['bt_start']} but model trained through {first_train_end}")
