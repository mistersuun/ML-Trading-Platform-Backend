"""WS3.5: versioned model artefacts (ml/store.py + MLPatternDetector persistence)."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

import ml_patterns
from ml import store
from ml.store import ModelStoreError
from ml_patterns import MLPatternDetector
from services import ml as ml_svc


@pytest.fixture(autouse=True)
def tiny(monkeypatch, tmp_path):
    monkeypatch.setattr(ml_patterns, "HAS_XGB", False)
    monkeypatch.setattr(ml_patterns, "HAS_LGBM", False)
    orig = MLPatternDetector._build_ensemble

    def small(self):
        self.n_estimators = 5
        return orig(self)

    monkeypatch.setattr(MLPatternDetector, "_build_ensemble", small)
    monkeypatch.setenv("ML_MODELS_DIR", str(tmp_path / "models"))


def _train_save(df, root, symbol="AAPL"):
    det = MLPatternDetector()
    metrics = det.train(df)
    return det, det.save(symbol, df=df, metrics=metrics, root=root)


def test_roundtrip_predictions_identical(gbm_frame, tmp_path):
    det, path = _train_save(gbm_frame, tmp_path)
    det2 = MLPatternDetector()
    det2.load(path)
    assert det2.feature_cols == det.feature_cols
    a, b = det.predict(gbm_frame), det2.predict(gbm_frame)
    assert np.array_equal(a["ml_confidence"].to_numpy(), b["ml_confidence"].to_numpy())


def test_layout_and_metadata(gbm_frame, tmp_path):
    det, path = _train_save(gbm_frame, tmp_path)
    assert path.parent == tmp_path / "AAPL" and re.fullmatch(r"\d{8}T\d{12}Z", path.name)
    assert (path / "model.joblib").is_file()
    meta = json.loads((path / "metadata.json").read_text())
    for lib in ("sklearn", "lightgbm", "xgboost", "numpy", "pandas"):
        assert lib in meta["library_versions"]
    assert meta["library_versions"]["sklearn"]
    assert "git_sha" in meta
    assert meta["data"]["n_rows"] == len(gbm_frame) and len(meta["data"]["hash"]) == 64
    assert meta["features"] == det.feature_cols
    assert meta["feature_schema_hash"] == store.schema_hash(det.feature_cols)
    assert meta["label_spec"]["horizon"] == det.horizon and "embargo" in meta["label_spec"]
    assert meta["seeds"]["seed"]
    assert "mean_cv_auc" in meta["metrics"]
    assert not list(path.parent.glob(".tmp-*"))


def test_major_version_mismatch_refused(gbm_frame, tmp_path):
    _, path = _train_save(gbm_frame, tmp_path)
    mp = path / "metadata.json"
    meta = json.loads(mp.read_text())
    meta["library_versions"]["sklearn"] = "0.24.2"
    mp.write_text(json.dumps(meta))
    with pytest.raises(ModelStoreError, match="sklearn.*major version"):
        MLPatternDetector().load(path)


def test_minor_version_difference_allowed(gbm_frame, tmp_path):
    _, path = _train_save(gbm_frame, tmp_path)
    mp = path / "metadata.json"
    meta = json.loads(mp.read_text())
    major = meta["library_versions"]["sklearn"].split(".")[0]
    meta["library_versions"]["sklearn"] = f"{major}.0.0"
    mp.write_text(json.dumps(meta))
    MLPatternDetector().load(path)


def test_feature_schema_mismatch_refused(gbm_frame, tmp_path, monkeypatch):
    _, path = _train_save(gbm_frame, tmp_path)
    monkeypatch.setattr(ml_patterns, "current_schema_hash", lambda: "different")
    with pytest.raises(ModelStoreError, match="feature schema"):
        MLPatternDetector().load(path)


def test_load_latest_skips_invalid_and_picks_newest(gbm_frame, tmp_path):
    det, p1 = _train_save(gbm_frame, tmp_path)
    p2 = det.save("AAPL", df=gbm_frame, root=tmp_path)
    assert p2.name > p1.name
    assert MLPatternDetector().load_latest("AAPL", tmp_path)["created_utc"] == \
        json.loads((p2 / "metadata.json").read_text())["created_utc"]
    # break the newest: it is skipped, the previous valid one is used
    meta = json.loads((p2 / "metadata.json").read_text())
    meta["library_versions"]["sklearn"] = "0.1"
    (p2 / "metadata.json").write_text(json.dumps(meta))
    got = MLPatternDetector().load_latest("AAPL", tmp_path)
    assert got["created_utc"] == json.loads((p1 / "metadata.json").read_text())["created_utc"]
    assert MLPatternDetector().load_latest("MSFT", tmp_path) is None


def test_corrupt_bundle_refused(gbm_frame, tmp_path):
    _, path = _train_save(gbm_frame, tmp_path)
    (path / "model.joblib").write_bytes(b"garbage")
    with pytest.raises(ModelStoreError):
        MLPatternDetector().load(path)


def test_retrain_policy_missing_fresh_stale(gbm_frame, tmp_path, monkeypatch):
    trains = []
    orig = MLPatternDetector.train
    monkeypatch.setattr(MLPatternDetector, "train", lambda self, df, **k: trains.append(1) or orig(self, df, **k))

    MLPatternDetector().load_or_train(gbm_frame, "AAPL", root=tmp_path)          # missing -> train
    assert len(trains) == 1
    MLPatternDetector().load_or_train(gbm_frame, "AAPL", root=tmp_path)          # fresh -> reuse
    assert len(trains) == 1
    assert len(store.versions("AAPL", tmp_path)) == 1

    # age the stored model past ML_RETRAIN_DAYS
    old = store.versions("AAPL", tmp_path)[0]
    meta = json.loads((old / "metadata.json").read_text())
    meta["created_utc"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    (old / "metadata.json").write_text(json.dumps(meta))
    MLPatternDetector().load_or_train(gbm_frame, "AAPL", root=tmp_path)          # stale -> retrain
    assert len(trains) == 2 and len(store.versions("AAPL", tmp_path)) == 2

    ml_svc.retrain(gbm_frame, "AAPL", root=tmp_path)                             # nightly -> always
    assert len(trains) == 3


def test_service_display_path_uses_store(synth):
    frame = synth.gbm_ohlc(n=900, seed=7)
    run = ml_svc.train_and_predict(frame, "ZZZ")
    run2 = ml_svc.train_and_predict(frame, "ZZZ")
    assert len(store.versions("ZZZ")) == 1
    assert (run.predictions["ml_confidence"].to_numpy() == run2.predictions["ml_confidence"].to_numpy()).all()


def test_walk_forward_still_retrains_and_does_not_touch_store(gbm_frame, tmp_path):
    det = MLPatternDetector()
    out = det.walk_forward_predict(gbm_frame)
    assert out["oos"].any() and det.oos_start is not None
    assert not (tmp_path / "models").exists()


def test_no_pickle_module_in_model_code():
    for f in ("ml_patterns.py", "ml/store.py"):
        src = (Path(__file__).resolve().parent.parent / f).read_text()
        assert not re.search(r"^\s*(import pickle|from pickle)", src, re.M)
        assert "pickle." not in src


def test_models_dir_gitignored():
    gi = (Path(__file__).resolve().parent.parent / ".gitignore").read_text().splitlines()
    assert "models/" in gi
