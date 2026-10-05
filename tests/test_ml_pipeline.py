"""WS2.5: ML without leakage. Fast tests use tiny forests; statistical tests are @slow."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.metrics import roc_auc_score

import config
import ml_patterns
from features import compute_features, get_feature_columns, prepare_ml_data
from ml.labels import make_labels
from ml.splits import PurgedTimeSeriesSplit
from ml_patterns import MLPatternDetector, ml_scan_candidate, p_up


@pytest.fixture
def tiny(monkeypatch):
    """RandomForest only, 5 trees."""
    monkeypatch.setattr(ml_patterns, "HAS_XGB", False)
    monkeypatch.setattr(ml_patterns, "HAS_LGBM", False)
    orig = MLPatternDetector._build_ensemble

    def small(self):
        self.n_estimators = 5
        return orig(self)

    monkeypatch.setattr(MLPatternDetector, "_build_ensemble", small)


# ----------------------------------------------------------------- labels / features
@pytest.mark.parametrize("h", [1, 3, 5])
def test_labels_exactly_h_trailing_nans(gbm_frame, h):
    lab = make_labels(gbm_frame, h)
    assert lab["y"].iloc[-h:].isna().all()
    assert lab["y"].iloc[:-h].notna().all()
    assert lab["fwd_ret"].iloc[-h:].isna().all()


def test_labels_exact_zero_is_nan():
    df = pd.DataFrame({"Close": [1.0, 1.0, 2.0, 1.0, 1.0]})
    y = make_labels(df, 1)["y"]
    assert np.isnan(y.iloc[0]) and y.iloc[1] == 1.0 and y.iloc[2] == 0.0 and np.isnan(y.iloc[3])
    assert np.isnan(y.iloc[4])


def test_no_target_column_reaches_X(gbm_frame):
    X, y, cols = prepare_ml_data(gbm_frame)
    assert not any(c.startswith("target_") for c in X.columns)
    assert not any(c.startswith("target_") for c in get_feature_columns(compute_features(gbm_frame)))
    assert len(X) == len(y) == len(gbm_frame) - 199 - 1  # sma_200 warm-up + 1 unresolved label


def test_features_causal_and_truncation_stable(synth):
    df = synth.gbm_ohlc(n=900, seed=3)
    full = compute_features(df, include_targets=False)
    # appending future bars never changes earlier rows
    short = compute_features(df.iloc[:700], include_targets=False)
    pd.testing.assert_frame_equal(short, full.iloc[:700])
    # starting later agrees once indicator warm-up has washed out
    late = compute_features(df.iloc[100:], include_targets=False)
    a, b = full.iloc[500:], late.loc[full.index[500]:]
    np.testing.assert_allclose(a.to_numpy(float), b.to_numpy(float), rtol=1e-3, atol=1e-4)


def test_schema_fixed_and_finite_for_zero_volume(synth):
    stock = synth.gbm_ohlc(n=500, seed=1)
    fx = synth.zero_volume_fx_frame(n=500, seed=2)
    fs, ff = compute_features(stock, include_targets=False), compute_features(fx, include_targets=False)
    assert list(fs.columns) == list(ff.columns)
    assert ff[["volume_z_21", "vwap_dist", "obv_slope_10"]].isna().all().all()
    for d in (stock, fx):
        X, _, _ = prepare_ml_data(d)
        assert list(X.columns) == list(fs.columns) and np.isfinite(X.to_numpy(float)).all()


def test_features_stationary_and_capped(gbm_frame):
    f = compute_features(gbm_frame, include_targets=False)
    for dropped in ("weekly_ret", "monthly_ret", "quarterly_ret", "atr_14", "vol_sma_5"):
        assert dropped not in f.columns
    assert f["macd"].abs().max() < 1 and f["consec_bullish"].max() <= 10 and f["consec_bearish"].max() <= 10
    scaled = compute_features(gbm_frame.assign(**{c: gbm_frame[c] * 100 for c in ["Open", "High", "Low", "Close"]}),
                              include_targets=False)
    np.testing.assert_allclose(f.drop(columns=[c for c in f if "vol_ratio_price" in c or "volume" in c]).to_numpy(float),
                               scaled.drop(columns=[c for c in f if "vol_ratio_price" in c or "volume" in c]).to_numpy(float),
                               rtol=1e-6, atol=1e-8, equal_nan=True)


def test_inf_mapped_to_nan(synth):
    df = synth.gbm_ohlc(n=300, seed=1)
    df.iloc[150, df.columns.get_loc("High")] = df["Low"].iloc[150]  # zero range
    f = compute_features(df, include_targets=False)
    assert not np.isinf(f.to_numpy(float)).any()


# ----------------------------------------------------------------- splits
def test_purged_split_gap_and_fixed_test_size():
    sp = PurgedTimeSeriesSplit(n_splits=5, test_size=50, horizon=3, embargo=5, min_train_size=20)
    folds = list(sp.split(np.zeros(500)))
    assert len(folds) == 5
    for tr, te in folds:
        assert len(te) == 50 and te[0] > tr[-1]
        # label of last train row spans up to tr[-1] + horizon; must end before test start - embargo
        assert tr[-1] + 3 < te[0] - 5 + 1 and te[0] - tr[-1] - 1 == sp.gap
    assert folds[-1][1][-1] == 499


def test_split_reduces_folds_when_data_short():
    sp = PurgedTimeSeriesSplit(n_splits=5, test_size=126, min_train_size=100)
    assert 1 <= len(list(sp.split(np.zeros(300)))) < 5


# ----------------------------------------------------------------- detector
def test_train_metrics_no_scaler_and_model_type(tiny, gbm_frame):
    det = MLPatternDetector(cv_test_bars=60)
    assert not hasattr(det, "scaler")
    m = det.train(gbm_frame)
    for k in ("mean_cv_logloss", "std_cv_logloss", "mean_cv_brier", "mean_cv_auc",
              "mean_cv_acc_minus_majority", "std_cv_acc_minus_majority", "mean_cv_accuracy", "n_samples"):
        assert k in m
    assert m["n_folds"] >= 2 and det.model_type == "rf" == m["model_type"]


def test_model_type_reflects_installed_models(monkeypatch):
    monkeypatch.setattr(ml_patterns, "HAS_XGB", True)
    monkeypatch.setattr(ml_patterns, "HAS_LGBM", False)
    det = MLPatternDetector(n_estimators=3)
    det._build_ensemble()
    assert det.model_type == "ensemble_rf_xgb"


def test_single_class_proba_explicit(tiny):
    det = MLPatternDetector()
    X = pd.DataFrame(np.random.default_rng(0).normal(size=(30, 3)))
    for cls in (0, 1):
        m = det._fit(X, pd.Series([cls] * 30))
        np.testing.assert_array_equal(p_up(m, X), np.full(30, float(cls)))


def test_walk_forward_oos_only_and_retrains(tiny, monkeypatch, gbm_frame):
    fits = []
    orig = MLPatternDetector._fit

    def spy(self, X, y):
        fits.append(len(X))
        return orig(self, X, y)

    monkeypatch.setattr(MLPatternDetector, "_fit", spy)
    det = MLPatternDetector()
    out = det.walk_forward_predict(gbm_frame)
    split = int(len(gbm_frame) * config.ML_TRAIN_TEST_SPLIT)
    assert det.oos_start == gbm_frame.index[split]
    assert (out["signal"].iloc[:split] == 0).all() and not out["oos"].iloc[:split].any()
    assert out["oos"].iloc[split:].all() and (out["signal"] != 0).any()
    assert len(fits) == -(-(len(gbm_frame) - split) // config.ML_RETRAIN_DAYS)
    assert fits == sorted(fits) and fits[0] < len(gbm_frame) * 0.75  # expanding, past-only
    assert det.oos_metrics["auc"] is not None and "baseline_auc" in det.oos_metrics


def test_walk_forward_ignores_future_bars(tiny, gbm_frame):
    """Changing bars from position 450 on leaves predictions before 450 unchanged."""
    a = MLPatternDetector().walk_forward_predict(gbm_frame)
    mod = gbm_frame.copy()
    mod.iloc[450:, :4] = mod.iloc[450:, :4].to_numpy() * 1.37
    b = MLPatternDetector().walk_forward_predict(mod)
    np.testing.assert_allclose(a["ml_confidence"].iloc[:450], b["ml_confidence"].iloc[:450], atol=1e-12, rtol=0)


def test_deterministic_with_full_ensemble(monkeypatch, gbm_frame):
    orig = MLPatternDetector._build_ensemble

    def small(self):
        self.n_estimators = 8
        return orig(self)

    monkeypatch.setattr(MLPatternDetector, "_build_ensemble", small)
    r = [MLPatternDetector().walk_forward_predict(gbm_frame.iloc[:420])["ml_confidence"] for _ in range(2)]
    pd.testing.assert_series_equal(r[0], r[1])


def test_oos_importance_reported(tiny, gbm_frame):
    det = MLPatternDetector()
    det.walk_forward_predict(gbm_frame)
    assert det.feature_importances is not None and set(det.feature_importances.index) <= set(det.feature_cols)


def test_ml_scan_candidate_contract(tiny, gbm_frame):
    r = ml_scan_candidate(gbm_frame, "SPY")
    for k in ("direction", "confidence", "oos_auc", "baseline_auc", "oos_backtest_summary", "oos_start"):
        assert k in r
    assert r["oos_start"] == gbm_frame.index[int(len(gbm_frame) * config.ML_TRAIN_TEST_SPLIT)].isoformat()
    assert r["direction"] in (-1, 0, 1) and 0.0 <= r["confidence"] <= 1.0
    if r["direction"]:
        assert r["confidence"] > config.ML_MIN_CONFIDENCE - 1e-9
    short = ml_scan_candidate(gbm_frame.iloc[:120], "SPY")
    assert short["direction"] == 0 and short["oos_start"] is None


def test_route_reports_oos_and_keeps_keys(tiny, patch_fetch):
    from server import app
    patch_fetch("SPY", 730)
    r = TestClient(app).post("/api/ml/predict", json={"symbol": "SPY", "period_days": 730})
    assert r.status_code == 200, r.text
    j = r.json()
    for k in ("symbol", "model_type", "feature_importance", "signals", "total_signals", "metrics",
              "equity_curve", "is_valid", "oos_start", "oos_metrics", "n_oos_bars"):
        assert k in j
    assert j["model_type"] == "rf"
    assert all(pd.Timestamp(s["date"]) >= pd.Timestamp(j["oos_start"]) for s in j["signals"])
    assert pd.Timestamp(j["equity_curve"][0]["date"]) >= pd.Timestamp(j["oos_start"])


# ----------------------------------------------------------------- slow statistical tests
def _cv_oos_auc(X, y, n_est=40, test_size=300, horizon=1):
    from sklearn.ensemble import RandomForestClassifier
    sp = PurgedTimeSeriesSplit(5, test_size, horizon, config.ML_EMBARGO_BARS, 100)
    ys, ps = [], []
    for tr, te in sp.split(X):
        m = RandomForestClassifier(n_estimators=n_est, max_depth=6, min_samples_leaf=20,
                                   random_state=config.SEED, n_jobs=-1).fit(X.iloc[tr], y.iloc[tr])
        ys.append(y.iloc[te]); ps.append(p_up(m, X.iloc[te]))
    return roc_auc_score(np.concatenate(ys), np.concatenate(ps))


@pytest.mark.slow
def test_leakage_canary_next_bar_feature_gives_auc_near_one(synth):
    df = synth.gbm_ohlc(n=2200, seed=11)
    X, y, _ = prepare_ml_data(df)
    leak = make_labels(df, 1)["fwd_ret"].reindex(X.index)  # next-bar return as a "feature"
    auc = _cv_oos_auc(X.assign(leak=leak), y, n_est=30)
    assert auc > 0.95, auc


@pytest.mark.slow
def test_shuffled_labels_give_auc_near_half(synth):
    df = synth.gbm_ohlc(n=2200, seed=12)
    X, y, _ = prepare_ml_data(df)
    ys = pd.Series(np.random.default_rng(config.SEED).permutation(y.to_numpy()), index=y.index)
    auc = _cv_oos_auc(X, ys)
    assert abs(auc - 0.5) < 0.05, auc


@pytest.mark.slow
def test_random_walk_oos_sharpe_about_zero_over_50_seeds(synth):
    import backtester
    sharpes, net_sharpes, viol = [], [], 0
    for seed in range(50):
        df = synth.gbm_ohlc(n=700, seed=1000 + seed, mu=0.0)
        det = MLPatternDetector(n_estimators=20)
        out = det.walk_forward_predict(df)
        nz = out.index[(out["signal"] != 0).to_numpy()]
        viol += int((nz < det.oos_start).sum())
        oos = out.loc[out["oos"]]
        # Gross, no stop/target: the synthetic wicks are drawn independently of the close path, which
        # biases stop/target touches (see BT-5 notes), so the no-skill check isolates the model.
        bt = backtester.classic_backtest(oos, "RW", "ml_ensemble", stop_loss=None, take_profit=None,
                                         commission=0.0, slippage=0.0)
        net = backtester.classic_backtest(oos, "RW", "ml_ensemble")
        sharpes.append(bt.sharpe_ratio)
        net_sharpes.append(net.sharpe_ratio)
    sharpes = np.array(sharpes)
    n_oos = 700 - int(700 * config.ML_TRAIN_TEST_SPLIT)
    thresh = 1.645 * np.sqrt(252 / n_oos)  # 95% one-sided null threshold for annualised Sharpe
    assert viol == 0
    assert abs(sharpes.mean()) < 0.5, sharpes.mean()
    assert (sharpes > thresh).mean() < 0.05, (sharpes > thresh).mean()
    assert np.mean(net_sharpes) < sharpes.mean()  # costs only hurt a no-skill strategy


def test_walk_forward_training_rows_resolve_before_block_start_minus_embargo(tiny, gbm_frame, monkeypatch):
    """Every fit sees only rows whose horizon-bar label resolved before (block start - embargo): spy on _fit."""
    import sys
    seen = []
    orig = MLPatternDetector._fit

    def spy(self, X, y):
        a = sys._getframe(1).f_locals["a"]                 # start of the block this model will predict
        seen.append((a, int(gbm_frame.index.get_indexer(X.index).max())))
        return orig(self, X, y)

    monkeypatch.setattr(MLPatternDetector, "_fit", spy)
    det = MLPatternDetector()
    det.walk_forward_predict(gbm_frame)
    assert len(seen) >= 2
    for a, last_row in seen:
        assert last_row + det.horizon < a - det.embargo, (a, last_row)


def test_walk_forward_ignores_random_future_bars(tiny, gbm_frame):
    """Random multiplicative noise on every bar from position 450 leaves earlier predictions unchanged."""
    a = MLPatternDetector().walk_forward_predict(gbm_frame)
    mod = gbm_frame.copy()
    f = np.random.default_rng(5).uniform(0.7, 1.4, size=len(mod) - 450)
    mod.iloc[450:, :4] = mod.iloc[450:, :4].to_numpy() * f[:, None]
    b = MLPatternDetector().walk_forward_predict(mod)
    np.testing.assert_allclose(a["ml_confidence"].iloc[:450], b["ml_confidence"].iloc[:450], atol=1e-12, rtol=0)


def test_prepare_ml_data_dead_columns_use_only_rows_before_the_cutoff():
    """Volume NaN before the split and present after it: with dead_cutoff the volume columns are neutral (0)
    for the early training rows (same schema as at prediction), instead of those rows being dropped."""
    import numpy as np
    from features import prepare_ml_data
    from tests.test_validation import path_walk
    df = path_walk(600, 3)
    df["Volume"] = np.where(np.arange(len(df)) < 300, np.nan, 1e6)
    full, _, _ = prepare_ml_data(df)
    cut, _, cols = prepare_ml_data(df, dead_cutoff=250)
    assert cut.index[0] < df.index[300] <= full.index[0]
    vcols = [c for c in cols if "volume" in c or "vwap" in c or "obv" in c]
    assert vcols and (cut.loc[cut.index < df.index[250], vcols] == 0.0).all().all()
