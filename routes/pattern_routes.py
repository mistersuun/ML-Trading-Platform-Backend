"""Pattern detection endpoints."""

import logging
from typing import Optional

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

import config as cfg
from api.errors import ApiError
from api.serialize import ok
from data_fetcher import fetch_ohlcv
from patterns import PATTERN_REGISTRY
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_pattern, check_symbol, finite, require_data

logger = logging.getLogger(__name__)
router = APIRouter()


class PatternRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = Field(730, ge=PERIOD_MIN, le=PERIOD_MAX)

    _sym = field_validator("symbol")(check_symbol)
    _pat = field_validator("pattern_name")(check_pattern)


@router.get("/list")
def list_patterns():
    """List all available patterns."""
    return ok({"patterns": list(PATTERN_REGISTRY.keys()), "count": len(PATTERN_REGISTRY)})


@router.post("/detect")
def detect_pattern(req: PatternRequest):
    """Detect a specific pattern on a symbol."""
    df = require_data(fetch_ohlcv(req.symbol, period_days=req.period_days), req.symbol)
    signals_df = PATTERN_REGISTRY[req.pattern_name](df)

    buys, sells = [], []
    for idx, row in signals_df.iterrows():
        if row.get("signal") == 1:
            buys.append({"date": idx.isoformat(), "price": round(float(row["Close"]), 4)})
        elif row.get("signal") == -1:
            sells.append({"date": idx.isoformat(), "price": round(float(row["Close"]), 4)})

    last_signals = signals_df[signals_df["signal"] != 0]
    latest = None
    if not last_signals.empty:
        last = last_signals.iloc[-1]
        latest = {
            "date": last.name.isoformat(),
            "signal": "BUY" if last["signal"] == 1 else "SELL",
            "price": round(float(last["Close"]), 4),
        }

    return ok({
        "symbol": req.symbol,
        "pattern": req.pattern_name,
        "total_signals": len(buys) + len(sells),
        "buys": buys,
        "sells": sells,
        "latest_signal": latest,
    })


class ScanRequest(BaseModel):
    markets: Optional[list[str]] = None
    patterns: Optional[list[str]] = None
    recency_days: int = Field(30, ge=1, le=10 ** 7)
    period_days: Optional[int] = Field(None, ge=PERIOD_MIN, le=PERIOD_MAX)  # None = config.LOOKBACK_DAYS

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, v):
        if v is not None:
            for name in v:
                check_pattern(name)
        return v


@router.post("/scan")
def scan_patterns(req: ScanRequest):
    """Scan the watchlist for recent pattern signals.

    The statistics attached to each signal are IN-SAMPLE display numbers (validation_status 'unvalidated');
    only `python main.py scan` runs the out-of-sample validation. Anything that failed is listed in `failed`."""
    from backtester import classic_backtest

    markets = req.markets or list(cfg.WATCHLIST.keys())
    unknown = [m for m in markets if m not in cfg.WATCHLIST]
    if unknown:
        raise ApiError(f"Unknown market(s): {unknown}", {"markets": unknown, "known": sorted(cfg.WATCHLIST)},
                       status_code=422, code="unknown_market")
    symbols = [s for m in markets for s in cfg.WATCHLIST[m]]
    pattern_names = req.patterns or list(PATTERN_REGISTRY.keys())

    results, failed = [], []
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym) if req.period_days is None else fetch_ohlcv(sym, period_days=req.period_days)
        except Exception as e:
            logger.warning("pattern scan: fetch failed for %s: %s: %s", sym, type(e).__name__, e)
            failed.append({"symbol": sym, "pattern": None, "error": type(e).__name__, "message": str(e)})
            continue
        if df.empty:
            logger.warning("pattern scan: no data for %s", sym)
            failed.append({"symbol": sym, "pattern": None, "error": "NoData", "message": f"No data for {sym}"})
            continue

        for pname in pattern_names:
            try:
                signals = PATTERN_REGISTRY[pname](df)
                last_signals = signals[signals["signal"] != 0]
                if last_signals.empty:
                    continue
                last = last_signals.iloc[-1]
                days_ago = (pd.Timestamp.now() - last.name).days
                if days_ago > req.recency_days:
                    continue

                bt = classic_backtest(signals, sym, pname)
                total_return = finite(bt.total_return_pct)
                results.append({
                    "symbol": sym,
                    "pattern": pname,
                    "signal": "BUY" if last["signal"] == 1 else "SELL",
                    "signal_date": last.name.isoformat(),
                    "days_ago": days_ago,
                    "price": float(last["Close"]),
                    "win_rate": finite(bt.win_rate),
                    "profit_factor": None if bt.profit_factor_undefined else finite(bt.profit_factor),
                    "sharpe": finite(bt.sharpe_ratio),
                    "total_return": total_return,
                    "total_return_pct": total_return,   # legacy name, same fraction; kept for one release
                    "max_drawdown": finite(bt.max_drawdown_pct),
                    "total_trades": bt.total_trades,
                    "is_valid": bt.is_valid,
                    "validation_status": "unvalidated",
                })
            except Exception as e:
                logger.warning("pattern scan: %s on %s failed: %s: %s", pname, sym, type(e).__name__, e)
                failed.append({"symbol": sym, "pattern": pname, "error": type(e).__name__, "message": str(e)})

    results.sort(key=lambda x: x["days_ago"])
    return ok({"count": len(results), "signals": results, "failed": failed})
