"""Pairs services (WS2.6 behaviour): analysis, scan, configured list.

The spread, z-score, signal, backtest and /scan all come from pairs_trading.compute_spread_stats
(causal rolling OLS); nothing here computes its own z."""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

import numpy as np

import config
import instruments
from api.errors import InsufficientHistory, NoData
from pairs_trading import (InsufficientData, analyze_pair, generate_pair_signals, is_valid_oos, is_valid_pair,
                           record_trials, scan_all_pairs, walk_forward_pairs)
from services.common import clean
from services.models import PairAnalysisResponse, PairsConfiguredResponse, PairsScanResponse
from services.providers import DataProvider

logger = logging.getLogger(__name__)


def scan_period_days() -> int:
    """Calendar days to request so PAIRS_MIN_ALIGNED_BARS trading bars are available."""
    return max(int(config.PAIRS_LOOKBACK), int(config.PAIRS_MIN_ALIGNED_BARS * 1.6))


def configured_pairs() -> PairsConfiguredResponse:
    return PairsConfiguredResponse(pairs=[{"a": a, "b": b} for a, b in config.PAIRS])


def scan_pairs_data(data: dict, pairs: Optional[list] = None, trials=None) -> list[dict]:
    """VALID pairs from `data`, BH-corrected across every pair tested (default: instruments.default_pairs()).

    Each pair's walk-forward OOS return series is recorded in `trials` (a trials.TrialRegistry) when given."""
    pairs = list(pairs) if pairs else instruments.default_pairs()
    if trials is None:
        return scan_all_pairs(data, pairs)
    with record_trials(trials):
        return scan_all_pairs(data, pairs)


def _pairs_registry(data: dict):
    """A fresh trial registry for one pairs scan (run id = UTC time + hash of the symbols scanned)."""
    from services import trial_runs
    tag = hashlib.sha1("|".join(sorted(data)).encode()).hexdigest()[:8]
    return trial_runs.new_registry(trial_runs.new_run_id("pairs", tag))


pairs_registry = _pairs_registry   # public name for the nightly scan (services.scan.scan_pairs)


def analyze(provider: DataProvider, symbol_a: str, symbol_b: str, period_days: int) -> PairAnalysisResponse:
    """Full cointegration analysis on a pair."""
    df_a = provider.ohlcv(symbol_a, period_days)
    df_b = provider.ohlcv(symbol_b, period_days)
    for sym, df in ((symbol_a, df_a), (symbol_b, df_b)):
        if df.empty:
            raise NoData(sym)

    common = df_a.index.intersection(df_b.index)
    df_a, df_b = df_a.loc[common], df_b.loc[common]
    try:
        analysis = analyze_pair(df_a, df_b, symbol_a, symbol_b)
    except InsufficientData as e:
        raise InsufficientHistory(f"Insufficient overlapping data: {e}") from e
    signals = generate_pair_signals(df_a, df_b, analysis)   # causal z / spread series for the chart only

    # WS4.5: validity and every displayed backtest number come from the walk-forward OOS run, never from a
    # full-sample backtest.  A pair with no complete OOS block is simply not valid.
    wf = walk_forward_pairs(df_a, df_b, symbol_a, symbol_b)
    bt = dict(wf.backtest)
    bt["current_zscore"] = analysis.current_zscore
    bt["non_executable"] = analysis.non_executable
    valid = bool(is_valid_pair(analysis) and is_valid_oos(wf))

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

    return PairAnalysisResponse.model_validate(clean({
        "symbol_a": symbol_a, "symbol_b": symbol_b,
        "is_cointegrated": analysis.is_cointegrated, "is_valid": valid,
        "coint_pvalue": analysis.coint_pvalue, "hedge_ratio": analysis.hedge_ratio,
        "half_life": analysis.half_life,                # None unless 0 < phi < 1
        "correlation": analysis.correlation,            # daily log-return correlation
        "current_zscore": analysis.current_zscore,      # last causal z (== last spread_data zscore)
        "n_obs": analysis.n_obs, "bars_per_year": analysis.bars_per_year,
        "non_executable": analysis.non_executable, "non_executable_reason": analysis.non_executable_reason,
        "spread_data": spread_data, "prices_a": prices_a, "prices_b": prices_b, "backtest": bt,
    }))


def scan(provider: DataProvider) -> PairsScanResponse:
    """Scan the default pairs (research-only pairs excluded); BH-corrected across all pairs scanned."""
    pairs = instruments.default_pairs()
    symbols = {s for pair in pairs for s in pair}

    data, failed = {}, []
    for sym in sorted(symbols):
        df = provider.ohlcv(sym, scan_period_days())
        if df.empty:
            logger.warning("pairs scan: no data for %s", sym)
            failed.append({"symbol": sym, "error": "NoData"})
        else:
            data[sym] = df

    results = clean(scan_pairs_data(data, pairs, trials=_pairs_registry(data) if data else None))
    return PairsScanResponse(count=len(results), pairs=results, n_pairs_configured=len(pairs), failed=failed)
