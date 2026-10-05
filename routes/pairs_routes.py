"""Pairs trading endpoints (WS2.6).

The spread, z-score, signal, backtest and /scan all come from pairs_trading.compute_spread_stats
(causal rolling OLS); the route does not compute its own z.  Responses contain plain Python types
only (no numpy scalars, no inf/NaN: None instead).
"""

import logging

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator, field_validator

import config
import instruments
from api.errors import InsufficientHistory, NoData
from api.serialize import ok, to_native
from data_fetcher import fetch_ohlcv
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_symbol
from pairs_trading import (
    InsufficientData, analyze_pair, backtest_pair, generate_pair_signals, is_valid_pair, scan_all_pairs,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class PairRequest(BaseModel):
    symbol_a: str
    symbol_b: str
    period_days: int = Field(504, ge=PERIOD_MIN, le=PERIOD_MAX)

    _a = field_validator("symbol_a")(check_symbol)
    _b = field_validator("symbol_b")(check_symbol)

    @model_validator(mode="after")
    def _distinct(self):
        if self.symbol_a == self.symbol_b:
            raise ValueError("symbol_a and symbol_b must differ")
        return self


_native = to_native  # backwards-compatible names
_sanitize = to_native


def _scan_period_days() -> int:
    """Calendar days to request so PAIRS_MIN_ALIGNED_BARS trading bars are available."""
    return max(int(config.PAIRS_LOOKBACK), int(config.PAIRS_MIN_ALIGNED_BARS * 1.6))


@router.get("/configured")
def get_configured_pairs():
    """Get list of configured pairs."""
    return ok({"pairs": [{"a": a, "b": b} for a, b in config.PAIRS]})


@router.post("/analyze")
def analyze(req: PairRequest):
    """Run full cointegration analysis on a pair."""
    df_a = fetch_ohlcv(req.symbol_a, period_days=req.period_days)
    df_b = fetch_ohlcv(req.symbol_b, period_days=req.period_days)

    for sym, df in ((req.symbol_a, df_a), (req.symbol_b, df_b)):
        if df.empty:
            raise NoData(sym)

    common = df_a.index.intersection(df_b.index)
    df_a, df_b = df_a.loc[common], df_b.loc[common]
    try:
        analysis = analyze_pair(df_a, df_b, req.symbol_a, req.symbol_b)
    except InsufficientData as e:
        raise InsufficientHistory(f"Insufficient overlapping data: {e}") from e
    valid = is_valid_pair(analysis)

    signals = generate_pair_signals(df_a, df_b, analysis)
    bt = backtest_pair(signals, analysis)

    spread_data = []
    for idx, row in signals[["spread", "zscore"]].iterrows():
        if not np.isfinite(row["spread"]):
            continue                                   # warm-up bars have no causal spread yet
        entry = {"date": idx.isoformat(), "spread": round(float(row["spread"]), 6)}
        if np.isfinite(row["zscore"]):
            entry["zscore"] = round(float(row["zscore"]), 6)
        spread_data.append(entry)

    prices_a = [{"date": idx.isoformat(), "price": round(float(v), 4)} for idx, v in df_a["Close"].items()]
    prices_b = [{"date": idx.isoformat(), "price": round(float(v), 4)} for idx, v in df_b["Close"].items()]

    return ok({
        "symbol_a": req.symbol_a,
        "symbol_b": req.symbol_b,
        "is_cointegrated": analysis.is_cointegrated,
        "is_valid": valid,
        "coint_pvalue": analysis.coint_pvalue,
        "hedge_ratio": analysis.hedge_ratio,
        "half_life": analysis.half_life,                # None unless 0 < phi < 1
        "correlation": analysis.correlation,            # daily log-return correlation
        "current_zscore": analysis.current_zscore,      # last causal z (== last spread_data zscore)
        "n_obs": analysis.n_obs,
        "bars_per_year": analysis.bars_per_year,
        "non_executable": analysis.non_executable,
        "non_executable_reason": analysis.non_executable_reason,
        "spread_data": spread_data,
        "prices_a": prices_a,
        "prices_b": prices_b,
        "backtest": bt,
    })


@router.post("/scan")
def scan_pairs():
    """Scan the default pairs (research-only pairs excluded); BH-corrected across all pairs scanned."""
    pairs = instruments.default_pairs()
    symbols = {s for pair in pairs for s in pair}

    data, failed = {}, []
    for sym in sorted(symbols):
        df = fetch_ohlcv(sym, period_days=_scan_period_days())
        if df.empty:
            logger.warning("pairs scan: no data for %s", sym)
            failed.append({"symbol": sym, "error": "NoData"})
        else:
            data[sym] = df

    results = scan_all_pairs(data, pairs)
    return ok({"count": len(results), "pairs": results, "n_pairs_configured": len(pairs), "failed": failed})
