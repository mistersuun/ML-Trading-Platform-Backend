"""ML pattern detection endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

import signals as sigmod
from api.serialize import ok
from data_fetcher import fetch_ohlcv
from ml_patterns import MLPatternDetector
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_symbol, cut_holdout, finite, holdout_info, metrics_payload, require_data

router = APIRouter()


class MLRequest(BaseModel):
    symbol: str
    period_days: int = Field(730, ge=PERIOD_MIN, le=PERIOD_MAX)
    include_holdout: bool = True      # False: only bars before config.HOLDOUT_START

    _sym = field_validator("symbol")(check_symbol)


@router.post("/predict")
def ml_predict(req: MLRequest):
    """Walk-forward ML predictions; the backtest and signals cover ONLY out-of-sample bars."""
    import backtester

    df = require_data(fetch_ohlcv(req.symbol, period_days=req.period_days), req.symbol)
    holdout = holdout_info(df)
    if not req.include_holdout:
        df = require_data(cut_holdout(df), req.symbol)

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

    bt = backtester.classic_backtest(oos, req.symbol, "ml_ensemble")

    equity_data = []
    if bt.equity_curve is not None and not bt.equity_curve.empty:
        for idx, val in bt.equity_curve.items():
            v = finite(val)
            if v is not None:
                equity_data.append({"date": idx.isoformat(), "value": round(v, 2)})

    return ok({
        "symbol": req.symbol,
        "model_type": detector.model_type,
        "feature_importance": importance,
        "signals": signals,
        "total_signals": len(signals),
        "metrics": metrics_payload(bt),
        "metrics_display": bt.summary(),
        "equity_curve": equity_data,
        "is_valid": bt.is_valid,
        "oos_start": detector.oos_start.isoformat() if detector.oos_start is not None else None,
        "n_oos_bars": int(len(oos)),
        "oos_metrics": detector.oos_metrics,
        "importance_basis": "permutation_oos_logloss",
        "holdout": holdout,
    })
