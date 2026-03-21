"""Pairs trading endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from data_fetcher import fetch_ohlcv
from pairs_trading import (
    analyze_pair, is_valid_pair, generate_pair_signals,
    backtest_pair, scan_all_pairs, compute_spread, compute_half_life,
)
import config

router = APIRouter()


class PairRequest(BaseModel):
    symbol_a: str
    symbol_b: str
    period_days: int = 504


@router.get("/configured")
def get_configured_pairs():
    """Get list of configured pairs."""
    return {"pairs": [{"a": a, "b": b} for a, b in config.PAIRS]}


@router.post("/analyze")
def analyze(req: PairRequest):
    """Run full cointegration analysis on a pair."""
    df_a = fetch_ohlcv(req.symbol_a, period_days=req.period_days)
    df_b = fetch_ohlcv(req.symbol_b, period_days=req.period_days)

    if df_a.empty or df_b.empty:
        return {"error": "Failed to fetch data for one or both symbols"}

    # Align
    common = df_a.index.intersection(df_b.index)
    if len(common) < 60:
        return {"error": "Insufficient overlapping data"}

    df_a = df_a.loc[common]
    df_b = df_b.loc[common]

    analysis = analyze_pair(df_a, df_b, req.symbol_a, req.symbol_b)
    valid = is_valid_pair(analysis)

    # Spread and z-score for charting
    spread = compute_spread(df_a["Close"], df_b["Close"], analysis.hedge_ratio)
    spread_mean = spread.rolling(60).mean()
    spread_std = spread.rolling(60).std()
    zscore = ((spread - spread_mean) / (spread_std + 1e-10))

    spread_data = []
    for idx in spread.index:
        s_val = spread.loc[idx]
        z_val = zscore.loc[idx] if idx in zscore.index else None
        if s_val is not None:
            entry = {"date": idx.isoformat(), "spread": round(float(s_val), 4)}
            if z_val is not None and not (isinstance(z_val, float) and z_val != z_val):
                entry["zscore"] = round(float(z_val), 4)
            spread_data.append(entry)

    # Price series for chart
    prices_a = [{"date": idx.isoformat(), "price": round(float(row["Close"]), 4)}
                for idx, row in df_a.iterrows()]
    prices_b = [{"date": idx.isoformat(), "price": round(float(row["Close"]), 4)}
                for idx, row in df_b.iterrows()]

    # Generate signals and backtest
    signals = generate_pair_signals(df_a, df_b, analysis)
    bt = backtest_pair(signals, analysis)

    return {
        "symbol_a": req.symbol_a,
        "symbol_b": req.symbol_b,
        "is_cointegrated": bool(analysis.is_cointegrated),
        "is_valid": bool(valid),
        "coint_pvalue": round(float(analysis.coint_pvalue), 6),
        "hedge_ratio": round(float(analysis.hedge_ratio), 4),
        "half_life": round(float(analysis.half_life), 1),
        "correlation": round(float(analysis.correlation), 4),
        "current_zscore": round(float(analysis.current_zscore), 4),
        "spread_data": spread_data,
        "prices_a": prices_a,
        "prices_b": prices_b,
        "backtest": _sanitize(bt),
    }


def _sanitize(obj):
    """Recursively convert numpy types to Python natives for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


@router.post("/scan")
def scan_pairs():
    """Scan all configured pairs."""
    # Fetch data for all symbols in pairs
    symbols = set()
    for a, b in config.PAIRS:
        symbols.add(a)
        symbols.add(b)

    data = {}
    for sym in symbols:
        df = fetch_ohlcv(sym, period_days=config.PAIRS_LOOKBACK)
        if not df.empty:
            data[sym] = df

    results = scan_all_pairs(data)
    return {"count": len(results), "pairs": results}
