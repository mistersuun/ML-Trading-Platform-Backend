"""ML pattern detection endpoints."""

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel
from data_fetcher import fetch_ohlcv
from ml_patterns import MLPatternDetector
from routes.helpers import json_response

router = APIRouter()


class MLRequest(BaseModel):
    symbol: str
    period_days: int = 730


@router.post("/predict")
def ml_predict(req: MLRequest):
    """Train ML ensemble and generate predictions."""
    df = fetch_ohlcv(req.symbol, period_days=req.period_days)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    detector = MLPatternDetector()
    predictions = detector.walk_forward_predict(df)

    # Feature importance (it's a pd.Series)
    importance = []
    if detector.feature_importances is not None:
        sorted_imp = detector.feature_importances.sort_values(ascending=False).head(20)
        for feat, imp in sorted_imp.items():
            importance.append({"feature": str(feat), "importance": round(float(imp), 4)})

    # Extract signals
    signals = []
    pred_signals = predictions[predictions["signal"] != 0]
    for idx, row in pred_signals.iterrows():
        entry = {
            "date": idx.isoformat(),
            "signal": "BUY" if row["signal"] == 1 else "SELL",
            "price": round(float(row["Close"]), 4),
        }
        if "ml_confidence" in row:
            entry["confidence"] = round(float(row["ml_confidence"]), 4)
        signals.append(entry)

    # Backtest
    from backtester import classic_backtest
    bt = classic_backtest(predictions, req.symbol, "ml_ensemble")

    equity_data = []
    if bt.equity_curve is not None and not bt.equity_curve.empty:
        for idx, val in bt.equity_curve.items():
            v = float(val)
            if not np.isnan(v):
                equity_data.append({"date": idx.isoformat(), "value": round(v, 2)})

    return json_response({
        "symbol": req.symbol,
        "model_type": "ensemble_rf_xgb_lgbm",
        "feature_importance": importance,
        "signals": signals,
        "total_signals": len(signals),
        "metrics": bt.summary(),
        "equity_curve": equity_data,
        "is_valid": bt.is_valid,
    })
