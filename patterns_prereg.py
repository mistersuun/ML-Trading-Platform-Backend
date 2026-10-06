"""Pre-registered research patterns for the POOLED (D18) shadow evaluation only.

Rules and parameters are copied verbatim from docs/research/preregistration-2026-10.md section 3. They are fixed,
published values: do not tune. Any change after a result has been seen is a NEW trial (N += 1), and a code change that
makes the function differ from the document is only a bug fix if it restores the document's rule exactly.

This module is deliberately NOT part of ``patterns.PATTERN_REGISTRY``: the per-symbol scan, ``universe_trials()`` (39 x 20),
the ``patterns.py`` module hash and the per-symbol ``strategy_version`` stay byte-identical while the forward test runs.
Only ``pooled_validation.pooled_registry()`` reads ``PREREG_PATTERNS``.

Convention: ``df -> df`` with a ``signal`` column (+1 buy, -1 exit, 0 none). Everything is causal: row t uses bars <= t.
A signal on bar t is filled at the OPEN of t+1 by the engine (``EXECUTION_MODE = "next_open"``).
"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import ta


def rsi2_pullback(df: pd.DataFrame) -> pd.DataFrame:
    """P1 (Connors & Alvarez 2008): buy RSI(2) < 10 above the 200-day SMA; exit on a close above SMA(5)."""
    out = df.copy()
    c = out["Close"]
    sma200 = c.rolling(200, min_periods=200).mean()
    sma5 = c.rolling(5, min_periods=5).mean()
    rsi2 = ta.momentum.rsi(c, window=2, fillna=False)
    entry = (c > sma200) & (rsi2 < 10)
    exit_ = (c > sma5) & ~entry          # entry wins if both hold on the same bar
    out["signal"] = 0
    out.loc[entry, "signal"] = 1
    out.loc[exit_, "signal"] = -1
    return out


def high52_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """P2 (Huddart-Lang-Yetman 2009; George-Hwang 2004): first close above the highest close of the prior 252 bars."""
    out = df.copy()
    c = out["Close"]
    prior_high = c.rolling(252, min_periods=252).max().shift(1)   # max close of bars t-252 .. t-1
    above = c > prior_high
    entry = above & ~above.shift(1, fill_value=False)             # first close above (a crossing)
    out["signal"] = 0
    out.loc[entry, "signal"] = 1                                  # no signal exit
    return out


PREREG_PATTERNS: dict[str, Callable] = {
    "rsi2_pullback": rsi2_pullback,
    "high52_breakout": high52_breakout,
}
