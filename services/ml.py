"""ML service: walk-forward predictions (API) and the legacy train/predict view (dashboard)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

import backtester
import config
import signals as sigmod
from ml import store as ml_store
from ml_patterns import MLPatternDetector
from services.common import clean, cut_holdout, equity_points, holdout_info, require_data
from services.models import MLPredictResponse
from services.providers import DataProvider


def predict(provider: DataProvider, symbol: str, period_days: int = 730,
            include_holdout: bool = True) -> MLPredictResponse:
    """Walk-forward ML predictions; the backtest and signals cover ONLY out-of-sample bars."""
    df = require_data(provider.ohlcv(symbol, period_days), symbol)
    holdout = holdout_info(df)
    if not include_holdout:
        df = require_data(cut_holdout(df), symbol)

    detector = MLPatternDetector()
    predictions = detector.walk_forward_predict(df)
    oos = predictions.loc[predictions["oos"]] if "oos" in predictions else predictions.iloc[0:0]

    importance = []
    if detector.feature_importances is not None:
        sorted_imp = detector.feature_importances.sort_values(ascending=False).head(20)
        for feat, imp in sorted_imp.items():
            importance.append({"feature": str(feat), "importance": float(imp)})

    signals = []
    for idx, row in oos[oos["signal"] != 0].iterrows():
        p_up = float(row["ml_confidence"])
        buy = row["signal"] == 1
        signals.append({
            "date": idx.isoformat(),
            "signal": "BUY" if buy else "SELL",
            "price": round(float(row["Close"]), 4),
            "confidence": sigmod.ml_confidence(p_up, 1 if buy else -1),   # directional: p_up BUY, 1 - p_up SELL
            "p_up": p_up,
        })

    bt = backtester.classic_backtest(oos, symbol, "ml_ensemble")
    om = detector.oos_metrics or {}
    last_reason = str(oos["abstain_reason"].iloc[-1]) if len(oos) and "abstain_reason" in oos else None

    return MLPredictResponse.model_validate(clean({
        "symbol": symbol,
        "model_type": detector.model_type,
        "feature_importance": importance,
        "signals": signals,
        "total_signals": len(signals),
        "calibrated": om.get("calibrated"),
        "model_selected": om.get("model_selected"),
        "abstain_reasons": om.get("abstain_reasons") or {},
        "last_abstain_reason": last_reason or None,
        "metrics": bt.metrics_payload(),
        "equity_curve": equity_points(bt.equity_curve),
        "is_valid": bt.is_valid,
        "oos_start": detector.oos_start.isoformat() if detector.oos_start is not None else None,
        "n_oos_bars": int(len(oos)),
        "oos_metrics": detector.oos_metrics,
        "importance_basis": "permutation_oos_logloss",
        "holdout": holdout,
    }))


@dataclass
class MLTrainRun:
    metrics: dict
    predictions: pd.DataFrame
    backtest: "backtester.BacktestResult"


def _stored_or_fresh(detector: MLPatternDetector, train_df: pd.DataFrame, symbol: str) -> dict:
    """Latest stored model when it is fresh AND was trained on data no later than `train_df` (so the view's
    'test' bars are not in-sample for it); otherwise a model trained in memory on `train_df` (not stored)."""
    meta = detector.load_latest(symbol)
    if meta is not None and not ml_store.is_stale(meta, detector.retrain_days):
        end = (meta.get("data") or {}).get("end")
        # Reuse only when the stored model provably ended on or before train_df (unknown end -> retrain).
        if end is not None and pd.Timestamp(end).tz_localize(None) <= train_df.index[-1].tz_localize(None):
            return meta
        metrics = detector.train(train_df)
        if detector.model is None:
            raise ValueError(f"cannot train a model for {symbol}: {metrics.get('error', 'unknown')}")
        return {"metrics": metrics}
    return detector.load_or_train(train_df, symbol)   # missing or stale: train on train_df and store a new version


def train_and_predict(df: pd.DataFrame, symbol: str) -> MLTrainRun:
    """Dashboard (legacy) view: fit on the first ML_TRAIN_TEST_SPLIT of `df`, predict every bar, backtest.

    The predictions and backtest here include training bars; use `predict` for out-of-sample numbers."""
    detector = MLPatternDetector()
    train_df = df.iloc[:int(len(df) * config.ML_TRAIN_TEST_SPLIT)]
    try:
        meta = _stored_or_fresh(detector, train_df, symbol)
    except ValueError:
        return MLTrainRun({"error": "insufficient_data"}, detector._empty(df),
                          backtester.classic_backtest(detector._empty(df), symbol, "ml_ensemble"))
    metrics = meta.get("metrics", {})
    pred_df = detector.predict(df, now=datetime.now(timezone.utc))   # last bar older than a week -> 'stale'
    return MLTrainRun(metrics, pred_df, backtester.classic_backtest(pred_df, symbol, "ml_ensemble"))


def retrain(df: pd.DataFrame, symbol: str, root=None) -> dict:
    """Nightly run: unconditionally train on `df` and store a new model version. Returns its metadata."""
    return MLPatternDetector().load_or_train(df, symbol, root=root, force=True)
