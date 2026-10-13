"""Causality helper: output on df[:T] must equal the first T rows of output on df[:T]+future bars."""
from __future__ import annotations

import numpy as np
import pandas as pd


def extend_with_random_bars(df: pd.DataFrame, n_future: int, seed: int = 123, sigma: float = 0.03) -> pd.DataFrame:
    """Append n_future random OHLCV bars (large random moves) after df's last bar."""
    rng = np.random.default_rng(seed)
    last = float(df["Close"].iloc[-1])
    close = last * np.exp(np.cumsum(rng.normal(0, sigma, n_future)))
    prev = np.concatenate([[last], close[:-1]])
    idx = pd.bdate_range(df.index[-1] + pd.offsets.BDay(1), periods=n_future)
    fut = pd.DataFrame({
        "Open": prev, "High": np.maximum(prev, close) * 1.01, "Low": np.minimum(prev, close) * 0.99,
        "Close": close, "Volume": rng.integers(500_000, 5_000_000, n_future),
    }, index=idx)
    return pd.concat([df, fut])


def _cmp_frames(short, long_, label):
    short = short.to_frame() if isinstance(short, pd.Series) else short
    long_ = long_.to_frame() if isinstance(long_, pd.Series) else long_
    long_ = long_.iloc[: len(short)]
    assert list(short.columns) == list(long_.columns), f"{label}: columns differ"
    for col in short.columns:
        a, b = short[col], long_[col]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            av, bv = a.to_numpy(float), b.to_numpy(float)
            same = np.isclose(av, bv, rtol=1e-9, atol=1e-12, equal_nan=True)
        else:
            av = a.astype(object).where(a.notna(), "<NA>").astype(str).to_numpy()
            bv = b.astype(object).where(b.notna(), "<NA>").astype(str).to_numpy()
            same = av == bv
        assert same.all(), (
            f"{label}: column {col!r} is not causal; {(~same).sum()} of {len(same)} rows changed when "
            f"future bars were appended (first at position {int(np.argmin(same))})"
        )


def assert_causal(fn, frames, T: int, n_future: int = 60, seed: int = 123):
    """fn(*frames) -> DataFrame/Series. Compare fn on frames[:T] vs first T rows of fn on frames[:T]+future bars.

    `frames` is one DataFrame or a tuple/list of DataFrames sharing an index.
    """
    if isinstance(frames, pd.DataFrame):
        frames = (frames,)
    base = [f.iloc[:T] for f in frames]
    ext = [extend_with_random_bars(f, n_future, seed=seed + i) for i, f in enumerate(base)]
    out_base = fn(*base)
    out_ext = fn(*ext)
    _cmp_frames(out_base, out_ext.iloc[:T] if hasattr(out_ext, "iloc") else out_ext, getattr(fn, "__name__", "fn"))
