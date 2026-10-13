"""Label construction, kept separate from features so targets can never leak into X."""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def make_labels(df: pd.DataFrame, horizon: int = config.ML_HORIZON_BARS, dead_zone: float = 0.0) -> pd.DataFrame:
    """Forward-return labels for a `horizon`-bar direction target.

    Returns a frame indexed like `df` with:
      fwd_ret : Close[t+h] / Close[t] - 1 (NaN for the last `horizon` rows: unresolved)
      y       : 1.0 if fwd_ret > dead_zone, 0.0 if fwd_ret < -dead_zone, NaN otherwise
                (unresolved rows and exact zeros / the dead zone are NaN, never 0).
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    c = df["Close"].astype(float)
    fwd = c.shift(-horizon) / c - 1.0
    fwd = fwd.replace([np.inf, -np.inf], np.nan)
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[fwd > dead_zone] = 1.0
    y[fwd < -dead_zone] = 0.0
    y[fwd.isna()] = np.nan
    return pd.DataFrame({"fwd_ret": fwd, "y": y})
