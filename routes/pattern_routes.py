"""Pattern detection endpoints."""

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
from data_fetcher import fetch_ohlcv
from patterns import PATTERN_REGISTRY, run_all_patterns
from routes.helpers import json_response

router = APIRouter()


class PatternRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = 730


@router.get("/list")
def list_patterns():
    """List all available patterns."""
    return {"patterns": list(PATTERN_REGISTRY.keys()), "count": len(PATTERN_REGISTRY)}


@router.post("/detect")
def detect_pattern(req: PatternRequest):
    """Detect a specific pattern on a symbol."""
    if req.pattern_name not in PATTERN_REGISTRY:
        return {"error": f"Unknown pattern: {req.pattern_name}"}

    df = fetch_ohlcv(req.symbol, period_days=req.period_days)
    if df.empty:
        return {"error": f"No data for {req.symbol}"}

    func = PATTERN_REGISTRY[req.pattern_name]
    signals_df = func(df)

    # Extract signal points
    buys = []
    sells = []
    for idx, row in signals_df.iterrows():
        if row.get("signal") == 1:
            buys.append({"date": idx.isoformat(), "price": round(float(row["Close"]), 4)})
        elif row.get("signal") == -1:
            sells.append({"date": idx.isoformat(), "price": round(float(row["Close"]), 4)})

    # Get last signal
    last_signals = signals_df[signals_df["signal"] != 0]
    latest = None
    if not last_signals.empty:
        last = last_signals.iloc[-1]
        latest = {
            "date": last.name.isoformat(),
            "signal": "BUY" if last["signal"] == 1 else "SELL",
            "price": round(float(last["Close"]), 4),
        }

    return {
        "symbol": req.symbol,
        "pattern": req.pattern_name,
        "total_signals": len(buys) + len(sells),
        "buys": buys,
        "sells": sells,
        "latest_signal": latest,
    }


class ScanRequest(BaseModel):
    markets: Optional[list[str]] = None
    patterns: Optional[list[str]] = None
    recency_days: int = 30


@router.post("/scan")
def scan_patterns(req: ScanRequest):
    """Scan watchlist for pattern signals. Returns detected signals with backtest validation."""
    from backtester import classic_backtest

    import config as cfg

    # Determine symbols
    markets = req.markets or list(cfg.WATCHLIST.keys())
    symbols = []
    for m in markets:
        symbols.extend(cfg.WATCHLIST.get(m, []))

    pattern_names = req.patterns or list(PATTERN_REGISTRY.keys())

    results = []
    for sym in symbols:
        df = fetch_ohlcv(sym)
        if df.empty:
            continue

        for pname in pattern_names:
            if pname not in PATTERN_REGISTRY:
                continue
            try:
                func = PATTERN_REGISTRY[pname]
                signals = func(df)
                last_signals = signals[signals["signal"] != 0]
                if last_signals.empty:
                    continue

                last = last_signals.iloc[-1]
                days_ago = (pd.Timestamp.now() - last.name).days
                if days_ago > req.recency_days:
                    continue

                # Quick backtest
                bt = classic_backtest(signals, sym, pname)

                results.append({
                    "symbol": sym,
                    "pattern": pname,
                    "signal": "BUY" if last["signal"] == 1 else "SELL",
                    "signal_date": last.name.isoformat(),
                    "days_ago": days_ago,
                    "price": round(float(last["Close"]), 4),
                    "win_rate": round(bt.win_rate, 4),
                    "profit_factor": round(bt.profit_factor, 2),
                    "sharpe": round(bt.sharpe_ratio, 2),
                    "total_return_pct": round(bt.total_return_pct, 2),
                    "total_trades": bt.total_trades,
                    "is_valid": bt.is_valid,
                })
            except Exception:
                continue

    # Sort by most recent signal first
    results.sort(key=lambda x: x["days_ago"])
    return json_response({"count": len(results), "signals": results})
