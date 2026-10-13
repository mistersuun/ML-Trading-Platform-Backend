"""Synthetic universes with PLANTED edges for the pooled-validation tests (D18). Offline, deterministic.

``planted_days`` picks signal bars from the DATE alone, so a frame generator (which plants a jump on the bar after each
signal) and a pattern function (which fires on those bars) agree without sharing state."""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from tests.fixtures import synthetic

SYMBOLS10 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "SPY", "QQQ", "JPM", "XOM", "DIA"]
START = "2021-10-08"
N_BARS = 1070                        # ends in late 2025-10: about 90 bars of hold-out (HOLDOUT_START 2025-07-01)


def planted_days(index: pd.DatetimeIndex, salt: int = 1, p: float = 0.1, gap: int = 6) -> np.ndarray:
    """Boolean mask of signal bars chosen from the DATE alone, at least ``gap`` bars apart (none in the first 30)."""
    x = (np.asarray([d.toordinal() for d in index], dtype=np.uint64) + np.uint64(salt) * np.uint64(1_000_003))
    with np.errstate(over="ignore"):             # a splitmix-style integer hash: salts give independent schedules
        x = (x ^ (x >> np.uint64(16))) * np.uint64(0x45D9F3B)
        x = (x ^ (x >> np.uint64(16))) * np.uint64(0x45D9F3B)
        x = x ^ (x >> np.uint64(16))
    raw = (x % np.uint64(10_000)).astype(np.int64) < int(p * 10_000)
    m = np.zeros(len(index), dtype=bool)
    last = -10 ** 9
    for t in np.flatnonzero(raw):
        if t >= 30 and t - last >= gap:
            m[t] = True
            last = t
    return m


def planted_frame(seed: int, edge: bool = True, jump: float = 0.06, salt: int = 1, p: float = 0.1,
                  window: tuple | None = None, n: int = N_BARS, spread: int = 3, start: str = START) -> pd.DataFrame:
    """GBM bars; after every planted signal bar the next ``spread`` bars (the position's life) share a total jump of
    ``jump`` when ``edge`` (and only bars inside ``window`` when given). Spread over several bars so the edge
    survives a one-bar signal delay (the pooled delay stress is a GATE)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, periods=n)
    r = rng.normal(0.0002, 0.011, n)
    sig = planted_days(idx, salt, p)
    if edge:
        for t in np.flatnonzero(sig):
            for k in range(1, spread + 1):
                b = t + k
                if b < n and (window is None or window[0] <= b < window[1]):
                    r[b] = np.log1p(jump) / spread + rng.normal(0, 0.002)
    close = 100.0 * np.exp(np.cumsum(r))
    return synthetic._ohlc_from_close(close, rng, idx, intraday_vol=0.006)


def planted_pattern(salt: int = 1, p: float = 0.1, hold: int = 3):
    """Pattern fn: +1 on planted bars, -1 ``hold`` bars later (the exit signal)."""
    def fn(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        sig = np.zeros(len(df), dtype=int)
        m = planted_days(df.index, salt, p)
        sig[m] = 1
        ex = np.flatnonzero(m) + hold
        ex = ex[ex < len(df)]
        sig[ex] = np.where(sig[ex] == 0, -1, sig[ex])
        out["signal"] = sig
        return out
    return fn


def planted_universe(symbols=SYMBOLS10, edge_symbols=None, jump: float = 0.06, window=None, salt: int = 1,
                     n: int = N_BARS) -> dict:
    edge_symbols = set(symbols if edge_symbols is None else edge_symbols)
    return {s: planted_frame(seed=100 + i, edge=s in edge_symbols, jump=jump, salt=salt, window=window, n=n)
            for i, s in enumerate(symbols)}


def noise_frames(symbols=SYMBOLS10, n: int = N_BARS, common: float = 0.0, seed: int = 7) -> dict:
    """Pure-noise universe; ``common`` adds a market factor shared by all symbols."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=START, periods=n)
    factor = rng.normal(0, 0.011, n)
    out = {}
    for i, s in enumerate(symbols):
        own = rng.normal(0.0002, 0.011, n)
        r = np.sqrt(common) * factor + np.sqrt(1 - common) * own if common else own
        close = 100.0 * np.exp(np.cumsum(r))
        out[s] = synthetic._ohlc_from_close(close, rng, idx, intraday_vol=0.006)
    return out


def use_universe(mp, symbols=SYMBOLS10):
    """Make ``symbols`` the registered pooled universe (the executable WATCHLIST['stocks'] rule)."""
    mp.setitem(config.WATCHLIST, "stocks", list(symbols))


def use_small_family(mp, names):
    """Treat ``names`` as the whole pooled pattern family (floor N, BH family and 'narrowed' all follow it)."""
    import pooled_validation as PV
    names = list(names)
    mp.setattr(PV, "pooled_pattern_names", lambda registry=None: sorted(names))
    mp.setattr(PV, "pooled_universe_trials", lambda: len(names))


def null_setup(symbols=("AAPL", "MSFT", "SPY", "JPM"), frames=None, salt=1):
    """SymbolData + OOS TradeRecs of the planted pattern on a few symbols (inputs of the cluster-preserving null).
    ``frames`` (symbol -> frame) replaces the default planted-edge frames, e.g. pure-noise ones."""
    import pooled_validation as PV
    import validation as V
    frames = frames or {s: planted_frame(100 + i) for i, s in enumerate(symbols)}
    symbols = tuple(frames)
    sds = {}
    for s, df in frames.items():
        sd = PV._prepare_symbol(s, df, config.HOLDOUT_START)
        sd.sigs = V.compute_signals(df, {"p": planted_pattern(salt)}, ["p"])
        sds[s] = sd
    cand = V.executable_candidates(["p"])[0]
    recs = []
    for s in symbols:
        sd = sds[s]
        wf = V.walk_forward(sd.df, [cand], signals=sd.sigs, train=252, test=126, step=126)
        sd.test_start, sd.oos_end = wf.folds[0].test_start, wf.folds[-1].test_end
        recs += PV._recs_from_oos(sd, wf.oos_trades)
    return sds, recs
