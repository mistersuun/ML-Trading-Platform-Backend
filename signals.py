"""Signal selection helpers (WS1.5): latest tradable signal, intent aggregation, ML confidence.

No sizing input is derived from backtest statistics. Pure functions, no I/O, no broker imports.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
import pandas as pd

import config
from execution import OrderIntent

# Lowest to highest. Only tiers in config.ORDER_ELIGIBLE_STATUSES may reach a broker.
VALIDATION_TIERS = ("unvalidated", "oos_validated", "deflated_validated")
UNVALIDATED = "unvalidated"


def _bar_date(idx) -> str:
    try:
        return pd.Timestamp(idx).date().isoformat()
    except (ValueError, TypeError):
        return str(idx)


def latest_signal(df: pd.DataFrame, max_staleness_bars: int = 1) -> tuple[int, Optional[str]]:
    """Most recent non-zero signal and its bar date, or (0, None) when there is none or it is stale.

    Staleness = bars between the signal bar and the last bar of `df` (0 = signal on the last bar).
    A signal older than `max_staleness_bars` is not actionable."""
    if "signal" not in df.columns or len(df) == 0:
        return 0, None
    sig = pd.to_numeric(df["signal"], errors="coerce").to_numpy(dtype=float)
    nz = np.flatnonzero(np.nan_to_num(sig, nan=0.0))
    if nz.size == 0:
        return 0, None
    pos = int(nz[-1])
    if (len(df) - 1 - pos) > max_staleness_bars:
        return 0, None
    return (1 if sig[pos] > 0 else -1), _bar_date(df.index[pos])


def ml_confidence(p_up: float, direction: int) -> float:
    """Probability the model assigns to `direction` (p_up for a BUY, 1 - p_up for a SELL), in [0, 1].
    Non-finite probabilities or direction 0 give 0.0 (which sizes to nothing)."""
    try:
        p = float(p_up)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(p) or direction not in (1, -1):
        return 0.0
    p = min(1.0, max(0.0, p))
    return p if direction == 1 else 1.0 - p


def latest_atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> Optional[float]:
    """Simple-mean ATR over the last `period` completed bars; None when it cannot be computed."""
    if len(df) < period + 1 or not {"High", "Low", "Close"} <= set(df.columns):
        return None
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.iloc[-period:].mean())
    return atr if math.isfinite(atr) and atr > 0 else None


def _tier(status: str) -> int:
    return VALIDATION_TIERS.index(status) if status in VALIDATION_TIERS else -1


def build_intents(candidates: Iterable[OrderIntent]) -> list[OrderIntent]:
    """Collapse candidates to ONE intent per (symbol, signal bar), across strategies and modes.

    Winner: highest validation tier, then highest confidence, then SELL over BUY (exits first),
    then smallest strategy_key. Output is sorted by (bar date, symbol) so runs are deterministic."""
    best: dict[tuple[str, str], OrderIntent] = {}
    for c in candidates:
        k = (c.symbol, c.signal_bar_date)
        cur = best.get(k)
        if cur is None or _rank(c) > _rank(cur):
            best[k] = c
    return [best[k] for k in sorted(best, key=lambda k: (k[1], k[0]))]


def _rank(c: OrderIntent) -> tuple:
    # larger tuple wins; strategy_key compares inverted via negated ordinals for a stable alphabetical tie-break
    return (_tier(c.validation_status), c.confidence, 1 if c.direction == -1 else 0,
            tuple(-ord(ch) for ch in c.strategy_key) + (1,))
