"""Seeded synthetic market-data generators (offline, deterministic).

All frames have a DatetimeIndex and columns Open/High/Low/Close/Volume with valid
OHLC invariants: Low <= min(Open, Close) and High >= max(Open, Close), all > 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_START = "2020-01-01"


def _ohlc_from_close(close: np.ndarray, rng: np.random.Generator, index: pd.DatetimeIndex,
                     intraday_vol: float = 0.01, mean_volume: float = 1_000_000) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    n = len(close)
    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = prev * np.exp(rng.normal(0, intraday_vol * 0.3, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, intraday_vol, n)))
    lo = np.minimum(open_, close) * (1 - np.minimum(np.abs(rng.normal(0, intraday_vol, n)), 0.5))
    vol = np.round(rng.lognormal(np.log(mean_volume), 0.3, n)).astype("int64")
    return pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=index)


def _bdays(n: int, start: str = DEFAULT_START) -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def gbm_ohlc(n: int = 500, seed: int = 42, mu: float = 0.08, sigma: float = 0.2,
             start_price: float = 100.0, start: str = DEFAULT_START) -> pd.DataFrame:
    """Geometric Brownian motion; mu/sigma annualised, 252 trading days/year."""
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    z = rng.standard_normal(n)
    log_ret = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * z
    close = start_price * np.exp(np.cumsum(log_ret))
    return _ohlc_from_close(close, rng, _bdays(n, start), intraday_vol=sigma / np.sqrt(252))


def random_walk_ohlc(n: int = 500, seed: int = 1, step: float = 1.0, start_price: float = 100.0,
                     start: str = DEFAULT_START) -> pd.DataFrame:
    """Arithmetic random walk (prices floored at 1.0 to stay positive)."""
    rng = np.random.default_rng(seed)
    close = np.maximum(start_price + np.cumsum(rng.normal(0, step, n)), 1.0)
    return _ohlc_from_close(close, rng, _bdays(n, start), intraday_vol=step / start_price)


def ou_pair(n: int = 500, seed: int = 7, beta: float = 1.5, half_life: float = 10.0,
            start: str = DEFAULT_START) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cointegrated pair: log(A) = c + beta*log(B) + OU spread with the given half-life.

    Returns (frame_a, frame_b). B is a GBM; the spread is mean-reverting.
    """
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    log_b = np.log(100.0) + np.cumsum((0.05 - 0.02) * dt + 0.2 * np.sqrt(dt) * rng.standard_normal(n))
    phi = np.exp(-np.log(2) / half_life)
    eps = rng.normal(0, 0.01, n)
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = phi * spread[t - 1] + eps[t]
    log_a = np.log(50.0) + beta * (log_b - log_b[0]) + spread
    idx = _bdays(n, start)
    a = _ohlc_from_close(np.exp(log_a), np.random.default_rng(seed + 1), idx)
    b = _ohlc_from_close(np.exp(log_b), np.random.default_rng(seed + 2), idx)
    return a, b


def _flat_frame(n: int, price: float, start: str = DEFAULT_START, freq: str = "B") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n, freq=freq)
    return pd.DataFrame({"Open": price, "High": price * 1.005, "Low": price * 0.995, "Close": price,
                         "Volume": 1_000_000}, index=idx)


def gap_through_stop_frame() -> pd.DataFrame:
    """40 flat bars at 100, then bar 30 gaps DOWN: open 80, high 82, low 78, close 81.

    Expected behaviour: a long position with a stop at 95 can NOT be filled at 95 on the
    gap bar; the earliest realistic fill is the gap open (80), i.e. exit price <= 80.
    Bars after the gap stay near 81. Gap bar position: iloc[30].
    """
    df = _flat_frame(40, 100.0)
    df.iloc[30:, df.columns.get_loc("Open")] = 81.0
    df.iloc[30:, df.columns.get_loc("High")] = 81.4
    df.iloc[30:, df.columns.get_loc("Low")] = 80.6
    df.iloc[30:, df.columns.get_loc("Close")] = 81.0
    df.iloc[30] = [80.0, 82.0, 78.0, 81.0, 5_000_000]
    return df


def split_day_frame(n: int = 40, split_at: int = 20) -> pd.DataFrame:
    """Unadjusted 2:1 split: price ~200 before bar `split_at`, ~100 after; volume doubles.

    Expected behaviour: a split is not a -50% return; adjusted-aware code must not treat
    the split bar as a crash (the raw close-to-close change at split_at is about -50%).
    """
    df = _flat_frame(n, 200.0)
    for col in ["Open", "High", "Low", "Close"]:
        df.iloc[split_at:, df.columns.get_loc(col)] /= 2.0
    df.iloc[split_at:, df.columns.get_loc("Volume")] *= 2
    return df


def zero_volume_fx_frame(n: int = 100, seed: int = 3, start: str = DEFAULT_START) -> pd.DataFrame:
    """FX-style data: realistic prices around 1.10 but Volume is always 0."""
    rng = np.random.default_rng(seed)
    close = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    df = _ohlc_from_close(close, rng, _bdays(n, start), intraday_vol=0.002)
    df["Volume"] = 0
    return df


def crypto_weekend_frame(n: int = 200, seed: int = 5, start: str = DEFAULT_START) -> pd.DataFrame:
    """Crypto-style data on calendar days (includes Saturdays and Sundays)."""
    rng = np.random.default_rng(seed)
    close = 30000 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    idx = pd.date_range(start=start, periods=n, freq="D")
    return _ohlc_from_close(close, rng, idx, intraday_vol=0.03)


def assert_ohlc_valid(df: pd.DataFrame) -> None:
    """Raise AssertionError if the frame violates OHLC invariants."""
    assert list(df.columns[:5]) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert not df[["Open", "High", "Low", "Close"]].isna().any().any()
    assert (df[["Open", "High", "Low", "Close"]] > 0).all().all()
    assert (df["High"] >= df[["Open", "Close"]].max(axis=1) - 1e-12).all()
    assert (df["Low"] <= df[["Open", "Close"]].min(axis=1) + 1e-12).all()
    assert (df["Volume"] >= 0).all()
