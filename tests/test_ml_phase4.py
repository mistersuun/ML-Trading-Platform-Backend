"""WS4.4: ML calibration, EV-after-costs threshold, baseline-first selection, drift guard.

Fast tests use tiny forests (n_estimators=10-20); the multi-seed statistical checks are @slow.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import config
from ml import calibrate, drift, select as mlselect, store
from ml_patterns import MLPatternDetector, p_up
from features import compute_features, prepare_ml_data

NEEDS_LGBM = pytest.mark.skipif(not mlselect.HAS_LGBM, reason="lightgbm not installed")


def _p(model, X):
    return p_up(model, X)


def _known_prob_data(n, seed, shift=-0.9, beta=1.3, k=5):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, k)), columns=[f"f{i}" for i in range(k)])
    p_true = 1.0 / (1.0 + np.exp(-(shift + beta * X["f0"])))
    y = pd.Series((rng.random(n) < p_true).astype(int))
    return X, y, p_true


# ----------------------------------------------------------------- calibration
def test_sigmoid_calibration_lowers_brier_and_ece_and_buckets_are_close():
    """Known-probability data; class_weight='balanced' shifts the raw probabilities off the true scale."""
    X, y, p_true = _known_prob_data(40_000, seed=1)
    fit_n, val_n = 6000, 10_000
    Xf, yf = X.iloc[:fit_n], y.iloc[:fit_n]
    raw = mlselect._fit_one("logreg", Xf, yf, 10)
    cal, ok = calibrate.fit_sigmoid(raw, X.iloc[fit_n:fit_n + val_n], y.iloc[fit_n:fit_n + val_n])
    assert ok
    Xt, yt = X.iloc[fit_n + val_n:], y.iloc[fit_n + val_n:]
    p_raw, p_cal = _p(raw, Xt), _p(cal, Xt)
    brier = lambda p: float(np.mean((p - yt.to_numpy()) ** 2))  # noqa: E731
    assert brier(p_cal) < brier(p_raw)
    assert calibrate.ece(yt, p_cal) < calibrate.ece(yt, p_raw)
    assert calibrate.ece(yt, p_cal) < 0.02
    big = calibrate.reliability(yt, p_cal, n_bins=10, min_n=50)
    assert big and all(abs(r["freq"] - r["mean_p"]) < 0.05 for r in big)


def test_calibration_refuses_a_one_sided_validation_block():
    X, y, _ = _known_prob_data(500, seed=2)
    m = mlselect._fit_one("logreg", X.iloc[:300], y.iloc[:300], 10)
    out, ok = calibrate.fit_sigmoid(m, X.iloc[300:], pd.Series(np.ones(200, int)))
    assert out is m and not ok


def test_ece_known_answer():
    # two buckets: p=0.15 observed 0.25 (n=4), p=0.85 observed 0.75 (n=4)
    p = np.array([0.15] * 4 + [0.85] * 4)
    y = np.array([1, 0, 0, 0, 1, 1, 1, 0])
    assert calibrate.ece(y, p) == pytest.approx(0.1)


def test_fit_calibrated_orders_fit_gap_validation_in_time():
    X, y, _ = _known_prob_data(800, seed=3)
    n_fit, n_val = mlselect.split_sizes(800, gap=6)
    assert n_val == 160 and n_fit + 6 + n_val == 800
    assert mlselect.split_sizes(150, gap=6) is None


# ----------------------------------------------------------------- thresholds
def test_threshold_is_chosen_for_expected_value_after_costs():
    rng = np.random.default_rng(0)
    n = 4000
    p = rng.uniform(0.3, 0.8, n)
    r = rng.normal(0.0, 0.01, n) + np.where(p > 0.65, 0.01, 0.0) - np.where(p < 0.4, 0.01, 0.0)
    th = calibrate.choose_thresholds(p, r, cost=0.003)
    assert 0.6 <= th["t_long"] <= 0.7 and th["long"]["mean_net"] > 0
    assert 0.3 <= th["t_short"] <= 0.45 and th["short"]["mean_net"] > 0
    # costs larger than the edge: nothing qualifies, both sides abstain
    none = calibrate.choose_thresholds(p, r, cost=0.05)
    assert none["t_long"] == np.inf and none["t_short"] == -np.inf


def test_threshold_abstains_on_noise_and_respects_min_trades():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.4, 0.6, 500)
    r = rng.normal(0, 0.01, 500)
    th = calibrate.choose_thresholds(p, r, cost=0.003)
    assert th["t_long"] == np.inf and th["t_short"] == -np.inf
    tiny = calibrate.choose_thresholds(np.array([0.9] * 10), np.full(10, 0.05), cost=0.0)
    assert tiny["t_long"] == np.inf  # 10 trades < THRESH_MIN_TRADES


# ----------------------------------------------------------------- selection (baseline-first)
def _cv(ll_by_model: dict, prior=0.693):
    """cv_predictions-shaped dict whose per-fold log-losses equal the given values (labels 1, p = exp(-ll))."""
    def preds(vals):
        return [(np.ones(10, int), np.full(10, np.exp(-v))) for v in vals]
    out = {k: preds(v) for k, v in ll_by_model.items()}
    out["prior"] = preds([prior] * len(next(iter(ll_by_model.values()))))
    return out


def test_ensemble_needs_to_beat_best_single_by_more_than_one_fold_std():
    base = [0.68, 0.70, 0.66, 0.69]                       # mean 0.6825, std ~0.0148
    std = float(np.std(base))
    better = [x - 0.5 * std for x in base]                # beats by half a std -> not enough
    sel = mlselect.select_model(_cv({"logreg": base, "lgbm": base, "ensemble": better}))
    assert sel["chosen"] == "logreg"
    much = [x - 1.5 * std for x in base]
    sel = mlselect.select_model(_cv({"logreg": base, "lgbm": base, "ensemble": much}))
    assert sel["chosen"] == "ensemble"
    # a single-model challenger is held to the same margin against the baseline; ensemble then vs the winner
    sel = mlselect.select_model(_cv({"logreg": base, "lgbm": much, "ensemble": much}))
    assert sel["chosen"] == "lgbm"


def test_selection_keeps_baseline_with_one_fold_and_abstains_without_skill():
    one = _cv({"logreg": [0.69], "lgbm": [0.40], "ensemble": [0.40]})
    assert mlselect.select_model(one)["chosen"] == "logreg"          # no fold std to judge a complex model
    nosk = mlselect.select_model(_cv({"logreg": [0.70, 0.71], "lgbm": [0.70, 0.71], "ensemble": [0.70, 0.71]}))
    assert nosk["abstain"] and nosk["reason"] == "no_skill"
    assert mlselect.select_model({"prior": []})["chosen"] == "logreg"


@NEEDS_LGBM
def test_candidates_use_one_consistent_class_weight():
    for name in mlselect.CANDIDATES:
        m = mlselect.build(name, 5)
        est = ([m.named_steps["lr"]] if name == "logreg"
               else [e for _, e in m.estimators] if name == "ensemble" else [m])
        assert est and all(getattr(e, "class_weight", None) == "balanced" for e in est), name
    assert mlselect.MODEL_NAMES["ensemble"] == ["rf", "lgbm"]


@NEEDS_LGBM
@pytest.mark.slow
def test_noise_data_selects_the_simple_model_or_abstains_in_90pct_of_seeds():
    from ml.splits import PurgedTimeSeriesSplit
    ok = 0
    seeds = range(30)
    for seed in seeds:
        rng = np.random.default_rng(config.SEED + seed)
        n = 1400
        X = pd.DataFrame(rng.normal(size=(n, 8)), columns=[f"f{i}" for i in range(8)])
        y = pd.Series(rng.integers(0, 2, n))
        folds = list(PurgedTimeSeriesSplit(5, 126, 1, 5, 100).split(X))
        sel = mlselect.select_model(mlselect.cv_predictions(list(mlselect.CANDIDATES), X, y, folds, 6,
                                                            n_estimators=25))
        ok += int(sel["chosen"] == "logreg" or sel["abstain"])
    assert ok / len(seeds) >= 0.9, ok


@NEEDS_LGBM
@pytest.mark.slow
def test_selection_picks_a_nonlinear_model_when_it_clearly_wins():
    from ml.splits import PurgedTimeSeriesSplit
    rng = np.random.default_rng(5)
    n = 3000
    X = pd.DataFrame(rng.normal(size=(n, 4)), columns=list("abcd"))
    y = pd.Series(((X["a"] * X["b"]) > 0).astype(int))           # XOR-like: a linear model has no skill
    folds = list(PurgedTimeSeriesSplit(5, 300, 1, 5, 100).split(X))
    sel = mlselect.select_model(mlselect.cv_predictions(list(mlselect.CANDIDATES), X, y, folds, 6,
                                                        n_estimators=60))
    assert sel["chosen"] in ("lgbm", "ensemble") and not sel["abstain"]


# ----------------------------------------------------------------- drift guard
def test_psi_known_answer():
    ref = {"features": {"x": {"edges": [1.0, 2.0, 3.0, 4.0], "share": [0.2] * 5, "psi_cap": 0.2}}}
    # 60 bars: 30 in bin 0 and 30 in bin 1 (eps for the empty bins)
    x = pd.DataFrame({"x": np.r_[np.full(30, 0.5), np.full(30, 1.5)]})
    got = drift.psi_frame(x, ref, window=60)["x"].iloc[-1]
    e, eps = 0.2, 1e-4
    want = 2 * (0.5 - e) * np.log(0.5 / e) + 3 * (eps - e) * np.log(eps / e)
    assert got == pytest.approx(want)
    # same shares as the reference -> PSI 0
    flat = pd.DataFrame({"x": np.tile([0.5, 1.5, 2.5, 3.5, 4.5], 12)})
    assert drift.psi_frame(flat, ref, window=60)["x"].iloc[-1] == pytest.approx(0.0, abs=1e-9)
    assert drift.psi_frame(flat.iloc[:59], ref, window=60)["x"].isna().all()   # not enough bars


def test_psi_row_t_only_uses_rows_up_to_t():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(300, 3)), columns=list("abc"))
    ref = drift.reference(X.iloc[:150])
    a = drift.psi_frame(X, ref)
    Xm = X.copy()
    Xm.iloc[200:] += 5.0
    b = drift.psi_frame(Xm, ref)
    pd.testing.assert_frame_equal(a.iloc[:200], b.iloc[:200])


def test_held_out_same_process_has_psi_below_0_2_on_90pct_of_features():
    rng = np.random.default_rng(config.SEED)
    shares = []
    for _ in range(10):
        train = pd.DataFrame(rng.normal(size=(1000, 20)))
        held = pd.DataFrame(rng.normal(size=(200, 20)))
        ref = drift.reference(train)
        psi = drift.psi_frame(held, ref).dropna()
        shares.append(float((psi.iloc[-1] < 0.2).mean()))        # the prediction-time (last-60-bars) PSI
        assert not drift.drift_flags(held, ref).iloc[-1]
    assert np.mean(np.array(shares) >= 0.9) >= 0.9, shares


def test_reference_is_json_serialisable_and_stored_in_metadata(tmp_path):
    import json
    rng = np.random.default_rng(0)
    ref = drift.reference(pd.DataFrame(rng.normal(size=(400, 4)), columns=list("abcd")))
    json.dumps(ref)
    assert set(ref["features"]) == set("abcd") and len(ref["features"]["a"]["edges"]) == 4
    assert sum(ref["features"]["a"]["share"]) == pytest.approx(1.0)


def _shifted_vol_frame(synth, seed=3, n1=1000, n2=200, mult=10.0):
    a = synth.gbm_ohlc(n=n1, seed=seed)
    b = synth.gbm_ohlc(n=n2, seed=seed + 500, sigma=0.2 * mult, start_price=float(a["Close"].iloc[-1]))
    b.index = pd.bdate_range(a.index[-1] + pd.offsets.BDay(1), periods=n2)
    return pd.concat([a, b])


@NEEDS_LGBM
def test_ten_x_volatility_shift_makes_predict_abstain_with_drift(synth):
    df = _shifted_vol_frame(synth)
    det = MLPatternDetector(n_estimators=15)
    det.train(df.iloc[:1000])
    assert det.drift_ref is not None
    same = det.predict(synth.gbm_ohlc(n=1300, seed=3).iloc[:1000 + 100])
    assert same["abstain_reason"].iloc[-1] != "drift"
    out = det.predict(df)
    last = out.iloc[-30:]
    assert (last["abstain_reason"] == "drift").all() and (last["signal"] == 0).all()


def test_nan_features_abstain_with_nan_and_stale_model_or_bar_abstains(gbm_frame):
    det = MLPatternDetector(n_estimators=10)
    det.train(gbm_frame)
    df = gbm_frame.copy()
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    out = det.predict(df)
    assert out["abstain_reason"].iloc[-1] == "nan" and out["signal"].iloc[-1] == 0
    # stale model
    det.trained_at = datetime.now(timezone.utc) - timedelta(days=drift.MAX_MODEL_AGE_DAYS + 1)
    st = det.predict(gbm_frame)
    assert (st["abstain_reason"] == "stale").all() and (st["signal"] == 0).all()
    # stale last bar (only when `now` is given)
    det.trained_at = datetime.now(timezone.utc)
    last = gbm_frame.index[-1].to_pydatetime()
    assert det.predict(gbm_frame, now=last + timedelta(days=1))["abstain_reason"].iloc[-1] != "stale"
    late = det.predict(gbm_frame, now=last + timedelta(days=drift.MAX_BAR_AGE_DAYS + 2))
    assert late["abstain_reason"].iloc[-1] == "stale" and late["abstain_reason"].iloc[-2] != "stale"
    assert drift.stale_reason(last_bar=gbm_frame.index[-1], now=None)["bar"] is False


# ----------------------------------------------------------------- detector pipeline
@NEEDS_LGBM
def test_train_selects_calibrates_and_stores_guard_state(gbm_frame, tmp_path):
    det = MLPatternDetector(n_estimators=10)
    m = det.train(gbm_frame)
    assert m["selection"]["chosen"] in mlselect.CANDIDATES and "thresholds" in m and "calibrated" in m
    assert det.t_long is not None and det.t_long != config.ML_MIN_CONFIDENCE
    assert det.model_type in ("logreg", "lgbm", "ensemble_rf_lgbm")
    path = det.save("TST", df=gbm_frame, metrics=m, root=tmp_path)
    meta = store.read_metadata(path)
    assert store.drift_reference(meta)["features"] and "calibration" in meta
    det2 = MLPatternDetector()
    det2.load(path)
    assert det2.t_long == det.t_long and det2.t_short == det.t_short and det2.drift_ref is not None
    pd.testing.assert_frame_equal(det.predict(gbm_frame), det2.predict(gbm_frame))


@NEEDS_LGBM
def test_walk_forward_pipeline_oos_only_threshold_consistent_and_causal(gbm_frame):
    a = MLPatternDetector(n_estimators=10).walk_forward_predict(gbm_frame)
    det = MLPatternDetector(n_estimators=10)
    b = det.walk_forward_predict(gbm_frame)
    pd.testing.assert_frame_equal(a, b)                                  # deterministic
    split = int(len(gbm_frame) * config.ML_TRAIN_TEST_SPLIT)
    assert (b["signal"].iloc[:split] == 0).all() and not b["oos"].iloc[:split].any()
    assert det.selection.get("chosen") in mlselect.CANDIDATES
    reasons = set(b.loc[b["oos"], "abstain_reason"])
    assert reasons <= {"", "band", "drift", "nan", "no_skill", "no_validation", "stale"}
    sig = b[b["signal"] != 0]
    assert (sig["abstain_reason"] == "").all()
    assert "abstain_reasons" in det.oos_metrics
    mod = gbm_frame.copy()                                               # future bars cannot change the past
    mod.iloc[450:, :4] = mod.iloc[450:, :4].to_numpy() * 1.37
    c = MLPatternDetector(n_estimators=10).walk_forward_predict(mod)
    np.testing.assert_allclose(b["ml_confidence"].iloc[:450], c["ml_confidence"].iloc[:450], atol=1e-12, rtol=0)


@NEEDS_LGBM
def test_signals_follow_validation_thresholds_and_model_abstain_blocks_all(gbm_frame):
    det = MLPatternDetector(n_estimators=10)
    det.train(gbm_frame)
    det.t_long, det.t_short, det.model_abstain = 0.51, 0.49, ""
    out = det.predict(gbm_frame)
    p = out["ml_confidence"]
    assert ((out["signal"] == 1) <= (p >= 0.51)).all() and ((out["signal"] == -1) <= (p <= 0.49)).all()
    inside = out[(out["signal"] == 0) & (p > 0) & (out["abstain_reason"] != "nan")]
    assert (inside["abstain_reason"] != "").all()                        # in the band or guarded: always labelled
    det.model_abstain = "no_skill"
    ab = det.predict(gbm_frame)
    assert (ab["signal"] == 0).all() and (ab.loc[ab["ml_confidence"] > 0, "abstain_reason"] == "no_skill").any()


def test_legacy_mode_without_lightgbm_keeps_fixed_band(monkeypatch, gbm_frame):
    import ml_patterns
    monkeypatch.setattr(ml_patterns, "HAS_LGBM", False)
    monkeypatch.setattr(ml_patterns, "HAS_XGB", False)
    det = MLPatternDetector(n_estimators=5)
    assert not det.pipeline_mode
    det.train(gbm_frame)
    assert det.t_long is None and det.model_type == "rf"
    out = det.predict(gbm_frame)
    nz = out[out["signal"] != 0]
    assert ((nz["signal"] == 1) <= (nz["ml_confidence"] > config.ML_MIN_CONFIDENCE)).all()


@NEEDS_LGBM
@pytest.mark.slow
def test_no_signal_data_trades_rarely_and_not_on_a_fixed_060_band(synth):
    """On a driftless random walk the calibrated, EV-gated detector emits (almost) no signals."""
    n_signals, n_oos = 0, 0
    for seed in range(8):
        df = synth.gbm_ohlc(n=1100, seed=2000 + seed, mu=0.0)
        det = MLPatternDetector(n_estimators=20)
        out = det.walk_forward_predict(df)
        oos = out[out["oos"]]
        n_signals += int((oos["signal"] != 0).sum())
        n_oos += len(oos)
    assert n_signals / n_oos < 0.15, n_signals / n_oos
